FROM node:22-alpine AS frontend
WORKDIR /build/frontend
COPY frontend/package.json frontend/pnpm-lock.yaml frontend/pnpm-workspace.yaml ./
RUN corepack enable && pnpm install --frozen-lockfile
COPY frontend/ ./
COPY integration/static/ /build/integration/static/
RUN pnpm build

FROM python:3.12-slim
WORKDIR /app
COPY backend/requirements.lock.txt backend/requirements.lock.txt
RUN pip install --no-cache-dir -r backend/requirements.lock.txt
COPY backend/ backend/
COPY vendor/ vendor/
COPY pyproject.toml ./
COPY app/ app/
RUN pip install --no-deps -e .
COPY config/ config/
COPY data/corridor/ data/corridor/
COPY data/akmola/ data/akmola/
COPY data/region/ data/region/
COPY data/traffic/ data/traffic/
COPY data/railsim/ data/railsim/
COPY scenarios/ scenarios/
COPY data/kazakhstan_railways.geojson data/kazakhstan_railways.geojson
COPY map.html map_data.js network.json build_network.py ./
COPY --from=frontend /build/frontend/dist frontend/dist
EXPOSE 8000
CMD ["uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8000"]
