# Agentic RISC uncertain-execution evidence

This repository starts a new Git history from the current Agentic RISC files.
Earlier signed bundles, source pins, test counts, screenshots and CI records are
not part of this published history and are not current evidence. This directory
contains an evidence notice, not an observed-run bundle.

Generate current observations from the repository root:

```bash
make reproduce-uncertain
```

The command executes the uncertain-execution comparison, verifies its evidence
and runs the supported regression checks. It writes a fresh bundle with actual
observations, source provenance, command results and captured UI evidence. See
the [experiment and reproduction guide](../../UNCERTAIN_EXECUTION.md) for the
schedule, output paths and verifier limits. An unsafe-control breach is an
expected experimental finding, never ordinary conformance success.

Publishing a new history does not promote a new measurement or alter a retained
signed artifact. Any independently retained older bundle still requires its
matching software release. The current verifier does not translate or re-sign
such artifacts. Use a fresh directory for current-format state; see the
[format boundary](../../NAMING_MIGRATION.md).

These are synthetic local effects, not an external exchange or payment service.
Local test results do not establish a hosted deployment, and replacing a complete
unanchored fixture history remains outside the verifier's guarantees.
