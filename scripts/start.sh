#!/usr/bin/env bash
# Start the whole platform on Podman: PostgreSQL, Qdrant, n8n, API, scheduler and web app.
#   scripts/start.sh            start (builds images only if they are missing)
#   scripts/start.sh --build    rebuild the images first, e.g. after code changes
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

BUILD=()
for arg in "$@"; do
  case "$arg" in
    --build) BUILD=(--force-recreate) ;;
    -h|--help) sed -n '2,4p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) die "unknown option: $arg" ;;
  esac
done

ensure_podman

if [ ! -f "$ROOT/.env" ]; then
  say "No .env yet: creating it from .env.example (rule-based agents, no API key needed)"
  sed 's/^RAP_LLM_PROVIDER=claude/RAP_LLM_PROVIDER=none/' "$ROOT/.env.example" > "$ROOT/.env"
fi

say "Starting containers"
# podman-compose keeps running containers when only the image changed, so after a build the
# containers are recreated from the new images.
if [ ${#BUILD[@]} -gt 0 ]; then
  say "Building images"
  compose build >/dev/null || die "image build failed; run the build without >/dev/null to see why"
fi
compose up -d "${BUILD[@]}" >/dev/null

wait_for() { # name url
  local i
  for i in $(seq 1 60); do
    curl -fsS -o /dev/null --max-time 3 "$2" 2>/dev/null && { say "$1 is up: $2"; return; }
    sleep 2
  done
  die "$1 did not answer at $2 within 2 minutes; see: podman logs recruiting-agent-platform_${1,,}_1"
}
wait_for API http://localhost:8000/health
wait_for Web http://localhost:3000/signin

# A fresh database has no users, and the web app's demo sign-in needs these five.
# Only on an empty user table, so real users are never mixed with demo ones.
users=$(compose exec -T postgres psql -U rap -d rap -tAc "SELECT count(*) FROM app_user" 2>/dev/null | tr -d '[:space:]')
if [ "$users" = "0" ]; then
  say "Empty database: adding the five demo staff users"
  compose exec -T postgres psql -U rap -d rap -qc "INSERT INTO app_user (email, display_name, role) VALUES
    ('administrator@cbs.example', 'Demo Administrator', 'administrator'),
    ('bd.manager@cbs.example', 'Demo BD Manager', 'bd_manager'),
    ('recruiter@cbs.example', 'Demo Recruiter', 'recruiter'),
    ('account.manager@cbs.example', 'Demo Account Manager', 'account_manager'),
    ('sales.manager@cbs.example', 'Demo Sales Manager', 'sales_manager')
    ON CONFLICT (email) DO NOTHING" >/dev/null
fi

cat <<EOF

  Web app   http://localhost:3000
  API docs  http://localhost:8000/docs
  n8n       http://localhost:5678

  Stop with: scripts/stop.sh
EOF
