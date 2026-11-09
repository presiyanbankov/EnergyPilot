FROM node:24-bookworm-slim AS dashboard
WORKDIR /build
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY backend ./backend
COPY data ./data
COPY --from=dashboard /build/dist/energypilot/browser ./frontend/dist/energypilot/browser
RUN useradd --create-home pilot && mkdir -p /var/lib/energypilot && chown -R pilot:pilot /app /var/lib/energypilot
USER pilot
ENV ENERGYPILOT_DB=/var/lib/energypilot/energy.sqlite3
EXPOSE 8000
CMD ["sh", "-c", "uvicorn backend.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
