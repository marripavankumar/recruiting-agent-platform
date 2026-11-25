#!/usr/bin/env bash
# Test data in the running stack's database (fictional companies, jobs, candidates; nothing sent).
#   scripts/testdata.sh load           add the test pipeline (needs an empty database)
#   scripts/testdata.sh clean          remove ALL business data; keeps staff users and settings
#   scripts/testdata.sh clean --yes    same, without asking
#   scripts/testdata.sh status         row counts
# Runs inside the API container, with the API's settings. Start the stack first (start.sh).
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

action="${1:-}"
case "$action" in
  load|status) ;;
  clean)
    if [ "${2:-}" != "--yes" ]; then
      read -r -p "Delete ALL companies, jobs, candidates, approvals and messages? Type yes: " answer
      [ "$answer" = "yes" ] || { echo "Nothing deleted."; exit 1; }
    fi ;;
  *) sed -n '2,7p' "$0" | sed 's/^# \{0,1\}//'; exit 2 ;;
esac

ensure_podman
args=("$action")
[ "$action" = "clean" ] && args+=(--yes)
compose exec -T api python -m recruiting.testdata "${args[@]}"
