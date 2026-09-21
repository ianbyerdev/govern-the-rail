import hmac
import os
import secrets
import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, Depends, Header, HTTPException
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import Field
from .models import Proposal, PolicyConfig, StrictModel, SAACError, require
from .service import SAACService
from .tape import verify_tape
from .config import setting
from .protocol import WIRE_VERSION


def token_file(path):
    if path.exists(): return path.read_text().strip()
    token = secrets.token_urlsafe(32)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f: f.write(token)
    return token


class ExecutionRequest(StrictModel):
    proposal: Proposal | None = None
    audience: str = "PAYMENTS-1"
    mode: str = "full"
    lose_receipt: bool = False


class ApprovalRequest(StrictModel):
    snapshot_hash: str
    approve: bool


class ResourceRequest(StrictModel):
    resource: str = "beneficiary:acme"
    version: int | None = Field(default=None, ge=1)
    alias_name: str | None = None
    alias_target: str | None = None
    route: str | None = None


class ClockRequest(StrictModel):
    seconds: int = Field(ge=1, le=3600)


class BreakerRequest(StrictModel):
    halted: bool


class CancelRequest(StrictModel):
    cancel: bool = False


class RailRequest(StrictModel):
    proposal: Proposal
    kappa: dict | None = None


class ReceiptRequest(StrictModel):
    receipt: dict


def create_app(directory=None, service=None, *, demo_limits=None):
    directory = Path(directory or setting("DATA_DIR", ".runtime"))
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    svc = service or SAACService(directory)
    operator_token = token_file(directory / "operator.token")
    actor_path = Path(setting("ACTOR_TOKEN_FILE") or str(directory / "actor.token"))
    actor_token = token_file(actor_path)
    if setting("ACTOR_TOKEN_FILE"):
        actor_path.chmod(0o640)  # Dedicated actor-only credential volume; shared container group.
    @asynccontextmanager
    async def lifespan(app):
        manager = getattr(app.state, 'demo_manager', None)
        async def reap():
            while True:
                await asyncio.sleep(30)
                await asyncio.to_thread(manager.cleanup)
        if manager:
            manager.start()
            manager.cleanup()
        cleanup_task = asyncio.create_task(reap()) if manager else None
        try: yield
        finally:
            if cleanup_task:
                cleanup_task.cancel()
                try: await cleanup_task
                except asyncio.CancelledError: pass
                await asyncio.to_thread(manager.close)

    app = FastAPI(title="Govern The Rail Lab · SAAC", version="0.1.0", lifespan=lifespan)
    app.state.service, app.state.operator_token, app.state.actor_token = svc, operator_token, actor_token

    def role(authorization: str = Header(default="")):
        token = authorization.removeprefix("Bearer ")
        if hmac.compare_digest(token, operator_token): return "operator"
        if hmac.compare_digest(token, actor_token): return "actor"
        raise HTTPException(401, "A local operator or actor credential is required.")

    def operator(who=Depends(role)):
        if who != "operator": raise HTTPException(403, "Actor credentials cannot access institutional controls or the authoritative book.")
        return who

    def actor_identity(proposal, who):
        if who == "actor":
            require(proposal.agent_id == "agent.demo" and proposal.principal_id == "institution.demo" and proposal.grant_id == "G-DEMO", "IDENTITY_BINDING", "The actor credential is bound to agent.demo and G-DEMO.")

    def owned_run(run_id, who):
        run = svc.get_run(run_id)
        actor_identity(Proposal.model_validate(run["proposal"]), who)
        return run

    def visible(run, who):
        if who == "operator": return run
        # Deliberate diagnostics, not access to pack storage, keys or control APIs.
        return {k:v for k,v in run.items() if k not in ("events", "risk_before", "risk_after")}

    @app.exception_handler(SAACError)
    async def domain_error(request, exc): return JSONResponse(status_code=409, content={"code": exc.code, "message": exc.message})

    @app.get("/api/health")
    def health(): return {"status": "ok", "contract": "SAAC",
                          "wire_version": WIRE_VERSION, "rail": "local simulation"}

    @app.get('/api/operator/session', dependencies=[Depends(operator)])
    def operator_session(): return {'active': True, 'mode': 'operator'}

    @app.post('/api/coverage/unprotected-sink', dependencies=[Depends(role)])
    def unprotected_counterexample():
        from .coverage import unprotected_write
        return {'protected': False, 'synthetic_only': True, 'effect': unprotected_write(directory/'coverage')}

    @app.post('/api/coverage/protected-attempt', dependencies=[Depends(role)])
    def unsigned_counterexample():
        return svc.socket.execute({}, Proposal())

    @app.post("/api/proposals")
    def propose(proposal: Proposal, preview: bool = False, who=Depends(role),
                request_id: str | None = Header(default=None, alias="Idempotency-Key")):
        actor_identity(proposal, who)
        return visible(svc.authority.propose(proposal, preview=preview, request_id=request_id), who)

    @app.post('/api/rail/execute')
    def rail_execute(request: RailRequest, who=Depends(role)):
        actor_identity(request.proposal, who)
        # Ordinary transport authentication is not execution authority. The socket
        # sees the caller's actual artifact, including an absent/forged κ.
        return svc.redeem(request.proposal, request.kappa)

    @app.post('/api/receipts')
    def receipt(request: ReceiptRequest, who=Depends(role)):
        from .protocol import verify_artifact as verify
        verify(request.receipt, svc.socket_signer.public, 'socket-1', 'saac-receipt')
        with svc.book.connect() as db:
            row = db.execute('SELECT run_id FROM reservations WHERE id=?', (request.receipt['reservation_id'],)).fetchone()
        require(row is not None, 'RECEIPT_BINDING', 'Receipt must reference an existing reservation.')
        owned_run(row['run_id'], who)
        result = svc.reconciler.accept(request.receipt)
        return result if who == 'operator' else {'applied': result['applied']}

    @app.get("/api/runs/{run_id}")
    def get_run(run_id: str, who=Depends(role)): return visible(owned_run(run_id, who), who)

    @app.post("/api/runs/{run_id}/execute")
    def execute(run_id: str, request: ExecutionRequest, who=Depends(role)):
        owned_run(run_id, who)
        if who == "actor":
            require(request.mode == "full" and not request.lose_receipt and request.audience == "PAYMENTS-1", "OPERATOR_ONLY", "Fault injection belongs to the operator lab.")
            if request.proposal: actor_identity(request.proposal, who)
        return visible(svc.execute(run_id, **request.model_dump(exclude={"proposal"}), proposal=request.proposal), who)

    @app.post("/api/operator/runs/{run_id}/approval", dependencies=[Depends(operator)])
    def approve(run_id: str, request: ApprovalRequest): return svc.authority.approve(run_id, **request.model_dump())

    @app.post("/api/operator/runs/{run_id}/reconcile", dependencies=[Depends(operator)])
    def reconcile(run_id: str, request: CancelRequest): return svc.reconcile(run_id, request.cancel)

    @app.get("/api/operator/state", dependencies=[Depends(operator)])
    def state(): return svc.view()

    @app.put("/api/operator/pack", dependencies=[Depends(operator)])
    def pack(config: PolicyConfig): return svc.publish(config)

    @app.post("/api/operator/resource", dependencies=[Depends(operator)])
    def resource(request: ResourceRequest): return svc.resource_update(**request.model_dump())

    @app.post("/api/operator/clock", dependencies=[Depends(operator)])
    def clock(request: ClockRequest): return {"now": svc.advance(request.seconds)}

    @app.post("/api/operator/breaker", dependencies=[Depends(operator)])
    def breaker(request: BreakerRequest):
        svc.breaker(request.halted)
        return svc.view()

    @app.get("/api/operator/tape", dependencies=[Depends(operator)])
    def tape():
        with svc.book.transaction() as db: events = svc.book.events(db)
        keys = {"riskbook-1": svc.issuer.public, "socket-1": svc.socket_signer.public}
        return {"events": events, "keys": keys, "verification": verify_tape(events, keys)}

    @app.post("/api/operator/lab/{scenario}", dependencies=[Depends(operator)])
    def lab(scenario: str):
        from .lab import run_scenario
        return run_scenario(directory / "labs", scenario)

    @app.post("/api/operator/lab-books/{book_id}/runs/{run_id}/{action}", dependencies=[Depends(operator)])
    def lab_action(book_id: str, run_id: str, action: str, request: dict):
        import re
        from pydantic import ValidationError
        require(bool(re.fullmatch(r"lab_[a-f0-9]{16}", book_id)), "LAB_ID", "Invalid lab identifier.")
        folder = directory / "labs" / book_id
        require((folder / "risk.sqlite").exists(), "LAB_ID", "This lab book does not exist.")
        lab_svc = SAACService(folder)
        try:
            if action == "execute":
                parsed = ExecutionRequest.model_validate(request)
                run = lab_svc.execute(run_id, **parsed.model_dump(exclude={"proposal"}), proposal=parsed.proposal)
            elif action == "approval":
                parsed = ApprovalRequest.model_validate(request)
                run = lab_svc.authority.approve(run_id, **parsed.model_dump())
            elif action == "reconcile":
                parsed = CancelRequest.model_validate(request)
                run = lab_svc.reconcile(run_id, parsed.cancel)
            else:
                raise HTTPException(404, "Unknown lab operation")
        except ValidationError as error:
            raise HTTPException(422, str(error)) from error
        return {"run": lab_svc.get_run(run_id), "view": lab_svc.view()}

    from .workbench import register
    experiment_store = register(app, directory, svc, operator, ApprovalRequest, ClockRequest, BreakerRequest)
    from .uncertain_api import register as register_uncertain
    register_uncertain(app, operator, lambda: experiment_store)
    from .coverage_api import register as register_coverage
    register_coverage(app, operator, lambda: experiment_store)

    from .swarm.api import register as register_swarm
    register_swarm(app, directory, operator)

    if setting('PUBLIC_DEMO', '1') == '1':
        from .public_demo import register as register_public
        register_public(app, directory, ApprovalRequest, ClockRequest, BreakerRequest, limits=demo_limits)

    frontend = Path(__file__).resolve().parent.parent / "frontend/dist"
    @app.get('/docs/uncertain-execution')
    def uncertainty_guide():
        return FileResponse(Path(__file__).resolve().parent.parent / 'docs/UNCERTAIN_EXECUTION.md',
                            media_type='text/plain; charset=utf-8')

    if frontend.exists():
        app.mount("/assets", StaticFiles(directory=frontend / "assets"), name="assets")
        @app.get("/")
        def index(): return FileResponse(frontend / "index.html")
    return app


def main():
    import uvicorn
    uvicorn.run(create_app(), host=setting("HOST", "127.0.0.1"), port=int(setting("PORT", "8000")))


if __name__ == "__main__": main()
