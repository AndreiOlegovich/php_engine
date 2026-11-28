#!/usr/bin/env bash
# version 0.1.0
# Remove a project that new-project.sh made: its folder and everything Docker
# and the tools keep under its name, so the name is free for a new start.
#
# Usage:
#   container/tools/remove-project.sh TARGET               # print the plan only
#   container/tools/remove-project.sh TARGET --yes         # remove
#   container/tools/remove-project.sh TARGET --yes --with-credentials
#
#   TARGET              the project root, the folder new-project.sh made (it
#                       holds container/.env with SITE_PROJECT)
#   --yes               remove; without it nothing is touched
#   --keep-folder       remove the Docker objects and data, keep TARGET
#   --with-credentials  also remove ~/.config/<project> (hosting credentials).
#                       Without it the folder stays, and new-project.sh refuses
#                       the name while it exists
#   --force             also when TARGET has uncommitted changes, commits that
#                       no remote has, or linked worktrees (agent worktrees)
#
# What goes, found by exact names (a project "hei" never touches "hei-hei"):
#   - the Compose project <project>: its containers, volumes and networks;
#   - the images <project>:*, <project>-pytest_web, <project>-agent and
#     <project>-agent-<flavour>, and every container started from them;
#   - the volume <project>-agent-data (the OpenCode sessions);
#   - ~/.local/share/<project> (logs, translation runs), ~/.cache/<project>
#     (deploy logs) and, with --with-credentials, ~/.config/<project>;
#   - TARGET itself.
# It refuses the engine checkout it runs from and any folder without
# SITE_PROJECT in container/.env: a site that new-project.sh did not make.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENGINE="$(cd "$SCRIPT_DIR/../.." && pwd -P)"

usage() {
  sed -n '2,/^set -euo pipefail$/p' "$0" | sed 's/^# \{0,1\}//' | grep -v '^set -euo'
}

TARGET=""
YES=0
KEEP_FOLDER=0
WITH_CREDENTIALS=0
FORCE=0
while [ $# -gt 0 ]; do
  case "$1" in
    --yes) YES=1; shift ;;
    --keep-folder) KEEP_FOLDER=1; shift ;;
    --with-credentials) WITH_CREDENTIALS=1; shift ;;
    --force) FORCE=1; shift ;;
    -h|--help) usage; exit 0 ;;
    -*) echo "ERROR: unknown option: $1 (see --help)." >&2; exit 2 ;;
    *) [ -z "$TARGET" ] || { echo "ERROR: one TARGET only." >&2; exit 2; }; TARGET="$1"; shift ;;
  esac
done
[ -n "$TARGET" ] || { usage >&2; exit 2; }
[ -d "$TARGET" ] || { echo "ERROR: $TARGET is not a folder." >&2; exit 2; }
TARGET="$(cd "$TARGET" && pwd -P)"

env_value() {
  [ -f "$1" ] || return 0
  sed -n "s/^$2=[\"']\{0,1\}\([^\"']*\)[\"']\{0,1\}[[:space:]]*\$/\1/p" "$1" | tail -n 1
}

# --- what this is --------------------------------------------------------
[ "$TARGET" != "$ENGINE" ] || { echo "ERROR: $TARGET is the engine this script runs from; it is never removed." >&2; exit 2; }
[ "$TARGET" != "/" ] && [ "$TARGET" != "$HOME" ] || { echo "ERROR: refusing $TARGET." >&2; exit 2; }
SLUG="$(env_value "$TARGET/container/.env" SITE_PROJECT)"
[ -n "$SLUG" ] || { echo "ERROR: $TARGET/container/.env names no SITE_PROJECT: new-project.sh did not make this folder." >&2; exit 2; }
case "$SLUG" in
  *[!a-z0-9_-]*|[!a-z0-9]*) echo "ERROR: SITE_PROJECT '$SLUG' is no project name new-project.sh writes." >&2; exit 2 ;;
esac
[ "$SLUG" != "$(env_value "$ENGINE/container/.env" SITE_PROJECT)" ] || { echo "ERROR: $SLUG is the name of the engine checkout itself." >&2; exit 2; }

problems=()
if git -C "$TARGET" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  if git -C "$TARGET" rev-parse -q --verify HEAD >/dev/null; then
    [ -z "$(git -C "$TARGET" status --short)" ] || problems+=("uncommitted changes")
    if [ -n "$(git -C "$TARGET" remote)" ]; then
      unpushed="$(git -C "$TARGET" log --oneline --branches --not --remotes | wc -l)"
      [ "$unpushed" = 0 ] || problems+=("$unpushed commit(s) no remote has")
    else
      problems+=("$(git -C "$TARGET" rev-list --count --all) commit(s) and no remote")
    fi
  fi
  worktrees="$(git -C "$TARGET" worktree list --porcelain | grep -c '^worktree ' || true)"
  [ "$worktrees" -le 1 ] || problems+=("$((worktrees - 1)) linked worktree(s)")
fi
if [ "${#problems[@]}" -gt 0 ]; then
  joined="$(IFS=';'; echo "${problems[*]}")"
  printf 'The project in %s has: %s.\n' "$TARGET" "${joined//;/, }"
  [ "$FORCE" = 1 ] || { echo "Nothing is removed without --force." >&2; exit 1; }
fi

# --- the plan --------------------------------------------------------------
CONFIG="${XDG_CONFIG_HOME:-$HOME/.config}/$SLUG"
DATA="${XDG_DATA_HOME:-$HOME/.local/share}/$SLUG"
CACHE="${XDG_CACHE_HOME:-$HOME/.cache}/$SLUG"
containers=() images=() volumes=() networks=()
if docker info >/dev/null 2>&1; then
  label="label=com.docker.compose.project=$SLUG"
  mapfile -t images < <(
    for ref in "$SLUG" "$SLUG-pytest_web" "$SLUG-agent" "$SLUG-agent-*"; do
      docker image ls --format '{{.Repository}}:{{.Tag}}' --filter "reference=$ref" --filter "reference=$ref:*"
    done | grep -v ':<none>$' | LC_ALL=C sort -u
  )
  mapfile -t containers < <(
    {
      docker ps -a --format '{{.Names}}' --filter "$label"
      for image in "${images[@]}"; do docker ps -a --format '{{.Names}}' --filter "ancestor=$image"; done
    } | LC_ALL=C sort -u
  )
  mapfile -t volumes < <({
    docker volume ls -q --filter "$label"
    docker volume inspect "$SLUG-agent-data" >/dev/null 2>&1 && echo "$SLUG-agent-data"
  } | LC_ALL=C sort -u)
  mapfile -t networks < <(docker network ls --format '{{.Name}}' --filter "$label" | LC_ALL=C sort -u)
else
  echo "NOTE: Docker is not running: its containers, images and volumes of $SLUG are not listed." >&2
fi

echo "Project $SLUG in $TARGET:"
show() {
  local label="$1"
  shift
  if [ $# -eq 0 ]; then printf '  %-12s none\n' "$label:"; else printf '  %-12s %s\n' "$label:" "$*"; fi
}
show containers "${containers[@]}"
show images "${images[@]}"
show volumes "${volumes[@]}"
show networks "${networks[@]}"
folders=()
[ "$KEEP_FOLDER" = 1 ] || folders+=("$TARGET ($(du -sh "$TARGET" 2>/dev/null | cut -f1))")
for dir in "$DATA" "$CACHE"; do [ ! -e "$dir" ] || folders+=("$dir"); done
if [ -e "$CONFIG" ]; then
  if [ "$WITH_CREDENTIALS" = 1 ]; then folders+=("$CONFIG"); else echo "  kept:        $CONFIG (credentials; --with-credentials removes it, new-project.sh refuses $SLUG while it exists)"; fi
fi
show folders "${folders[@]}"

if [ "$YES" != 1 ]; then
  echo
  echo "Nothing was removed. Run again with --yes to remove the above."
  exit 0
fi

# --- removal -------------------------------------------------------------
echo
[ "${#containers[@]}" -eq 0 ] || docker rm -f "${containers[@]}" >/dev/null
[ "${#volumes[@]}" -eq 0 ] || docker volume rm "${volumes[@]}" >/dev/null
[ "${#networks[@]}" -eq 0 ] || docker network rm "${networks[@]}" >/dev/null
[ "${#images[@]}" -eq 0 ] || docker image rm "${images[@]}" >/dev/null
for dir in "$DATA" "$CACHE"; do [ ! -e "$dir" ] || rm -rf -- "$dir"; done
[ "$WITH_CREDENTIALS" != 1 ] || [ ! -e "$CONFIG" ] || rm -rf -- "$CONFIG"
if [ "$KEEP_FOLDER" != 1 ]; then
  cd /
  rm -rf -- "$TARGET"
fi
echo "Removed project $SLUG. new-project.sh can use the name again$([ -e "$CONFIG" ] && echo " once $CONFIG is gone")."
