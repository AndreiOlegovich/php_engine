#!/usr/bin/env bash
# version 0.0.3
# Detect unexpected diff artifacts vs HEAD before they get committed:
#   1. whitespace/EOL-only diffs (e.g. CRLF->LF flips from scripted rewrites)
#   2. CRLF line endings lost (HEAD blob has CR, worktree file does not)
#   3. U+FFFD replacement chars introduced (encoding damage from
#      read-as-utf8/write scripted edits)
#   4. full-file rewrites (added == deleted lines on files > 100 lines)
# The rolling feed JSON files are exempt from formatting/EOL/full-rewrite
# checks: the feed writer intentionally serializes and rebalances them whole.
#
# Usage:
#   ./check-diff-artifacts.sh [--staged] [--all]
#     default: worktree (unstaged) files, --staged: index, --all: both.
# Exit 0 when clean, 1 when artifacts found. Untracked files are skipped
# (nothing to compare against).
set -u
cd "$(git rev-parse --show-toplevel)" || exit 1

MODE="worktree"
if [ "${1:-}" = "--staged" ]; then MODE="staged"; fi
if [ "${1:-}" = "--all" ]; then MODE="all"; fi

FOUND=0
report() { echo "ARTIFACT: $1"; FOUND=1; }

check_file() {
    local f="$1" range="$2"
    local feed_data=0
    local -a diff_args=()
    if [ -n "$range" ]; then
        diff_args=("$range")
    fi
    case "$f" in
        container/src/feed/feed.json|container/src/feed/arch.json)
            feed_data=1
            ;;
    esac
    # 1+4: need a real diff first
    if [ -z "$(git diff "${diff_args[@]}" --numstat -- "$f" | awk '{print $1+$2}')" ]; then
        return 0
    fi
    local adds dels
    read -r adds dels _ < <(git diff "${diff_args[@]}" --numstat -- "$f")
    # Binary files show '-' counts: line-based artifact checks (and their
    # arithmetic) do not apply; byte checks below would false-positive.
    if [ "$adds" = "-" ] || [ "$dels" = "-" ]; then
        return 0
    fi
    if [ -z "$adds$dels" ] || [ "$adds$dels" = "00" ]; then
        return 0
    fi
    if [ "$feed_data" -eq 0 ]; then
        # 1. whitespace-only diff?
        local wsum
        wsum=$(git diff "${diff_args[@]}" -w --numstat -- "$f" | awk '{print $1+$2}')
        if [ -z "$wsum" ]; then
            report "$f [$MODE]: whitespace/EOL-only diff ($adds+/ ${dels}-, invisible under -w)"
        fi
    fi
    # 4. full-file rewrite? The post-commit feed writer serializes these
    # bounded/archival JSON datasets from scratch during normal rotations.
    if [ "$feed_data" -eq 0 ] && [ "$adds" = "$dels" ] && [ "$adds" -gt 100 ]; then
        report "$f [$MODE]: full-file rewrite ($adds lines removed+added, possible EOL flip)"
    fi
    # blob-vs-worktree byte checks need the worktree file (skip for --staged
    # when worktree copy may differ from index; still useful: compare index)
    local target
    if [ "$MODE" = "staged" ]; then
        target=$(mktemp)
        git show ":$f" > "$target" 2>/dev/null || return 0
    else
        target="$f"
    fi
    # 2. CRLF lost?
    if [ "$feed_data" -eq 0 ] && git show "HEAD:$f" 2>/dev/null | grep -q $'\r'; then
        if ! grep -q $'\r' "$target"; then
            report "$f [$MODE]: CRLF line endings lost (HEAD blob has CR)"
        fi
    fi
    # 3. U+FFFD introduced? (UTF-8 bytes EF BF BD)
    local head_fffd new_fffd
    head_fffd=$(git show "HEAD:$f" 2>/dev/null | grep -c $'\xef\xbf\xbd' || true)
    new_fffd=$(grep -c $'\xef\xbf\xbd' "$target" || true)
    if [ "$new_fffd" -gt "$head_fffd" ]; then
        report "$f [$MODE]: U+FFFD replacement chars introduced ($head_fffd -> $new_fffd, encoding damage?)"
    fi
    if [ "$MODE" = "staged" ]; then rm -f "$target"; fi
}

list_files() {
    case "$MODE" in
        staged) git diff --cached --name-only --diff-filter=ACM ;;
        all) { git diff --name-only; git diff --cached --name-only; } | sort -u ;;
        *) git diff --name-only ;;
    esac
}

while IFS= read -r f; do
    [ -n "$f" ] || continue
    if [ "$MODE" = "worktree" ]; then range=""; else range="--cached"; fi
    check_file "$f" "$range"
done < <(list_files)

if [ "$FOUND" -eq 0 ]; then echo "check-diff-artifacts: clean"; fi
exit "$FOUND"
