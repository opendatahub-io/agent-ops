import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";

const source = readFileSync(new URL("../app.js", import.meta.url), "utf8");
const catalog = JSON.parse(readFileSync(new URL("../products.json", import.meta.url), "utf8"));
const guide = "https://github.com/opendatahub-io/agent-ops/blob/main/guides/getting-started-openshell-openshift.md";

// Minimal DOM adapter so the shipped renderer and its click handlers run without dependencies.
class Element {
  constructor(tag) {
    this.tagName = tag;
    this.children = [];
    this.dataset = {};
    this.attributes = {};
    this.listeners = {};
    this.className = "";
    this._text = "";
    this.classList = {
      add: cls => { this.className += ` ${cls}`; },
      remove: cls => { this.className = this.className.split(/\s+/).filter(c => c !== cls).join(" "); },
      toggle: (cls, on = !this.className.split(/\s+/).includes(cls)) => {
        this.classList.remove(cls);
        if (on) this.classList.add(cls);
      },
    };
  }
  set textContent(value) { this._text = String(value); this.children = []; }
  get textContent() { return this._text + this.children.map(c => c.textContent).join(""); }
  append(...nodes) {
    for (const node of nodes) { node.parent = this; this.children.push(node); }
  }
  replaceChildren(...nodes) { this._text = ""; this.children = []; this.append(...nodes); }
  replaceWith(node) {
    const index = this.parent.children.indexOf(this);
    node.parent = this.parent;
    this.parent.children[index] = node;
  }
  setAttribute(key, value) { this.attributes[key] = value; }
  addEventListener(event, handler) { this.listeners[event] = handler; }
  querySelectorAll(selector) {
    return descendants(this).filter(node => node.className.split(/\s+/).includes(selector.slice(1)));
  }
}

function descendants(node) { return node.children.flatMap(child => [child, ...descendants(child)]); }

async function boot({ status = { products: [] }, statusFailure, catalogFailure } = {}) {
  const roots = Object.fromEntries(
    ["product-nav", "sync-time", "content-inner", "sidebar", "menu-toggle"].map(id => {
      const node = new Element("div");
      node.id = id;
      return [id, node];
    }),
  );
  const errors = [];
  const document = {
    createElement: tag => new Element(tag),
    createDocumentFragment: () => new Element("fragment"),
    createTextNode: text => { const node = new Element("text"); node.textContent = text; return node; },
    getElementById: id => roots[id] || Object.values(roots).flatMap(descendants).find(n => n.id === id),
    querySelectorAll: selector => Object.values(roots).flatMap(root => root.querySelectorAll(selector)),
  };
  const context = vm.createContext({
    document, Intl, Date, setTimeout,
    location: { hash: "#openshell" },
    history: { replaceState() {} },
    window: { scrollTo() {} },
    console: { error: (...args) => errors.push(args) },
    fetch: async path => {
      const failure = path === "products.json" ? catalogFailure : statusFailure;
      if (failure === "network") throw new TypeError("Network unavailable");
      return {
        ok: failure !== "http",
        status: failure === "http" ? 503 : 200,
        json: async () => {
          if (failure === "json") throw new SyntaxError("Invalid JSON");
          return path === "products.json" ? catalog : status;
        },
      };
    },
  });
  vm.runInContext(source, context);
  await new Promise(setImmediate);
  return { roots, document, context, errors };
}

test("Get started and the first install card open the Agent Ops guide", async () => {
  const { roots } = await boot();
  const nodes = descendants(roots["content-inner"]);
  assert.equal(nodes.find(n => n.textContent === "Get started →").href, guide);
  const card = nodes.find(n => n.className === "card");
  assert.equal(descendants(card).find(n => n.tagName === "a").href, guide);
  assert.equal(descendants(card).filter(n => n.tagName === "code").length, 0);
  assert.equal(roots["content-inner"].textContent.includes("undefined"), false);
});

test("a published E2E image cannot override a failing CI run", async () => {
  const { context } = await boot();
  context.fixture = {
    images: [{ name: "E2E Tests", repository: "quay.io/opendatahub/odh-openshell-e2e", pull: { digest: "sha256:test" } }],
    workflows: [{ name: "Required CI Gates", repo: "opendatahub-io/openshell", status: "completed", conclusion: "failure" }],
  };
  const banner = vm.runInContext("renderE2eBanner(fixture)", context);
  assert.match(banner.textContent, /Image available/);
  assert.match(banner.textContent, /Failed/);
  assert.match(banner.textContent, /ODH midstream/);
  assert.doesNotMatch(banner.textContent, /Passing/);
});

test("workflow tabs distinguish ODH midstream and RHDS downstream", async () => {
  const { context } = await boot();
  context.fixture = { workflows: [
    { name: "Required CI Gates", repo: "opendatahub-io/openshell" },
    { name: "Required CI Gates", repo: "red-hat-data-services/openshell" },
  ] };
  const section = vm.runInContext("renderStatus({}, fixture)", context);
  const tabs = section.querySelectorAll(".sub-tab-btn");
  assert.equal(tabs.length, 2);
  assert.match(tabs[0].textContent, /^ODH midstream/);
  assert.match(tabs[1].textContent, /^RHDS downstream/);
});

for (const failure of ["http", "json", "network"]) {
  test(`a ${failure} status-load failure stays visible while guides work`, async () => {
    const { roots, errors, document } = await boot({ statusFailure: failure });
    assert.equal(roots["sync-time"].textContent, "Status unavailable");
    assert.match(roots["content-inner"].textContent, /Live image, CI, and release status is unavailable/);
    assert.equal(descendants(roots["content-inner"]).find(n => n.textContent === "Get started →").href, guide);
    assert.equal(errors[0][1].code, "STATUS_LOAD_FAILED");
    assert.equal(errors[0][1].stage, "load_status");
    assert.ok(errors[0][1].cause);
    const releases = descendants(roots["content-inner"]).find(n => n.dataset.tab === "releases");
    releases.listeners.click();
    const pane = document.getElementById("tab-pane");
    assert.match(pane.textContent, /ODH midstream/);
    assert.match(pane.textContent, /Release data unavailable/);
    assert.match(roots["content-inner"].textContent, /status is unavailable/);
  });
}

test("a catalog-load failure reports a diagnostic and visible error", async () => {
  const { roots, errors } = await boot({ catalogFailure: "http" });
  assert.match(roots["content-inner"].textContent, /The page could not be loaded/);
  assert.equal(errors[0][1].code, "PAGE_INIT_FAILED");
});
