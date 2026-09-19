import unicodedata
from .crypto import digest
from .models import Effect, Proposal, require

SCHEMA_HASH = digest({"operation": "payment|dispatch", "money": "positive integer USD cents", "schema_version": 1})


def alias(value): return unicodedata.normalize("NFKC", value).strip().casefold()


def resolve(proposal: Proposal, state: dict, grant: dict, db=None) -> Effect:
    if proposal.operation not in ('payment', 'dispatch'):
        from .profiles import resolve as domain_resolve, PROFILE_OPERATIONS
        require(any(proposal.operation in ops for ops in PROFILE_OPERATIONS.values()), 'UNMEDIATED_OPERATION', 'No protected adapter implements this operation.')
        return domain_resolve(proposal, state, grant, db)
    require(state.get('profile', 'payments') == 'payments', 'PROFILE_BINDING', 'Payment authority belongs to a payment book.')
    require(proposal.operation in ("payment", "dispatch"), "UNMEDIATED_OPERATION", "This operation has no protected effect adapter.")
    common = {"pack_hash": state["pack_hash"], "grant_version": grant["version"], "schema_hash": SCHEMA_HASH}
    if proposal.operation == "dispatch":
        require(proposal.delegation is not None, "MISSING_SCOPE", "Dispatch must name the entire child grant scope.")
        return Effect(p=proposal.principal_id, s=proposal.socket_id, o="dispatch", r="grant:"+grant["id"],
                      x=proposal.delegation.model_dump(), c=common)
    rid = state["aliases"].get(alias(proposal.beneficiary))
    require(rid in state["resources"], "UNKNOWN_RESOURCE", "The risk-book resolver does not recognize this beneficiary.")
    resource = state["resources"][rid]
    require(proposal.route in state["route_versions"], "UNKNOWN_ROUTE", "The resolver does not recognize this route.")
    return Effect(p=proposal.principal_id, s=proposal.socket_id, o="payment", r=rid,
                  x={"amount_cents": proposal.amount_cents, "currency": proposal.currency,
                     "route": proposal.route, "purpose": proposal.purpose},
                  c={**common, "resource_version": resource["version"], "route_version": state["route_versions"][proposal.route],
                     "sanctions_version": state["sanctions_version"], "sanctioned": resource["sanctioned"]})
