"""Intentionally unprotected, synthetic-only sink. Never an SAAC rail adapter."""
import sqlite3
from pathlib import Path
from .risk_book import uid


def unprotected_write(directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    entry = {'id': uid('unsafe'), 'payload': 'Synthetic audit output',
             'warning': 'Intentionally unprotected dummy sink: an ordinary actor credential needs no capability.'}
    with sqlite3.connect(directory/'dummy-sink.sqlite') as db:
        db.execute('CREATE TABLE IF NOT EXISTS dummy_output (id TEXT PRIMARY KEY, payload TEXT NOT NULL)')
        db.execute('INSERT INTO dummy_output VALUES (?,?)', (entry['id'], entry['payload']))
    return entry
