# OpenCode session tools

`container/tools/ai/opencode-docker.sh` runs OpenCode in a container with
the named Docker volume `<project>-agent-data` mounted at
`/home/agent/.local/share/opencode`. Every session started there (TUI or
`run`) is stored in that volume, not in the host's `opencode session list`.
`agent_sessions.py` reads the volume and prints those sessions: the task a
session was given, the assistant's replies and tool calls, and why it
stopped (for example a free-model usage limit).

The tool copies `opencode.db`, `opencode.db-wal` and `opencode.db-shm` into
a temporary directory, so recent messages in the WAL are included, then
opens the copy read-only. It never writes to the volume.

## Commands

```bash
# Newest sessions: id, local time, message count, title
python3 container/tools/opencode/agent_sessions.py list

# The task prompt: the first user message of a session
python3 container/tools/opencode/agent_sessions.py show ses_... --prompts

# Find the session whose task mentioned something
python3 container/tools/opencode/agent_sessions.py search "php functions"

# Whole transcript; add --tool-output and --reasoning for the full picture
python3 container/tools/opencode/agent_sessions.py show ses_...
```

`--json` is available on every subcommand. Long texts are shortened; add
`--full` to print them whole.

## Other database or volume

```bash
# A database exported earlier (cp of the volume, no Docker needed)
python3 container/tools/opencode/agent_sessions.py list --db /tmp/exported.db

# Another volume or image
python3 container/tools/opencode/agent_sessions.py list --volume other-data --image <project>-agent
```

`OPENCODE_AGENT_VOLUME` and `OPENCODE_AGENT_IMAGE` set the same defaults.

## Continuing a session

The database keeps the session id; the matching command resumes it in the
same volume:

```bash
container/tools/ai/opencode-docker.sh ../<project>-agent-example -s ses_...
```

## Paid models (OpenCode Go)

Free models need no credentials. The volume starts with no paid-provider
account, so `-m opencode-go/...` fails with `Model unavailable` until
OpenCode Go is connected once (key from the Go page of
<https://opencode.ai/console>):

```bash
docker run --rm -it \
  -v <project>-agent-data:/home/agent/.local/share/opencode \
  <project>-agent auth login opencode-go --method key
```

The login is saved in the volume's database and survives `--rm` runs.
Check it with `auth list`, then pass the model:

```bash
docker run --rm -v <project>-agent-data:/home/agent/.local/share/opencode <project>-agent auth list

container/tools/ai/opencode-docker.sh ../<project>-agent-example run \
  -m opencode-go/deepseek-v4.1-flash "Continue the task."
```

## Tests

```bash
cd container && ./tools/run-tests-venv.sh -- tests/tools/test_opencode_agent_sessions.py -q
```
