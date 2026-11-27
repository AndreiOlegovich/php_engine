#!/usr/bin/env bash
# version 0.5.0
# Run the onboarding step a new project is at, then show the next one:
#   container/tools/onboard.sh                    # the current step
#   container/tools/onboard.sh /path/to/old-site  # the copy step
#   container/tools/onboard.sh beget              # the hosting step
# The steps live in container/tools/onboard.py; see its --help.
set -euo pipefail
exec python3 "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/onboard.py" step "$@"
