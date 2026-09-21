# Govern The Rail Lab documentation

**The actor proposes. The RISC Runtime authorizes. The RISC Gateway enforces.**

Start with the [project README](../README.md) to run the demo and understand the
architecture. Agentic RISC names the institutional framework; an Action Contract
links the authorized effect lifecycle. The lab uses synthetic effects to make
its authority boundaries and limitations inspectable. Historical `saac` package
and wire identifiers remain compatible.

## Current guides

| Read this | To understand |
|---|---|
| [Architecture](ARCHITECTURE.md) | Agent, authority and protected rail boundaries; capabilities and reconciliation |
| [Demo walkthrough](DEMO_GUIDE.md) | Payments, trading, referrals and contained execution |
| [Swarm walkthrough](SWARM_DEMO_GUIDE.md) | Shared capacity, delegation and concurrent agents |
| [Uncertain execution experiment](UNCERTAIN_EXECUTION.md) | Missing receipts, false timeout capacity, recovery and reproducible evidence |
| [Coverage schedules C8–C10](COVERAGE_SCHEDULES.md) | Independent rail persistence, revocation with live occupancy, and a true shared allocation |
| [Public demo hosting](PUBLIC_DEMO.md) | Visitor isolation, expiry, limits and hosting configuration |
| [Threat model](THREAT_MODEL.md) | Enforced properties, assumptions and remaining demo shortcuts |
| [Executable specification](SPEC.md) | The contract implemented by the common core |

The package and source directory are `saac`; installation uses `saac-reference`.
The [project README](../README.md#package-and-protocol) describes the SDK,
commands and wire format.

## Validation

[Validation and benchmarks](VALIDATION.md) explains the invariant tests, browser
checks, and measured synthetic workloads. The executable negative cases live in
[the test fixtures](../tests/fixtures/swarm-negative-vectors.json).

These fixture documents share the repository's [MIT license](../LICENSE).
The white paper is a separate work. Runtime state, credentials and private keys
are not documentation and do not belong in this directory or in published examples.
