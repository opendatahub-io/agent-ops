/* ── Helpers ─────────────────────────────────────────────────────────── */

function el(tag, cls, text) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text != null) node.textContent = text;
  return node;
}

function formatDate(value) {
  if (!value) return "";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return String(value);
  return new Intl.DateTimeFormat("en", { dateStyle: "medium", timeStyle: "short" }).format(d);
}

function shortDigest(digest) {
  if (!digest) return "";
  return "sha256:" + digest.replace(/^sha256:/, "").slice(0, 12);
}

function extLink(url, text, cls) {
  const a = el("a", cls, text);
  a.href = url;
  a.target = "_blank";
  a.rel = "noreferrer";
  return a;
}

function statusPill(workflow) {
  const s = workflow.status;
  const c = workflow.conclusion;
  if (s === "queued" || s === "pending" || s === "waiting") return ["Queued", "pill-info"];
  if (s === "in_progress") return ["Running", "pill-warn"];
  if (c === "success") return ["Passed", "pill-ok"];
  if (c === "failure" || c === "timed_out") return ["Failed", "pill-bad"];
  if (c === "cancelled") return ["Cancelled", "pill-neutral"];
  if (c === "skipped") return ["Skipped", "pill-neutral"];
  return ["No data", "pill-neutral"];
}

function repositoryLabel(repo) {
  if (repo.startsWith("opendatahub-io/")) return `ODH midstream · ${repo}`;
  if (repo.startsWith("red-hat-data-services/")) return `RHDS downstream · ${repo}`;
  return repo;
}

/* ── Pagination ─────────────────────────────────────────────────────── */

const PAGE_SIZE = 6;

function paginate(items, page) {
  const start = (page - 1) * PAGE_SIZE;
  return {
    items: items.slice(start, start + PAGE_SIZE),
    page,
    totalPages: Math.max(1, Math.ceil(items.length / PAGE_SIZE)),
    total: items.length,
  };
}

function renderPager(page, totalPages, onPage) {
  if (totalPages <= 1) return null;
  const bar = el("div", "pager");
  const prev = el("button", "pager-btn", "← Previous");
  prev.type = "button";
  prev.disabled = page <= 1;
  prev.addEventListener("click", () => onPage(page - 1));
  bar.append(prev);
  bar.append(el("span", "pager-info", `Page ${page} of ${totalPages}`));
  const next = el("button", "pager-btn", "Next →");
  next.type = "button";
  next.disabled = page >= totalPages;
  next.addEventListener("click", () => onPage(page + 1));
  bar.append(next);
  return bar;
}

async function copyCmd(text, btn) {
  const orig = btn.textContent;
  try { await navigator.clipboard.writeText(text); btn.textContent = "Copied!"; }
  catch { btn.textContent = "Error"; }
  setTimeout(() => { btn.textContent = orig; }, 1400);
}

function cmdBlock(value) {
  const wrap = el("div", "cmd-wrap");
  wrap.append(el("code", null, value));
  const btn = el("button", "cmd-copy", "Copy");
  btn.type = "button";
  btn.addEventListener("click", () => copyCmd(value, btn));
  wrap.append(btn);
  return wrap;
}

/* ── Data fetching ──────────────────────────────────────────────────── */

let catalog = null;
let liveData = null;
let liveDataError = false;

async function fetchJson(path) {
  const response = await fetch(path, { cache: "no-store" });
  if (!response.ok) throw new Error(`GET ${path} failed: HTTP ${response.status}`);
  return response.json();
}

async function loadData() {
  liveDataError = false;
  const [cat, live] = await Promise.all([
    fetchJson("products.json"),
    fetchJson("data/status.json").catch(error => {
      liveDataError = true;
      console.error("Live status unavailable", {
        stage: "load_status", code: "STATUS_LOAD_FAILED", resource: "data/status.json", cause: error,
      });
      return null;
    }),
  ]);
  catalog = cat;
  liveData = live;
}

function liveProduct(key) {
  return (liveData?.products || []).find(p => p.key === key) || null;
}

/* ── Sidebar ────────────────────────────────────────────────────────── */

const nav = document.getElementById("product-nav");
const syncEl = document.getElementById("sync-time");
const contentInner = document.getElementById("content-inner");
const sidebar = document.getElementById("sidebar");
const toggle = document.getElementById("menu-toggle");
let activeKey = null;
let activeTab = null;

toggle.addEventListener("click", () => sidebar.classList.toggle("open"));

function buildNav() {
  const products = catalog?.products || [];
  products.forEach(product => {
    const item = el("a", "nav-item", product.name);
    item.dataset.key = product.key;
    item.addEventListener("click", () => selectProduct(product.key));
    nav.append(item);
  });
  if (liveData?.generatedAt) {
    syncEl.textContent = "Synced " + formatDate(liveData.generatedAt);
  } else if (liveDataError) {
    syncEl.textContent = "Status unavailable";
  }
}

function selectProduct(key) {
  activeKey = key;
  activeTab = null;
  nav.querySelectorAll(".nav-item").forEach(item => {
    item.classList.toggle("active", item.dataset.key === key);
  });
  sidebar.classList.remove("open");
  const product = (catalog?.products || []).find(p => p.key === key);
  if (!product) return;
  renderProduct(product);
  window.scrollTo(0, 0);
  history.replaceState(null, "", "#" + key);
}

/* ── Tab system ─────────────────────────────────────────────────────── */

const TABS = [
  { id: "install", label: "Install" },
  { id: "images", label: "Container Images" },
  { id: "docs", label: "Documentation" },
  { id: "status", label: "Status" },
  { id: "releases", label: "Releases" },
];

function renderTabs(product, live) {
  const bar = el("div", "tab-bar");
  for (const tab of TABS) {
    const btn = el("button", "tab-btn", tab.label);
    btn.dataset.tab = tab.id;
    btn.type = "button";
    if (tab.id === "install") btn.classList.add("active");
    btn.addEventListener("click", () => switchTab(tab.id, product, live));
    bar.append(btn);
  }
  return bar;
}

function switchTab(tabId, product, live) {
  if (activeTab === tabId) return;
  activeTab = tabId;
  document.querySelectorAll(".tab-btn").forEach(btn => {
    btn.classList.toggle("active", btn.dataset.tab === tabId);
  });
  const pane = document.getElementById("tab-pane");
  if (!pane) return;
  pane.replaceChildren(renderTabContent(tabId, product, live));
  pane.scrollTop = 0;
}

function renderTabContent(tabId, product, live) {
  const frag = document.createDocumentFragment();
  switch (tabId) {
    case "status": frag.append(renderStatus(product, live)); break;
    case "releases": frag.append(renderReleases(product, live)); break;
    case "images": frag.append(renderImages(product, live)); break;
    case "install": frag.append(renderInstall(product)); break;
    case "docs": frag.append(renderDocs(product)); break;
  }
  return frag;
}

/* ── Renderers ──────────────────────────────────────────────────────── */

function renderHero(p) {
  const hero = el("div", "hero");
  hero.append(el("div", "hero-eyebrow", "Red Hat OpenShift AI"));
  hero.append(el("h1", null, p.name));
  if (p.tagline) hero.append(el("p", "hero-tagline", p.tagline));
  if (p.description) hero.append(el("p", "hero-description", p.description));
  const actions = el("div", "hero-actions");
  const primaryDoc = (p.docs || []).find(d => d.url);
  if (primaryDoc) actions.append(extLink(primaryDoc.url, "Get started →", "btn btn-primary"));
  if (p.source) actions.append(extLink(p.source, p.sourceLabel || "Source code", "btn btn-secondary"));
  hero.append(actions);
  return hero;
}

function renderE2eBanner(live) {
  const images = live?.images || [];
  const e2eImg = images.find(i => i.name === "E2E Tests");
  const workflows = live?.workflows || [];
  const ciGates = workflows.filter(w =>
    w.name.toLowerCase().includes("required ci gates") ||
    w.name.toLowerCase().includes("e2e")
  );

  const banner = el("div", "e2e-banner");
  const title = el("div", "e2e-banner-title");
  title.append(el("strong", null, "E2E test image"));

  if (e2eImg) {
    const hasStable = e2eImg.pull != null;
    if (hasStable) {
      title.append(el("span", "pill pill-info", "Image available"));
    } else {
      title.append(el("span", "pill pill-neutral", "No stable tag"));
    }
    banner.append(title);
    banner.append(el("p", "e2e-details", "Image availability only. Workflow results below are separate runs, not validation of these image digests."));

    const details = el("div", "e2e-details");
    if (e2eImg.lastModified) {
      details.append(el("span", null, "Latest build: " + formatDate(e2eImg.lastModified)));
    }
    if (e2eImg.pull?.digest) {
      details.append(el("span", "e2e-digest", shortDigest(e2eImg.pull.digest)));
    }
    const repoShort = e2eImg.repository.replace("quay.io/", "");
    details.append(extLink(`https://quay.io/repository/${repoShort}?tab=tags`, "View on Quay →", "e2e-link"));
    banner.append(details);

    if (e2eImg.builds?.length) {
      const recent = el("div", "e2e-recent");
      recent.append(el("span", "e2e-recent-label", "Recent builds:"));
      for (const b of e2eImg.builds.slice(0, 3)) {
        const tag = el("span", "e2e-tag");
        const shortName = b.name.length > 16 ? b.name.slice(0, 12) + "…" : b.name;
        tag.textContent = shortName;
        tag.title = b.name;
        recent.append(tag);
      }
      banner.append(recent);
    }
  } else {
    title.append(el("span", "pill pill-neutral", "No image data"));
    banner.append(title);
  }

  if (ciGates.length) {
    const gates = el("div", "e2e-gates");
    for (const wf of ciGates) {
      const row = el("div", "e2e-gate-row");
      const [label, cls] = statusPill(wf);
      row.append(el("span", "pill " + cls, label));
      row.append(el("span", "e2e-gate-name", `${wf.name}`));
      row.append(el("span", "e2e-gate-repo", repositoryLabel(wf.repo)));
      if (wf.updatedAt) row.append(el("span", "e2e-gate-time", formatDate(wf.updatedAt)));
      if (wf.url) row.append(extLink(wf.url, "View", "e2e-gate-link"));
      gates.append(row);
    }
    banner.append(gates);
  }

  return banner;
}

function renderReleaseList(releases, page) {
  const container = el("div", null);
  container.id = "release-list";
  const { items, totalPages } = paginate(releases, page);

  for (const rel of items) {
    const row = el("div", "release-row");
    const header = el("div", "release-header");
    const title = el("div", "release-title");
    title.append(el("strong", null, `OpenShell ${rel.upstreamVersion}`));
    title.append(el("span", "release-tag", rel.tag));
    header.append(title);
    const pillText = rel.allBuilt ? "Images available" : "Images incomplete";
    const pillCls = rel.allBuilt ? "pill pill-ok" : "pill pill-warn";
    header.append(el("span", pillCls, pillText));
    row.append(header);

    const compList = el("div", "release-components");
    for (const comp of rel.components || []) {
      const item = el("div", "release-comp");
      const statusIcon = comp.found ? "✓" : "✗";
      const statusCls = comp.found ? "comp-ok" : "comp-missing";
      item.append(el("span", statusCls, statusIcon));
      const nameShort = comp.repository.replace("quay.io/opendatahub/", "");
      item.append(el("span", "comp-label", `${nameShort}:${comp.tag}`));
      if (comp.found && comp.lastModified) {
        item.append(el("span", "comp-meta", formatDate(comp.lastModified)));
      }
      if (comp.found) {
        item.append(extLink(`${comp.registryUrl}?tab=tags&tag=${comp.tag}`, "Quay", "comp-link"));
      }
      compList.append(item);
    }
    row.append(compList);
    container.append(row);
  }

  const pager = renderPager(page, totalPages, p => {
    const old = document.getElementById("release-list");
    old.replaceWith(renderReleaseList(releases, p));
  });
  if (pager) container.append(pager);
  return container;
}

function renderReleases(product, live) {
  const section = el("div", "section");
  section.append(el("h2", null, "Version releases"));
  section.append(el("p", "section-lead", "Version-tagged ODH midstream images. An upstream release appears here after an ODH release tag is created; each component is then checked in Quay. Image availability does not establish deployment validation."));
  if (product.releaseRepo) {
    section.append(extLink(`https://github.com/${product.releaseRepo}/tags`, "ODH midstream release tags →", "doc-open"));
  }
  if (!live) {
    section.append(el("p", "section-lead", "Release data unavailable."));
    return section;
  }
  const releases = live?.releases || [];
  if (!releases.length) {
    section.append(el("p", "section-lead", "No version-tagged releases found."));
    return section;
  }
  section.append(renderReleaseList(releases, 1));
  return section;
}

function renderImageCard(img, liveImg) {
  const card = el("div", "image-card");
  const headerRow = el("div", "image-header");
  headerRow.append(el("h3", null, img.name));
  if (img.release) headerRow.append(el("span", "image-badge", "Release"));
  if (img.name === "E2E Tests") headerRow.append(el("span", "image-badge image-badge-e2e", "E2E"));
  card.append(headerRow);
  if (img.role) card.append(el("p", "role", img.role));
  const tag = img.pullTag || "odh-stable";
  card.append(cmdBlock(`podman pull ${img.repository}:${tag}`));
  const meta = el("div", "image-meta");
  if (liveImg?.pull) {
    meta.innerHTML = `<strong>${tag}</strong> · ${shortDigest(liveImg.pull.digest)} · ${formatDate(liveImg.pull.lastModified)}<br/>`;
  }
  const repo = img.repository.replace("quay.io/", "");
  meta.append(extLink(`https://quay.io/repository/${repo}`, "View on Quay →", null));
  card.append(meta);

  if (liveImg?.builds?.length) {
    const builds = el("div", "image-builds");
    builds.append(el("span", "image-builds-label", "Recent builds"));
    for (const b of liveImg.builds.slice(0, 3)) {
      const bRow = el("div", "image-build-row");
      const shortName = b.name.length > 20 ? b.name.slice(0, 16) + "…" : b.name;
      bRow.append(el("span", "image-build-tag", shortName));
      bRow.title = b.name;
      if (b.lastModified) bRow.append(el("span", "image-build-time", formatDate(b.lastModified)));
      builds.append(bRow);
    }
    card.append(builds);
  }
  return card;
}

function renderImageGrid(images, liveImages, page) {
  const container = el("div", null);
  container.id = "image-grid";
  const { items, totalPages } = paginate(images, page);
  const grid = el("div", "image-row");
  for (const img of items) {
    grid.append(renderImageCard(img, liveImages.get(img.repository)));
  }
  container.append(grid);
  const pager = renderPager(page, totalPages, p => {
    document.getElementById("image-grid").replaceWith(renderImageGrid(images, liveImages, p));
  });
  if (pager) container.append(pager);
  return container;
}

function renderImages(product, live) {
  const section = el("div", "section");
  section.append(el("h2", null, "Container images"));
  section.append(el("p", "section-lead", "Published to the Open Data Hub Quay organisation. Pull with podman or docker."));
  const liveImages = new Map((live?.images || []).map(i => [i.repository, i]));
  const allImages = product.images || [];
  section.append(renderImageGrid(allImages, liveImages, 1));
  return section;
}

function renderWorkflowPage(workflows, page, paneId) {
  const container = el("div", null);
  container.id = paneId;
  const { items, totalPages } = paginate(workflows, page);
  const list = el("ul", "checks-list");
  for (const wf of items) {
    const row = el("li", "check-row");
    const info = el("div", "check-info");
    info.append(el("div", "check-name", wf.name));
    const meta = [wf.branch, formatDate(wf.updatedAt)].filter(Boolean).join(" · ");
    if (meta) info.append(el("div", "check-meta", meta));
    row.append(info);
    const [label, cls] = statusPill(wf);
    row.append(el("span", "pill " + cls, label));
    if (wf.url) row.append(extLink(wf.url, "View", "check-link"));
    list.append(row);
  }
  container.append(list);
  const pager = renderPager(page, totalPages, p => {
    document.getElementById(paneId).replaceWith(renderWorkflowPage(workflows, p, paneId));
  });
  if (pager) container.append(pager);
  return container;
}

function renderStatus(product, live) {
  const section = el("div", "section");
  section.append(renderE2eBanner(live));

  const workflows = live?.workflows || [];
  if (!workflows.length) {
    section.append(el("p", "section-lead", "No CI workflow data available."));
    return section;
  }

  section.append(el("h2", null, "CI status"));
  section.append(el("p", "section-lead", "Public GitHub Actions workflows from ODH midstream and RHDS downstream. These runs are not tied to the image digests shown on this page."));

  const groups = new Map();
  for (const wf of workflows) {
    const repo = wf.repo || "unknown";
    if (!groups.has(repo)) groups.set(repo, []);
    groups.get(repo).push(wf);
  }
  const repos = [...groups.keys()];

  if (repos.length > 1) {
    const subBar = el("div", "sub-tab-bar");
    const pane = el("div", null);
    pane.id = "status-sub-pane";

    repos.forEach((repo, i) => {
      const btn = el("button", "sub-tab-btn", repositoryLabel(repo));
      btn.dataset.repo = repo;
      btn.type = "button";
      if (i === 0) btn.classList.add("active");
      btn.addEventListener("click", () => {
        subBar.querySelectorAll(".sub-tab-btn").forEach(b => {
          b.classList.toggle("active", b.dataset.repo === repo);
        });
        const content = el("div", null);
        content.id = "status-sub-pane";
        const paneId = "wf-" + repo.replace(/\//g, "-");
        content.append(renderWorkflowPage(groups.get(repo), 1, paneId));
        document.getElementById("status-sub-pane").replaceWith(content);
      });
      subBar.append(btn);
    });

    section.append(subBar);
    const firstPaneId = "wf-" + repos[0].replace(/\//g, "-");
    pane.append(renderWorkflowPage(groups.get(repos[0]), 1, firstPaneId));
    section.append(pane);
  } else if (repos.length === 1) {
    const groupLabel = el("div", "check-group-label");
    groupLabel.append(extLink(`https://github.com/${repos[0]}/actions`, repositoryLabel(repos[0]), null));
    section.append(groupLabel);
    const paneId = "wf-" + repos[0].replace(/\//g, "-");
    section.append(renderWorkflowPage(groups.get(repos[0]), 1, paneId));
  }

  return section;
}

function renderInstallGrid(downloads, page) {
  const container = el("div", null);
  container.id = "install-grid";
  const { items, totalPages } = paginate(downloads, page);
  const grid = el("div", "cards");
  for (const dl of items) {
    const card = el("div", "card");
    card.append(el("h3", null, dl.title));
    if (dl.detail) card.append(el("p", null, dl.detail));
    if (dl.value) card.append(cmdBlock(dl.value));
    if (dl.url) card.append(extLink(dl.url, "Open guide →", "doc-open"));
    grid.append(card);
  }
  container.append(grid);
  const pager = renderPager(page, totalPages, p => {
    document.getElementById("install-grid").replaceWith(renderInstallGrid(downloads, p));
  });
  if (pager) container.append(pager);
  return container;
}

function renderInstall(p) {
  const section = el("div", "section");
  section.append(el("h2", null, "Install"));
  section.append(el("p", "section-lead", "Start with the OpenShift walkthrough. Its pinned versions and experimental test-cluster setup are separate from the newest image builds listed here."));
  if (p.requirements) {
    const req = el("div", "requirements");
    req.append(el("strong", null, "Requirements: "));
    req.append(document.createTextNode(p.requirements));
    section.append(req);
  }
  section.append(renderInstallGrid(p.downloads || [], 1));
  return section;
}

function renderDocsGrid(docs, page) {
  const container = el("div", null);
  container.id = "docs-grid";
  const { items, totalPages } = paginate(docs, page);
  const grid = el("div", "cards");
  for (const doc of items) {
    const card = extLink(doc.url, null, "card doc-card");
    card.append(el("h3", null, doc.title));
    if (doc.detail) card.append(el("p", null, doc.detail));
    card.append(el("span", "doc-open", "Open →"));
    grid.append(card);
  }
  container.append(grid);
  const pager = renderPager(page, totalPages, p => {
    document.getElementById("docs-grid").replaceWith(renderDocsGrid(docs, p));
  });
  if (pager) container.append(pager);
  return container;
}

function renderDocs(p) {
  const section = el("div", "section");
  section.append(el("h2", null, "Documentation"));
  section.append(el("p", "section-lead", "Everything you need to deploy, configure, and operate."));
  section.append(renderDocsGrid(p.docs || [], 1));
  return section;
}

/* ── Product page assembly ──────────────────────────────────────────── */

function renderProduct(p) {
  const live = liveProduct(p.key);
  const fragment = document.createDocumentFragment();
  fragment.append(renderHero(p));
  if (liveDataError) {
    const notice = el("p", "requirements", "Live image, CI, and release status is unavailable. Documentation and install links remain available.");
    notice.setAttribute("role", "alert");
    fragment.append(notice);
  }
  fragment.append(renderTabs(p, live));
  const pane = el("div", null);
  pane.id = "tab-pane";
  activeTab = "install";
  pane.append(renderTabContent("install", p, live));
  fragment.append(pane);
  contentInner.replaceChildren(fragment);
}

/* ── Init ───────────────────────────────────────────────────────────── */

(async () => {
  try {
    await loadData();
    buildNav();
    const hash = location.hash.replace("#", "");
    const first = (catalog?.products || [])[0];
    const initial = hash && (catalog?.products || []).find(p => p.key === hash) ? hash : first?.key;
    if (initial) selectProduct(initial);
  } catch (err) {
    console.error("Page initialization failed", {
      stage: "initialize_page", code: "PAGE_INIT_FAILED", cause: err,
    });
    const splash = el("div", "splash");
    splash.append(el("h1", null, "Could not load"));
    splash.append(el("p", null, "The page could not be loaded. Please try again later."));
    contentInner.replaceChildren(splash);
  }
})();
