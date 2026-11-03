#!/usr/bin/env bash
# Guard: staged NEW pages must not match an .htaccess redirect source.
# A new article whose URL is redirected (e.g. to /about/) would be
# unreachable from the moment it is committed. Uses the staged
# .htaccess so a same-commit rule+page mistake is caught too. Rules are
# read by container/tools/htaccess_redirects.py, which understands both
# the mod_rewrite pairs used since 2026-10-22 and old Redirect lines.
set -euo pipefail

HTACCESS="$(git show ":container/src/.htaccess" 2>/dev/null || true)"
[ -n "$HTACCESS" ] || exit 0

mapfile -t added < <(git diff --cached --name-only --diff-filter=A -- container/src/ | grep '\.php$' || true)
[ "${#added[@]}" -gt 0 ] || exit 0

urls=()
for file in "${added[@]}"; do
    rel="${file#container/src}"
    # Dot folders hold the framework (.php/, ao/.php/), not pages, as in
    # generate-sitemap.sh: /.php/x.php is redirected to /.php, and that is fine.
    [[ "$rel" == */.* ]] && continue
    if [[ "$rel" == */index.php ]]; then
        urls+=("${rel%index.php}")
    else
        urls+=("$rel")
    fi
done
[ "${#urls[@]}" -gt 0 ] || exit 0

staged="$(mktemp)"
trap 'rm -f -- "$staged"' EXIT
printf '%s\n' "$HTACCESS" > "$staged"

if ! hits="$(python3 "$(dirname "$0")/htaccess_redirects.py" match "$staged" "${urls[@]}")"; then
    while IFS=$'\t' read -r url line status pattern; do
        echo "ERROR: new page $url is a redirect source (.htaccess $line, $status $pattern)." >&2
    done <<< "$hits"
    echo "Remove the redirect or pick a different URL." >&2
    exit 1
fi
