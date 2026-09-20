# Observed comparison

Executable source: `d71558e5df1623445ee54c83538e5f44cffd3d2d`.
Clean source checkout.

| Phase | Policy | U | Q | Available | Rail obligation |
|---|---|---:|---:|---:|---:|
| admission | evidence | 0 | 10 | 0 | 0 |
| admission | timeout | 0 | 10 | 0 | 0 |
| hidden_fills | evidence | 0 | 10 | 0 | 10 |
| hidden_fills | timeout | 0 | 10 | 0 | 10 |
| deadline | evidence | 0 | 10 | 0 | 10 |
| deadline | timeout | 0 | 0 | 10 | 10 |
| second_batch | evidence | 0 | 10 | 0 | 10 |
| second_batch | timeout | 0 | 10 | 0 | 20 |
| recovered_fills | evidence | 4 | 6 | 0 | 10 |
| recovered_fills | timeout | 0 | 10 | 0 | 20 |
| cancellations | evidence | 4 | 0 | 6 | 4 |
| cancellations | timeout | 0 | 10 | 0 | 14 |

The timeout policy is a deliberately invalid experimental control.
Evidence verifier success means its breach was detected, not that it passed normal conformance.
See evidence.json for signed artifacts, raw rail observations, source/runtime metadata and test identifiers.
Private keys, credentials and temporary databases are excluded.
