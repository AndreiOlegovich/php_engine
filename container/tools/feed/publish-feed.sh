#!/usr/bin/env bash
# version 1.0.0
# Publish the article feed: build container/src/feed/feed.json and arch.json
# from the first-parent history of main and commit only those two files.
#
# Run it on main, by hand or before a deploy. It reads the commits after the
# one stored in feed.json ("through"), posts the article pages they created,
# modified or moved (dated by the commit), writes both files atomically
# under a lock in the git common dir, and runs
#   git commit -m "Feed: ..." -- container/src/feed/feed.json container/src/feed/arch.json
# so anything else that is staged stays staged. No hook writes the feed.
#
# Usage:
#   ./publish-feed.sh --dry-run          # show the entries, change nothing
#   ./publish-feed.sh                    # build and commit
#   ./publish-feed.sh --skip <commit>    # leave a bulk plumbing commit out
#   ./publish-feed.sh --from <commit>    # first run, or restart after a rewrite
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

exec python3 "$SCRIPT_DIR/post_to_feed.py" "$@"
