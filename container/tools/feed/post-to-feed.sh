#!/usr/bin/env bash
# version 0.2.0
# Post-commit hook: post article pages from the commit that just completed.
#
# For each committed container/src/**/*.php path it decides
# article-or-not (see post_to_feed.py and if_file_is_an_article.py),
# extracts the page title, and prepends {url, title, date, action} entries
# (action is 'created' for added files, 'modified' otherwise). The feed
# page holds the newest 100 entries; older ones spill into feed/arch.json.
#
# Updated feed files are staged for the next commit. A post-commit hook
# cannot modify the commit that has already been created.
#
# Usage (manual runs process HEAD; optional paths filter that commit):
#   ./post-to-feed.sh [PATH ...]
#   ./post-to-feed.sh            # process the most recent commit
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

exec python3 "$SCRIPT_DIR/post_to_feed.py" "$@"
