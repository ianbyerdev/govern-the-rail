"""Separate proposer process. No risk-book, signer or operator-control imports."""
import argparse
from typing import Protocol
import httpx
from .adapters import MODES, propose_via
from .sdk import SAACClient, HTTPTransport, Identity
from .config import setting


class Proposer(Protocol):
    def propose(self, observed_risk: dict) -> dict: ...


class MockProposer:
    def propose(self, observed_risk):
        return {"amount_cents": 7500, "beneficiary": "acme", "actor_risk_observed": observed_risk}


def main():
    parser = argparse.ArgumentParser(description="SAAC actor-only client")
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--amount", type=int, default=7500)
    parser.add_argument("--integration", choices=MODES, default="harness")
    parser.add_argument("--request-id", help="Reuse for retries of one authorization request")
    args = parser.parse_args()
    token = setting("ACTOR_TOKEN")
    if not token: parser.error("Set SAAC_ACTOR_TOKEN to the actor-only credential from .runtime/actor.token")
    proposal = MockProposer().propose({"used_cents": 0, "reserved_cents": 0, "limit_cents": 99999999})
    proposal["amount_cents"] = args.amount
    with httpx.Client(base_url=args.url, headers={"Authorization": "Bearer "+token}) as client:
        sdk = SAACClient(HTTPTransport(client), Identity())
        run = propose_via(args.integration, sdk, proposal, request_id=args.request_id)
        if "kappa" in run:
            execution = sdk.execute(run.get('execution_proposal', run['proposal']), run['kappa'])
            run['execution_result'] = execution
            if execution['accepted']:
                sdk.reconcile(execution['receipt'])
                response = client.get(f"/api/runs/{run['id']}")
                response.raise_for_status()
                run = response.json()
    print(__import__("json").dumps(run, indent=2))


if __name__ == "__main__": main()
