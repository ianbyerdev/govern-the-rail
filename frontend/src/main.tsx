import { useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { Layers3, ArrowRight, ShieldCheck, Users, Clock3 } from "lucide-react";
import { SwarmLab } from "./SwarmLab";
import { Workbench } from "./Workbench";
import { UncertainLab } from "./UncertainLab";
import { CoverageLab } from "./CoverageLab";
import { request, type Access } from "./access";
import "./style.css";

const incoming = new URLSearchParams(location.hash.slice(1)).get("token");
if (incoming) {
  sessionStorage.setItem("saac-operator", incoming);
  history.replaceState(null, "", location.pathname);
}

function App() {
  const [access, setAccess] = useState<Access | null>(null);
  const [workspace, setWorkspace] = useState(
    sessionStorage.getItem("saac-workspace") || "rails",
  );
  const [busy, setBusy] = useState("Connecting"),
    [error, setError] = useState("");
  const [notice, setNotice] = useState(""),
    [draft, setDraft] = useState("");
  const [enabled, setEnabled] = useState(true),
    [minutes, setMinutes] = useState(60);
  const [duration, setDuration] = useState(60);

  function showWorkspace(value: string) {
    sessionStorage.setItem("saac-workspace", value);
    setWorkspace(value);
  }

  function enter(value: Access) {
    const identity = value.mode === "operator" ? "operator" : value.session_id!;
    const previousOwner = sessionStorage.getItem("saac-workspace-owner");
    if (
      previousOwner !== identity &&
      !(previousOwner === null && value.mode === "operator")
    ) {
      for (const key of [
        "saac-book",
        "saac-campaign",
        "saac-workspace",
        "saac-uncertain",
      ])
        sessionStorage.removeItem(key);
      setWorkspace("rails");
    }
    sessionStorage.setItem("saac-workspace-owner", identity);
    setAccess(value);
    setNotice("");
  }

  useEffect(() => {
    let active = true;
    (async () => {
      const token = sessionStorage.getItem("saac-operator");
      if (token) {
        try {
          await request("/api/operator/session", { mode: "operator", token });
          if (active) {
            enter({ mode: "operator", token });
            setBusy("");
          }
          return;
        } catch {
          sessionStorage.removeItem("saac-operator");
        }
      }
      try {
        const response = await fetch("/api/demo/session", {
          credentials: "same-origin",
        });
        if (response.status === 404) {
          if (active) setEnabled(false);
          return;
        }
        if (!response.ok)
          throw Error("The demo is temporarily unavailable. Please try again.");
        const session = await response.json();
        if (active) {
          setDuration(Math.ceil(session.limits.lifetime_seconds / 60));
          if (session.active) enter(session);
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
  }, []);

  useEffect(() => {
    function ended(event: Event) {
      const source = (event as CustomEvent).detail;
      if (
        !access ||
        (source &&
          (source.mode !== access.mode ||
            (source.mode === "visitor" &&
              source.session_id !== access.session_id)))
      )
        return;
      setAccess(null);
      sessionStorage.removeItem("saac-operator");
      setNotice("Your session has ended. Start a new demo to continue.");
    }
    window.addEventListener("saac-session-ended", ended);
    return () => window.removeEventListener("saac-session-ended", ended);
  }, [access]);

  useEffect(() => {
    if (access?.mode !== "visitor") return;
    function tick() {
      const remaining = Math.max(
        0,
        Math.ceil((access!.expires_at! * 1000 - Date.now()) / 60000),
      );
      setMinutes(remaining);
      if (!remaining) window.dispatchEvent(new Event("saac-session-ended"));
    }
    tick();
    const timer = setInterval(tick, 1000);
    return () => clearInterval(timer);
  }, [access]);

  async function start() {
    setBusy("Preparing your workspace");
    setError("");
    try {
      enter(await request("/api/demo/session", undefined, {}));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy("");
    }
  }

  async function end() {
    setBusy("Ending session");
    setError("");
    try {
      if (access?.mode === "visitor")
        await request("/api/demo/session", access, undefined, "DELETE");
      sessionStorage.removeItem("saac-operator");
      setAccess(null);
      setNotice(
        access?.mode === "visitor"
          ? "Demo ended. Its temporary workspace will be removed."
          : "Administrator signed out.",
      );
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy("");
    }
  }

  if (access)
    return (
      <>
        <div className="session-banner" aria-label="Session controls">
          <span>
            {access.mode === "visitor" ? (
              <>Private demo · {minutes} min remaining · synthetic data only</>
            ) : (
              "Administrator workspace"
            )}
          </span>
          <button disabled={!!busy} onClick={end}>
            {access.mode === "visitor" ? "End demo" : "Sign out"}
          </button>
          {error && <span role="alert">{error}</span>}
        </div>
        {workspace === "coverage" ? (
          <CoverageLab access={access} onBack={() => showWorkspace("rails")} />
        ) : workspace === "uncertain" ? (
          <UncertainLab access={access} onBack={() => showWorkspace("rails")} />
        ) : workspace === "swarm" ? (
          <SwarmLab access={access} onBack={() => showWorkspace("rails")} />
        ) : (
          <Workbench
            access={access}
            onOpenSwarm={() => showWorkspace("swarm")}
            onOpenUncertain={() => showWorkspace("uncertain")}
            onOpenCoverage={() => showWorkspace("coverage")}
            onLogout={end}
          />
        )}
      </>
    );

  return (
    <main className="login public-entry">
      <div>
        <div className="entry-mark">
          <Layers3 size={32} />
        </div>
        <div className="eyebrow">Agentic RISC · Independent institutional controls</div>
        <h1>Govern The Rail Lab</h1>
        <p className="entry-intro">
          The actor proposes. The RISC Runtime authorizes. The RISC Gateway enforces.
        </p>
        <p>
          Try a payment, challenge the boundary, or launch 100 agents against
          one shared limit. Follow every capability and receipt.
        </p>
        {notice && (
          <p className="message" role="status">
            {notice}
          </p>
        )}
        {error && (
          <p className="error" role="alert">
            {error}
          </p>
        )}
        {enabled && (
          <>
            <button
              className="primary start-demo"
              disabled={!!busy}
              onClick={start}
            >
              {busy || "Start demo"}
              <ArrowRight size={18} />
            </button>
            <p className="entry-reassurance">
              No account or credential needed.
            </p>
            <div className="entry-features">
              <span>
                <ShieldCheck size={19} />
                <strong>Your own workspace</strong>Experiment without changing
                anyone else’s demo.
              </span>
              <span>
                <Users size={19} />
                <strong>One shared authority</strong>Every agent in your
                experiment uses the same book.
              </span>
              <span>
                <Clock3 size={19} />
                <strong>{duration}-minute session</strong>Export evidence you
                want to keep. Temporary data is cleared on expiry or a host
                reset.
              </span>
            </div>
          </>
        )}
        <details className="admin-entry">
          <summary>Administrator access</summary>
          <p className="small muted">
            For the host’s saved experiments and real contained-process tests.
          </p>
          <form
            onSubmit={async (event) => {
              event.preventDefault();
              setBusy("Signing in");
              setError("");
              try {
                const candidate: Access = { mode: "operator", token: draft };
                await request("/api/operator/session", candidate);
                sessionStorage.setItem("saac-operator", draft);
                enter(candidate);
                setDraft("");
              } catch (e) {
                setError((e as Error).message);
              } finally {
                setBusy("");
              }
            }}
          >
            <label>
              Operator credential
              <input
                type="password"
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                autoComplete="off"
              />
            </label>
            <button disabled={!!busy || !draft}>
              Open administrator workspace
              <ArrowRight size={16} />
            </button>
          </form>
        </details>
      </div>
    </main>
  );
}
createRoot(document.getElementById("root")!).render(<App />);
