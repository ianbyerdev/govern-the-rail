# Agentic RISC C8–C10 evidence

This repository starts a new Git history from the current Agentic RISC files.
Earlier signed bundles, source pins, test counts, screenshots and proposed
manuscript patches are not part of this published history and are not current
evidence. This directory contains an evidence notice, not an observed-run bundle.

Generate current observations from the repository root:

```bash
make reproduce-coverage
```

The command executes C8, C9 and C10, verifies their exports and runs the supported
regression checks. Each run writes a new directory under
`.runtime/coverage-evidence/`. The [reproduction guide](../../COVERAGE_SCHEDULES.md)
documents individual commands, bundle contents, source provenance and limits.
Expected checkpoints remain specifications until compared with the actual run.
Unsafe controls must retain ordinary conformance failure and a separate result
showing that the intended breach was detected.

Publishing a new history does not promote a new measurement or alter a retained
signed artifact. Any independently retained older bundle still requires its
matching software release. Use a fresh directory for current-format state; see
the [format boundary](../../NAMING_MIGRATION.md).

C8 demonstrates separate processes and stores on one trusted host; public C8
execution remains disabled. Local checks do not establish hosted-provider
support. Fixture histories are signed but not externally anchored, and replacing
an entire history and its keys remains outside the verifier's guarantees. No
production integration or measured availability benefit is claimed.
