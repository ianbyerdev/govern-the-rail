#!/bin/sh
set -eu

export AGENTIC_RISC_PUBLIC_ORIGIN="${AGENTIC_RISC_PUBLIC_ORIGIN:-${RENDER_EXTERNAL_URL:?Render public URL is required}}"
# Render's custom command can bypass the image ENTRYPOINT. Apply the same
# writable-state and network defaults on both container launch paths.
exec sh /app/container_start.sh python -m agentic_risc.api
