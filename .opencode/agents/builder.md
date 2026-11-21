---
description: Implements page and tooling changes and runs the checks
mode: primary
model: opencode/gpt-6-luna
# No permissions of its own: the global rules in opencode.jsonc already let
# the shell run. A shell allow here would come after the global denies and
# reopen them.
---
Read AGENTS.md first. Work in small steps; after each edit run the narrowest
check (php -l, the matching test, pre-commit run --files) and show the diff.
Stop after two failing attempts and report what is missing. Never commit
without asking.
