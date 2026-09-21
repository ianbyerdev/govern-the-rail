import { useEffect, useState } from "react";
import {
  ArrowRight,
  ArrowLeft,
  Layers3,
  ShieldCheck,
  Terminal,
  Activity,
  FileHeart,
  Wallet,
  Plus,
  RotateCcw,
  Play,
  LockKeyhole,
  CheckCircle2,
  AlertTriangle,
  GitBranch,
  Zap,
} from "lucide-react";
import { request as sessionRequest, type Access } from "./access";
import type { Json, Run } from "./types";
import { short } from "./types";
import {
  domains,
  faults,
  effectTitle,
  policyConfig,
  currency,
  units,
  type Domain,
} from "./domains";
import { DomainForm } from "./components/DomainForm";
import { EffectCard } from "./components/EffectCard";
import { BudgetBook } from "./components/BudgetBook";
import { EvidencePanel } from "./components/EvidencePanel";
import { PolicyEditor } from "./components/PolicyEditor";
import { Lifecycle } from "./components/Lifecycle";
import {
  IntegrationChoice,
  ArchitectureScenarios,
} from "./components/ArchitectureGuide";
const icons = {
  payments: Wallet,
  trading: Activity,
  referrals: FileHeart,
  runtime: Terminal,
};

export function Workbench({
  access,
  onLogout,
  onOpenSwarm,
  onOpenUncertain,
  onOpenCoverage,
}: {
  access: Access;
  onLogout: () => void;
  onOpenSwarm: () => void;
  onOpenUncertain: () => void;
  onOpenCoverage: () => void;
}) {
  const prefix =
    access.mode === "visitor"
      ? "/api/demo/workbench"
      : "/api/operator/workbench";
  const [catalog, setCatalog] = useState<Json>(null),
    [domain, setDomain] = useState<Domain>("payments"),
    [book, setBook] = useState(""),
    [view, setView] = useState<Json>(null);
  const [draft, setDraft] = useState<Json>({}),
    [run, setRun] = useState<Run | null>(null),
    [busy, setBusy] = useState("Connecting"),
    [error, setError] = useState(""),
    [notice, setNotice] = useState("");
  const [fault, setFault] = useState("none"),
    [race, setRace] = useState<Json>(null),
    [probe, setProbe] = useState<Json>(null),
    [cancelRun, setCancelRun] = useState<Json>(null);
  const [integration, setIntegration] = useState("native"),
    [demonstration, setDemonstration] = useState<Json>(null),
    [raceMode, setRaceMode] = useState("saac");
  async function api(path: string, body?: Json, method?: string) {
    return sessionRequest(path, access, body, method);
  }
  async function catalogRefresh() {
    const c = await api(prefix);
    setCatalog(c);
    return c;
  }
  function apply(value: Json) {
    setView(value.view);
    if (value.run) setRun(value.run);
    if (value.cancel_run) setCancelRun(value.cancel_run);
    return value;
  }
  async function load(id: string, c: Json) {
    const value = await api(`${prefix}/books/${id}`);
    setBook(id);
    setView(value.view);
    setDomain(value.view.profile);
    setDraft({
      ...c.defaults[value.view.profile],
      actor_risk_observed: { available: 999999 },
    });
    setRun(null);
    setFault("none");
    setRace(null);
    setProbe(null);
    setCancelRun(null);
    setDemonstration(null);
    sessionStorage.setItem("saac-book", id);
    if (value.view.runs[0])
      setRun(
        (await api(`${prefix}/books/${id}/runs/${value.view.runs[0].id}`)).run,
      );
  }
  async function fresh(profile = domain) {
    const value = await api(prefix + "/books", { profile });
    const c = await catalogRefresh();
    await load(value.book_id, c);
    setNotice(
      "Fresh isolated experiment. Earlier books and their evidence are retained.",
    );
    return value;
  }
  async function task(label: string, fn: () => Promise<void>) {
    setBusy(label);
    setError("");
    setNotice("");
    try {
      await fn();
    } catch (e) {
      setError((e as Error).message);
      if (book) {
        try {
          const data = await api(`${prefix}/books/${book}`);
          setView(data.view);
          if (run)
            setRun((await api(`${prefix}/books/${book}/runs/${run.id}`)).run);
        } catch {
          /* Retain original error if reconnect also fails. */
        }
      }
    } finally {
      setBusy("");
    }
  }
  useEffect(() => {
    let active = true;
    (async () => {
      try {
        const c = await catalogRefresh();
        if (!active) return;
        const saved = c.books.find(
          (b: Json) => b.id === sessionStorage.getItem("saac-book"),
        );
        const recent =
          saved ||
          c.books.find(
            (b: Json) => b.profile === "payments" && b.id !== "main",
          );
        if (recent) await load(recent.id, c);
        else {
          const v = await api(prefix + "/books", { profile: "payments" });
          await load(v.book_id, await catalogRefresh());
        }
      } catch (e) {
        if (active) setError((e as Error).message);
      } finally {
        if (active) setBusy("");
      }
    })();
    return () => {
      active = false;
    };
  }, [access]);
  async function choose(profile: Domain) {
    await task("Opening domain workspace", async () => {
      const c = await catalogRefresh();
      const existing = c.books.find(
        (b: Json) => b.profile === profile && b.id !== "main",
      );
      if (existing) await load(existing.id, c);
      else await fresh(profile);
    });
  }
  const url = `${prefix}/books/${book}`;
  async function request(preview: boolean, proposal = draft) {
    await task(
      preview
        ? "Resolving and evaluating without authority"
        : "Atomically reserving capacity and issuing authority",
      async () => {
        setProbe(null);
        setCancelRun(null);
        setDemonstration(null);
        const value = await api(url + "/proposals", {
          proposal,
          preview,
          integration,
        });
        apply(value);
      },
    );
  }
  async function approve(approve: boolean) {
    if (!run) return;
    await task(
      approve
        ? "Rechecking the exact approved snapshot"
        : "Rejecting the reviewed action",
      async () => {
        apply(
          await api(`${url}/runs/${run.id}/approval`, {
            snapshot_hash: run.snapshot_hash,
            approve,
          }),
        );
      },
    );
  }
  async function execute(replay = false) {
    if (!run) return;
    await task("Protected socket verification", async () => {
      let request: Json = { pause_receipt: true };
      const proposal: Json = { ...(run.execution_proposal || run.proposal) };
      if (!replay) {
        if (fault === "tamper") {
          if (domain === "payments") proposal.amount_cents += 2000;
          else if (domain === "trading") proposal.quantity += 500;
          else if (domain === "referrals")
            proposal.documents = [...proposal.documents, "doc-7"];
          else proposal.network = ["external"];
          request.proposal = proposal;
        }
        if (fault === "substitute") {
          if (domain === "payments") proposal.beneficiary = "community";
          else proposal.recipient = "south-clinic";
          request.proposal = proposal;
        }
        if (fault === "patient") {
          proposal.patient = "P-205";
          request.proposal = proposal;
        }
        if (fault === "route") {
          proposal.route = "BROKER-B/session19";
          request.proposal = proposal;
        }
        if (fault === "audience") request.audience = "UNRELATED-SOCKET";
        if (fault === "expiry")
          await api(url + "/clock", {
            seconds: Math.min(3600, (run.proposal.ttl_seconds || 120) + 1),
          });
        if (["stale", "consent", "document", "rebind"].includes(fault))
          await api(url + "/state", {
            change:
              fault === "stale"
                ? "resource"
                : fault === "rebind"
                  ? "alias"
                  : fault,
          });
        if (fault === "pack")
          await api(
            url + "/pack",
            {
              ...policyConfig(view.state.pack),
              version: view.state.pack.version + 1,
            },
            "PUT",
          );
        if (fault === "rebind") {
          proposal.beneficiary = "payroll";
          request.proposal = proposal;
        }
        if (fault === "alias") {
          if (domain === "payments") proposal.beneficiary = "vendor-001";
          else proposal.instrument = "XYZ.US";
          request.proposal = proposal;
        }
        if (fault === "lost") request.lose_receipt = true;
        if (["before_dispatch", "after_claim", "after_run"].includes(fault))
          request.runner_fault = fault;
      }
      apply(await api(`${url}/runs/${run.id}/execute`, request));
    });
  }
  async function reconcile() {
    if (!run) return;
    await task("Recovering and reconciling signed evidence", async () =>
      apply(await api(`${url}/runs/${run.id}/reconcile`, {})),
    );
  }
  async function normal() {
    await task("Running a normal action", async () => {
      setDemonstration(null);
      let value = await api(url + "/proposals", {
        proposal: draft,
        integration,
      });
      apply(value);
      if (value.run.kappa) {
        value = await api(`${url}/runs/${value.run.id}/execute`, {
          pause_receipt: false,
        });
        apply(value);
      } else if (value.run.status === "awaiting_approval")
        setNotice(
          "Human review is required. Inspect the exact snapshot before approving.",
        );
    });
  }
  async function inspect(id: string) {
    await task("Loading retained evidence", async () => {
      setCancelRun(null);
      setProbe(null);
      apply(await api(`${url}/runs/${id}`));
    });
  }
  async function stateChange(change: string) {
    await task("Changing authoritative state", async () => {
      apply(await api(url + "/state", { change }));
      setNotice(
        "Authoritative state changed in this book. Previously issued κ retains its original bindings.",
      );
    });
  }
  async function quickHuman() {
    await task("Preparing an exact human-review experiment", async () => {
      const value = await fresh(domain);
      const p = { ...catalog.defaults[domain] };
      if (domain === "payments") p.amount_cents = 15000;
      const config = policyConfig(value.view.state.pack);
      if (domain === "payments") config.approval_above_cents = 10000;
      else
        config.approval_above = Object.fromEntries(
          Object.keys(config.limits).map((k) => [k, 0]),
        );
      await api(
        `${prefix}/books/${value.book_id}/pack`,
        { ...config, version: config.version + 1 },
        "PUT",
      );
      setDraft(p);
      apply(
        await api(`${prefix}/books/${value.book_id}/proposals`, {
          proposal: p,
          integration,
        }),
      );
    });
  }
  async function coverage(name: string) {
    await task("Running the fixed coverage probe", async () => {
      const value = await api(url + "/probe", { probe: name });
      apply(value);
      setProbe(value.probe);
    });
  }
  async function demonstrate(scenario: string) {
    await task("Running the architecture demonstration", async () => {
      const value = await api(`${prefix}/demonstrations/${scenario}`, {
        integration,
      });
      await load(value.book_id, await catalogRefresh());
      apply(value);
      setDemonstration(value.demonstration);
    });
  }
  if (!view)
    return (
      <div className="connection">
        <Layers3 size={32} />
        <h1>Govern The Rail Lab</h1>
        <p>{busy || "Unable to connect to the local workspace."}</p>
        {error && <p role="alert">{error}</p>}
        <button onClick={onLogout}>Return to start</button>
      </div>
    );
  const d = domains[domain],
    effect = run?.snapshot?.effect;
  const execution = run?.execution,
    result = execution?.result;
  const decision = run?.socket_decision;
  const actorClaim =
    run?.proposal.actor_risk_observed?.available ??
    run?.proposal.actor_risk_observed?.limit_cents;
  const socketDenied = decision && decision.code !== "EXECUTED";
  const pendingReceipt =
    !!execution && (!run?.receipt || run.receipt.revision < execution.revision);
  const queued = domain === "runtime" && result?.status === "queued";
  const issued = !!run?.kappa;
  const unspent = issued && !execution;
  const approvedPending = run?.status === "awaiting_approval";
  const publicRuntime = access.mode === "visitor" && domain === "runtime";
  const canIssue = !run || run.status === "preview" || run.status === "denied";
  let nextLabel = "New proposal",
    next = () => {
      setRun(null);
      setFault("none");
      setProbe(null);
      setCancelRun(null);
    };
  if (!run) {
    nextLabel = "Resolve & evaluate";
    next = () => void request(true);
  } else if (canIssue) {
    nextLabel = "Request authority";
    next = () => void request(false);
  } else if (approvedPending) {
    nextLabel = "Review exact effect below";
    next = () => {};
  } else if (unspent) {
    nextLabel = publicRuntime
      ? "Execution requires administrator access"
      : "Present κ to socket";
    next = () => void execute();
  } else if (pendingReceipt || queued || run.status === "uncertain") {
    nextLabel = queued
      ? "Dispatch / recover job"
      : run.status === "uncertain"
        ? "Recover signed ρ"
        : "Reconcile signed ρ";
    next = () => void reconcile();
  }
  const workingOrder =
    domain === "trading" &&
    ["working", "partial"].includes(result?.order?.status);
  const latestReceipt = run?.events
    ?.filter((e) => e.kind === "RECEIPT_CREATED")
    .at(-1)?.data;
  const receiptTitle =
    run?.status === "uncertain"
      ? "Receipt missing · capacity remains held"
      : pendingReceipt
        ? "ρ recorded at socket · awaiting reconciliation"
        : run?.receipt
          ? "ρ accepted · authoritative book updated"
          : socketDenied
            ? "Execution refused · reservation retained"
            : "ρ returns here after execution";
  return (
    <div className="workspace-shell">
      <header className="app-header">
        <div className="brand">
          <span>
            <Layers3 size={21} />
          </span>
          <strong>Agentic RISC</strong>
          <em>/ Govern The Rail Lab</em>
        </div>
        <div className="header-right">
          <span className="local-status">
            <i />
            Local simulation
          </span>
          <span className="small muted">
            <LockKeyhole size={14} />{" "}
            {access.mode === "visitor"
              ? "Your demo institution"
              : "Institution operator"}
          </span>
        </div>
      </header>
      <nav className="domain-nav" aria-label="Use case">
        {(Object.keys(domains) as Domain[]).map((key) => {
          const Icon = icons[key];
          return (
            <button
              disabled={!!busy}
              aria-pressed={domain === key}
              key={key}
              onClick={() => choose(key)}
            >
              <Icon size={17} />
              {domains[key].label}
            </button>
          );
        })}
        <button onClick={onOpenSwarm}>
          <GitBranch size={17} /> Incident / Swarm Lab
        </button>
        <button onClick={onOpenUncertain}>
          <ShieldCheck size={17} /> Missing receipt experiment
        </button>
        <button onClick={onOpenCoverage}>
          <ShieldCheck size={17} /> Coverage schedules C8–C10
        </button>
      </nav>
      <main className="workbench-main">
        <div className="page-heading">
          <div>
            <div className="eyebrow">Agentic RISC · Authority in motion</div>
            <h1>{d.title}</h1>
            <p>{d.subtitle}</p>
          </div>
          <div className="book-controls">
            <label>
              Experiment book
              <select
                aria-label="Experiment book"
                disabled={!!busy}
                value={book}
                onChange={(e) =>
                  task("Opening the selected book", () =>
                    load(e.target.value, catalog),
                  )
                }
              >
                {catalog?.books
                  .filter((b: Json) => b.profile === domain)
                  .map((b: Json) => (
                    <option key={b.id} value={b.id}>
                      {b.label}
                    </option>
                  ))}
              </select>
            </label>
            <button
              disabled={!!busy}
              onClick={() =>
                task("Creating an isolated book", async () => {
                  await fresh();
                })
              }
            >
              <Plus size={16} />
              Fresh experiment
            </button>
          </div>
        </div>
        <div className="context-strip">
          <span className="live-badge">Live experiment</span>
          <span>
            {short(book)} · {view.state.pack.pack_id}
          </span>
          <span>{run ? `Action ${short(run.id)}` : "No action yet"}</span>
          <span className="context-clock">
            Clock {new Date(view.now * 1000).toISOString().slice(11, 19)} UTC
          </span>
        </div>
        {(error || notice || busy) && (
          <div
            role={error ? "alert" : "status"}
            className={"message " + (error ? "error" : "")}
          >
            {error || busy || notice}
            {busy && !error ? "…" : ""}
          </div>
        )}
        <IntegrationChoice
          mode={integration}
          setMode={setIntegration}
          busy={!!busy}
        />
        <div className="action-bar">
          <label>
            At the consequential boundary
            <select
              aria-label="Attack / failure condition"
              disabled={!!busy}
              value={fault}
              onChange={(e) => {
                setFault(e.target.value);
                if (e.target.value === "rebind" && !issued)
                  setDraft({ ...draft, beneficiary: "payroll" });
              }}
            >
              {faults[domain].map(([v, l]) => (
                <option key={v} value={v}>
                  {l}
                </option>
              ))}
            </select>
          </label>
          <div className="action-buttons">
            {!issued && !approvedPending && (
              <button disabled={!!busy || publicRuntime} onClick={normal}>
                <Play size={16} />
                Run normal action
              </button>
            )}
            <button
              className="primary"
              disabled={!!busy || approvedPending || (publicRuntime && unspent)}
              onClick={next}
            >
              {nextLabel}
              <ArrowRight size={16} />
            </button>
          </div>
        </div>
        <Lifecycle
          key={`lifecycle:${book}:${run?.id || "none"}`}
          run={run}
          view={view}
          busy={!!busy}
        />
        <div className="ownership-grid">
          <section className="owner-lane actor-lane" aria-label="Actor environment">
            <div className="lane-heading">
              <div className="eyebrow">
                01 / Actor environment
              </div>
              <h2>Propose</h2>
              <span>Model + harness · snapshot →</span>
            </div>
            <div className="lane-body">
              {run ? (
                <>
                  <div className="recorded-proposal">
                    <div className="eyebrow">Recorded raw proposal</div>
                    <h3>
                      {domain === "payments"
                        ? `${currency(run.proposal.amount_cents)} to “${run.proposal.beneficiary}”`
                        : domain === "trading"
                          ? `${run.proposal.quantity || "Cancel"} ${run.proposal.instrument || run.proposal.order_id}`
                          : domain === "referrals"
                            ? `Send ${run.proposal.documents.length} records for ${run.proposal.patient}`
                            : run.proposal.operation === "job.delegate"
                              ? `Delegate to ${run.proposal.delegation.agent_id}`
                              : "Run the audit script in workspace/demo"}
                    </h3>
                    <p>
                      {run.proposal.agent_id}
                      <br />
                      {run.proposal.principal_id}
                    </p>
                    <div className="actor-belief">
                      <div className="eyebrow">Non-authoritative snapshot</div>
                      <p>
                        Actor claims{" "}
                        {actorClaim == null
                          ? "unspecified"
                          : units[domains[domain].unit].format(actorClaim)}{" "}
                        available capacity. The institution independently checks
                        its book.
                      </p>
                    </div>
                  </div>
                  <details className="next-draft">
                    <summary>Edit a new proposal</summary>
                    <DomainForm
                      domain={domain}
                      draft={draft}
                      setDraft={setDraft}
                      busy={!!busy}
                      issued={!!run}
                    />
                    {run && (
                      <button
                        className="wide secondary-proposal"
                        disabled={!!busy}
                        onClick={() => request(true)}
                      >
                        Resolve this draft as a new action
                      </button>
                    )}
                  </details>
                </>
              ) : (
                <DomainForm
                  domain={domain}
                  draft={draft}
                  setDraft={setDraft}
                  busy={!!busy}
                  issued={false}
                />
              )}
              <div className="boundary-note">
                <LockKeyhole size={16} />
                <p>
                  Cannot change packs, keys, limits, reservations or the kill
                  switch.
                </p>
              </div>
            </div>
          </section>
          <section
            className="owner-lane risk-lane"
            aria-label="Institutional authority"
          >
            <div className="lane-heading">
              <div className="eyebrow">02 / RISC Runtime</div>
              <h2>Authorize</h2>
              <span>Institutional authority book · κ → Gateway</span>
            </div>
            <div className="lane-body">
              <EffectCard effect={effect} review={approvedPending} />
              <div
                className={
                  "verdict " +
                  (run?.decision?.verdict === "deny" ? "negative" : "")
                }
              >
                <span>
                  {run?.decision?.verdict === "deny" ? (
                    <AlertTriangle size={17} />
                  ) : run?.decision ? (
                    <CheckCircle2 size={17} />
                  ) : (
                    <ShieldCheck size={17} />
                  )}
                </span>
                <div>
                  <strong>
                    {run?.decision?.verdict?.toUpperCase() || "No verdict yet"}
                  </strong>
                  <p>
                    {run?.decision?.verdict === "allow"
                      ? "Decision recorded · not executable authority"
                      : run?.decision?.message ||
                        "The independent runtime evaluates the live pack."}
                  </p>
                  {run?.decision && <code>{run.decision.code}</code>}
                </div>
              </div>
              {approvedPending && (
                <div className="human-review">
                  <h3>Approve this effect only</h3>
                  <p>
                    Review the resolved resource, arguments, destination and
                    versions above. This approval cannot transfer to a changed
                    action.
                  </p>
                  <code>{short(run?.snapshot_hash)}</code>
                  <div className="button-row">
                    <button
                      className="primary"
                      disabled={!!busy}
                      onClick={() => approve(true)}
                    >
                      Approve exact snapshot
                    </button>
                    <button disabled={!!busy} onClick={() => approve(false)}>
                      Reject
                    </button>
                  </div>
                </div>
              )}
              <div className={"capability " + (!issued ? "absent" : "")}>
                <div>
                  <ShieldCheck size={17} />
                  <strong>
                    {issued ? "κ · Exact-effect capability" : "κ · Not issued"}
                  </strong>
                </div>
                <p>
                  {issued
                    ? `For ${run.kappa.aud} only`
                    : "A proposal or ALLOW verdict cannot execute an effect."}
                </p>
                {issued && (
                  <>
                    <div className="small">
                      {execution
                        ? "Nonce consumed · replay prohibited"
                        : "Single-use nonce · capacity committed"}
                    </div>
                    <div className="small muted">
                      Expires{" "}
                      {new Date(run.kappa.exp * 1000)
                        .toISOString()
                        .slice(11, 19)}{" "}
                      UTC · Ed25519
                    </div>
                    <code>{short(run.reservation_id)}</code>
                  </>
                )}
              </div>
              <BudgetBook metrics={view.metrics} domain={domain} />
            </div>
          </section>
          <section
            className="owner-lane socket-lane"
            aria-label="Protected socket"
          >
            <div className="lane-heading">
              <div className="eyebrow">03 / RISC Gateway</div>
              <h2>Enforce</h2>
              <span>{decision?.audience || d.audience} · protected socket</span>
            </div>
            <div className="lane-body">
              <div className="check-heading">Independent verification</div>
              <ul className="socket-checks">
                {[
                  "Issuer signature",
                  "Audience + principal",
                  "Nonce + expiry",
                  "Live pack + grant",
                  "Resource + state bindings",
                  "Exact effect",
                ].map((label, i) => (
                  <li key={label}>
                    <span className={decision && !socketDenied ? "passed" : ""}>
                      {!decision
                        ? "○"
                        : !socketDenied
                          ? "✓"
                          : i === 0 && decision.signature_valid
                            ? "✓"
                            : "·"}
                    </span>
                    {label}
                  </li>
                ))}
              </ul>
              {decision && (
                <div
                  className={
                    "socket-decision " +
                    (socketDenied ? "rejected" : "accepted")
                  }
                >
                  <strong>{decision.code}</strong>
                  <p>{decision.message}</p>
                  <span className="small">
                    Signature:{" "}
                    {decision.signature_valid ? "verified" : "not verified"}
                  </span>
                </div>
              )}
              <div className={"consequence " + (result ? "occurred" : "")}>
                <div className="eyebrow">
                  {result ? "Durable socket outcome" : "Consequential effect"}
                </div>
                <h3>
                  {!result
                    ? "No effect yet"
                    : result.status === "non_use"
                      ? "Unused authority closed"
                      : result.status === "paid"
                        ? "Payment recorded"
                        : result.status === "disclosed"
                          ? "Sealed packet disclosed"
                          : result.order
                            ? `${result.order.status} · ${result.order.filled.toLocaleString()} / ${result.order.quantity.toLocaleString()} filled`
                            : result.status === "completed"
                              ? "Constrained audit completed"
                              : result.status === "queued"
                                ? "Dispatch intent committed"
                                : result.status}
                </h3>
                {!result ? (
                  <p>
                    The socket must verify κ itself. The actor cannot substitute
                    an evaluator’s verdict.
                  </p>
                ) : (
                  <>
                    <p>
                      {domain === "trading" && result.order
                        ? `${currency(result.order.notional)} realized. ${result.order.quantity - result.order.filled} shares ${workingOrder ? "can still fill." : "closed."}`
                        : domain === "referrals"
                          ? "The recipient-owned synthetic inbox contains the exact sealed records. This disclosure cannot be undone."
                          : domain === "runtime"
                            ? "Starts and job slots settle only from accepted runner evidence."
                            : `Canonical beneficiary: ${result.beneficiary || effect?.r}`}
                    </p>
                    <div className="small muted">
                      Execution {short(execution?.id)} · revision{" "}
                      {execution?.revision}
                    </div>
                  </>
                )}
              </div>
              {workingOrder && (
                <div className="order-actions">
                  <button
                    disabled={!!busy}
                    onClick={() =>
                      task("Simulating a bounded venue fill", async () => {
                        apply(
                          await api(`${url}/runs/${run?.id}/fill`, {
                            quantity: Math.min(
                              400,
                              result.order.quantity - result.order.filled,
                            ),
                            lose_receipt: fault === "lost",
                          }),
                        );
                      })
                    }
                  >
                    Simulate{" "}
                    {Math.min(400, result.order.quantity - result.order.filled)}
                    -share fill
                  </button>
                  <button
                    disabled={!!busy}
                    onClick={() =>
                      task(
                        "Authorizing and confirming cancellation",
                        async () => {
                          apply(await api(`${url}/runs/${run?.id}/cancel`, {}));
                        },
                      )
                    }
                  >
                    Authorize cancel remainder
                  </button>
                  <p className="small muted">
                    The OMS exclusively owns the simulated EMS. Cancellation is
                    a separate exact effect.
                  </p>
                </div>
              )}
              {cancelRun && (
                <details className="compact-details">
                  <summary>Inspect separate cancellation κ</summary>
                  <pre>{JSON.stringify(cancelRun.kappa, null, 2)}</pre>
                </details>
              )}
              {result?.evidence?.probe_results && (
                <div className="probe-results">
                  <strong>Actual sandbox probes</strong>
                  {result.evidence.probe_results.probes.map((p: Json) => (
                    <div key={p.probe}>
                      <span>{p.blocked ? "Blocked" : "Reached"}</span>
                      <p>
                        {p.probe}
                        <small>{p.mechanism}</small>
                      </p>
                    </div>
                  ))}
                </div>
              )}
              {execution && (
                <button
                  className="replay-button"
                  disabled={!!busy}
                  onClick={() => execute(true)}
                >
                  <RotateCcw size={15} />
                  Attempt replay
                </button>
              )}
              {(unspent || queued) && (
                <button
                  className="replay-button"
                  disabled={!!busy}
                  onClick={() =>
                    task("Proving non-use and closing redemption", async () => {
                      apply(
                        await api(`${url}/runs/${run?.id}/close-unused`, {}),
                      );
                    })
                  }
                >
                  Close unused authority + release
                </button>
              )}
              {latestReceipt && (
                <div className="small socket-knowledge">
                  Socket has signed ρ, revision {latestReceipt.revision}.<br />
                  {pendingReceipt
                    ? "Risk book has not accepted this revision."
                    : "Risk book has accepted the current evidence."}
                </div>
              )}
            </div>
          </section>
          <div className="actor-footnote">
            Proposal ≠ authority
            <br />
            Actor beliefs never reserve capacity.
          </div>
          <div
            className={
              "receipt-return " +
              (run?.status === "uncertain" ? "uncertain" : "")
            }
            aria-label="Receipt reconciliation"
          >
            <ArrowLeft size={24} />
            <div>
              <strong>{receiptTitle}</strong>
              <p>
                {run?.status === "uncertain"
                  ? "The socket may have executed. Expiry cannot manufacture free capacity."
                  : pendingReceipt
                    ? "The authoritative book keeps Q until it verifies and accepts this evidence."
                    : run?.receipt
                      ? workingOrder
                        ? "Fill usage is settled; the working remainder remains reserved."
                        : "Reconciliation applied signed evidence exactly once."
                      : "Signed execution evidence flows back to the institution."}
              </p>
            </div>
          </div>
        </div>
        {domain === "payments" && (
          <ArchitectureScenarios
            busy={!!busy}
            demo={demonstration}
            run={demonstrate}
            inspect={inspect}
          />
        )}
        <div className="experiment-tools">
          <div>
            <div className="eyebrow">Explore the invariants</div>
            <h2>Change one condition. Watch the boundary.</h2>
            <p>
              Faults operate on the selected action and book. Fresh experiments
              preserve earlier evidence.
            </p>
          </div>
          <div className="button-row">
            <label>
              Concurrency mode
              <select
                disabled={!!busy}
                value={raceMode}
                onChange={(e) => setRaceMode(e.target.value)}
              >
                <option value="saac">SAAC atomic authority</option>
                <option value="naive">Naive policy</option>
              </select>
            </label>
            <button disabled={!!busy} onClick={quickHuman}>
              <ShieldCheck size={16} />
              Human review experiment
            </button>
            <button
              disabled={!!busy}
              onClick={() =>
                task(
                  "Launching 100 concurrent requests against one shared collar",
                  async () => {
                    const value = await api(`${prefix}/race/${domain}`, {
                      mode: raceMode,
                    });
                    const c = await catalogRefresh();
                    await load(value.book_id, c);
                    apply(value);
                    setRace(value.race);
                  },
                )
              }
            >
              <Zap size={16} />
              Launch 100 concurrent requests
            </button>
          </div>
        </div>
        {race && (
          <div className="race-result">
            <div>
              <h3>
                {race.mode === "naive"
                  ? "Naive policy exceeds the limit"
                  : race.accepted === 10
                    ? "Invariant holds"
                    : "Inspect admission results"}
              </h3>
              <p>
                {race.mode === "naive"
                  ? "100 stale checks pass · 0 capabilities · 0 protected effects"
                  : `${race.accepted} capabilities issued · ${race.denied} denied at reservation`}
              </p>
              {domain === "payments" && (
                <p className="race-money">
                  100 agents × $100,000 · shared limit $1,000,000
                  <br />
                  {race.mode === "naive"
                    ? "$10,000,000 potential exposure"
                    : "$1,000,000 reserved · $9,000,000 refused"}
                </p>
              )}
              <div
                className="request-grid"
                aria-label="100 concurrent request outcomes"
              >
                {(race.mode === "naive"
                  ? Array(100).fill(true)
                  : race.outcomes
                ).map((ok: boolean, i: number) => (
                  <button
                    disabled={!!busy || race.mode === "naive"}
                    onClick={() => inspect(race.requests[i].run_id)}
                    className={
                      race.mode === "naive"
                        ? "unsafe-admitted"
                        : ok
                          ? "accepted"
                          : ""
                    }
                    key={i}
                    aria-label={`Request ${i + 1}: ${race.mode === "naive" ? "unsafe stale check" : ok ? "reserved" : "denied"}`}
                  />
                ))}
              </div>
            </div>
            <div className="unsafe-comparison">
              <div className="eyebrow">
                Intentionally unsafe comparison · separate memory
              </div>
              <h3>
                {race.unsafe.accepted} admitted against 10 available units
              </h3>
              <p>
                Each naive request checks the same stale snapshot, then updates
                later. This path has no signing key or protected socket.
              </p>
              <strong>{race.unsafe.reserved - 10} units oversubscribed</strong>
              {domain === "payments" && (
                <p className="race-money">
                  $10,000,000 potential exposure
                  <br />
                  $9,000,000 over the shared limit
                </p>
              )}
              <p className="small">
                SAAC tape verification:{" "}
                {race.verification.valid ? "passed" : "failed"}
              </p>
            </div>
          </div>
        )}
        {
          <section className="coverage-lab">
            <div>
              <div className="eyebrow">Coverage / containment lab</div>
              <h2>Which mechanism stops the attempt?</h2>
              <p>
                A protected gate can refuse a call. OS containment limits
                reachable paths. An unprotected endpoint remains a gap.
              </p>
              {domain === "runtime" && (
                <p>
                  A script hash binds code identity; it does not prove the
                  script’s semantic effects. The fixed swarm workers submit
                  typed API proposals through a supervisor-owned gateway; each
                  consequential operation still requires its own κ at that rail.
                </p>
              )}
            </div>
            <div className="button-row">
              <button disabled={!!busy} onClick={() => coverage("unsigned")}>
                Call socket without κ
              </button>
              {domain === "runtime" && (
                <>
                  <button
                    disabled={!!busy}
                    onClick={() => coverage("delegate")}
                  >
                    <GitBranch size={16} />
                    Delegate narrower authority
                  </button>
                  <button disabled={!!busy} onClick={() => coverage("expand")}>
                    Attempt delegation expansion
                  </button>
                  <button
                    disabled={!!busy}
                    onClick={() => coverage("unprotected")}
                  >
                    Try isolated unprotected sink
                  </button>
                </>
              )}
            </div>
            {domain === "runtime" && !catalog.containment_available && (
              <p className="message error">
                {access.mode === "visitor"
                  ? "Public demos show job authorization and delegation. Real process execution is available in the administrator workspace."
                  : "Bubblewrap is unavailable here. Runtime execution fails closed; install Bubblewrap on Linux to run the contained job."}
              </p>
            )}
            {probe && (
              <div
                className={
                  "coverage-result " +
                  (!probe.blocked && !probe.run ? "gap" : "")
                }
              >
                <strong>
                  {probe.blocked
                    ? "Attempt blocked"
                    : probe.run
                      ? "Narrow grant issued"
                      : "Bypass succeeds in the dummy sink"}
                </strong>
                <p>{probe.mechanism}</p>
                <pre>
                  {JSON.stringify(probe.result || probe.run?.decision, null, 2)}
                </pre>
                {probe.run?.receipt?.result?.child_grant && (
                  <p>
                    Child {probe.run.receipt.result.child_grant.agent_id}{" "}
                    retains session{" "}
                    {probe.run.receipt.result.child_grant.session_id}. Its scope
                    and expiry cannot exceed the parent.
                  </p>
                )}
              </div>
            )}
          </section>
        }
        <EvidencePanel
          key={`${book}:${run?.id || "none"}`}
          run={run}
          busy={!!busy}
          onTape={() => api(url + "/tape")}
        />
        <PolicyEditor
          key={book}
          domain={domain}
          pack={view.state.pack}
          busy={!!busy}
          publish={(p) =>
            task("Publishing the signed pack", async () => {
              apply(await api(url + "/pack", p, "PUT"));
              setNotice(
                "Pack superseded in this experiment. Existing κ retains the old hash.",
              );
            })
          }
          change={stateChange}
          halted={view.state.breaker}
          halt={() =>
            task("Changing institutional grant state", async () =>
              apply(
                await api(url + "/breaker", { halted: !view.state.breaker }),
              ),
            )
          }
        />
        {view.runs.length > 0 && (
          <details className="history">
            <summary>
              Retained actions in this book{" "}
              <span>{view.runs.length} shown</span>
            </summary>
            <div className="history-list">
              {view.runs.map((r: Run) => (
                <button
                  key={r.id}
                  disabled={!!busy}
                  onClick={() => inspect(r.id)}
                >
                  <span>{short(r.id)}</span>
                  <strong>{effectTitle(r.snapshot?.effect)}</strong>
                  <span className="status-tag">
                    {r.status.replaceAll("_", " ")}
                  </span>
                </button>
              ))}
            </div>
          </details>
        )}
        <footer className="app-footer">
          <span>
            Agentic RISC · executable research fixture · synthetic resources only
          </span>
          <span>
            Valid authority is a prerequisite for the protected effect.
          </span>
        </footer>
      </main>
    </div>
  );
}
