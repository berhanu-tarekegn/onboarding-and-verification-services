#!/usr/bin/env bash
set -euo pipefail

if ! command -v temporal >/dev/null 2>&1; then
  echo "Temporal CLI is not installed." >&2
  echo "Install it first, for example on macOS:" >&2
  echo "  brew install temporal" >&2
  exit 1
fi

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DATA_DIR="${TEMPORAL_LOCAL_DATA_DIR:-${REPO_ROOT}/.local/temporal}"
DB_FILE="${TEMPORAL_LOCAL_DB_FILE:-${DATA_DIR}/temporal.db}"

mkdir -p "${DATA_DIR}"

echo "Starting Temporal local dev server..."
echo "  gRPC: http://127.0.0.1:7233"
echo "  UI:   http://127.0.0.1:8233"
echo "  DB:   ${DB_FILE}"

exec temporal server start-dev --db-filename "${DB_FILE}"
