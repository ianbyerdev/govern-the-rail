import { useEffect, useRef, useState } from "react";
import {
  ArrowLeft,
  ArrowRight,
  Check,
  Clock3,
  Download,
  FileText,
  Play,
  RotateCcw,
  ShieldCheck,
  TriangleAlert,
} from "lucide-react";
import { request, type Access } from "./access";
import "./uncertain.css";

const stages = [
  {
    id: "admission",
    label: "Initial admission",
    explanation:
      "The same concurrency barrier releases distinct actors into each isolated book. Admission reserves capacity atomically before a capability can execute.",
  },
  {
    id: "hidden_fills",
    label: "Working orders & hidden fills",
    explanation:
      "Capabilities are redeemed while valid. Acceptance creates working orders; it is not a fill. The rail fills some orders, but their fill receipts are withheld from both authority books.",
  },
  {
    id: "deadline",
    label: "Deadline",
    explanation:
      "Capability expiry does not cancel an existing order. The evidence-based policy starts investigation and retains uncertain commitments. The deliberately unsafe control releases them without terminal evidence.",
  },
  {
    id: "second_batch",
    label: "Second batch",
    explanation:
      "Fresh requests with new identifiers compete for reported availability. Newly admitted capabilities are redeemed while valid. The fixture observer counts actual fills and live orders from the durable rail journal.",
  },
  {
    id: "recovered_fills",
    label: "Recovered fills",
    explanation:
      "Previously withheld signed fill receipts reach reconciliation. Consumed capacity replaces reserved capacity in the evidence-based book. Late evidence also exposes the unsafe control’s unresolved liability.",
  },
  {
    id: "cancellations",
    label: "Confirmed cancellations",
    explanation:
      "Separately authorized cancellations close the original remaining orders. Terminal evidence releases their reservations once. Previously consumed units stay consumed; the unsafe control retains its late-evidence inconsistency.",
  },
] as const;

type Admission = { requested: number; admitted: number; denied: number };
type Policy = {
  id: string;
  label: string;
  book: {
    limit: number;
    consumed: number;
    reserved: number;
    available: number;
  };
  admission: { original: Admission; new: Admission };
  rail: {
    working_orders: number;
    filled_orders: number;
    executed_units: number;
    withheld_receipts: number;
    accepted_evidence: number;
  };
  observer: {
    executed_units: number;
    working_units: number;
    obligation_units: number;
    outstanding_promises: number;
    total_commitment_units: number;
    modeled_available_units: number;
    breach_units: number;
    unaccounted_units: number;
  };
  reconciliation: {
    state: string;
    due: number;
    in_progress: number;
    unresolved: number;
    resolved: number;
    investigations_started: number;
    history?: { state: string; ts: number; reservation_id: string }[];
    exceptions: {
      reservation_id: string;
      age_seconds: number;
      reason: string;
      next_action: string;
    }[];
  };
  invalid_transitions: number;
  receipt_rejections: {
    reservation_id: string;
    code: string;
    message: string;
    receipt: { revision: number; receipt_id: string };
  }[];
};
type Frame = {
  stage: string;
  now: number;
  policies: { evidence: Policy; timeout: Policy };
};
type Experiment = Frame & {
  id: string;
  title: string;
  completed_stages: string[];
  config: {
    unit_notional_cents: number;
    limit_units: number;
    reconcile_by: number;
    [key: string]: unknown;
  };
  frames: Frame[];
  timeline: { stage: string; now: number; label: string }[];
  replay: boolean;
};
type Catalog = {
  experiments: { id: string; stage: string; completed_stages: string[] }[];
};

function formatClock(now: number) {
  return new Date(now * 1000).toISOString().slice(11, 19) + " UTC";
}

function PolicyPanel({ policy, unsafe }: { policy: Policy; unsafe?: boolean }) {
  const book = policy.book;
  const observer = policy.observer;
  const reconciliation = policy.reconciliation;
  const hasBreach = observer.breach_units > 0;
  return (
    <article
      className={`uncertain-policy ${unsafe ? "unsafe" : "evidence"}`}
      aria-label={
        unsafe ? "Release on timeout policy" : "Evidence-based policy"
      }
    >
      <div className="uncertain-policy-title">
        <span className="uncertain-policy-icon">
          {unsafe ? <TriangleAlert size={22} /> : <ShieldCheck size={22} />}
        </span>
        <div>
          <div className="eyebrow">
            {unsafe
              ? "Intentionally incorrect experimental control"
              : "Evidence-based policy"}
          </div>
          <h2>
            {unsafe ? "Release on timeout" : "Evidence-based reconciliation"}
          </h2>
          <p>
            {unsafe
              ? "Deliberately unsafe comparison · separate experiment book"
              : "Uncertainty retains the reservation until accepted evidence"}
          </p>
        </div>
      </div>

      <div className="uncertain-book-label">
        Authority book <span>available = L − U − Q</span>
      </div>
      <dl className="uncertain-book">
        {(
          [
            ["Limit", book.limit, "L"],
            ["Consumed", book.consumed, "U"],
            ["Reserved", book.reserved, "Q"],
            ["Available", book.available, "L − U − Q"],
          ] as const
        ).map(([label, value, symbol]) => (
          <div key={label} data-metric={label.toLowerCase()}>
            <dt>
              {label} <small>{symbol}</small>
            </dt>
            <dd>{value}</dd>
          </div>
        ))}
      </dl>

      <div className={`uncertain-observer ${hasBreach ? "breached" : ""}`}>
        <div className="uncertain-observer-heading">
          <span>Experiment-observed rail obligation</span>
          <strong data-metric="obligation">
            {observer.obligation_units}
            <small> units</small>
          </strong>
        </div>
        <p>
          {observer.executed_units} executed + {observer.working_units} live
          order commitments
        </p>
        <div className="uncertain-observer-footer">
          <span>
            {hasBreach ? <TriangleAlert size={14} /> : <Check size={14} />}
            {hasBreach
              ? `${observer.breach_units} units above the ceiling`
              : "Within the modeled ceiling"}
          </span>
          <span>{observer.outstanding_promises} valid unredeemed promises</span>
        </div>
        <p className="uncertain-total">
          Including outstanding promises:{" "}
          <b>{observer.total_commitment_units} units</b> · each redeemed order
          replaces its promise.
        </p>
        <p className="uncertain-modeled-available">
          Modeled availability:{" "}
          <b data-metric="modeled-available">
            {observer.modeled_available_units}
          </b>{" "}
          units · ceiling − total modeled commitment.
        </p>
      </div>

      <dl className="uncertain-admissions">
        <div>
          <dt>
            Original requests{" "}
            <span>{policy.admission.original.requested} submitted</span>
          </dt>
          <dd>
            <b>{policy.admission.original.admitted}</b> admitted ·{" "}
            {policy.admission.original.denied} denied
          </dd>
        </div>
        <div>
          <dt>
            New requests <span>{policy.admission.new.requested} submitted</span>
          </dt>
          <dd>
            <b>{policy.admission.new.admitted}</b> admitted ·{" "}
            {policy.admission.new.denied} denied
          </dd>
        </div>
      </dl>
      <dl className="uncertain-rail">
        <div>
          <dt>Working orders</dt>
          <dd>{policy.rail.working_orders}</dd>
        </div>
        <div>
          <dt>Filled orders</dt>
          <dd>{policy.rail.filled_orders}</dd>
        </div>
        <div>
          <dt>Withheld receipts</dt>
          <dd>{policy.rail.withheld_receipts}</dd>
        </div>
        <div>
          <dt>Accepted evidence</dt>
          <dd>{policy.rail.accepted_evidence}</dd>
        </div>
      </dl>

      <div className="uncertain-reconciliation">
        <div>
          <Clock3 size={16} />
          <h3>Reconciliation</h3>
          <span className={`uncertain-state ${reconciliation.state}`}>
            {reconciliation.state.replaceAll("_", " ")}
          </span>
        </div>
        <p>
          Due {reconciliation.due} · In progress {reconciliation.in_progress} ·
          Unresolved {reconciliation.unresolved} · Resolved{" "}
          {reconciliation.resolved}
        </p>
        <p>
          {reconciliation.investigations_started} evidence queries started.
          Counts above show current reservation states.
        </p>
        {!!reconciliation.history?.length && (
          <details>
            <summary>Recorded reconciliation transitions</summary>
            <ol className="uncertain-exceptions">
              {reconciliation.history.map((event, index) => (
                <li key={index}>
                  <span>
                    {formatClock(event.ts)} ·{" "}
                    <b>{event.state.replaceAll("_", " ")}</b>
                  </span>
                  <code>{event.reservation_id}</code>
                </li>
              ))}
            </ol>
          </details>
        )}
        {!!observer.unaccounted_units && (
          <p className="uncertain-liability" role="status">
            Unaccounted liability: <b>{observer.unaccounted_units} units</b>.
            These durable rail facts remain visible even when the authority
            cannot accept a late receipt.
          </p>
        )}
        {policy.receipt_rejections.length > 0 && (
          <details className="uncertain-liability">
            <summary>
              {policy.receipt_rejections.length} receipt rejections recorded;
              inspect reasons
            </summary>
            <ul className="uncertain-exceptions">
              {policy.receipt_rejections.map((rejection) => (
                <li key={rejection.receipt.receipt_id}>
                  <b>{rejection.code}</b>
                  <code>{rejection.reservation_id}</code>
                  <p>
                    Receipt revision {rejection.receipt.revision}:{" "}
                    {rejection.message}
                  </p>
                </li>
              ))}
            </ul>
          </details>
        )}
        {reconciliation.exceptions.length > 0 && (
          <details>
            <summary>
              Exception queue · {reconciliation.exceptions.length} unresolved
              reservations
            </summary>
            <ul className="uncertain-exceptions">
              {reconciliation.exceptions.map((exception) => (
                <li key={exception.reservation_id}>
                  <code>{exception.reservation_id}</code>
                  <span>
                    Age: {exception.age_seconds} seconds · {exception.reason}
                  </span>
                  <p>Next action: {exception.next_action}</p>
                </li>
              ))}
            </ul>
          </details>
        )}
        {unsafe && policy.invalid_transitions > 0 && (
          <p className="uncertain-liability">
            {policy.invalid_transitions} deliberately invalid timeout releases
            recorded. Exact accounting transitions are retained in the evidence
            export.
          </p>
        )}
      </div>
    </article>
  );
}

export function UncertainLab({
  access,
  onBack,
}: {
  access: Access;
  onBack: () => void;
}) {
  const prefix =
    access.mode === "visitor"
      ? "/api/demo/uncertain"
      : "/api/operator/uncertain";
  const [experiment, setExperiment] = useState<Experiment | null>(null);
  const [catalog, setCatalog] = useState<Catalog["experiments"]>([]);
  const [replayStage, setReplayStage] = useState<string | null>(null);
  const [busy, setBusy] = useState("Loading experiment");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const locked = useRef(false);
  const active = useRef(true);
  const createRequest = useRef(crypto.randomUUID());

  function apply(value: Experiment) {
    if (!active.current) return;
    setExperiment(value);
    setReplayStage(null);
    sessionStorage.setItem("saac-uncertain", value.id);
    setCatalog((previous) => [
      {
        id: value.id,
        stage: value.stage,
        completed_stages: value.completed_stages,
      },
      ...previous.filter((item) => item.id !== value.id),
    ]);
  }
  async function task(label: string, work: () => Promise<void>) {
    if (locked.current) return;
    locked.current = true;
    setBusy(label);
    setError("");
    setNotice("");
    try {
      await work();
    } catch (caught) {
      if (active.current) setError((caught as Error).message);
    } finally {
      locked.current = false;
      if (active.current) setBusy("");
    }
  }
  useEffect(() => {
    active.current = true;
    void task("Loading experiment", async () => {
      const value: Catalog = await request(prefix, access);
      if (!active.current) return;
      setCatalog(value.experiments);
      const saved = sessionStorage.getItem("saac-uncertain");
      const recent =
        value.experiments.find((item) => item.id === saved) ||
        value.experiments[0];
      if (recent) apply(await request(`${prefix}/${recent.id}`, access));
    });
    return () => {
      active.current = false;
    };
  }, [prefix]);

  async function create() {
    const value: Experiment = await request(prefix, access, {
      request_id: createRequest.current,
    });
    createRequest.current = crypto.randomUUID();
    apply(value);
    return value;
  }
  async function execute(stage?: string) {
    await task(
      stage
        ? "Executing the next recorded stage"
        : "Running the six-stage experiment",
      async () => {
        const current = experiment || (await create());
        const value: Experiment = await request(
          `${prefix}/${current.id}/${stage ? "step" : "run"}`,
          access,
          stage ? { stage } : {},
        );
        apply(value);
      },
    );
  }
  async function download() {
    if (!experiment) return;
    await task("Preparing evidence export", async () => {
      const evidence = await request(
        `${prefix}/${experiment.id}/export`,
        access,
      );
      const blob = new Blob([JSON.stringify(evidence, null, 2) + "\n"], {
        type: "application/json",
      });
      const href = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = href;
      link.download = `${experiment.id}-evidence.json`;
      link.click();
      URL.revokeObjectURL(href);
      setNotice(
        "Evidence downloaded, including backend events and verification results.",
      );
    });
  }
  const completed = experiment?.completed_stages || [];
  const nextStage = stages.find((stage) => !completed.includes(stage.id));
  const frame = replayStage
    ? experiment?.frames.find((item) => item.stage === replayStage)
    : experiment;
  const selectedStage = stages.find((stage) => stage.id === frame?.stage);
  const checkpoint = experiment?.frames.find(
    (item) => item.stage === "second_batch",
  );
  return (
    <main className="uncertain-main">
      <div className="uncertain-navigation">
        <button onClick={onBack}>
          <ArrowLeft size={16} />
          Back to rail workbench
        </button>
        <a href="/docs/uncertain-execution" target="_blank" rel="noreferrer">
          <FileText size={16} />
          Local reproduction instructions
        </a>
      </div>
      <header className="uncertain-heading">
        <div className="eyebrow">
          Govern The Rail · Uncertain execution experiment
        </div>
        <h1>A missing receipt does not create capacity</h1>
        <p>
          When an order may already have executed, does a timer make its
          capacity safe to promise again?
        </p>
        <div className="uncertain-principles">
          <span>
            <ShieldCheck size={16} />
            Both paths reserve atomically
          </span>
          <span>
            Same admission, signatures, exact effects and single-use redemption
          </span>
        </div>
      </header>
      <div className="uncertain-deadline-note">
        <Clock3 size={20} />
        <p>
          A deadline starts reconciliation; it does not prove that an order
          never executed.
        </p>
      </div>

      <section
        className="uncertain-controls"
        aria-label="Uncertain execution controls"
      >
        <div className="button-row">
          <button
            className="primary"
            disabled={!!busy || !nextStage}
            onClick={() => execute()}
          >
            <Play size={16} />
            {completed.length ? "Run remaining stages" : "Run experiment"}
          </button>
          <button
            disabled={!!busy || !nextStage}
            onClick={() => execute(nextStage?.id)}
          >
            <ArrowRight size={16} />
            {nextStage ? `Step: ${nextStage.label}` : "All stages complete"}
          </button>
          <button
            disabled={!!busy || !experiment}
            onClick={() =>
              task("Creating a fresh isolated experiment", async () => {
                await create();
                setNotice(
                  "Fresh isolated experiment. Earlier runs and their evidence are retained in this session.",
                );
              })
            }
          >
            <RotateCcw size={16} />
            Reset to new experiment
          </button>
          <button disabled={!!busy || !experiment} onClick={download}>
            <Download size={16} />
            Export evidence
          </button>
        </div>
        {catalog.length > 0 && (
          <label>
            Experiment record
            <select
              aria-label="Uncertain experiment record"
              value={experiment?.id || ""}
              disabled={!!busy}
              onChange={(event) =>
                task("Opening the selected experiment", async () =>
                  apply(
                    await request(`${prefix}/${event.target.value}`, access),
                  ),
                )
              }
            >
              {catalog.map((item, index) => (
                <option key={item.id} value={item.id}>
                  {item.id} ·{" "}
                  {item.stage === "ready"
                    ? "Ready"
                    : stages.find((stage) => stage.id === item.stage)?.label ||
                      item.stage}
                  {index === 0 ? " · latest" : ""}
                </option>
              ))}
            </select>
          </label>
        )}
      </section>
      <div className="uncertain-feedback" aria-live="polite">
        {busy ||
          notice ||
          "Synthetic, bounded and isolated to your workspace. No real orders or accounts."}
      </div>
      {error && (
        <p className="error uncertain-error" role="alert">
          {error}
        </p>
      )}

      <ol className="uncertain-timeline" aria-label="Shared event timeline">
        {stages.map((stage, index) => {
          const recorded = completed.includes(stage.id);
          return (
            <li key={stage.id}>
              <button
                disabled={!!busy || (!recorded && stage.id !== nextStage?.id)}
                className={`${recorded ? "recorded" : ""} ${frame?.stage === stage.id ? "selected" : ""}`}
                aria-current={frame?.stage === stage.id ? "step" : undefined}
                aria-label={`${recorded ? "Replay" : "Execute"} ${stage.label}`}
                onClick={() =>
                  recorded ? setReplayStage(stage.id) : execute(stage.id)
                }
              >
                <span>{recorded ? <Check size={15} /> : index + 1}</span>
                <b>{stage.label}</b>
                <small>
                  {recorded
                    ? "Recorded · inspect replay"
                    : stage.id === nextStage?.id
                      ? "Next stage"
                      : "Waiting"}
                </small>
              </button>
            </li>
          );
        })}
      </ol>

      {frame && experiment ? (
        <>
          <section
            className="uncertain-stage"
            aria-label="Current experiment frame"
          >
            <div>
              <span className={`uncertain-mode ${replayStage ? "replay" : ""}`}>
                {replayStage
                  ? "Replay · recorded backend frame"
                  : "Live backend result"}
              </span>
              <span className="small muted">
                Virtual clock {formatClock(frame.now)}
              </span>
            </div>
            <h2>{selectedStage?.label || "Ready to begin"}</h2>
            <p>
              {selectedStage?.explanation ||
                "Two independent books share the same configuration and event schedule. Execute the first admission stage or run the complete experiment."}
            </p>
            {replayStage && (
              <button onClick={() => setReplayStage(null)}>
                Return to latest result <ArrowRight size={14} />
              </button>
            )}
          </section>
          <div className="uncertain-panels">
            <PolicyPanel policy={frame.policies.evidence} />
            <PolicyPanel policy={frame.policies.timeout} unsafe />
          </div>
          {checkpoint && (
            <section
              className="uncertain-result"
              aria-label="Second-batch comparison"
            >
              <div>
                <div className="eyebrow">Recorded second-batch checkpoint</div>
                <h2>One ceiling. Two different obligations.</h2>
                <p>
                  Before evidence recovery or cancellation, actual fills and
                  live orders produce these totals.
                </p>
              </div>
              <div className="uncertain-comparison">
                <span>
                  <b>
                    {checkpoint.policies.evidence.observer.obligation_units}
                  </b>
                  Evidence-based
                </span>
                <span>
                  <b>{checkpoint.policies.timeout.observer.obligation_units}</b>
                  Release on timeout
                </span>
              </div>
              <button onClick={() => setReplayStage("second_batch")}>
                Inspect checkpoint replay
                <ArrowRight size={16} />
              </button>
            </section>
          )}
          <section
            className="uncertain-method"
            aria-label="Measurement and evidence"
          >
            <div>
              <h3>What the measurements mean</h3>
              <p>
                One displayed unit = {experiment.config.unit_notional_cents}{" "}
                integer notional cents. This is synthetic buy-order accounting,
                with no portfolio netting or market-risk claim.
              </p>
              <p>
                The authority book sees only accepted evidence. The experiment
                observer is a fixture oracle derived from durable rail events,
                not an external auditor or a signed institutional receipt.
              </p>
            </div>
            <div>
              <h3>Reconciliation, not automatic release</h3>
              <p>
                At {formatClock(experiment.config.reconcile_by)}, unresolved
                commitments become due for investigation. The deadline bounds
                investigation and escalation, not the time an unavailable system
                must answer.
              </p>
              <p>
                Rail obligation = cumulative executed units + remaining live
                order commitments. Total modeled commitment also includes valid,
                unredeemed promises, counted once.
              </p>
            </div>
            <details>
              <summary>Recorded schedule and configuration</summary>
              <pre>
                {JSON.stringify(
                  { config: experiment.config, timeline: experiment.timeline },
                  null,
                  2,
                )}
              </pre>
            </details>
          </section>
        </>
      ) : (
        <section className="uncertain-empty">
          <ShieldCheck size={34} />
          <h2>Follow the reservation beyond its timer</h2>
          <p>
            Run the matched experiment once, or inspect its six stages. Every
            number will come from backend admission, execution and
            reconciliation events.
          </p>
          <div>
            Atomic admission <ArrowRight size={15} />
            Uncertain execution <ArrowRight size={15} />
            Accepted evidence
          </div>
        </section>
      )}
    </main>
  );
}
