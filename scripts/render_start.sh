#!/bin/sh
set -eu

export SAAC_PUBLIC_ORIGIN="${SAAC_PUBLIC_ORIGIN:-${RENDER_EXTERNAL_URL:?Render public URL is required}}"
exec python -m saac.api
