"""Pure policy evaluation. Its returned verdict is never socket authority."""
from copy import deepcopy
from .models import SAACError, require


def delegation_check(child, parent, now):
    require(child["principal_id"] == parent["principal_id"], "DELEGATION_PRINCIPAL", "A new principal needs an independent institutional grant.")
    require(now < child["exp"] <= parent["exp"], "DELEGATION_EXPIRY", "Child lifetime must fit inside the parent lifetime.")
    require(child["max_amount_cents"] <= parent["max_amount_cents"], "DELEGATION_EXPANSION", "A child cannot increase the amount ceiling.")
    for field in ("resources", "routes", "currencies", "purposes", "operations"):
        require(set(child[field]) <= set(parent[field]), "DELEGATION_EXPANSION", f"Child {field} must be a subset of parent authority.")
    require(parent["depth"] < 4, "DELEGATION_DEPTH", "This pack limits delegation to four generations.")


def evaluate(effect: dict, pack: dict, grant: dict, risk: dict, now: int, approved: bool, breaker: bool) -> dict:
    if effect['o'] not in ('payment', 'dispatch'):
        from .profiles import evaluate as domain_evaluate
        return domain_evaluate(effect, pack, grant, risk, now, approved, breaker)
    e = deepcopy(effect)
    verdict = "allow"
    try:
        require(not breaker, "BREAKER_HALT", "The institutional breaker has halted new effects.")
        require(grant["active"] and now < grant["exp"], "GRANT_INACTIVE", "The grant is expired, revoked or degraded.")
        require(e["p"] == grant["principal_id"] and e["s"] == grant["socket_id"], "GRANT_BINDING", "Principal and audience must match the institutional grant.")
        require(e["o"] in grant["operations"], "GRANT_SCOPE", "The grant does not authorize this effect class.")
        if e["o"] == "dispatch":
            delegation_check(e["x"], grant, now)
            return {"verdict": "allow", "code": "SCOPED_DISPATCH", "message": "Child authority is a subset of the parent.", "effect": e, "bound": 0}
        require(e["r"] in grant["resources"] and e["r"] in pack["beneficiaries"], "BENEFICIARY_DENIED", "The canonical beneficiary is outside the allowlist.")
        require(not e["c"]["sanctioned"], "SANCTIONS_DENIED", "This beneficiary is blocked by the reference state.")
        for field, scopes in (("route", "routes"), ("currency", "currencies"), ("purpose", "purposes")):
            require(e["x"][field] in grant[scopes] and e["x"][field] in pack[scopes], "SCOPE_DENIED", f"The {field} is outside pack or grant scope.")
        amount = e["x"]["amount_cents"]
        require(type(amount) is int and amount > 0, "INVALID_AMOUNT", "The amount must be positive integer cents.")
        ceiling = min(pack["per_action_cents"], grant["max_amount_cents"])
        if amount > ceiling and pack["allow_attenuation"]:
            e["x"]["amount_cents"] = amount = ceiling
            verdict = "modify"
        require(amount <= ceiling, "ACTION_LIMIT", "This amount exceeds the per-action or grant ceiling.")
        require(risk["used_cents"] + risk["reserved_cents"] + amount <= pack["session_limit_cents"], "SESSION_LIMIT", "Settled usage plus reservations leaves insufficient session capacity.")
        if amount > pack["approval_above_cents"] and not approved:
            return {"verdict": "escalate", "code": "HUMAN_APPROVAL", "message": "An operator must approve this exact resolved snapshot.", "effect": e, "bound": amount}
        return {"verdict": verdict, "code": "ATTENUATED" if verdict == "modify" else "POLICY_ALLOW", "message": "Amount reduced within the original effect scope." if verdict == "modify" else "The effect cleared the current pack; a verdict alone is not authority.", "effect": e, "bound": amount}
    except SAACError as error:
        return {"verdict": "degrade" if breaker else "deny", "code": error.code, "message": error.message, "effect": e, "bound": 0}


def assert_attenuation(original: dict, modified: dict):
    old, new = deepcopy(original), deepcopy(modified)
    a, b = old["x"].pop("amount_cents", 0), new["x"].pop("amount_cents", 0)
    require(old == new and 0 < b <= a, "MODIFY_UP", "Modify can only lower amount without changing any other authority dimension.")
