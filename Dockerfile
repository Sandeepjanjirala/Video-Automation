FROM python:3.12-slim-bookworm

# Install system dependencies including FFmpeg and build tools
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
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
COPY run.py alembic.ini ./

# Shared development media storage mount point
VOLUME ["/app/data"]

EXPOSE 8787

CMD ["python", "run.py"]
