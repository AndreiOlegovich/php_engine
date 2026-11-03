#!/usr/bin/env bash
# version 0.0.1
# Sample script for test_check_version_sh.py: carries a "# version" header
# so the check-version gate can verify bumped vs unbumped staged changes.
# It does nothing by design; tests copy it into scratch git repos and
# never modify this file itself.
set -euo pipefail

true
