#!/usr/bin/env python3
"""Pull public image tags and GitHub Actions runs into data/status.json."""

from __future__ import annotations

import json
import http.client
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CATALOG_PATH = ROOT / "products.json"
OUTPUT_PATH = ROOT / "data" / "status.json"
USER_AGENT = "ai-platform-wayfinder"
CHANNEL_NAMES = ("latest", "main", "nightly", "stable", "odh-stable", "odh-pr")
NOISE_SUFFIXES = (".sig", ".att", ".sbom")


def is_noise_tag(name: str) -> bool:
    if name.startswith("sha256-"):
        return True
    return name.endswith(NOISE_SUFFIXES)


def is_detail_tag(name: str) -> bool:
    """Arch slices and per-task Konflux tags. The commit and PR tags cover the same push."""
    lowered = name.lower()
    if "build-image" in lowered:
        return True
    if lowered.endswith((".prefetch", ".git", "-linux-amd64", "-linux-arm64")):
        return True
    return "-linux-" in lowered


def tag_time(tag: dict) -> datetime:
    raw = tag.get("last_modified") or tag.get("lastModified") or ""
    try:
        parsed = parsedate_to_datetime(raw)
    except (TypeError, ValueError, IndexError):
        return datetime.min.replace(tzinfo=timezone.utc)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def select_channels(tags: list[dict]) -> list[dict]:
    newest: dict[str, dict] = {}
    for tag in tags:
        name = tag.get("name") or ""
        if name not in CHANNEL_NAMES or is_noise_tag(name):
            continue
        current = newest.get(name)
        if current is None or tag_time(tag) > tag_time(current):
            newest[name] = tag
    return [newest[name] for name in CHANNEL_NAMES if name in newest]


def select_build_tags(tags: list[dict], limit: int = 6) -> list[dict]:
    clean = [
        tag
        for tag in tags
        if tag.get("name")
        and not is_noise_tag(tag["name"])
        and not is_detail_tag(tag["name"])
        and tag["name"] not in CHANNEL_NAMES
    ]
    clean.sort(key=tag_time, reverse=True)
    chosen = []
    seen_digests = set()
    for tag in clean:
        digest = tag.get("manifest_digest") or tag.get("digest")
        if digest and digest in seen_digests:
            continue
        if digest:
            seen_digests.add(digest)
        chosen.append(tag)
        if len(chosen) >= limit:
            break
    return chosen


def public_tag(tag: dict) -> dict:
    return {
        "name": tag.get("name"),
        "digest": tag.get("manifest_digest") or tag.get("digest"),
        "lastModified": tag.get("last_modified") or tag.get("lastModified"),
    }


def registry_url(repository: str) -> str:
    namespace, name = repository.removeprefix("quay.io/").split("/", 1)
    return f"https://quay.io/repository/{namespace}/{name}"


def summarize_image(image: dict, tags: list[dict]) -> dict:
    builds = [public_tag(tag) for tag in select_build_tags(tags)]
    channels = [public_tag(tag) for tag in select_channels(tags)]
    pull_tag = image.get("pullTag") or "odh-stable"
    pull = next((tag for tag in channels if tag["name"] == pull_tag), None)
    last_modified = (pull or (builds[0] if builds else None) or {}).get("lastModified")
    return {
        "name": image["name"],
        "role": image.get("role", ""),
        "repository": image["repository"],
        "registryUrl": registry_url(image["repository"]),
        "pullTag": pull_tag,
        "pull": pull,
        "lastModified": last_modified,
        "channels": channels,
        "builds": builds,
    }


def summarize_workflow(workflow: dict, run: dict | None) -> dict:
    summary = {
        "name": workflow["name"],
        "repo": workflow["repo"],
        "conclusion": None,
        "status": "missing",
        "branch": None,
        "sha": None,
        "updatedAt": None,
        "url": f"https://github.com/{workflow['repo']}/actions",
    }
    if not run:
        return summary
    summary.update(
        {
            "conclusion": run.get("conclusion"),
            "status": run.get("status") or "completed",
            "branch": run.get("head_branch"),
            "sha": run.get("head_sha"),
            "updatedAt": run.get("updated_at"),
            "url": run.get("html_url") or summary["url"],
        }
    )
    return summary


def fetch_version_tag(repository: str, version: str) -> dict | None:
    """Look up a specific version tag on a Quay repository."""
    namespace, name = quay_namespace(repository)
    base = f"https://quay.io/api/v1/repository/{namespace}/{name}/tag/"
    data = fetch_json(f"{base}?specificTag={version}&limit=1&onlyActiveTags=true")
    tags = (data or {}).get("tags") or []
    return next((tag for tag in tags if tag.get("name") == version), None)


def fetch_releases(repo: str, headers: dict[str, str]) -> list[dict]:
    """Fetch rhaiv release tags, sorted by numeric version across all pages."""
    releases = []
    page = 1
    while True:
        payload = fetch_json(
            f"https://api.github.com/repos/{repo}/tags?per_page=100&page={page}",
            headers,
        )
        if payload is None:
            raise RuntimeError(f"GitHub release repository not found: {repo}")
        for tag_obj in payload:
            name = tag_obj.get("name") or ""
            match = re.fullmatch(r"v(\d+\.\d+\.\d+)-rhaiv\.(\d+)", name)
            if not match:
                continue
            releases.append(
                {
                    "tag": name,
                    "upstreamVersion": match.group(1),
                    "rhaivNumber": int(match.group(2)),
                    "commitSha": tag_obj.get("commit", {}).get("sha"),
                }
            )
        if len(payload) < 100:
            break
        page += 1
    releases.sort(
        key=lambda r: (tuple(map(int, r["upstreamVersion"].split("."))), r["rhaivNumber"]),
        reverse=True,
    )
    return releases


def correlate_releases(
    releases: list[dict],
    images: list[dict],
    tags_by_repo: dict[str, list[dict]],
    limit: int = 8,
) -> list[dict]:
    """For each release tag, check which images have a matching Quay tag."""
    release_images = [img for img in images if img.get("release")]
    result = []
    for rel in releases[:limit]:
        tag_name = rel["tag"]
        components = []
        all_found = bool(release_images)
        for img in release_images:
            repo = img["repository"]
            quay_tag = fetch_version_tag(repo, tag_name)
            found = quay_tag is not None
            if not found:
                all_found = False
            components.append(
                {
                    "name": img["name"],
                    "repository": repo,
                    "registryUrl": registry_url(repo),
                    "tag": tag_name,
                    "found": found,
                    "digest": (quay_tag.get("manifest_digest") if quay_tag else None),
                    "lastModified": (
                        quay_tag.get("last_modified") if quay_tag else None
                    ),
                }
            )
        result.append(
            {
                "tag": tag_name,
                "upstreamVersion": rel["upstreamVersion"],
                "rhaivNumber": rel["rhaivNumber"],
                "commitSha": rel.get("commitSha"),
                "allBuilt": all_found,
                "components": components,
            }
        )
    return result


def build_status(
    catalog: dict,
    tags_by_repo: dict[str, list[dict]],
    runs_by_key: dict[tuple[str, str], dict | None],
    releases_by_repo: dict[str, list[dict]],
    generated_at: str,
) -> dict:
    products = []
    for product in catalog["products"]:
        release_repo = product.get("releaseRepo")
        releases = []
        if release_repo and release_repo in releases_by_repo:
            releases = correlate_releases(
                releases_by_repo[release_repo],
                product.get("images") or [],
                tags_by_repo,
            )
        products.append(
            {
                "key": product["key"],
                "name": product["name"],
                "tagline": product.get("tagline", ""),
                "summary": product.get("summary", ""),
                "requirements": product.get("requirements", ""),
                "source": product.get("source"),
                "docs": product.get("docs") or [],
                "downloads": product.get("downloads") or [],
                "images": [
                    summarize_image(image, tags_by_repo.get(image["repository"], []))
                    for image in product.get("images") or []
                ],
                "workflows": [
                    summarize_workflow(
                        workflow, runs_by_key.get((workflow["repo"], workflow["name"]))
                    )
                    for workflow in product.get("workflows") or []
                ],
                "releases": releases,
            }
        )
    return {"generatedAt": generated_at, "products": products}


def load_catalog(path: Path) -> dict:
    with path.open(encoding="utf-8") as handle:
        catalog = json.load(handle)
    if not isinstance(catalog.get("products"), list) or not catalog["products"]:
        raise ValueError(f"{path} must contain a non-empty products list")
    return catalog


def github_token() -> str | None:
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        return token
    try:
        completed = subprocess.run(
            ["gh", "auth", "token"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    token = completed.stdout.strip()
    return token or None


def fetch_json(url: str, headers: dict[str, str] | None = None) -> dict | None:
    for attempt in range(3):
        try:
            return fetch_json_once(url, headers)
        except (http.client.IncompleteRead, http.client.RemoteDisconnected, TimeoutError) as exc:
            print(json.dumps({
                "stage": "fetch_source", "code": "SOURCE_TRANSPORT_INTERRUPTED",
                "url": url, "attempt": attempt + 1, "cause": type(exc).__name__,
            }), file=sys.stderr)
            if attempt == 2:
                raise RuntimeError(f"Source fetch interrupted after 3 attempts: {url}") from exc
            time.sleep(attempt + 1)
    raise AssertionError("unreachable")


def fetch_json_once(url: str, headers: dict[str, str] | None = None) -> dict | None:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **(headers or {})})
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        detail = exc.read().decode("utf-8", errors="replace")[:300]
        raise RuntimeError(f"GET {url} failed: HTTP {exc.code} {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"GET {url} failed: {exc.reason}") from exc


def quay_namespace(repository: str) -> tuple[str, str]:
    if not repository.startswith("quay.io/"):
        raise ValueError(f"{repository} is not a quay.io repository")
    namespace, name = repository.removeprefix("quay.io/").split("/", 1)
    return namespace, name


def fetch_image_tags(repository: str) -> list[dict]:
    namespace, name = quay_namespace(repository)
    base = f"https://quay.io/api/v1/repository/{namespace}/{name}/tag/"
    listing = fetch_json(f"{base}?limit=100&page=1&onlyActiveTags=true")
    if listing is None:
        raise RuntimeError(f"Quay repository not found: {repository}")
    tags = list(listing.get("tags") or [])
    seen = {(tag.get("name"), tag.get("manifest_digest"), tag.get("last_modified")) for tag in tags}
    for channel in CHANNEL_NAMES:
        extra = fetch_json(f"{base}?specificTag={channel}&limit=10")
        for tag in (extra or {}).get("tags") or []:
            key = (tag.get("name"), tag.get("manifest_digest"), tag.get("last_modified"))
            if key not in seen:
                seen.add(key)
                tags.append(tag)
    return tags


def github_headers() -> dict[str, str]:
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = github_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def fetch_workflow_index(repo: str, headers: dict[str, str]) -> dict[str, dict]:
    found: dict[str, dict] = {}
    for page in range(1, 6):
        payload = fetch_json(
            f"https://api.github.com/repos/{repo}/actions/workflows?per_page=100&page={page}",
            headers,
        )
        if payload is None:
            raise RuntimeError(f"GitHub repository not found: {repo}")
        workflows = payload.get("workflows") or []
        for workflow in workflows:
            found[workflow["name"]] = workflow
        if len(workflows) < 100:
            break
    return found


def fetch_latest_run(repo: str, workflow_id: int, headers: dict[str, str]) -> dict | None:
    payload = fetch_json(
        f"https://api.github.com/repos/{repo}/actions/workflows/{workflow_id}/runs?per_page=1",
        headers,
    )
    if payload is None:
        return None
    runs = payload.get("workflow_runs") or []
    return runs[0] if runs else None


def collect(catalog: dict) -> dict:
    headers = github_headers()
    tags_by_repo: dict[str, list[dict]] = {}
    runs_by_key: dict[tuple[str, str], dict | None] = {}
    workflow_indexes: dict[str, dict[str, dict]] = {}
    releases_by_repo: dict[str, list[dict]] = {}

    for product in catalog["products"]:
        for image in product.get("images") or []:
            repository = image["repository"]
            if repository not in tags_by_repo:
                tags_by_repo[repository] = fetch_image_tags(repository)
        for workflow in product.get("workflows") or []:
            repo = workflow["repo"]
            if repo not in workflow_indexes:
                workflow_indexes[repo] = fetch_workflow_index(repo, headers)
            match = workflow_indexes[repo].get(workflow["name"])
            if match is None:
                runs_by_key[(repo, workflow["name"])] = None
                continue
            runs_by_key[(repo, workflow["name"])] = fetch_latest_run(repo, match["id"], headers)
        release_repo = product.get("releaseRepo")
        if release_repo and release_repo not in releases_by_repo:
            releases_by_repo[release_repo] = fetch_releases(release_repo, headers)

    generated_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    return build_status(catalog, tags_by_repo, runs_by_key, releases_by_repo, generated_at)


def main() -> int:
    catalog = load_catalog(CATALOG_PATH)
    status = collect(catalog)
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(status, indent=2) + "\n", encoding="utf-8")
    image_count = sum(len(product["images"]) for product in status["products"])
    workflow_count = sum(len(product["workflows"]) for product in status["products"])
    print(
        f"Wrote {OUTPUT_PATH.relative_to(ROOT)} "
        f"({len(status['products'])} products, {image_count} images, {workflow_count} workflows)"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
