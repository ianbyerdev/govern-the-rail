"""Institution-only, visitor-isolated access to the bounded uncertainty fixture."""
from contextlib import nullcontext
import json
from pathlib import Path
import re

from fastapi import Depends
from pydantic import Field

from .models import StrictModel, require
from .risk_book import uid


class CreateExperiment(StrictModel):
    request_id: str | None = Field(default=None, min_length=1, max_length=128)


class StepExperiment(StrictModel):
    stage: str = Field(min_length=1, max_length=32)


class UncertainStore:
    def __init__(self, experiments):
        self.experiments = experiments
        self.directory = experiments.directory.parent / 'uncertain'
        self.directory.mkdir(exist_ok=True)
        self.demo = experiments.demo

    def get(self, identifier):
        from .uncertain import UncertainExperiment
        require(bool(re.fullmatch(r'uncertain_[a-f0-9]{16}', identifier)),
                'EXPERIMENT_ID', 'Invalid uncertainty experiment identifier.')
        folder = self.directory / identifier
        require((folder / 'control.sqlite').exists(), 'EXPERIMENT_ID',
                'This uncertainty experiment does not exist in your workspace.')
        return UncertainExperiment(folder)

    def create(self, request_id=None):
        from .uncertain import UncertainExperiment
        with self.experiments.lock:
            if request_id:
                for record in self.directory.glob('uncertain_*/request.json'):
                    if json.loads(record.read_text())['request_id'] == request_id:
                        return record.parent.name, self.get(record.parent.name)
            if self.demo and self.experiments.book_count() + 2 > self.demo.manager.limits.books:
                from .public_demo import refuse
                refuse('DEMO_BOOK_LIMIT', 'This comparison needs two experiment books. Export your evidence and end the demo when its book limit is reached.')
            identifier = uid('uncertain')
            experiment = UncertainExperiment(self.directory / identifier)
            (self.directory / identifier / 'request.json').write_text(json.dumps({'request_id': request_id}))
            self.record_source(identifier, 'created')
            return identifier, experiment

    def record_source(self, identifier, action):
        from .uncertain_provenance import provenance
        # Append-only provenance captures the source actually present for each
        # operation, including a deployment changed between step requests.
        with (self.directory / identifier / 'source.jsonl').open('a') as handle:
            handle.write(json.dumps({'action': action, **provenance()}) + '\n')

    def listing(self):
        return [{'id': folder.name, 'stage': view['stage'],
                 'completed_stages': view['completed_stages']}
                for folder in sorted(self.directory.glob('uncertain_*'), key=lambda p: p.stat().st_mtime, reverse=True)
                if (folder / 'control.sqlite').exists()
                for view in [self.get(folder.name).view()]]


def register(app, operator, store_dependency, prefix='/api/operator/uncertain'):
    def select_store(experiments=Depends(store_dependency)):
        return UncertainStore(experiments)

    @app.get(prefix, dependencies=[Depends(operator)])
    def catalog(store=Depends(select_store)):
        return {'experiments': store.listing()}

    @app.post(prefix, dependencies=[Depends(operator)])
    def create(request: CreateExperiment, store=Depends(select_store)):
        identifier, experiment = store.create(request.request_id)
        return {**experiment.view(), 'id': identifier}

    @app.get(prefix+'/{identifier}', dependencies=[Depends(operator)])
    def view(identifier: str, store=Depends(select_store)):
        return {**store.get(identifier).view(), 'id': identifier}

    @app.post(prefix+'/{identifier}/step', dependencies=[Depends(operator)])
    def step(identifier: str, request: StepExperiment, store=Depends(select_store)):
        experiment = store.get(identifier)
        with store.demo.heavy() if store.demo else nullcontext():
            store.record_source(identifier, request.stage)
            return {**experiment.step(request.stage), 'id': identifier}

    @app.post(prefix+'/{identifier}/run', dependencies=[Depends(operator)])
    def run(identifier: str, store=Depends(select_store)):
        experiment = store.get(identifier)
        with store.demo.heavy() if store.demo else nullcontext():
            store.record_source(identifier, 'run')
            return {**experiment.run(), 'id': identifier}

    @app.get(prefix+'/{identifier}/export', dependencies=[Depends(operator)])
    def export(identifier: str, store=Depends(select_store)):
        from .uncertain_verifier import verify_bundle
        with store.demo.heavy(budget=False) if store.demo else nullcontext():
            evidence = store.get(identifier).export()
            evidence['id'] = identifier
            record = store.directory / identifier / 'source.jsonl'
            evidence['source_observations'] = [json.loads(line) for line in record.read_text().splitlines()] if record.exists() else []
            evidence['verification'] = verify_bundle(evidence)
            return evidence
