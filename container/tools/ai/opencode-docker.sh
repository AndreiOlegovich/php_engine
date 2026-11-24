#!/usr/bin/env bash
# version 0.8.3
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
#   container/tools/ai/opencode-docker.sh --opencode-key ~/.config/opencode-agent/opencode.key ../site-agent run -m opencode-go/<model> "<task>"
#     --opencode-key <file>: an OpenCode key (Zen or Go) for this run only,
#     from a file outside the repo; the container gets it as OPENCODE_API_KEY.
#   container/tools/ai/opencode-docker.sh --rust ../site-agent run "<task>"
#     A language flag picks the image that can run the examples of the pages
#     of that language; one flag per run, the last one wins:
#     --assembler --c --cpp --fortran --cobol --go --rust --haskell, or
#       --native: the image of Dockerfile.native.opencode (nasm, gcc, g++,
#       gfortran, cobc, go, rustc and cargo, ghc and cabal).
#     --java --groovy --dotnet --elixir --thrift, or --vm: the image of
#       Dockerfile.vm.opencode (JDK and Maven, Groovy, the .NET SDK,
#       Erlang/OTP, Elixir, the Thrift compiler).
#     --javascript --js --typescript, or --script: the image of
#       Dockerfile.script.opencode (Node.js, npm, tsc, eslint, Ruby).
#     --erlang: the image of Dockerfile.erlang.opencode, Erlang/OTP alone
#       (erl, erlc, escript).
#     --ruby: the image of Dockerfile.ruby.opencode, Ruby alone (ruby, irb,
#       gem, erb).
#     PHP, Python and Perl need no flag: every image has them.
# At start it prints to stderr the model and the file or option it comes
# from, the image and the name of the container, between empty lines.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
# shellcheck disable=SC1091  # shared resolver is checked separately
. "$SCRIPT_DIR/../project-env.sh"
rw=(); ro_ext=(); keys=(); flavour=
while :; do
  case "${1:-}" in
    --rw) rw+=("${2:?--rw needs a path inside the worktree}"); shift 2 ;;
    --ro-ext) ro_ext+=("${2:?--ro-ext needs a directory}"); shift 2 ;;
    --openrouter-key) keys+=(OPENROUTER_API_KEY "${2:?--openrouter-key needs a file}"); shift 2 ;;
    --opencode-key) keys+=(OPENCODE_API_KEY "${2:?--opencode-key needs a file}"); shift 2 ;;
    --native|--assembler|--c|--cpp|--fortran|--cobol|--go|--rust|--haskell) flavour=native; shift ;;
    --vm|--java|--groovy|--dotnet|--elixir|--thrift) flavour=vm; shift ;;
    --script|--javascript|--js|--typescript) flavour=script; shift ;;
    --erlang) flavour=erlang; shift ;;
    --ruby) flavour=ruby; shift ;;
    *) break ;;
  esac
done
dockerfile="Dockerfile${flavour:+.$flavour}.opencode"; image="$SITE_PROJECT-agent${flavour:+-$flavour}"
WT="$(cd "${1:?usage: opencode-docker.sh [--rw <path>]... [--openrouter-key <file>] [--opencode-key <file>] [--<language> | --native | --vm | --script] <worktree> [run <task> | shell]}" && pwd)"
shift

[ -d "$WT/container/src" ] || { echo "ERROR: $WT/container/src not found." >&2; exit 1; }
# A key goes in through the environment of this run only, never as a
# file: one in the repo or the worktree would be mounted, or committed.
env=()
for ((i = 0; i < ${#keys[@]}; i += 2)); do
  var="${keys[i]}"; key_file="${keys[i + 1]}"
  [ -r "$key_file" ] || { echo "ERROR: cannot read $key_file." >&2; exit 1; }
  case "$(realpath "$key_file")" in
    "$(realpath "$REPO_ROOT")"/*|"$(realpath "$WT")"/*)
      echo "ERROR: keep the key file outside the repo and the worktree." >&2; exit 1 ;;
  esac
  key="$(tr -d '[:space:]' < "$key_file")"
  [ -n "$key" ] || { echo "ERROR: $key_file is empty." >&2; exit 1; }
  export "$var=$key"
  env+=(-e "$var")
done
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

# What this run uses: the model, where it comes from, the image and the
# name of the container. To stderr, so stdout holds only what OpenCode says.
config="$SCRIPT_DIR/docker/opencode.jsonc"
case "${1:-}" in
  run)   what="one task" ;;
  shell) what="a shell; OpenCode is not started, the model is for opencode commands in it" ;;
  *)     what="the TUI" ;;
esac
model=; model_from=
if [ "${1:-}" != shell ]; then
  prev=
  for a in "$@"; do
    case "$prev" in -m|--model) model="$a"; model_from="$prev on the command line" ;; esac
    case "$a" in --model=*) model="${a#--model=}"; model_from="--model on the command line" ;; esac
    prev="$a"
  done
fi
if [ -z "$model" ]; then
  model="$(sed -n 's/^[[:space:]]*"model"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$config" | head -n 1)"
  model_from="$config"
  [ -n "$model" ] || model="the default of OpenCode: the file has no model line"
fi
container="$image-$(printf '%s' "${WT##*/}" | tr -c 'A-Za-z0-9_.-' '-')-$$"
{
  echo
  echo "OpenCode in Docker: $what"
  echo "  model:      $model"
  echo "  from:       $model_from"
  echo "  image:      $image, built from container/containerfiles/$dockerfile"
  echo "  container:  $container"
  echo "  worktree:   $WT"
  echo
} >&2

docker build -q -t "$image" \
  --build-arg HOST_UID="$(id -u)" --build-arg HOST_GID="$(id -g)" \
  -f "$REPO_ROOT/container/containerfiles/$dockerfile" \
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
# --ro-ext <dir>: another checkout (e.g. the php_engine repo) shown read-only
# at /work/external/<name>. Its .env and creds files are masked like the
# worktree's tracked ones; nothing in it is writable.
for d in "${ro_ext[@]}"; do
  [ -d "$d" ] || { echo "ERROR: --ro-ext $d: not a directory." >&2; exit 1; }
  d="$(cd "$d" && pwd)"
  name="$(basename "$d")"
  mounts+=(-v "$d:/work/external/$name:ro")
  while IFS= read -r f; do
    mounts+=(-v "$SCRIPT_DIR/docker/masked:/work/external/$name/$f:ro")
  done < <(git -C "$d" ls-files | grep -E '(^|/)\.env$|creds' || true)
done
# Tracked files that the deny rules of the host protect: an empty read-only
# file in their place, even under --rw. Edit them by hand on the host.
while IFS= read -r f; do
  mounts+=(-v "$SCRIPT_DIR/docker/masked:/work/$f:ro")
done < <(git -C "$WT" ls-files -- container | grep -E '\.env$|creds' || true)
mounts+=(-v "$SCRIPT_DIR/docker/opencode.jsonc:/work/opencode.jsonc:ro"
         -v "$SCRIPT_DIR/docker/CONTAINER.md:/work/CONTAINER.md:ro"
         -v "$SITE_PROJECT-agent-data:/home/agent/.local/share/opencode")

entry=()
case "${1:-}" in
  run)   shift; set -- run --auto "$@" ;;
  shell) shift; entry=(--entrypoint sh) ;;
  *)     set -- --auto "$@" ;;
esac
tty=(); [ -t 0 ] && tty=(-t)

exec docker run --rm -i "${tty[@]}" --name "$container" \
  --cap-drop ALL --security-opt no-new-privileges \
  "${env[@]}" "${mounts[@]}" "${entry[@]}" "$image" "$@"
