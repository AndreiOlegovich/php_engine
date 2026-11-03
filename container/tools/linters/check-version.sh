#!/usr/bin/env bash
# version 0.3.1
# Pre-commit gate: version markers must be bumped with every change.
#
# Any staged page file (.php/.html/.htm) containing an HTML version marker
# must carry a different marker than HEAD (new files with a marker pass).
# Markdown/scripts are skipped here: they may quote `<!-- version … -->` as
# an example, and the gate itself contains the grep pattern. This keeps
# data/page reverse-chronology honest: edited content always ships a new
# version string.
#
# Same rule for maintained shell scripts: a staged .sh file carrying a
# "# version X.Y.Z" header (line 2 after the shebang, else line 1) must
# bump it on every change (new files pass; scripts without a header are
# skipped). Shell headers were previously convention-only, which let
# unbumped script changes through.
#
# Exit 0: no staged version markers, or all of them changed (or are new).
# Exit 1: lists each file whose marker is identical to HEAD.
set -euo pipefail

failures=0
while IFS= read -r file; do
  [ -n "$file" ] || continue
  case "$file" in
    *.php|*.html|*.htm) ;;
    *) continue ;;
  esac
  new_markers="$(git show ":$file" 2>/dev/null | grep -o '<!-- version [^>]*-->' | sort -u || true)"
  [ -z "$new_markers" ] && continue
  old_markers="$(git show "HEAD:$file" 2>/dev/null | grep -o '<!-- version [^>]*-->' | sort -u || true)"
  if [ "$new_markers" = "$old_markers" ]; then
    echo "ERROR: $file changed but its HTML version marker is unchanged ($new_markers)." >&2
    echo "Bump the HTML version marker in $file (see this script's header)." >&2
    failures=1
  fi
done < <(git diff --cached --name-only --diff-filter=ACMR)

while IFS= read -r file; do
  [ -n "$file" ] || continue
  case "$file" in *.sh) ;; *) continue ;; esac
  new_markers="$(git show ":$file" 2>/dev/null | grep -o '^# version [^ ]*' | sort -u || true)"
  [ -z "$new_markers" ] && continue
  old_markers="$(git show "HEAD:$file" 2>/dev/null | grep -o '^# version [^ ]*' | sort -u || true)"
  if [ "$new_markers" = "$old_markers" ]; then
    echo "ERROR: $file changed but its '# version' header is unchanged ($new_markers)." >&2
    echo "Bump the '# version X.Y.Z' header in $file." >&2
    failures=1
  fi
done < <(git diff --cached --name-only --diff-filter=ACMR)

exit "$failures"
