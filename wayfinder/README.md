# AI Platform Wayfinder

Public status site for Red Hat OpenShift AI components. Shows container image
builds from [Quay](https://quay.io/organization/opendatahub), CI results from
GitHub Actions, install commands, and documentation links.

Hosted from `opendatahub-io/agent-ops` at
<https://opendatahub-io.github.io/agent-ops/wayfinder/>. The initial catalog covers
OpenShell; the catalog format supports additional products.

## Deployment and refresh

Enable **Settings → Pages → Source: GitHub Actions** in this repository. After
merging, `Wayfinder sync and deploy` refreshes the public data and deploys on site
changes, manual dispatch, and a nominal 15-minute schedule. GitHub may delay or
skip scheduled runs. Its standard `GITHUB_TOKEN` supplies public GitHub API access;
no personal token or additional secret is needed. Organization policies must allow
the workflow actions and the `github-pages` deployment environment.

The workflow publishes only the static site and generated status JSON. Guides,
tests, and sync code stay in the source repository. A failed refresh fails the
build and leaves the last successful deployment available; check Actions for the
failure and the page's sync timestamp for data age. Generated data is not committed.

Source migrated from `dimakis/ai-platform-status` at
`8ebc7ca615c3d41325b86ea1b09127e5d1589706`, including the OpenShell fixes from
<https://github.com/dimakis/ai-platform-status/pull/1>.

The site is a single-page app with a persistent sidebar listing every product.
Adding a new product means adding an entry to `products.json` and running sync.

## OpenShell sources

- **Getting started:** [Agent Ops on OpenShift](https://github.com/opendatahub-io/agent-ops/blob/main/guides/getting-started-openshell-openshift.md).
  Follow the guide's pinned versions and prerequisites. It describes an
  experimental installation for isolated test clusters; its examples do not
  validate the newest images on this site.
- **ODH midstream:** [opendatahub-io/openshell](https://github.com/opendatahub-io/openshell)
  supplies the release tags matched against public `quay.io/opendatahub` images.
- **RHDS downstream:** [red-hat-data-services/openshell](https://github.com/red-hat-data-services/openshell)
  is a separate source. Its workflow results are labeled separately from ODH.

The Releases tab discovers `vX.Y.Z-rhaiv.N` tags from ODH midstream and checks
the four core images in Quay. An NVIDIA upstream release alone does not create
an ODH release entry. Published images, test-image availability, and independent
CI runs do not prove that a particular image set passed deployment validation.
The Releases tab offers pull commands for the highest versioned release when
all its core images are available. If that release is incomplete, it shows the
missing images instead of silently choosing an older release. `odh-stable`
continues to track builds independently of these version tags.

## Quick start

```bash
cd wayfinder
python3 sync.py          # fetch latest tags and workflow runs
python3 -m http.server 8787   # open http://localhost:8787
```

## How it works

| File              | Purpose |
|---|---|
| `products.json`   | Product catalog — names, docs, install commands, image repos, workflows |
| `sync.py`         | Reads public Quay and GitHub APIs, writes `data/status.json` |
| `index.html`      | Shell: sidebar + content area |
| `app.js`          | Fetches catalog + status, renders the selected product |
| `styles.css`      | Layout and design tokens |

## Data sources

Only public APIs — no credentials stored, no OAuth proxy, no private registries.

- **Quay tags**: `https://quay.io/api/v1/repository/{ns}/{name}/tag/`
- **GitHub Actions**: workflow index + latest run per workflow
- **GitHub token**: `gh auth token` (optional, raises rate limit)

`quay.io/rhoai` and Konflux data stay off this site until those results are public.

## Adding a product

1. Add an object to `products.json` → `products[]`.
2. Run `python3 sync.py`.
3. Open the site — the new product appears in the sidebar.

Required fields: `key`, `name`. Everything else (images, workflows, docs,
downloads) is optional but makes the page useful.

The first documentation link is the hero's **Get started** target. Download
cards may contain a copyable `value`, a guide `url`, or both. `sourceLabel`
optionally labels the source-code button.

## Tests

```bash
python3 -m unittest discover tests/ -v
node --check app.js
node --test tests/test_app.mjs
```

Tests run on pull requests without contacting registries or inference providers.
If the live status snapshot cannot be loaded, the page keeps the guides usable,
shows that status is unavailable, and logs a structured diagnostic in the console.
The catalog renders before the independent status request completes. Status
updates preserve the selected product and tab; browser requests time out after
15 seconds. Sync checks release images with at most four concurrent requests,
and rejects malformed repository configuration before fetching or replacing
the previous snapshot. Optional local GitHub CLI token lookup times out after
five seconds with an explicit anonymous-read fallback diagnostic.
