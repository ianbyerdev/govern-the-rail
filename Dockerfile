FROM node:22-bookworm-slim AS frontend
WORKDIR /app/frontend
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim AS runtime
WORKDIR /app
COPY pyproject.toml requirements.lock ./
COPY saac/ ./saac/
RUN pip install --no-cache-dir -r requirements.lock . \
    && groupadd -g 20000 saac \
    && useradd -u 10001 -g 20000 institution \
    && useradd -u 10002 -g 20000 actor \
    && mkdir -p /state /actor-credential \
    && chown 10001:20000 /state /actor-credential
COPY --from=frontend /app/frontend/dist ./frontend/dist
COPY docs/UNCERTAIN_EXECUTION.md ./docs/UNCERTAIN_EXECUTION.md
COPY scripts/render_start.sh ./render_start.sh
USER 10001:20000
ENV SAAC_DATA_DIR=/state SAAC_HOST=0.0.0.0 SAAC_ACTOR_TOKEN_FILE=/actor-credential/actor.token
EXPOSE 8000
CMD ["python", "-m", "saac.api"]
