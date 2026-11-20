import sys
import http.client
import io
import gzip
import tempfile
import urllib.error
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import sync

from sync import (  # noqa: E402
    build_status,
    collect,
    correlate_releases,
    fetch_releases,
    fetch_version_tag,
    is_detail_tag,
    is_noise_tag,
    select_build_tags,
    select_channels,
    load_catalog,
    CATALOG_PATH,
)


class TransportFailureTest(unittest.TestCase):
    def test_wrapped_connection_timeout_retries_then_recovers(self):
        timeout = urllib.error.URLError(TimeoutError("connection timed out"))
        response = io.BytesIO(b'{"tags": []}')
        response.headers = {}
        with (
            patch("sync.urllib.request.urlopen", side_effect=[timeout, response]) as request,
            patch("sync.time.sleep"),
            patch("sys.stderr", new_callable=io.StringIO) as diagnostic,
        ):
            self.assertEqual(sync.fetch_json("https://example.org/tags"), {"tags": []})
        self.assertEqual(request.call_count, 2)
        self.assertIn("SOURCE_TRANSPORT_INTERRUPTED", diagnostic.getvalue())

    def test_non_timeout_url_errors_do_not_retry(self):
        failure = urllib.error.URLError("DNS lookup failed")
        with patch("sync.urllib.request.urlopen", side_effect=failure) as request:
            with self.assertRaisesRegex(RuntimeError, "DNS lookup failed") as caught:
                sync.fetch_json("https://example.org/tags")
        self.assertIs(caught.exception.__cause__, failure)
        self.assertEqual(request.call_count, 1)

    def test_gzip_response_is_requested_and_decoded(self):
        response = io.BytesIO(gzip.compress(b'{"tags": [{"name": "odh-stable"}]}'))
        response.headers = {"Content-Encoding": "gzip"}
        with patch("sync.urllib.request.urlopen", return_value=response) as request:
            self.assertEqual(sync.fetch_json("https://example.org/tags"), {"tags": [{"name": "odh-stable"}]})
        self.assertEqual(request.call_args.args[0].get_header("Accept-encoding"), "gzip")

    def test_truncated_gzip_response_recovers_with_a_fresh_request(self):
        payload = gzip.compress(b'{"tags": []}')
        partial = io.BytesIO(payload[:-5])
        partial.headers = {"Content-Encoding": "gzip"}
        complete = io.BytesIO(payload)
        complete.headers = {"Content-Encoding": "gzip"}
        with (
            patch("sync.urllib.request.urlopen", side_effect=[partial, complete]) as request,
            patch("sync.time.sleep"),
            patch("sys.stderr", new_callable=io.StringIO) as diagnostic,
        ):
            self.assertEqual(sync.fetch_json("https://example.org/tags"), {"tags": []})
        self.assertEqual(request.call_count, 2)
        self.assertIn('"cause": "EOFError"', diagnostic.getvalue())

    def test_persistent_truncated_gzip_preserves_previous_snapshot(self):
        responses = []
        for _ in range(3):
            response = io.BytesIO(gzip.compress(b'{"tags": []}')[:-5])
            response.headers = {"Content-Encoding": "gzip"}
            responses.append(response)
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "status.json"
            output.write_text('{"previous": true}')
            with (
                patch.object(sync, "OUTPUT_PATH", output),
                patch("sync.load_catalog", return_value={"products": [{"key": "test", "images": [{"repository": "quay.io/example/image"}]}]}),
                patch("sync.urllib.request.urlopen", side_effect=responses) as request,
                patch("sync.time.sleep"),
                patch("sys.stderr", new_callable=io.StringIO) as diagnostic,
            ):
                with self.assertRaisesRegex(RuntimeError, "after 3 attempts") as caught:
                    sync.main()
            self.assertIsInstance(caught.exception.__cause__, EOFError)
            self.assertEqual(request.call_count, 3)
            self.assertEqual(output.read_text(), '{"previous": true}')
            self.assertEqual(diagnostic.getvalue().count("SOURCE_TRANSPORT_INTERRUPTED"), 3)

    def test_truncated_response_recovers_with_a_fresh_request(self):
        partial = http.client.IncompleteRead(b'{"tags":', 30)
        response = io.BytesIO(b'{"tags": []}')
        response.headers = {}
        with (
            patch("sync.urllib.request.urlopen", side_effect=[partial, response]) as request,
            patch("sync.time.sleep"),
            patch("sys.stderr", new_callable=io.StringIO) as diagnostic,
        ):
            self.assertEqual(sync.fetch_json("https://example.org/tags"), {"tags": []})
        self.assertEqual(request.call_count, 2)
        self.assertIn("SOURCE_TRANSPORT_INTERRUPTED", diagnostic.getvalue())

    def test_persistent_truncation_preserves_previous_snapshot(self):
        partial = http.client.IncompleteRead(b"partial", 30)
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "status.json"
            output.write_text('{"previous": true}')
            with (
                patch.object(sync, "OUTPUT_PATH", output),
                patch("sync.load_catalog", return_value={"products": [{"key": "test", "images": [{"repository": "quay.io/example/image"}]}]}),
                patch("sync.urllib.request.urlopen", side_effect=partial) as request,
                patch("sync.time.sleep"),
                patch("sys.stderr", new_callable=io.StringIO) as diagnostic,
            ):
                with self.assertRaisesRegex(RuntimeError, "after 3 attempts") as caught:
                    sync.main()
            self.assertIs(caught.exception.__cause__, partial)
            self.assertEqual(request.call_count, 3)
            self.assertEqual(output.read_text(), '{"previous": true}')
            self.assertEqual(diagnostic.getvalue().count("SOURCE_TRANSPORT_INTERRUPTED"), 3)

    def test_image_pages_are_combined_and_channel_queries_are_active_only(self):
        def fetch(url):
            if "specificTag=" in url:
                self.assertIn("limit=1&onlyActiveTags=true", url)
                return {"tags": []}
            if "page=1&" in url:
                return {"tags": [{"name": "build-a"}], "has_additional": True}
            self.assertIn("page=2&", url)
            return {"tags": [{"name": "build-b"}], "has_additional": False}
        with patch("sync.fetch_json", side_effect=fetch):
            tags = sync.fetch_image_tags("quay.io/opendatahub/example")
        self.assertEqual([tag["name"] for tag in tags], ["build-a", "build-b"])

    def test_build_discovery_continues_beyond_a_hundred_noise_tags(self):
        pages = 0
        def fetch(url):
            nonlocal pages
            if "specificTag=" in url:
                return {"tags": []}
            pages += 1
            if pages <= 21:
                return {"tags": [{"name": f"sha256-noise-{pages}-{i}.sig"} for i in range(5)], "has_additional": True}
            return {"tags": [{"name": "commit-abc", "manifest_digest": "sha256:abc"}], "has_additional": False}
        with patch("sync.fetch_json", side_effect=fetch):
            tags = sync.fetch_image_tags("quay.io/example/image")
        self.assertEqual([tag["name"] for tag in select_build_tags(tags)], ["commit-abc"])
        self.assertEqual(pages, 22)


class TagSelectionTest(unittest.TestCase):
    def test_noise_tags_are_signatures_and_attestations(self):
        self.assertTrue(is_noise_tag("sha256-abc.att"))
        self.assertTrue(is_noise_tag("odh-pr-108.sig"))
        self.assertTrue(is_noise_tag("image.sbom"))
        self.assertFalse(is_noise_tag("odh-pr-108"))
        self.assertFalse(is_noise_tag("odh-pr"))

    def test_build_tags_keep_newest_unique_digests(self):
        tags = [
            {
                "name": "odh-pr-108",
                "manifest_digest": "sha256:aaa",
                "last_modified": "Wed, 28 Oct 2026 18:57:08 -0000",
            },
            {
                "name": "odh-pr",
                "manifest_digest": "sha256:aaa",
                "last_modified": "Wed, 28 Oct 2026 18:57:07 -0000",
            },
            {
                "name": "sha256-fff.sig",
                "manifest_digest": "sha256:bbb",
                "last_modified": "Wed, 28 Oct 2026 18:57:09 -0000",
            },
            {
                "name": "odh-pr-107",
                "manifest_digest": "sha256:ccc",
                "last_modified": "Wed, 28 Oct 2026 18:40:00 -0000",
            },
        ]
        chosen = select_build_tags(tags, limit=6)
        self.assertEqual([tag["name"] for tag in chosen], ["odh-pr-108", "odh-pr-107"])

    def test_detail_tags_are_hidden_behind_the_commit_tag(self):
        self.assertTrue(is_detail_tag("odh-pr-linux-m2xlarge-arm64"))
        self.assertTrue(is_detail_tag("odh-openshell-gateway-on-pull-request-mgv5t-build-images-1"))
        self.assertTrue(is_detail_tag("odh-pr.prefetch"))
        self.assertTrue(is_detail_tag("odh-pr.git"))
        tags = [
            {
                "name": "odh-pr",
                "manifest_digest": "sha256:aaa",
                "last_modified": "Wed, 28 Oct 2026 18:57:09 -0000",
            },
            {
                "name": "odh-pr-d943e91fd3996cf999da0ba8e5d2e2d64c213dfc",
                "manifest_digest": "sha256:aaa",
                "last_modified": "Wed, 28 Oct 2026 18:57:08 -0000",
            },
            {
                "name": "odh-pr-linux-m2xlarge-amd64",
                "manifest_digest": "sha256:bbb",
                "last_modified": "Wed, 28 Oct 2026 18:52:56 -0000",
            },
        ]
        chosen = select_build_tags(tags, limit=6)
        self.assertEqual(
            [tag["name"] for tag in chosen],
            ["odh-pr-d943e91fd3996cf999da0ba8e5d2e2d64c213dfc"],
        )

    def test_channels_keep_the_newest_pointer_for_each_name(self):
        tags = [
            {
                "name": "odh-pr",
                "manifest_digest": "sha256:old",
                "last_modified": "Wed, 28 Oct 2026 18:41:21 -0000",
            },
            {
                "name": "odh-pr",
                "manifest_digest": "sha256:new",
                "last_modified": "Wed, 28 Oct 2026 18:56:53 -0000",
            },
            {
                "name": "odh-pr-108",
                "manifest_digest": "sha256:new",
                "last_modified": "Wed, 28 Oct 2026 18:57:08 -0000",
            },
        ]
        channels = select_channels(tags)
        self.assertEqual(len(channels), 1)
        self.assertEqual(channels[0]["manifest_digest"], "sha256:new")


class StatusAssemblyTest(unittest.TestCase):
    def test_build_status_shapes_images_and_missing_workflows(self):
        catalog = {
            "products": [
                {
                    "key": "openshell",
                    "name": "OpenShell",
                    "summary": "Public images and tests.",
                    "source": "https://github.com/opendatahub-io/openshell",
                    "images": [
                        {
                            "name": "Gateway",
                            "repository": "quay.io/opendatahub/odh-openshell-gateway",
                        }
                    ],
                    "workflows": [
                        {"repo": "opendatahub-io/openshell", "name": "Kubernetes E2E Test"},
                        {"repo": "opendatahub-io/openshell", "name": "Missing Workflow"},
                    ],
                }
            ]
        }
        tags = [
            {
                "name": "odh-pr-108",
                "manifest_digest": "sha256:aaa",
                "last_modified": "Wed, 28 Oct 2026 18:57:08 -0000",
            }
        ]
        runs = {
            ("opendatahub-io/openshell", "Kubernetes E2E Test"): {
                "conclusion": "success",
                "status": "completed",
                "head_branch": "main",
                "head_sha": "abc123",
                "updated_at": "2026-10-28T18:26:23Z",
                "html_url": "https://github.com/opendatahub-io/openshell/actions/runs/1",
            },
            ("opendatahub-io/openshell", "Missing Workflow"): None,
        }
        status = build_status(
            catalog,
            {"quay.io/opendatahub/odh-openshell-gateway": tags},
            runs,
            {},
            "2026-10-28T19:00:00+00:00",
        )
        image = status["products"][0]["images"][0]
        self.assertEqual(image["builds"][0]["name"], "odh-pr-108")
        self.assertEqual(
            image["registryUrl"],
            "https://quay.io/repository/opendatahub/odh-openshell-gateway",
        )
        workflows = status["products"][0]["workflows"]
        self.assertEqual(workflows[0]["conclusion"], "success")
        self.assertEqual(workflows[0]["branch"], "main")
        self.assertEqual(workflows[1]["status"], "missing")
        self.assertEqual(image["pullTag"], "odh-stable")
        self.assertEqual(status["products"][0]["docs"], [])


class ReleaseDiscoveryTest(unittest.TestCase):
    @patch("sync.fetch_json")
    def test_release_versions_sort_numerically_and_exclude_non_release_tags(self, fetch):
        fetch.return_value = [
            {"name": name, "commit": {"sha": name}}
            for name in [
                "v0.1.9-rhaiv.9", "v0.1.10-rhaiv.2", "v0.1.10-rhaiv.10",
                "v0.2.0-rhaiv.0", "v0.1.7", "v0.1.10-rhaiv.11.prefetch",
            ]
        ]
        releases = fetch_releases("opendatahub-io/openshell", {})
        self.assertEqual(
            [release["tag"] for release in releases],
            ["v0.2.0-rhaiv.0", "v0.1.10-rhaiv.10", "v0.1.10-rhaiv.2", "v0.1.9-rhaiv.9"],
        )

    @patch("sync.fetch_json")
    def test_release_discovery_continues_past_a_page_of_unrelated_tags(self, fetch):
        fetch.side_effect = [
            [{"name": f"unrelated-{i}"} for i in range(100)],
            [{"name": "v0.1.6-rhaiv.4", "commit": {"sha": "midstream"}}],
        ]
        releases = fetch_releases("opendatahub-io/openshell", {})
        self.assertEqual(releases[0]["tag"], "v0.1.6-rhaiv.4")
        self.assertIn("page=2", fetch.call_args.args[0])

    @patch("sync.fetch_json", return_value=None)
    def test_missing_release_repository_is_an_error(self, fetch):
        with self.assertRaisesRegex(RuntimeError, "release repository not found"):
            fetch_releases("opendatahub-io/missing", {})

    @patch("sync.fetch_json")
    def test_version_lookup_requires_an_exact_active_tag(self, fetch):
        fetch.return_value = {"tags": [{"name": "odh-stable"}]}
        self.assertIsNone(fetch_version_tag("quay.io/opendatahub/gateway", "v0.1.7-rhaiv.0"))
        self.assertIn("onlyActiveTags=true", fetch.call_args.args[0])

    @patch("sync.fetch_version_tag")
    def test_partial_release_and_empty_component_list_are_not_all_built(self, fetch):
        release = {"tag": "v0.1.6-rhaiv.4", "upstreamVersion": "0.1.6", "rhaivNumber": 4}
        images = [
            {"name": "Gateway", "repository": "quay.io/opendatahub/gateway", "release": True},
            {"name": "CLI", "repository": "quay.io/opendatahub/cli", "release": True},
        ]
        fetch.side_effect = [{"manifest_digest": "sha256:gateway"}, None]
        result = correlate_releases([release], images, {})[0]
        self.assertFalse(result["allBuilt"])
        self.assertTrue(result["components"][0]["found"])
        self.assertFalse(result["components"][1]["found"])
        self.assertFalse(correlate_releases([release], [], {})[0]["allBuilt"])

    def test_openshell_catalog_discovers_public_images_from_midstream(self):
        catalog = load_catalog(CATALOG_PATH)
        release = {"tag": "v0.1.6-rhaiv.4", "upstreamVersion": "0.1.6", "rhaivNumber": 4}
        with (
            patch("sync.github_headers", return_value={}),
            patch("sync.fetch_image_tags", return_value=[]),
            patch("sync.fetch_workflow_index", return_value={}),
            patch("sync.fetch_releases", side_effect=lambda repo, headers: [release] if repo == "opendatahub-io/openshell" else []),
            patch("sync.fetch_version_tag", return_value={"manifest_digest": "sha256:built"}),
        ):
            product = collect(catalog)["products"][0]
        self.assertEqual(product["releases"][0]["tag"], "v0.1.6-rhaiv.4")
        self.assertTrue(product["releases"][0]["allBuilt"])


if __name__ == "__main__":
    unittest.main()
