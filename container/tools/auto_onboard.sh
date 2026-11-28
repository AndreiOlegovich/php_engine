#!/usr/bin/env bash
# version 0.4.0
# Walk a new project through its whole onboarding in one run, asking when it
# needs you (the old site's folder, the profile lines, the hosting, the first
# commit). The recommended way after new-project.sh.
# The steps live in container/tools/onboard.py; see its --help.
set -euo pipefail
exec python3 "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/onboard.py" "$@"
