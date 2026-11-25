# Shared by start.sh and stop.sh. Not meant to be run on its own.

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE_FILE="$ROOT/infra/docker-compose.yml"

say() { printf '\033[1m==>\033[0m %s\n' "$*"; }
die() { printf 'error: %s\n' "$*" >&2; exit 1; }

# Podman must be reachable. On Windows and macOS it runs in a VM ("podman machine").
ensure_podman() {
  command -v podman >/dev/null 2>&1 || die "podman is not installed (see docs/05_Environment_Setup_Plan.md, section 6)"
  podman info >/dev/null 2>&1 && return
  say "Starting the Podman machine"
  podman machine start >/dev/null || die "could not start the Podman machine (create one with: podman machine init)"
  podman info >/dev/null 2>&1 || die "Podman is still not reachable"
}

# `podman compose` when its provider runs; otherwise podman-compose as a Python module through
# uv (Windows Smart App Control can block the podman-compose.exe launcher).
compose() {
  if [ -z "${COMPOSE_CMD:-}" ]; then
    if podman compose version >/dev/null 2>&1; then
      COMPOSE_CMD="podman compose"
    elif command -v uv >/dev/null 2>&1; then
      COMPOSE_CMD="uv run --quiet --no-project --with podman-compose python -m podman_compose"
    else
      die "no compose provider: install podman-compose (uv tool install podman-compose)"
    fi
  fi
  $COMPOSE_CMD -f "$COMPOSE_FILE" --profile app "$@"
}
