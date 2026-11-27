"use client";
import { useEffect, useRef, useState } from "react";

type Proposal = {
  title: string;
  parcel_id: string;
  work_type: string;
  start_date: string;
  end_date: string;
  height_m: number;
  footprint_m2: number;
  materials: string;
  colour: string;
  intended_use: string;
  narrative: string;
  drainage_changes: boolean;
};
type Ticket = {
  id: string;
  proposal: Proposal;
  status: string;
  progress: string;
  created_at: string;
  active_attempt?: string;
  decision?: {
    summary: string;
    reasons: {
      explanation: string;
      policy_citations: { document_id: string; section: string }[];
      evidence_ids: string[];
    }[];
    termination_reason: string;
    decision_origin: string;
  };
  events?: {
    kind: string;
    details: Record<string, unknown>;
    created_at: string;
  }[];
};
const initial: Proposal = {
  title: "",
  parcel_id: "RC-103",
  work_type: "repair",
  start_date: "2027-07-12",
  end_date: "2027-07-16",
  height_m: 2,
  footprint_m2: 12,
  materials: "",
  colour: "",
  intended_use: "",
  narrative: "",
  drainage_changes: false,
};
const examples: { label: string; description: string; proposal: Proposal }[] = [
  {
    label: "The lantern repair",
    description: "A modern repair with an old exception.",
    proposal: {
      ...initial,
      title: "Restore the Lantern Lane evening light",
      parcel_id: "RC-101",
      materials:
        "Preserve original visible stone and metal; hidden LED internals only",
      colour: "Existing stone grey; no joinery changes",
      intended_use: "Unchanged public evening lantern",
      narrative:
        "Repair the existing protected public evening lantern. Replace only failed hidden lighting components with LEDs. Preserve all outward form, visible material, height, footprint and historic public evening-light function. No excavation, exterior habitat disturbance or drainage changes.",
    },
  },
  {
    label: "The everyday stair",
    description: "The same shape, a different purpose.",
    proposal: {
      ...initial,
      title: "Reopen the ceremonial stair for commuters",
      parcel_id: "RC-104",
      footprint_m2: 24,
      materials: "Matching original stone",
      colour: "Stone grey",
      intended_use: "Everyday commuter access",
      narrative:
        "Repair the ceremonial stair using matching original stone within its existing height and footprint. Reopen the staircase for everyday commuter access rather than ceremonial occasions. There are no drainage changes or groundworks.",
    },
  },
  {
    label: "The reversible pavilion",
    description: "A temporary structure with a careful footprint.",
    proposal: {
      ...initial,
      title: "Freestanding garden reading pavilion",
      work_type: "new_structure",
      parcel_id: "RC-103",
      materials: "Timber on removable surface pads",
      colour: "Natural timber",
      intended_use: "Existing private garden use",
      narrative:
        "Place a freestanding timber reading pavilion on surface pads in the existing garden. It can be removed completely without excavation and leaves the existing garden use intact. No walls, no drainage changes, no new foundations and no canal silhouette changes.",
    },
  },
  {
    label: "The nesting-season works",
    description: "An exception meets a later override.",
    proposal: {
      ...initial,
      title: "Temporary pavilion beside the canal",
      parcel_id: "RC-102",
      work_type: "new_structure",
      start_date: "2027-04-12",
      end_date: "2027-04-15",
      materials: "Removable timber panels",
      colour: "Natural wood",
      intended_use: "Private garden",
      narrative:
        "Install a temporary garden pavilion with no excavation during April. It is entirely reversible and we would like the reversible-work exemption to apply. The exterior work will take three days beside the listed canal.",
    },
  },
];

async function api(path: string, options?: RequestInit) {
  const response = await fetch(`/api${path}`, {
    ...options,
    headers: { "Content-Type": "application/json", ...options?.headers },
  });
  const body = await response.json();
  if (!response.ok)
    throw new Error(
      typeof body.detail === "string"
        ? body.detail
        : "Please check the form and try again.",
    );
  return body;
}

function submissionKey() {
  if (typeof crypto.randomUUID === "function") return crypto.randomUUID();
  return Array.from(crypto.getRandomValues(new Uint8Array(16)), (byte) =>
    byte.toString(16).padStart(2, "0"),
  ).join("");
}

export default function Home() {
  // Invalidate all responses belonging to an earlier identity or login attempt.
  const identityVersion = useRef(0);
  const loginPending = useRef(false);
  const [loggingIn, setLoggingIn] = useState(false);
  const pendingSubmission = useRef<{ fingerprint: string; key: string } | null>(
    null,
  );
  const [customer, setCustomer] = useState<string | null>(null),
    [view, setView] = useState("requests");
  const [tickets, setTickets] = useState<Ticket[]>([]),
    [selected, setSelected] = useState<Ticket | null>(null);
  const [form, setForm] = useState<Proposal>(initial),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false);
  useEffect(() => {
    const version = identityVersion.current;
    let active = true;
    api("/session")
      .then((x) => {
        if (active && identityVersion.current === version)
          setCustomer(x.customer);
      })
      .catch(() => {});
    return () => {
      active = false;
    };
  }, []);
  useEffect(() => {
    if (!customer) return;
    let active = true;
    const version = identityVersion.current;
    let sequence = 0;
    let accepted = 0;
    const refresh = async () => {
      const current = ++sequence;
      const valid = () =>
        active && identityVersion.current === version && current > accepted;
      try {
        const data = await api("/requests");
        if (valid()) {
          accepted = current;
          setTickets(data);
        }
      } catch (e) {
        if (valid()) {
          accepted = current;
          setError((e as Error).message);
        }
      }
    };
    refresh();
    const id = setInterval(refresh, 3000);
    return () => {
      active = false;
      clearInterval(id);
    };
  }, [customer]);
  useEffect(() => {
    if (!selected) return;
    const updated = tickets.find((x) => x.id === selected.id);
    if (
      updated &&
      JSON.stringify(updated) !==
        JSON.stringify({ ...selected, events: undefined })
    )
      setSelected(updated);
  }, [tickets]); // refresh decision while its detail view is open
  async function login(name: string) {
    if (loginPending.current) return;
    loginPending.current = true;
    setLoggingIn(true);
    const version = ++identityVersion.current;
    setCustomer(null);
    setTickets([]);
    setSelected(null);
    setForm(initial);
    setView("requests");
    setBusy(false);
    pendingSubmission.current = null;
    setError("");
    try {
      await api("/session", {
        method: "POST",
        body: JSON.stringify({ customer: name }),
      });
      if (identityVersion.current === version) setCustomer(name);
    } catch (e) {
      if (identityVersion.current === version) setError((e as Error).message);
    } finally {
      loginPending.current = false;
      setLoggingIn(false);
    }
  }
  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!customer || busy) return;
    const version = identityVersion.current;
    const body = JSON.stringify(form);
    const fingerprint = JSON.stringify([customer, body]);
    if (pendingSubmission.current?.fingerprint !== fingerprint) {
      pendingSubmission.current = { fingerprint, key: submissionKey() };
    }
    const key = pendingSubmission.current.key;
    setBusy(true);
    setError("");
    try {
      const ticket = await api("/requests", {
        method: "POST",
        headers: { "Idempotency-Key": key },
        body,
      });
      if (identityVersion.current !== version) return;
      setTickets((t) => [ticket, ...t.filter((item) => item.id !== ticket.id)]);
      setSelected(ticket);
      setView("requests");
      setForm(initial);
      pendingSubmission.current = null;
    } catch (e) {
      if (identityVersion.current === version) setError((e as Error).message);
    } finally {
      if (identityVersion.current === version) setBusy(false);
    }
  }
  function startRequest() {
    pendingSubmission.current = null;
    setView("new");
  }
  const field = (key: keyof Proposal, value: string | number | boolean) =>
    setForm((f) => ({ ...f, [key]: value }));
  return (
    <>
      <header className="masthead">
        <a className="brand" href="/">
          <span className="compass">✦</span> RED COMPASS
          <span className="brand-divider" />{" "}
          <small>Construction approvals</small>
        </a>
        <span className="demo-tag">FICTIONAL COMPANY · LIVE AGENT DEMO</span>
      </header>
      <div className="shell">
        <aside>
          <div className="workspace-label">CUSTOMER PORTAL</div>
          <button
            className={view === "requests" ? "nav active" : "nav"}
            onClick={() => {
              setView("requests");
              setSelected(null);
            }}
          >
            ▤ <span>Your requests</span>
            <span className="count">{tickets.length}</span>
          </button>
          <button
            className={view === "new" ? "nav active" : "nav"}
            onClick={() => {
              startRequest();
              setSelected(null);
            }}
          >
            ＋ <span>New request</span>
          </button>
          <div className="sidebar-note">
            <span className="live-dot" /> A little unusual.
            <br />
            Thoroughly reviewed.
            <p>Every application gets its own isolated review environment.</p>
          </div>
          <div className="customer-switch">
            <label htmlFor="customer">Demo customer</label>
            <select
              id="customer"
              disabled={loggingIn}
              value={customer || "alex"}
              onChange={(e) => login(e.target.value)}
            >
              <option value="alex">Alex Morgan</option>
              <option value="sam">Sam Rivera</option>
            </select>
          </div>
        </aside>
        <main>
          {error && (
            <div role="alert" className="alert danger">
              <strong>Something needs attention</strong>
              <p>{error}</p>
            </div>
          )}
          {!customer ? (
            <section className="welcome">
              <p className="eyebrow">A CLEAR PATH THROUGH COMPLEX POLICY</p>
              <h1>
                Make room for
                <br />
                your next idea.
              </h1>
              <p>
                Tell us what you’re building. We’ll work through the fine print.
              </p>
              <div className="login-cards">
                {["alex", "sam"].map((name) => (
                  <button
                    key={name}
                    disabled={loggingIn}
                    onClick={() => login(name)}
                  >
                    Continue as {name === "alex" ? "Alex Morgan" : "Sam Rivera"}{" "}
                    <span>→</span>
                  </button>
                ))}
              </div>
              <p className="muted">
                Seeded demo identities. No registration required.
              </p>
            </section>
          ) : view === "new" ? (
            <>
              <div className="page-heading">
                <div>
                  <p className="eyebrow">LET’S SEE WHAT’S POSSIBLE</p>
                  <h1>New construction request</h1>
                  <p>Give us the details. We’ll take care of the guidelines.</p>
                </div>
              </div>
              <div className="alert info">
                <strong>A fictional place with very particular rules</strong>
                <p>
                  Try an example or describe your own project. Reviews use Red
                  Compass policies and fictional parcel records.
                </p>
              </div>
              <div className="examples">
                {examples.map((x) => (
                  <button key={x.label} onClick={() => setForm(x.proposal)}>
                    <span>↗</span>
                    <strong>{x.label}</strong>
                    <small>{x.description}</small>
                  </button>
                ))}
              </div>
              <form onSubmit={submit} className="form-card">
                <h2>Project details</h2>
                <p className="muted">
                  All fields are required unless marked optional.
                </p>
                <label>
                  Project title
                  <input
                    required
                    minLength={3}
                    maxLength={160}
                    value={form.title}
                    onChange={(e) => field("title", e.target.value)}
                    placeholder="What are you planning?"
                  />
                </label>
                <div className="form-grid">
                  <label>
                    Parcel
                    <select
                      value={form.parcel_id}
                      onChange={(e) => field("parcel_id", e.target.value)}
                    >
                      <option value="RC-101">RC-101 · 14 Lantern Lane</option>
                      <option value="RC-102">RC-102 · 6 Bell Yard</option>
                      <option value="RC-103">RC-103 · 8 Moss Court</option>
                      <option value="RC-104">
                        RC-104 · 1 Procession Steps
                      </option>
                    </select>
                  </label>
                  <label>
                    Work type
                    <select
                      value={form.work_type}
                      onChange={(e) => field("work_type", e.target.value)}
                    >
                      {[
                        "repair",
                        "extension",
                        "groundworks",
                        "new_structure",
                        "demolition",
                      ].map((x) => (
                        <option key={x} value={x}>
                          {x.replace("_", " ")}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label>
                    Start date
                    <input
                      required
                      type="date"
                      value={form.start_date}
                      onChange={(e) => field("start_date", e.target.value)}
                    />
                  </label>
                  <label>
                    End date
                    <input
                      required
                      type="date"
                      min={form.start_date}
                      value={form.end_date}
                      onChange={(e) => field("end_date", e.target.value)}
                    />
                  </label>
                  <label>
                    Height (metres)
                    <input
                      required
                      type="number"
                      min="0"
                      max="200"
                      step="0.1"
                      value={form.height_m}
                      onChange={(e) =>
                        field("height_m", Number(e.target.value))
                      }
                    />
                  </label>
                  <label>
                    Footprint (m²)
                    <input
                      required
                      type="number"
                      min="0"
                      max="10000"
                      step="0.1"
                      value={form.footprint_m2}
                      onChange={(e) =>
                        field("footprint_m2", Number(e.target.value))
                      }
                    />
                  </label>
                </div>
                {(["materials", "colour", "intended_use"] as const).map(
                  (key) => (
                    <label key={key}>
                      {key === "intended_use"
                        ? "Intended use"
                        : key[0].toUpperCase() + key.slice(1)}
                      <input
                        required
                        minLength={2}
                        value={form[key]}
                        onChange={(e) => field(key, e.target.value)}
                      />
                    </label>
                  ),
                )}
                <label>
                  Describe the proposal
                  <textarea
                    required
                    minLength={20}
                    maxLength={12000}
                    rows={6}
                    value={form.narrative}
                    onChange={(e) => field("narrative", e.target.value)}
                    placeholder="Include what will change, what will stay the same, and any circumstances the reviewer should consider."
                  />
                </label>
                <label className="checkbox">
                  <input
                    type="checkbox"
                    checked={form.drainage_changes}
                    onChange={(e) =>
                      field("drainage_changes", e.target.checked)
                    }
                  />{" "}
                  This project changes drainage
                </label>
                <div className="form-footer">
                  <span className="muted">
                    You can follow progress in Your requests.
                  </span>
                  <button className="primary" disabled={busy}>
                    {busy ? "Submitting…" : "Submit for review →"}
                  </button>
                </div>
              </form>
            </>
          ) : selected ? (
            <>
              <button className="back" onClick={() => setSelected(null)}>
                ← All requests
              </button>
              <div className="page-heading">
                <div>
                  <p className="eyebrow">
                    REQUEST {selected.id.slice(0, 8).toUpperCase()}
                  </p>
                  <h1>{selected.proposal.title}</h1>
                  <p>
                    {selected.proposal.parcel_id} · Submitted{" "}
                    {new Date(selected.created_at).toLocaleDateString()}
                  </p>
                </div>
                <Status value={selected.status} />
              </div>
              {selected.decision ? (
                <section className={`decision-panel ${selected.status}`}>
                  <span className="decision-icon">
                    {selected.status === "approved" ? "✓" : "×"}
                  </span>
                  <div>
                    <p className="eyebrow">REVIEW COMPLETE</p>
                    <h2>
                      {selected.status === "approved"
                        ? "Your project is approved"
                        : "Your project was not approved"}
                    </h2>
                    <p>{selected.decision.summary}</p>
                  </div>
                </section>
              ) : (
                <section className="review-panel">
                  <span className="spinner" />
                  <div>
                    <h2>
                      {selected.progress === "queued"
                        ? "Your request is in the queue"
                        : selected.progress === "interrupted"
                          ? "Review interrupted"
                          : "Your request is being reviewed"}
                    </h2>
                    <p>
                      {selected.progress === "interrupted"
                        ? "The review could not finish. No approval or denial has been issued."
                        : "The reviewer will check applicable policies and supporting records before making a decision."}
                    </p>
                    <small className="muted">
                      {selected.progress.replaceAll("_", " ")}
                      {selected.active_attempt
                        ? ` · Attempt ${selected.active_attempt.slice(0, 8)}`
                        : ""}
                    </small>
                  </div>
                </section>
              )}
              {selected.decision && (
                <section className="reason-card">
                  <h2>Why this decision?</h2>
                  {selected.decision.reasons.map((reason, i) => (
                    <article key={i}>
                      <h3>Finding {String(i + 1).padStart(2, "0")}</h3>
                      <p>{reason.explanation}</p>
                      <div className="citations">
                        {reason.policy_citations.map((c, j) => (
                          <span key={j}>
                            {c.document_id} · § {c.section}
                          </span>
                        ))}
                      </div>
                      {reason.evidence_ids.length > 0 && (
                        <p className="muted">
                          Supporting records: {reason.evidence_ids.join(", ")}
                        </p>
                      )}
                    </article>
                  ))}
                </section>
              )}
              <section className="reason-card">
                <h2>Your proposal</h2>
                <p>{selected.proposal.narrative}</p>
                <dl>
                  <div>
                    <dt>Materials</dt>
                    <dd>{selected.proposal.materials}</dd>
                  </div>
                  <div>
                    <dt>Intended use</dt>
                    <dd>{selected.proposal.intended_use}</dd>
                  </div>
                  <div>
                    <dt>Schedule</dt>
                    <dd>
                      {selected.proposal.start_date} to{" "}
                      {selected.proposal.end_date}
                    </dd>
                  </div>
                  <div>
                    <dt>Dimensions</dt>
                    <dd>
                      {selected.proposal.height_m} m ·{" "}
                      {selected.proposal.footprint_m2} m²
                    </dd>
                  </div>
                </dl>
              </section>
            </>
          ) : (
            <>
              <div className="page-heading">
                <div>
                  <p className="eyebrow">FROM FIRST IDEA TO FINAL ANSWER</p>
                  <h1>Your requests</h1>
                  <p>A clear view of what’s pending, and what’s possible.</p>
                </div>
                <button className="primary" onClick={() => startRequest()}>
                  ＋ New request
                </button>
              </div>
              <div className="stats">
                {[
                  ["Total requests", tickets.length],
                  [
                    "Pending",
                    tickets.filter((t) => t.status === "pending").length,
                  ],
                  [
                    "Approved",
                    tickets.filter((t) => t.status === "approved").length,
                  ],
                  [
                    "Denied",
                    tickets.filter((t) => t.status === "denied").length,
                  ],
                ].map(([label, value]) => (
                  <div key={label}>
                    <span>{label}</span>
                    <strong>{value}</strong>
                  </div>
                ))}
              </div>
              <section className="requests-card">
                <div className="section-heading">
                  <h2>All requests</h2>
                  <span className="muted">Updates automatically</span>
                </div>
                {tickets.length === 0 ? (
                  <div className="empty">
                    <span className="empty-mark">⌂</span>
                    <h2>Every project starts somewhere.</h2>
                    <p>
                      You haven’t submitted a request yet.
                      <br />
                      Start with your idea. We’ll help with the fine print.
                    </p>
                    <button className="primary" onClick={() => startRequest()}>
                      Create your first request →
                    </button>
                  </div>
                ) : (
                  <div className="request-list">
                    {tickets.map((t) => (
                      <button key={t.id} onClick={() => setSelected(t)}>
                        <div className="request-symbol">⌂</div>
                        <div className="request-text">
                          <strong>{t.proposal.title}</strong>
                          <small>
                            {t.proposal.parcel_id} ·{" "}
                            {new Date(t.created_at).toLocaleDateString()} ·{" "}
                            {t.progress.replaceAll("_", " ")}
                          </small>
                        </div>
                        <Status value={t.status} />
                        <span className="arrow">→</span>
                      </button>
                    ))}
                  </div>
                )}
              </section>
              <div className="how-it-works">
                <span>
                  01 <strong>Tell us your plan</strong>
                </span>
                <span>
                  02 <strong>We check the fine print</strong>
                </span>
                <span>
                  03 <strong>Get a reasoned answer</strong>
                </span>
              </div>
            </>
          )}
          <footer>
            RED COMPASS <span>Fictional policies. Real sandbox isolation.</span>
          </footer>
        </main>
      </div>
    </>
  );
}
function Status({ value }: { value: string }) {
  return (
    <span className={`status ${value}`}>
      <span />
      {value[0].toUpperCase() + value.slice(1)}
    </span>
  );
}
