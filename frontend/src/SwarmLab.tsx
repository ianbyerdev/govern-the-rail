import { useEffect, useRef, useState } from "react";
import {
  ArrowLeft,
  ArrowRight,
  Play,
  Pause,
  SkipForward,
  ShieldCheck,
  Network,
  Layers3,
  FileJson,
  ExternalLink,
  RotateCcw,
} from "lucide-react";
import { request as sessionRequest, type Access } from "./access";
import type { Json } from "./types";
import "./swarm.css";

const colors: Record<string, string> = {
  c: "#899a95",
  a: "#4e87ac",
  p: "#d8dedc",
  e: "#16775e",
  d: "#cb704d",
  b: "#8752a2",
  s: "#a6a6ad",
};
const labels: Record<string, string> = {
  c: "Closed without effect",
  a: "κ issued · no effect",
  p: "Pending",
  e: "Executed",
  d: "Denied",
  b: "Unprotected effect",
  s: "No causal proposal",
};
const units: Record<string, string> = {
  agent_slots: "Concurrent child slots",
  agent_starts: "Lifetime child starts",
  messages: "Shared messages",
  read_bytes: "Read bytes",
  disclosed_bytes: "Disclosed bytes",
};

function Population({
  states,
  selected,
  onSelect,
}: {
  states: string;
  selected: number;
  onSelect: (n: number) => void;
}) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const geometry = useRef({ cols: 1, cell: 10, width: 1, height: 1 });
  useEffect(() => {
    const el = canvas.current!;
    function draw() {
      const w = el.getBoundingClientRect().width,
        cols = Math.max(
          10,
          Math.floor(
            w / (states.length > 1200 ? 6 : states.length > 100 ? 10 : 26),
          ),
        ),
        cell = w / cols;
      const h = Math.max(170, Math.ceil(states.length / cols) * cell);
      geometry.current = { cols, cell, width: w, height: h };
      el.style.height = h + "px";
      el.width = w * devicePixelRatio;
      el.height = h * devicePixelRatio;
      const ctx = el.getContext("2d")!;
      ctx.scale(devicePixelRatio, devicePixelRatio);
      for (let i = 0; i < states.length; i++) {
        const x = (i % cols) * cell + cell / 2,
          y = Math.floor(i / cols) * cell + cell / 2;
        ctx.beginPath();
        ctx.fillStyle = colors[states[i]];
        ctx.arc(x, y, Math.max(1.5, cell * 0.32), 0, Math.PI * 2);
        ctx.fill();
        if (i === selected) {
          ctx.beginPath();
          ctx.strokeStyle = "#142c25";
          ctx.lineWidth = 1.5;
          ctx.arc(x, y, cell * 0.46, 0, Math.PI * 2);
          ctx.stroke();
        }
      }
    }
    const observer = new ResizeObserver(draw);
    observer.observe(el);
    draw();
    return () => observer.disconnect();
  }, [states, selected]);
  return (
    <canvas
      ref={canvas}
      role="img"
      aria-label={`${states.length} logical actors. Use the actor selector below for an accessible equivalent.`}
      onClick={(e) => {
        const r = e.currentTarget.getBoundingClientRect(),
          g = geometry.current;
        const i =
          Math.floor((e.clientY - r.top) / g.cell) * g.cols +
          Math.floor((e.clientX - r.left) / g.cell);
        if (i < states.length) onSelect(i);
      }}
    />
  );
}
function JsonObject({ value }: { value: Json }) {
  return <pre className="swarm-json">{JSON.stringify(value, null, 2)}</pre>;
}

export function SwarmLab({
  access,
  onBack,
}: {
  access: Access;
  onBack: () => void;
}) {
  const prefix =
    access.mode === "visitor" ? "/api/demo/swarm" : "/api/operator/swarm";
  const [catalog, setCatalog] = useState<Json>(null),
    [campaign, setCampaign] = useState<Json>(null),
    [states, setStates] = useState("");
  const [scenario, setScenario] = useState("fanout"),
    [population, setPopulation] = useState(100),
    [posture, setPosture] = useState("full"),
    [scheduling, setScheduling] = useState("replay");
  const [selected, setSelected] = useState(0),
    [intent, setIntent] = useState<Json>(null),
    [tab, setTab] = useState("Story");
  const [events, setEvents] = useState<Json[]>([]),
    [eventCursor, setEventCursor] = useState(0),
    [eventObject, setEventObject] = useState<Json>(null);
  const [busy, setBusy] = useState(""),
    [error, setError] = useState(""),
    [proof, setProof] = useState<Json>(null),
    [comparison, setComparison] = useState<Json>(null);
  const current = useRef("");
  async function api(path: string, body?: Json) {
    return sessionRequest(prefix + path, access, body);
  }
  async function task(label: string, fn: () => Promise<void>) {
    setBusy(label);
    setError("");
    try {
      await fn();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy("");
    }
  }
  useEffect(() => {
    api("")
      .then(setCatalog)
      .catch((e) => setError(e.message));
  }, []);
  async function refresh(id: string) {
    const [s, p] = await Promise.all([
      api("/" + id),
      api("/" + id + "/population"),
    ]);
    if (current.current !== id) return;
    setCampaign(s);
    setStates(p.states);
  }
  async function choose(id: string) {
    current.current = id;
    setIntent(null);
    setEvents([]);
    setEventCursor(0);
    setEventObject(null);
    setSelected(0);
    setProof(null);
    await refresh(id);
    sessionStorage.setItem("saac-campaign", id);
  }
  useEffect(() => {
    if (catalog && !campaign) {
      const saved = sessionStorage.getItem("saac-campaign");
      const reopen =
        catalog.campaigns.find((c: Json) => c.id === saved) ||
        catalog.campaigns[0];
      if (reopen) task("Opening experiment", () => choose(reopen.id));
    }
  }, [catalog]);
  useEffect(() => {
    if (!campaign) return;
    const id = campaign.id;
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      try {
        if (active) await refresh(id);
      } catch (e) {
        if (active) setError((e as Error).message);
      }
      if (active) timer = setTimeout(poll, 900);
    }
    timer = setTimeout(poll, 900);
    return () => {
      active = false;
      clearTimeout(timer);
    };
  }, [campaign?.id]);
  useEffect(() => {
    if (!campaign) return;
    const id = campaign.id;
    let cancelled = false;
    if (intent?.seq !== selected) setIntent(null);
    api("/" + id + "/intents/" + selected)
      .then((v) => {
        if (!cancelled && current.current === id) setIntent(v);
      })
      .catch((e) => {
        if (!cancelled) setError(e.message);
      });
    return () => {
      cancelled = true;
    };
  }, [
    campaign?.id,
    selected,
    states[selected],
    campaign?.institutional?.receipts_accepted,
    campaign?.children?.stopped,
  ]);
  const running = campaign?.status === "running",
    sc = catalog?.scenarios.find(
      (s: Json) => s.id === (campaign?.scenario || scenario),
    ),
    cfg = catalog?.scenarios.find((s: Json) => s.id === scenario);
  async function create() {
    const c = await api("", { scenario, population, posture, scheduling });
    setComparison(null);
    await choose(c.id);
    setCatalog(await api(""));
  }
  async function control(action: string) {
    await api("/" + campaign.id + "/control", { action });
    await refresh(campaign.id);
  }
  async function loadEvents() {
    const v = await api("/" + campaign.id + "/events?after=" + eventCursor);
    setEvents((old) => [...old, ...v.items]);
    setEventCursor(v.next_cursor);
  }
  async function compare() {
    const before = campaign;
    const next = await api("/" + campaign.id + "/compare", { posture });
    setComparison(before);
    await choose(next.id);
    setCatalog(await api(""));
  }
  const run = intent?.run,
    k = run?.kappa,
    rho = run?.receipt;
  const n = (v: number | undefined) => (v || 0).toLocaleString();
  return (
    <div className="workspace-shell swarm-shell">
      <header className="app-header">
        <div className="brand">
          <span>
            <Layers3 size={21} />
          </span>
          <strong>SAAC</strong>
          <em>/ Govern The Rail Lab</em>
        </div>
        <div className="header-right">
          <span className="local-status">
            <i /> Local synthetic resources
          </span>
          <button onClick={onBack}>
            <ArrowLeft size={16} /> Other use cases
          </button>
        </div>
      </header>
      <main className="swarm-main">
        <div className="swarm-heading">
          <div>
            <div className="eyebrow">SAAC · Incident / Swarm Lab</div>
            <h1>Many agents. One authority boundary.</h1>
            <p>
              Watch intent spread. See which effects acquire authority—and what
              happens when a path has no gate.
            </p>
          </div>
          <span className="swarm-research-tag">
            Executable counterfactual
            <br />
            <b>Not a historical reenactment</b>
          </span>
        </div>
        <section className="swarm-setup" aria-label="Experiment setup">
          <label>
            Scenario
            <select
              value={scenario}
              onChange={(e) => setScenario(e.target.value)}
            >
              {catalog?.scenarios.map((s: Json) => (
                <option key={s.id} value={s.id}>
                  {s.title}
                </option>
              ))}
            </select>
          </label>
          <label>
            Logical population
            <select
              value={population}
              onChange={(e) => setPopulation(+e.target.value)}
            >
              {[2, 100, 1200, 5000]
                .filter((n) => n <= (catalog?.max_population ?? 100))
                .map((n) => (
                  <option key={n} value={n}>
                    {n.toLocaleString()} actors
                  </option>
                ))}
            </select>
          </label>
          <label>
            Coverage posture
            <select
              value={posture}
              onChange={(e) => setPosture(e.target.value)}
            >
              {catalog?.postures.map((p: Json) => (
                <option key={p.id} value={p.id}>
                  {p.title}
                </option>
              ))}
            </select>
          </label>
          <label>
            Execution mode
            <select
              value={scheduling}
              onChange={(e) => setScheduling(e.target.value)}
            >
              <option value="replay">Fixed intent replay · 1 worker</option>
              <option value="reactive">
                Reactive prerequisites · 1 worker
              </option>
              <option value="concurrent">Concurrent stress · 16 workers</option>
            </select>
          </label>
          <button
            className="primary"
            disabled={!!busy}
            onClick={() => task("Creating experiment", create)}
          >
            Create experiment <ArrowRight size={16} />
          </button>
          <p>
            {cfg?.summary} <span>Setup changes apply to a new experiment.</span>
          </p>
        </section>
        {error && (
          <div className="error" role="alert">
            {error}
          </div>
        )}
        {busy && (
          <div className="swarm-busy" role="status">
            {busy}…
          </div>
        )}
        {!campaign ? (
          <section className="swarm-empty">
            <Network size={40} />
            <h2>Start with the delegation storm</h2>
            <p>
              100 actors request a child. Only 64 slots exist. Run the same
              proposals with and without protected boundaries, then inspect an
              individual capability.
            </p>
            <div className="swarm-onboarding">
              <span>1 · Create experiment</span>
              <ArrowRight />
              <span>2 · Play or single-step</span>
              <ArrowRight />
              <span>3 · Select a dot to follow its evidence</span>
            </div>
          </section>
        ) : (
          <>
            <section className="swarm-toolbar">
              <div>
                <span className={"swarm-status " + campaign.status}>
                  {campaign.status}
                </span>
                <strong>{sc?.title}</strong>
                <span>
                  {campaign.posture === "full"
                    ? "All modeled paths protected"
                    : campaign.posture === "partial"
                      ? "Coverage gap enabled"
                      : "INTENTIONALLY UNSAFE · no SAAC"}
                </span>
              </div>
              <div className="button-row">
                <button
                  className="primary"
                  disabled={!!busy || running || campaign.status === "complete"}
                  onClick={() => task("Starting", () => control("play"))}
                >
                  <Play size={15} /> Play
                </button>
                <button
                  disabled={!running}
                  onClick={() =>
                    task("Pausing after current batch", () => control("pause"))
                  }
                >
                  <Pause size={15} /> Pause
                </button>
                <button
                  disabled={!!busy || running || campaign.status === "complete"}
                  onClick={() => task("One intent", () => control("step"))}
                >
                  <SkipForward size={15} /> Step
                </button>
              </div>
            </section>
            {campaign.error && (
              <div role="alert" className="error">
                {campaign.error}
              </div>
            )}
            <div className="swarm-progress">
              <i
                style={{
                  width: (100 * campaign.processed) / campaign.population + "%",
                }}
              />
            </div>
            <div className="swarm-run-facts">
              <span>
                {n(campaign.processed)} / {n(campaign.population)} intents
                processed
              </span>
              <span>
                {campaign.worker_limit} bounded host scheduler worker
                {campaign.worker_limit > 1 ? "s" : ""} ·{" "}
                {campaign.real_model_calls} model calls
              </span>
              <span>
                {(campaign.wall_ms / 1000).toFixed(1)}s accumulated wall time ·
                virtual tick {campaign.virtual_tick}
              </span>
            </div>
            <section className="swarm-flow" aria-label="Authority flow">
              <article className="swarm-population">
                <div className="swarm-section-label">
                  <b>01</b>
                  <span>Actors / untrusted proposals</span>
                </div>
                <h2>{n(campaign.population)} logical actors</h2>
                <p className="muted small">
                  Each dot is a bootstrap peer. A dot’s color describes its
                  proposal outcome, not whether the agent is “safe.”
                </p>
                <Population
                  states={states}
                  selected={selected}
                  onSelect={setSelected}
                />
                <div className="swarm-legend">
                  {Object.entries(labels).map(([s, l]) => (
                    <span key={s}>
                      <i style={{ background: colors[s] }} />
                      {l}
                    </span>
                  ))}
                </div>
                <div className="swarm-actor-select">
                  <label>
                    Inspect actor / intent
                    <input
                      aria-label="Actor index"
                      type="number"
                      min={0}
                      max={campaign.population - 1}
                      value={selected}
                      onChange={(e) =>
                        setSelected(
                          Math.max(
                            0,
                            Math.min(
                              campaign.population - 1,
                              Number(e.target.value),
                            ),
                          ),
                        )
                      }
                    />
                  </label>
                  <div>
                    <b>actor-{String(selected).padStart(4, "0")}</b>
                    <span>{labels[states[selected]] || "Loading"}</span>
                  </div>
                  <button
                    disabled={selected === 0}
                    onClick={() => setSelected((s) => s - 1)}
                    aria-label="Previous actor"
                  >
                    ←
                  </button>
                  <button
                    disabled={selected === campaign.population - 1}
                    onClick={() => setSelected((s) => s + 1)}
                    aria-label="Next actor"
                  >
                    →
                  </button>
                </div>
                <details>
                  <summary>Accessible actor list · first 100</summary>
                  <div className="swarm-actor-list">
                    {Array.from(states.slice(0, 100)).map((s, i) => (
                      <button key={i} onClick={() => setSelected(i)}>
                        Actor {i} · {labels[s]}
                      </button>
                    ))}
                  </div>
                  <p className="small">
                    The index selector reaches every actor, including
                    populations above 100.
                  </p>
                </details>
                <div className="swarm-actor-note">
                  Actor belief: “There is plenty of capacity.”
                  <br />
                  <b>This statement does not alter the risk book.</b>
                </div>
              </article>
              <article className="swarm-authority">
                <div className="swarm-section-label">
                  <b>02</b>
                  <span>Institution / authoritative state</span>
                </div>
                <h2>Resolve → reserve → κ</h2>
                <p className="muted small">
                  One campaign book across every actor, descendant and socket.
                  Admission commits risk and authority together.
                </p>
                <div className="swarm-gates">
                  <span>Canonical resource</span>
                  <ArrowRight size={13} />
                  <span>Live pack v{campaign.state.pack_version}</span>
                  <ArrowRight size={13} />
                  <span>Exact-effect κ</span>
                </div>
                <div className="swarm-budgets">
                  {[
                    "agent_slots",
                    "agent_starts",
                    "disclosed_bytes",
                    "messages",
                    "read_bytes",
                  ].map((key) => {
                    const b = campaign.risk.budgets[key];
                    return (
                      <div key={key} data-testid={"swarm-budget-" + key}>
                        <div>
                          <b>{units[key]}</b>
                          <span>
                            {n(b.used + b.reserved)} / {n(b.limit)}
                          </span>
                        </div>
                        <div className="budget-meter">
                          <i
                            style={{
                              width:
                                (b.limit ? (100 * b.used) / b.limit : 0) + "%",
                            }}
                          />
                          <i
                            className="held"
                            style={{
                              width:
                                (b.limit ? (100 * b.reserved) / b.limit : 0) +
                                "%",
                            }}
                          />
                        </div>
                        <small>
                          {n(b.used)} committed · {n(b.reserved)} held ·{" "}
                          {n(b.available)} available
                        </small>
                      </div>
                    );
                  })}
                </div>
                <div className="swarm-denials">
                  <b>Recorded reasons</b>
                  {Object.entries(campaign.reasons).map(([code, count]) => (
                    <div key={code}>
                      <code>{code}</code>
                      <strong>{n(count as number)}</strong>
                    </div>
                  ))}
                  {!Object.keys(campaign.reasons).length && (
                    <p>No decisions yet.</p>
                  )}
                </div>
              </article>
              <article className="swarm-resources">
                <div className="swarm-section-label">
                  <b>03</b>
                  <span>Sockets / consequential effects</span>
                </div>
                <h2>Each boundary verifies</h2>
                <p className="muted small">
                  Four independently keyed sockets. Knowing a key, URL or peer
                  does not replace audience-bound authority.
                </p>
                <div className="swarm-sockets">
                  {[
                    ["ARTIFACT-1", "Team artifacts & messages"],
                    ["EGRESS-1", "Sealed local publishing"],
                    ["RESOURCE-1", "Synthetic resource access"],
                    ["SUPERVISOR-1", "Scoped child starts & stops"],
                  ].map(([id, label]) => (
                    <div key={id}>
                      <ShieldCheck size={19} />
                      <span>
                        <b>{label}</b>
                        <code>{id}</code>
                      </span>
                      <span className="swarm-gated">κ gate</span>
                    </div>
                  ))}
                </div>
                <div className="swarm-child-count">
                  <strong>{n(campaign.children.running)}</strong>
                  <span>
                    admitted logical children running
                    <br />
                    {n(campaign.children.stopped)} stopped with exit evidence
                  </span>
                </div>
                <div
                  className={
                    "swarm-oracle " + (campaign.posture === "full" ? "off" : "")
                  }
                >
                  <b>
                    {campaign.posture === "full"
                      ? "Uncovered path disabled"
                      : "Uncovered local side door"}
                  </b>
                  <p>Simulator oracle only · no κ or institutional ρ</p>
                  <strong>
                    {n(campaign.oracle.bypasses)} effects ·{" "}
                    {n(campaign.oracle.disclosed_bytes)} disclosed bytes
                  </strong>
                </div>
              </article>
              <div className="swarm-receipt-return">
                <RotateCcw size={20} />
                <div>
                  <b>ρ returns evidence to the risk book</b>
                  <span>
                    Loss preserves reservations. Reconciliation needs an
                    accepted observer. Revocation does not undo a completed
                    effect.
                  </span>
                </div>
                <div>
                  <strong>{n(campaign.institutional.uncertain)}</strong>{" "}
                  uncertain
                </div>
                <button
                  disabled={
                    !!busy ||
                    running ||
                    !campaign.institutional.pending_receipts
                  }
                  onClick={() =>
                    task("Returning signed receipts", () => control("recover"))
                  }
                >
                  Return missing receipts
                </button>
              </div>
            </section>
            <div
              className="swarm-counts"
              aria-label="Institutional lifecycle counts"
            >
              {[
                ["Scenario intents processed", campaign.processed],
                ["κ issued", campaign.institutional.capabilities],
                ["κ redeemed", campaign.institutional.redemptions],
                ["ρ accepted", campaign.institutional.receipts_accepted],
              ].map(([label, value]) => (
                <div key={label}>
                  <strong>{n(value as number)}</strong>
                  <span>{label}</span>
                </div>
              ))}
            </div>
            <section className="swarm-inspector">
              <div className="swarm-inspector-heading">
                <div>
                  <div className="eyebrow">Follow one action</div>
                  <h2>
                    Actor {selected} ·{" "}
                    {intent?.outcome?.code || "awaiting proposal"}
                  </h2>
                </div>
                <nav aria-label="Evidence view">
                  {["Story", "Proposal", "Effect", "κ", "ρ", "Trace"].map(
                    (t) => (
                      <button
                        key={t}
                        aria-pressed={tab === t}
                        onClick={() => setTab(t)}
                      >
                        {t}
                      </button>
                    ),
                  )}
                </nav>
              </div>
              <div className="swarm-selected-actions">
                <button
                  disabled={
                    !!busy ||
                    running ||
                    intent?.status !== "pending" ||
                    campaign.posture === "baseline"
                  }
                  onClick={() =>
                    task("Staging exact authority", async () => {
                      setIntent(
                        await api(
                          "/" + campaign.id + "/intents/" + selected + "/probe",
                          { action: "stage" },
                        ),
                      );
                      await refresh(campaign.id);
                    })
                  }
                >
                  Stage selected κ
                </button>
                {["tamper", "audience", "stale", "expiry", "replay"].map(
                  (action) => (
                    <button
                      key={action}
                      disabled={!!busy || running || !k}
                      onClick={() =>
                        task("Testing selected boundary", async () => {
                          setIntent(
                            await api(
                              "/" +
                                campaign.id +
                                "/intents/" +
                                selected +
                                "/probe",
                              { action },
                            ),
                          );
                          await refresh(campaign.id);
                        })
                      }
                    >
                      {
                        {
                          tamper: "Modify effect",
                          audience: "Wrong socket",
                          stale: "Change resource version",
                          expiry: "Advance past expiry",
                          replay: run?.execution
                            ? "Attempt replay"
                            : "Present original κ",
                        }[action]
                      }
                    </button>
                  ),
                )}
                <span>
                  {run?.socket_decision?.code ||
                    (k
                      ? "Authority exists; the socket still decides."
                      : "Stage to inspect authority before any effect.")}
                </span>
              </div>
              {tab === "Story" ? (
                <div className="swarm-story">
                  <div>
                    <h3>What the actor wanted</h3>
                    <p>{intent?.data?.actor_belief}</p>
                    <code>{intent?.data?.proposal?.operation}</code>
                    <p>
                      {intent?.data?.proposal?.resource ||
                        "A scoped local child / team artifact"}
                    </p>
                  </div>
                  <div>
                    <h3>What the institution authorized</h3>
                    <p>
                      {run?.decision?.message ||
                        "This intent has not entered institutional mediation."}
                    </p>
                    <b>
                      {k
                        ? "Signed κ exists for this exact effect."
                        : "No execution capability."}
                    </b>
                  </div>
                  <div>
                    <h3>What actually occurred</h3>
                    <p>
                      {intent?.outcome?.message ||
                        (run?.execution?.result?.status === "non_use"
                          ? "Unused authority was closed; no effect occurred."
                          : run?.execution
                            ? "The protected socket recorded this effect."
                            : "No execution result yet.")}
                    </p>
                    {run?.socket_decision && (
                      <p>{run.socket_decision.message}</p>
                    )}
                    <b>
                      {rho
                        ? "Signed receipt reconciled."
                        : run?.status === "uncertain"
                          ? "Receipt delivery lost; reservation remains."
                          : "No reconciled receipt for this intent."}
                    </b>
                  </div>
                  <div className="swarm-relations">
                    <div>
                      <h3>Communication edges</h3>
                      {intent?.relations?.communication_edge ? (
                        <p>
                          <code>
                            {intent.relations.communication_edge.from}
                          </code>{" "}
                          →{" "}
                          <code>{intent.relations.communication_edge.to}</code>
                          <br />
                          {intent.relations.communication_edge.status} ·
                          recipient{" "}
                          {intent.relations.communication_edge.recipient}
                        </p>
                      ) : (
                        <p>No message edge in this selected intent.</p>
                      )}
                    </div>
                    <div>
                      <h3>Delegation edges</h3>
                      {intent?.relations?.delegation_edges?.length ? (
                        intent.relations.delegation_edges.map((edge: Json) => (
                          <p key={edge.to}>
                            <code>{edge.from}</code> → <code>{edge.to}</code>
                            <br />
                            Narrowed grant · {edge.status} · shared campaign
                          </p>
                        ))
                      ) : (
                        <p>No child authority issued by this selected actor.</p>
                      )}
                    </div>
                    <p>
                      Reading a peer message does not inherit the peer’s grant.
                      Bootstrap peers are independently registered; they are not
                      all children spawned by one agent.
                    </p>
                  </div>
                </div>
              ) : tab === "Proposal" ? (
                <JsonObject value={intent?.data} />
              ) : tab === "Effect" ? (
                <JsonObject
                  value={
                    run?.snapshot?.effect || {
                      message: "No effect has been resolved.",
                    }
                  }
                />
              ) : tab === "κ" ? (
                <>
                  <p>
                    {k
                      ? `Issuer: ${k.kid} · audience: ${k.aud} · socket signature check: ${run.socket_decision?.signature_valid === true ? "verified" : "not recorded"}`
                      : "No κ was issued. A proposal or a verdict cannot execute."}
                  </p>
                  <JsonObject value={k || null} />
                </>
              ) : tab === "ρ" ? (
                <>
                  <p>
                    The observer signature, audience, nonce, execution and
                    cumulative settlement must match the durable journal.
                  </p>
                  <JsonObject
                    value={
                      rho || {
                        message:
                          run?.status === "uncertain"
                            ? "Receipt unavailable to reconciliation; reservation remains authoritative."
                            : "No accepted receipt yet.",
                      }
                    }
                  />
                </>
              ) : (
                <div className="swarm-trace">
                  {run?.events?.map((e: Json) => (
                    <details key={e.seq}>
                      <summary>
                        <span>#{e.seq}</span> {e.kind}
                      </summary>
                      <JsonObject value={e.data} />
                    </details>
                  )) || (
                    <p>
                      No institutional trace; an uncovered effect is an oracle
                      observation.
                    </p>
                  )}
                </div>
              )}
            </section>
            <section className="swarm-experiments">
              <div>
                <h3>Intervene at the boundary</h3>
                <p>
                  Pause first. Issued authority remains tied to its live pack;
                  already running children remain until separately stopped.
                </p>
                <div className="button-row">
                  <button
                    disabled={!!busy || running}
                    onClick={() =>
                      task("Publishing next pack", () => control("pack"))
                    }
                  >
                    Publish next pack
                  </button>
                  <button
                    disabled={!!busy || running}
                    onClick={() =>
                      task("Revoking authority", () => control("revoke"))
                    }
                  >
                    Halt authority
                  </button>
                  <button
                    disabled={!!busy || running || !campaign.state.halted}
                    onClick={() =>
                      task("Restoring institutional supervision", () =>
                        control("restore"),
                      )
                    }
                  >
                    Restore supervision
                  </button>
                  <button
                    disabled={
                      !!busy ||
                      running ||
                      campaign.state.halted ||
                      !campaign.children.running
                    }
                    onClick={() =>
                      task("Authorizing child stops", () => control("stop"))
                    }
                  >
                    Authorize stops
                  </button>
                  <button
                    disabled={!!busy || running}
                    onClick={() =>
                      task("Proving unused authority", () =>
                        control("close-unused"),
                      )
                    }
                  >
                    Close unused κ
                  </button>
                </div>
              </div>
              <div>
                <h3>Compare the same intents</h3>
                <p>
                  Choose a coverage posture above. Clone the exact proposal
                  manifest into an independent book, then press Play.
                </p>
                <button
                  disabled={!!busy || running}
                  onClick={() => task("Cloning intent replay", compare)}
                >
                  Compare with {posture} coverage
                </button>
                {comparison && (
                  <p className="small">
                    Previous run: {comparison.posture} ·{" "}
                    {n(comparison.institutional.redemptions)} protected
                    redemptions · {n(comparison.oracle.bypasses)} oracle
                    effects. Manifest {campaign.intent_manifest_hash.slice(-12)}
                    .
                  </p>
                )}
              </div>
            </section>
            <section className="swarm-bottom">
              <details>
                <summary>Source context & limits of the claim</summary>
                <p>
                  <b>{sc?.claim}</b>
                </p>
                {catalog?.sources
                  .filter((s: Json) => sc?.sources.includes(s.id))
                  .map((s: Json) => (
                    <article key={s.id}>
                      <a href={s.url} target="_blank" rel="noreferrer">
                        {s.organization} · {s.title} <ExternalLink size={12} />
                      </a>
                      <p className="small">
                        Published {s.published} · events: {s.event_period}
                      </p>
                      <p>{s.fact}</p>
                    </article>
                  ))}
                <p>
                  Invented parameters: local resources, dialogues, limits,
                  scopes and schedules. No exploit chain or historical model
                  reasoning is reproduced.
                </p>
                <ul>
                  {catalog?.limitations.map((s: string) => (
                    <li key={s}>{s}</li>
                  ))}
                </ul>
              </details>
              <details>
                <summary>Real contained-worker proof · 2 processes</summary>
                {catalog?.contained_workers === false && (
                  <p className="message">
                    Available in the administrator workspace. Public demos use
                    logical actors without launching processes.
                  </p>
                )}
                <p>
                  Separate from the logical population: fixed Python workers in
                  Bubblewrap have no host network, risk-book files or operator
                  credential. Their private pipes are bound by the supervisor to
                  registered actors. A forged peer identity must fail.
                </p>
                <button
                  disabled={
                    !!busy || running || catalog?.contained_workers === false
                  }
                  onClick={() =>
                    task("Running contained workers", async () => {
                      setProof(
                        await api("/" + campaign.id + "/workers", { count: 2 }),
                      );
                      await refresh(campaign.id);
                    })
                  }
                >
                  Run 2 contained workers
                </button>
                {proof && <JsonObject value={proof} />}
              </details>
              <details>
                <summary>Campaign event journal · cursor pagination</summary>
                <div className="swarm-event-list">
                  {events.map((e) => (
                    <button
                      key={e.seq}
                      onClick={() =>
                        task("Loading event", async () =>
                          setEventObject(
                            await api("/" + campaign.id + "/events/" + e.seq),
                          ),
                        )
                      }
                    >
                      #{e.seq} · {e.kind}
                    </button>
                  ))}
                </div>
                <button
                  disabled={!!busy || eventCursor >= campaign.event_cursor}
                  onClick={() => task("Loading event page", loadEvents)}
                >
                  Load next 100 events
                </button>
                {eventObject && <JsonObject value={eventObject} />}
              </details>
            </section>
            <div className="swarm-footer">
              <label>
                Reopen experiment
                <select
                  value={campaign.id}
                  onChange={(e) =>
                    task("Opening experiment", () => choose(e.target.value))
                  }
                >
                  {catalog?.campaigns.map((c: Json) => (
                    <option key={c.id} value={c.id}>
                      {c.scenario} · {c.population} · {c.posture} ·{" "}
                      {c.id.slice(-6)}
                    </option>
                  ))}
                </select>
              </label>
              <button
                disabled={!!busy || running}
                onClick={() =>
                  task("Verifying and exporting evidence", async () => {
                    const data = await api("/" + campaign.id + "/export");
                    const blob = new Blob([JSON.stringify(data, null, 2)], {
                      type: "application/json",
                    });
                    const url = URL.createObjectURL(blob);
                    const a = document.createElement("a");
                    a.href = url;
                    a.download = campaign.id + ".json";
                    a.click();
                    URL.revokeObjectURL(url);
                  })
                }
              >
                <FileJson size={16} /> Verify & export tape
              </button>
              <span>
                Signed artifacts are real. Resources and actor dialogue are
                synthetic.
              </span>
            </div>
          </>
        )}
      </main>
    </div>
  );
}
