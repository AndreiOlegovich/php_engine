#!/usr/bin/env bash
# version 0.3.0
# Run OpenCode with --auto in a container that sees container/src of an
# agent worktree read-write, and the instructions, docs and tools read-only.
#
# Usage (repo root of the main checkout):
#   git worktree add ../site-agent -b agent/<task>
#   container/tools/ai/opencode-docker.sh ../site-agent                 # TUI
#   container/tools/ai/opencode-docker.sh ../site-agent run "<task>"    # one task
#   container/tools/ai/opencode-docker.sh ../site-agent shell           # a shell
#   container/tools/ai/opencode-docker.sh --rw container/tools/deploy ../site-agent run "<task>"
#     --rw <path>: writable for this run, a path inside the worktree; repeat
#     it for several paths. Masked files stay empty even under it.
#   container/tools/ai/opencode-docker.sh --openrouter-key ~/.config/opencode-agent/openrouter.key ../site-agent run -m openrouter/<model> "<task>"
#     --openrouter-key <file>: an OpenRouter key for this run only, from a
#     file outside the repo; the container gets it as OPENROUTER_API_KEY.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
rw=(); key_file=
while :; do
  case "${1:-}" in
    --rw) rw+=("${2:?--rw needs a path inside the worktree}"); shift 2 ;;
    --openrouter-key) key_file="${2:?--openrouter-key needs a file}"; shift 2 ;;
    *) break ;;
  esac
done
WT="$(cd "${1:?usage: opencode-docker.sh [--rw <path>]... [--openrouter-key <file>] <worktree> [run <task> | shell]}" && pwd)"
shift

[ -d "$WT/container/src" ] || { echo "ERROR: $WT/container/src not found." >&2; exit 1; }
# The key goes in through the environment of this run only, never as a
# file: one in the repo or the worktree would be mounted, or committed.
env=()
if [ -n "$key_file" ]; then
  [ -r "$key_file" ] || { echo "ERROR: cannot read $key_file." >&2; exit 1; }
  case "$(realpath "$key_file")" in
    "$(realpath "$REPO_ROOT")"/*|"$(realpath "$WT")"/*)
      echo "ERROR: keep the key file outside the repo and the worktree." >&2; exit 1 ;;
  esac
  OPENROUTER_API_KEY="$(tr -d '[:space:]' < "$key_file")"
  [ -n "$OPENROUTER_API_KEY" ] || { echo "ERROR: $key_file is empty." >&2; exit 1; }
  export OPENROUTER_API_KEY
  env=(-e OPENROUTER_API_KEY)
fi
ro=(AGENTS.md .claude/skills skill.md _internal_ai_skill_for_files.md
    container/docs container/tools container/tests)
for p in "${rw[@]}"; do
  case "$p" in /*|*..*) echo "ERROR: --rw $p: use a path inside the worktree." >&2; exit 1 ;; esac
  [ -e "$WT/$p" ] || { echo "ERROR: --rw $p: not found in $WT." >&2; exit 1; }
done
# Only tracked files go in: the ignored ones are logs, visitor data, backups.
# Python caches are ignored too, but they hold nothing but compiled tools.
ignored="$(git -C "$WT" status --short --ignored -- container "${ro[@]}" "${rw[@]}" \
  | grep '^!!' | grep -v '__pycache__/$' || true)"
if [ -n "$ignored" ]; then
  echo "ERROR: $WT holds ignored files; use a fresh worktree:" >&2
  echo "$ignored" | head -5 >&2
  exit 1
fi
in_list() { local x="$1" y; shift; for y in "$@"; do [ "$x" = "$y" ] && return 0; done; return 1; }

docker build -q -t opencode-agent \
  --build-arg HOST_UID="$(id -u)" --build-arg HOST_GID="$(id -g)" \
  -f "$REPO_ROOT/container/containerfiles/Dockerfile.opencode" \
  "$REPO_ROOT/container/containerfiles" >/dev/null

# Read-write: the docroot. Read-only: what the agent reads and runs, unless
# the task named it with --rw.
mounts=(-v "$WT/container/src:/work/container/src")
for p in "${ro[@]}"; do
  [ -e "$WT/$p" ] || continue
  if in_list "$p" "${rw[@]}"; then mounts+=(-v "$WT/$p:/work/$p")
  else mounts+=(-v "$WT/$p:/work/$p:ro"); fi
done
# --rw inside a read-only folder: a writable mount on top of it.
for p in "${rw[@]}"; do
  in_list "$p" container/src "${ro[@]}" || mounts+=(-v "$WT/$p:/work/$p")
done
# Tracked files that the deny rules of the host protect: an empty read-only
# file in their place, even under --rw. Edit them by hand on the host.
while IFS= read -r f; do
  mounts+=(-v "$SCRIPT_DIR/docker/masked:/work/$f:ro")
done < <(git -C "$WT" ls-files -- container | grep -E '\.env$|creds' || true)
mounts+=(-v "$SCRIPT_DIR/docker/opencode.jsonc:/work/opencode.jsonc:ro"
         -v "$SCRIPT_DIR/docker/CONTAINER.md:/work/CONTAINER.md:ro"
         -v opencode-agent-data:/home/agent/.local/share/opencode)

entry=()
case "${1:-}" in
  run)   shift; set -- run --auto "$@" ;;
  shell) shift; entry=(--entrypoint sh) ;;
  *)     set -- --auto "$@" ;;
esac
tty=(); [ -t 0 ] && tty=(-t)

exec docker run --rm -i "${tty[@]}" \
  --cap-drop ALL --security-opt no-new-privileges \
  "${env[@]}" "${mounts[@]}" "${entry[@]}" opencode-agent "$@"
