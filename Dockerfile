# Debian 13 provides FFmpeg 7.1 and libass 0.17.3 (caption box styles, rotation metadata).
FROM python:3.12-slim-trixie AS app

# Install system dependencies including FFmpeg and build tools
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    fonts-dejavu-core \
    fonts-noto-core \
    libpq-dev \
    gcc \
    curl \
    procps \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy dependency requirements and install
COPY requirements.txt requirements-tested.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source code
COPY studio/ studio/
COPY config/ config/
COPY core/ core/
COPY templates/ templates/
COPY run.py manage.py ./

# Shared development media storage mount point
VOLUME ["/app/data"]

EXPOSE 8788

CMD ["python", "run.py"]

# Test image (docker build --target test): Node.js for the DOM tests plus the dev dependencies.
FROM node:24-trixie-slim AS node

FROM app AS test
COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm
COPY requirements-dev.txt ./
RUN pip install --no-cache-dir -r requirements-dev.txt
COPY frontend/package.json frontend/package-lock.json frontend/
RUN cd frontend && npm ci --no-audit --no-fund
COPY tests/ tests/
COPY scripts/ scripts/
CMD ["python", "scripts/run_audit_tests.py"]

# The default target remains the application image used by docker compose.
FROM app
