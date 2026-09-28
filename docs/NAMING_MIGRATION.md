# Agentic RISC names and format boundary

The framework, dashboard, API, package and developer tools use **Agentic RISC**.
The Actor environment proposes actions, the RISC Runtime owns the institutional
authority book, and the RISC Gateway enforces exact-effect authority. An Action
Contract links that lifecycle; it is not another service.

## Current names

| Surface | Name |
|---|---|
| Python package / source directory | `agentic_risc` |
| Python distribution | `agentic-risc-reference` |
| Runtime, actor SDK and domain exception | `RISCService`, `RISCClient`, `RISCError` |
| Installed commands | `agentic-risc-runtime`, `agentic-risc-actor` |
| Module commands | `python -m agentic_risc.api`, `python -m agentic_risc.actor` |
| Adapter tools | `agentic_risc.propose`, `agentic_risc.execute`, `agentic_risc.reconcile` |
| Configuration | `AGENTIC_RISC_*` |
| Frontend package | `agentic-risc-rail-lab` |
| Visitor cookie / mutation header | `agentic_risc_demo` / `X-Agentic-RISC-Demo` |
| Browser storage and event prefix | `agentic-risc-` |
| Concurrency mode / tape filename | `risc` / `agentic-risc-tape.json` |
| Snapshot format | `agentic_risc_version: "0.4.0"` |
| Signed policy and capability types | `agentic-risc-pack`, `agentic-risc-kappa` |
| Signed outcome types | `agentic-risc-receipt`, `agentic-risc-runner-exit`, `agentic-risc-worker-exit` |

There are no compatibility aliases for configuration, adapter tool names, visitor
cookies, mutation headers or concurrency mode. Set documented names explicitly.

## State and verification boundary

Wire version 0.4.0 is a breaking format boundary. Unsupported signed types,
snapshot versions and existing state from another format fail closed. A format
change cannot establish that an earlier obligation disappeared or that a retry
is a new request.

Use a fresh environment and a separate, fresh data directory for new runs:

```bash
make install
make build
.venv/bin/python scripts/demo.py --data-dir .runtime/agentic-risc-0.4 --port 8001
```

Choose another unused directory when repeating this setup. Retain existing books,
keys and outstanding commitments with their matching software release. Resolve
those commitments through that release's ordinary accepted-evidence interface;
do not rename signed fields, reset its counters, or reuse its allocation in a
fresh book. Production migration of outstanding authority is not implemented.

Browser sessions start again under the documented cookie and storage names.
Operators sign in with the administrator credential for the selected fresh
workspace. A visitor session does not inherit administrator access.

## Evidence and new repository history

This repository starts a new Git history from the current Agentic RISC files.
Earlier commits and signed evidence archives are not part of the published
history. Their previous measurements and CI records do not validate this source.
The [C8–C10 evidence notice](research/coverage-evidence/README.md) and
[uncertain-execution evidence notice](research/uncertain-evidence/README.md)
explain how to produce new observations.

An independently retained older artifact still requires its recorded software
release and environment. Do not translate or re-sign it to imply a new
measurement. The current verifier checks current-format evidence only.

A new Git history and format boundary do not establish a new research result or
hosted deployment. Run `make test`, `make build` and `make test-ui` for this
checkout; fresh observed evidence requires a reproduction run with its own
executed source commit, clean/dirty status and source-tree digest. The history
change does not remove outstanding obligations from any retained Runtime book.
