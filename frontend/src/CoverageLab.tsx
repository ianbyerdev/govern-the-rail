import { useEffect, useState } from "react";
import { ArrowLeft, ArrowRight, Download, Play, RotateCcw, ShieldCheck, TriangleAlert } from "lucide-react";
import { request, type Access } from "./access";
import "./coverage.css";

type CaseId = "C8" | "C9" | "C10";
type Checkpoint = {
  id: string;
  U: number;
  Q: number;
  L: number;
  E: number;
  available: number;
  ledger_compliant: boolean;
  coverage: boolean;
  shared_ceiling_compliant?: boolean;
  outstanding_promises: number;
  evidence_domain: string;
  L_star?: number;
  local_books?: Record<string, Record<string, unknown>>;
  [key: string]: unknown;
};
type Branch = {
  checkpoints: Checkpoint[];
  label?: string;
  [key: string]: unknown;
};
type ObservedRun = {
  id: string;
  case: CaseId;
  title: string;
  status: string;
  error?: string;
  branches?: Record<string, Branch>;
  units?: { scale?: number; name?: string; display_name?: string; [key: string]: unknown };
  provenance?: { source_commit?: string; source_dirty?: boolean; source_tree_sha256?: string; [key: string]: unknown };
  verification?: { valid?: boolean; negative_control_detected?: boolean; conformance?: Record<string, { valid: boolean }>; [key: string]: unknown };
  [key: string]: unknown;
};
type Catalog = {
  cases: { id: CaseId; title: string; supported: boolean; support: string; implementation_status: string }[];
  runs: { id: string; case: CaseId; title: string; status: string }[];
};

const introductions: Record<CaseId, string> = {
  C8: "The rail can accept work while its receipt is withheld. Restarting the Runtime must preserve the original allocation. A deliberately unsafe timeout release reveals what happens when uncertainty is mistaken for spare capacity.",
  C9: "Revocation stops an actor from starting new work. It does not undo a child’s accepted commitment. A competitor with an independently valid grant tests the same shared headroom; institutional cleanup later closes the child’s work.",
  C10: "Two locally compliant books can promise the same downstream capacity twice. Compare three independent configurations: uncoordinated limits, a common covering reservation, and a conserved split of rights.",
};
const branchLabels: Record<string, string> = {
  safe: "Evidence-preserving Runtime",
  unsafe: "Deliberately unsafe control",
  covering: "Common covering reservation",
  split: "Conserved split of rights",
};

function words(value: string) {
  return value.replaceAll("_", " ").replace(/^./, (letter) => letter.toUpperCase());
}

function Predicate({ label, value, detail }: { label: string; value?: boolean; detail: string }) {
  return <div className={`coverage-predicate ${value === false ? "failed" : value === true ? "passed" : "unknown"}`}>
    {value === false ? <TriangleAlert size={16} /> : <ShieldCheck size={16} />}
    <span>{label}<small>{detail}</small></span>
    <b>{value === undefined ? "Unavailable" : value ? "Pass" : "Fail"}</b>
  </div>;
}

function NumberMetric({ label, symbol, value, format }: {
  label: string; symbol: string; value: number; format: (value: number) => string;
}) {
  return <div data-metric={symbol}><dt>{label}<small>{symbol}</small></dt>
    <dd className={value < 0 ? "negative" : ""}>{format(value)}</dd></div>;
}

function BranchPanel({ name, branch, index, format, conformance, isC10 }: {
  name: string; branch: Branch; index: number; format: (value: number) => string;
  conformance?: boolean; isC10: boolean;
}) {
  const actualIndex = Math.min(index, branch.checkpoints.length - 1);
  const checkpoint = branch.checkpoints[actualIndex];
  if (!checkpoint) return <article className="coverage-branch"><h3>{branch.label || words(name)}</h3><p>No recorded checkpoint.</p></article>;
  const localBooks = checkpoint.local_books;
  const label = branch.label || branchLabels[name] || words(name);
  const covering = checkpoint.covering as { Q?: number; config?: { split?: Record<string, number> | null } } | null | undefined;
  return <article className="coverage-branch" aria-label={label}>
    <div className="coverage-branch-heading"><div><div className="eyebrow">{isC10 ? "Independent configuration" : "Matched branch"}</div>
      <h3>{label}</h3></div>
      {conformance !== undefined && <span className={`coverage-tag ${conformance ? "pass" : "fail"}`}>
        Ordinary conformance: {conformance ? "pass" : "fail"}</span>}
    </div>
    <p className="coverage-checkpoint-name">{typeof checkpoint.label === "string" ? checkpoint.label : words(checkpoint.id)}{actualIndex < index ? " · last recorded checkpoint" : ""}</p>
    <dl className="coverage-metrics">
      <NumberMetric label={isC10 ? "Applicable ceiling" : "Limit"} symbol="L" value={checkpoint.L} format={format} />
      <NumberMetric label="Consumed" symbol="U" value={checkpoint.U} format={format} />
      <NumberMetric label="Reserved" symbol="Q" value={checkpoint.Q} format={format} />
      <NumberMetric label="Available capacity" symbol="L − U − Q" value={checkpoint.available} format={format} />
    </dl>
    {localBooks && <div className="coverage-local-books"><h4>Local Runtime books</h4>
      <div className="coverage-table-scroll"><table><thead><tr><th>Book</th><th>L</th><th>U</th><th>Q</th><th>Available</th><th>Ledger</th></tr></thead>
        <tbody>{Object.entries(localBooks).map(([bookName, book]) => {
          const n = (key: string, alias: string) => typeof book[key] === "number" ? book[key] as number : typeof book[alias] === "number" ? book[alias] as number : undefined;
          const l = n("L", "limit"), u = n("U", "consumed"), q = n("Q", "reserved");
          const available = n("available", "headroom");
          const compliant = typeof book.ledger_compliant === "boolean" ? book.ledger_compliant : l !== undefined && u !== undefined && q !== undefined ? u + q <= l : undefined;
          return <tr key={bookName}><th>{bookName}</th><td>{l === undefined ? "—" : format(l)}</td><td>{u === undefined ? "—" : format(u)}</td><td>{q === undefined ? "—" : format(q)}</td><td>{available === undefined ? "—" : format(available)}</td><td className={compliant === false ? "negative" : ""}>{compliant === undefined ? "Unavailable" : compliant ? "Pass" : "Fail"}</td></tr>;
        })}</tbody></table></div>
    </div>}
    {isC10 && <p className="coverage-allocation" data-metric="covering">{covering ? <>
      Covering allocation held: <b>{typeof covering.Q === "number" ? format(covering.Q) : "Unavailable"}</b>
      {covering.config?.split && <> · assigned rights {Object.entries(covering.config.split).map(([key, value]) => `${key}: ${format(value)}`).join(" + ")}</>}
    </> : "No common covering allocation: local limits are independently assigned."}</p>}
    <div className="coverage-obligation">
      <span>Actual modeled obligation <b>E = {format(checkpoint.E)}</b></span>
      <p>Consumed effects, residual commitments, and usable promises; each lifecycle counted once.</p>
      <p>{checkpoint.outstanding_promises} outstanding promises outside accepted rail executions · evidence domain: {checkpoint.evidence_domain}</p>
      {typeof checkpoint.coverage_gap === "number" && <p>Coverage gap E − U − Q: <b className={checkpoint.coverage_gap > 0 ? "negative" : ""}>{format(checkpoint.coverage_gap)}</b> · positive means uncovered obligation.</p>}
    </div>
    <div className="coverage-predicates">
      <Predicate label="Ledger compliance" value={checkpoint.ledger_compliant} detail="U + Q ≤ L at the applicable scope" />
      <Predicate label="Coverage" value={checkpoint.coverage} detail="E ≤ U + Q · independently reconstructed" />
      {isC10 && <Predicate label="True shared ceiling" value={checkpoint.shared_ceiling_compliant}
        detail={`E ≤ L*${typeof checkpoint.L_star === "number" ? ` = ${format(checkpoint.L_star)}` : ""} · one downstream account`} />}
    </div>
    {isC10 && <p className="coverage-scope-note">A green local book can coexist with a red shared scope. Covering reservations and their local suballocations describe the same lifecycles.</p>}
    <details className="coverage-details"><summary>Checkpoint evidence and {isC10 ? "covering allocation / split" : "Runtime snapshot"}</summary>
      <pre>{JSON.stringify(checkpoint, null, 2)}</pre></details>
  </article>;
}

export function CoverageLab({ access, onBack }: { access: Access; onBack: () => void }) {
  const prefix = access.mode === "visitor" ? "/api/demo/coverage" : "/api/operator/coverage";
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  const [selected, setSelected] = useState<CaseId>(access.mode === "visitor" ? "C9" : "C8");
  const [run, setRun] = useState<ObservedRun | null>(null);
  const [cursor, setCursor] = useState(0);
  const [busy, setBusy] = useState("Loading coverage schedules");
  const [error, setError] = useState("");
  const [requestId, setRequestId] = useState(() => crypto.randomUUID());

  useEffect(() => {
    let current = true;
    request(prefix, access).then((value: Catalog) => { if (current) setCatalog(value); })
      .catch((e: Error) => { if (current) setError(e.message); })
      .finally(() => { if (current) setBusy(""); });
    return () => { current = false; };
  }, [prefix, access]);

  async function openRun(identifier: string) {
    setBusy("Opening recorded observation"); setError("");
    try {
      const value: ObservedRun = await request(`${prefix}/runs/${identifier}`, access);
      setSelected(value.case); setRun(value); setCursor(0);
    } catch (e) { setError((e as Error).message); }
    finally { setBusy(""); }
  }

  function choose(value: CaseId) {
    setSelected(value); setRun(null); setCursor(0); setError(""); setRequestId(crypto.randomUUID());
  }

  async function execute() {
    setBusy(`Executing ${selected} backend schedule`); setError("");
    try {
      const value: ObservedRun = await request(`${prefix}/${selected}/run`, access, { request_id: requestId });
      setRun(value); setCursor(0);
      setCatalog(await request(prefix, access));
    } catch (e) { setError((e as Error).message); }
    finally { setBusy(""); }
  }

  async function download() {
    if (!run) return;
    setBusy("Exporting observed evidence"); setError("");
    try {
      const value = await request(`${prefix}/runs/${run.id}/export`, access);
      const url = URL.createObjectURL(new Blob([JSON.stringify(value, null, 2)], { type: "application/json" }));
      const anchor = document.createElement("a"); anchor.href = url; anchor.download = `${run.case}-${run.id}.json`; anchor.click(); URL.revokeObjectURL(url);
    } catch (e) { setError((e as Error).message); }
    finally { setBusy(""); }
  }

  const definition = catalog?.cases.find((item) => item.id === selected);
  const branches = Object.entries(run?.branches || {});
  const frameCount = Math.max(0, ...branches.map(([, branch]) => branch.checkpoints.length));
  const scale = run?.units?.scale;
  const format = (value: number) => typeof value !== "number" ? "Unavailable" :
    typeof scale === "number" && scale > 0 ? new Intl.NumberFormat("en-US", { maximumFractionDigits: 6 }).format(value / scale) : String(value);
  const verified = run?.verification;
  const source = run?.provenance;

  return <main className="coverage-lab">
    <header className="coverage-header"><button onClick={onBack}><ArrowLeft size={16} /> Rail workbench</button><span>Agentic RISC · Coverage schedules</span></header>
    <section className="coverage-intro">
      <div className="eyebrow">Independent controls, broader delegation</div>
      <h1>Agents choose actions. Institutions set the limits.</h1>
      <p>Agentic RISC keeps authority ownership, shared accounting, exact-effect execution, and accepted-outcome reconciliation independent of the actor. An Action Contract links that lifecycle; it is not another service.</p>
    </section>
    <section className="coverage-architecture" aria-label="Agentic RISC architecture">
      <div><span>01</span><h2>Actor environment</h2><p>Model + harness propose an exact effect within a delegated mandate.</p></div><ArrowRight className="coverage-flow-arrow" size={22} />
      <div><span>02</span><h2>RISC Runtime</h2><p>Institutional authority book · policy, grants, allocation, issuance, reconciliation.</p></div><ArrowRight className="coverage-flow-arrow" size={22} />
      <div><span>03</span><h2>RISC Gateway</h2><p>Protected rail · bound authority, live eligibility, durable single-use redemption.</p></div>
      <p className="coverage-return">Accepted outcomes return to the Runtime ← a testing oracle is never authority to release capacity.</p>
    </section>
    <nav className="coverage-cases" aria-label="Coverage schedules">{catalog?.cases.map((item) => <button key={item.id} aria-pressed={selected === item.id} disabled={!!busy} onClick={() => choose(item.id)}>
      <strong>{item.id}</strong><span>{item.title}</span></button>)}</nav>
    <section className="coverage-experiment" aria-label="Selected coverage schedule">
      <div className="coverage-experiment-title"><div><div className="eyebrow">{selected} · {run?.status === "observed" ? "Observed run" : definition?.implementation_status || "Loading implementation status"}</div>
        <h2>{definition?.title || "Coverage schedule"}</h2><p>{introductions[selected]}</p></div>
        <span className="coverage-tag">{run?.status === "observed" ? source?.source_dirty === false ? "Clean source observation" : "Implemented / unpinned observation" : "No observed run selected"}</span>
      </div>
      <p className="coverage-support">{definition?.support} {access.mode === "visitor" ? "Public sessions share the existing quotas and heavy-job controls." : "Separate-process results cover a same-host test topology only."}</p>
      <div className="coverage-controls"><button className="primary" disabled={!!busy || !definition?.supported || run?.status === "observed"} onClick={execute}><Play size={16} />Run {selected} schedule</button>
        <button disabled={!!busy || !run} onClick={() => choose(selected)}><RotateCcw size={16} />New isolated run</button>
        <button disabled={!!busy || run?.status !== "observed"} onClick={download}><Download size={16} />Export observed evidence</button>
        <label>Saved observations<select aria-label="Saved coverage observation" disabled={!!busy || !catalog?.runs.length} value={run?.id || ""} onChange={(event) => openRun(event.target.value)}>
          <option value="" disabled>Choose a run</option>{catalog?.runs.map((item) => <option key={item.id} value={item.id}>{item.case} · {item.id.slice(-8)} · {item.status}</option>)}</select></label>
      </div>
      <div className="coverage-feedback" role="status">{busy || (run?.status === "observed" ? "Backend schedule complete. The controls below replay its recorded observations." : "Run the fixed schedule to capture observations. Expected checkpoints are specifications, not measurements.")}</div>
      {(error || run?.error) && <p role="alert" className="error">{error || run?.error}</p>}
    </section>
    {run?.status === "observed" && <>
      <section className="coverage-replay" aria-label="Observed run replay">
        <div><div className="eyebrow">Replay state · recorded backend observations</div><h2>Checkpoint {cursor + 1} of {frameCount}</h2>
          <p>Replay does not execute new work. Ordinary conformance summarizes the whole run. {selected === "C10" && "Panels are independent configurations, not cumulative transitions."}</p></div>
        <div><button disabled={!!busy || cursor === 0} onClick={() => setCursor(cursor - 1)}><ArrowLeft size={16} />Previous checkpoint</button>
          <button disabled={!!busy || cursor >= frameCount - 1} onClick={() => setCursor(cursor + 1)}>Step replay<ArrowRight size={16} /></button>
          <button disabled={!!busy || cursor >= frameCount - 1} onClick={() => setCursor(frameCount - 1)}>Final checkpoint</button></div>
      </section>
      <p className="coverage-unit-note">{typeof scale === "number" ? `Displayed synthetic units · ${scale} integer domain units per displayed unit.` : "Values shown in exported integer domain units."} Actual arithmetic remains integer-based. Negative capacity and coverage gaps remain visible.</p>
      <section className={`coverage-branches ${selected === "C10" ? "three" : ""}`} aria-label="Observed accounting">
        {branches.map(([name, branch]) => <BranchPanel key={name} name={name} branch={branch} index={cursor} format={format} isC10={selected === "C10"} conformance={verified?.conformance?.[name]?.valid} />)}
      </section>
      <section className="coverage-verification" aria-label="Run verification">
        <div><h2>Observed evidence and verification</h2><p>The intentionally unsafe branch must fail ordinary conformance. Detecting that failure is a separate experiment result; it never turns the unsafe branch into a conforming one.</p></div>
        <div className="coverage-verification-results"><Predicate label="Experiment verifier" value={verified?.valid} detail="Evidence checks and expected-versus-observed comparison" />
          <Predicate label="Intended breach detected" value={verified?.negative_control_detected} detail="Deliberately unsafe control; historical violations retained" /></div>
        <p className="coverage-source">Executed source: <code>{source?.source_commit || "Unavailable"}</code> · {source?.source_dirty === false ? "clean" : source?.source_dirty === true ? "dirty / unpinned" : "status unavailable"}<br />Source-tree digest: <code>{source?.source_tree_sha256 || "Unavailable"}</code></p>
        <details className="coverage-details"><summary>Technical details, expected checkpoints, topology, and verifier output</summary>
          <p>Expected checkpoints remain expected-only data. The branch panels above render recorded backend checkpoints. Oracle reconstruction cannot authorize Runtime release; accepted institutional evidence uses the ordinary receipt interface.</p>
          <pre>{JSON.stringify({ units: run.units, accounting_window: run.accounting_window, topology: run.topology, expected: run.expected, expected_vs_observed: run.expected_vs_observed, verification: run.verification, provenance: run.provenance, limitations: run.limitations }, null, 2)}</pre></details>
      </section>
    </>}
    <footer className="coverage-limitations">Synthetic order/account fixtures. No production OMS, payment, or EHR integration; no distributed exactly-once or host compromise claim. Hosted C8 execution is unsupported. Local results and public visitor support are evaluated separately.</footer>
  </main>;
}
