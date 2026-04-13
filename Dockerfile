FROM python:3.12-slim AS base

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# ── Test stage: build fails if tests don't pass ─────────────────────
FROM base AS test

COPY requirements-test.txt .
RUN pip install --no-cache-dir -r requirements-test.txt

RUN pytest tests/ -v --tb=short

# ── Production stage ─────────────────────────────────────────────────
FROM base AS production

RUN mkdir -p /app/data

VOLUME ["/app/data", "/app/config"]

ENTRYPOINT ["python", "main.py"]
CMD ["daemon"]
