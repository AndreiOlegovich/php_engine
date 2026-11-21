---
description: Final correctness pass over staged diffs; reports only, never edits
mode: subagent
model: opencode/claude-opus-5-5
steps: 12
permissions:
  - action: edit
    resource: "*"
    effect: deny
  - action: shell
    resource: "*"
    effect: deny
  - action: shell
    resource: "git diff *"
    effect: allow
  - action: shell
    resource: "git show *"
    effect: allow
  - action: shell
    resource: "git log *"
    effect: allow
  - action: shell
    resource: "git status *"
    effect: allow
  # Repeated from opencode.jsonc: agent rules come after the global ones and
  # the last match wins, so the git allows above would otherwise reopen these
  # (git diff --no-index prints any file).
  - action: shell
    resource: "*.env*"
    effect: deny
  - action: shell
    resource: "*creds*"
    effect: deny
  - action: shell
    resource: "*.config/php_engine*"
    effect: deny
  - action: shell
    resource: "*php_engine/logs*"
    effect: deny
  - action: shell
    resource: "*.ssh/*"
    effect: deny
  - action: shell
    resource: "*.gnupg*"
    effect: deny
  - action: shell
    resource: "*.docker/config.json*"
    effect: deny
  - action: shell
    resource: "*opencode/auth.json*"
    effect: deny
  - action: shell
    resource: "*opencode.db*"
    effect: deny
---
Review the staged diff (git diff --cached) against the rules in AGENTS.md.
Report bugs first, in severity order, with file and line. End with a verdict:
ship, fix first, or block. Do not edit files.
