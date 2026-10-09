#!/usr/bin/env bash
set -e

# Resolve repository directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_DIR}"

export PYTHONPATH="${REPO_DIR}:${PYTHONPATH}"
export STUDIO_DISTRIBUTED=1
export STUDIO_DATABASE_URL="postgresql+psycopg2://postgres:postgres@127.0.0.1:5433/studio"
export CELERY_BROKER_URL="redis://127.0.0.1:6379/0"
export CELERY_RESULT_BACKEND="redis://127.0.0.1:6379/1"
export STUDIO_CACHE_REDIS_URL="redis://127.0.0.1:6379/2"

PYTHON_EXEC="/opt/celery_venv/bin/python"
if [ ! -f "${PYTHON_EXEC}" ]; then
    PYTHON_EXEC="python3"
fi

echo "[Telugu Reel Studio] Starting Phase 3 Distributed Stack in WSL Linux..."

# Ensure PostgreSQL and Redis are running
service postgresql start || true
redis-server --daemonize yes --appendonly yes --maxmemory 512mb --maxmemory-policy noeviction --save 60 1 || true

# Check connectivity
echo "[Telugu Reel Studio] Verifying PostgreSQL (5433) and Redis (6379)..."
"${PYTHON_EXEC}" -c "
import socket
for name, port in [('PostgreSQL', 5433), ('Redis', 6379)]:
    s = socket.socket()
    res = s.connect_ex(('127.0.0.1', port))
    s.close()
    if res != 0:
        raise SystemExit(f'Failed to connect to {name} on port {port}')
print('[OK] Infrastructure services reachable.')
"

echo "[Telugu Reel Studio] Launching Workers and Dispatcher..."
echo "  - Network Worker Pool (2 slots): Queue 'network'"
echo "  - Render Worker Pool (1 slot): Queue 'render'"
echo "  - Outbox Dispatcher: PostgreSQL -> Celery/Redis"

# Trap to kill background jobs on script exit
trap 'kill $(jobs -p) 2>/dev/null || true' EXIT

"${PYTHON_EXEC}" -m celery -A studio.celery_app worker -Q network -c 2 --loglevel=info -n worker_network@%h &
NETWORK_PID=$!

"${PYTHON_EXEC}" -m celery -A studio.celery_app worker -Q render -c 1 --loglevel=info -n worker_render@%h &
RENDER_PID=$!

"${PYTHON_EXEC}" -m studio.dispatcher &
DISPATCHER_PID=$!

echo "[Telugu Reel Studio] All Phase 3 background services running (PIDs: $NETWORK_PID, $RENDER_PID, $DISPATCHER_PID)."
wait
