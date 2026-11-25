#!/usr/bin/env bash
# Stop the platform. Data stays in the Podman volumes for the next start.
#   scripts/stop.sh             stop and remove the containers
#   scripts/stop.sh --machine   also stop the Podman machine (frees its memory on Windows/macOS)
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

MACHINE=false
for arg in "$@"; do
  case "$arg" in
    --machine) MACHINE=true ;;
    -h|--help) sed -n '2,4p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) die "unknown option: $arg" ;;
  esac
done

if podman info >/dev/null 2>&1; then
  say "Stopping containers"
  compose down >/dev/null
else
  say "Podman is not running: nothing to stop"
fi

if $MACHINE && podman machine inspect >/dev/null 2>&1; then
  say "Stopping the Podman machine"
  podman machine stop >/dev/null
fi
say "Stopped"
