#!/bin/sh
set -eu

# Explicit settings, including empty values, take precedence over image defaults.
export AGENTIC_RISC_DATA_DIR="${AGENTIC_RISC_DATA_DIR-/state}"
export AGENTIC_RISC_HOST="${AGENTIC_RISC_HOST-0.0.0.0}"
export AGENTIC_RISC_ACTOR_TOKEN_FILE="${AGENTIC_RISC_ACTOR_TOKEN_FILE-/actor-credential/actor.token}"
exec "$@"
