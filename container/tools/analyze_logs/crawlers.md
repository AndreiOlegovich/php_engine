# Crawlers reference list (`crawlers.json`)

Reference only — `check_bots.py` does **not** read this file (it keeps its
own `KNOWN_BOT_UAS` token tuple). Use this list when researching an unknown
user agent from the logs, or when deciding which tokens to promote into the
detection pipeline.

- Entries: 1520 (built 2026-10-16).
- Entry shape: `pattern` (regex or substring as given by the source),
  `description`, `url` (vendor/pattern docs), `source`, plus `operator`
  on vendor-sourced AI/SEO crawler entries.

## Sources (all online, all content originates from them)

1. `https://github.com/monperrus/crawler-user-agents` — community list of
   HTTP user agents used by robots/crawlers/spiders, single JSON file
   (`crawler-user-agents.json`). 1501 patterns at build time.
2. `https://developers.cloudflare.com/ai-crawl-control/reference/bots/` —
   AI crawler user-agent tokens (GPTBot, ClaudeBot family, Perplexity,
   Meta, Amazon, Google AI agents, …).
3. `https://developers.openai.com/api/docs/bots` — OpenAI crawler docs
   (GPTBot, ChatGPT-User, OAI-SearchBot, OAI-AdsBot), including the note
   that version numbers change, so substring match beats exact match.
4. `https://support.claude.com/en/articles/8896518-does-anthropic-crawl-data-from-the-web-and-how-can-site-owners-block-the-crawler`
   — Anthropic crawler docs (ClaudeBot, Claude-SearchBot, Claude-User;
   `anthropic-ai` / `claude-web` are legacy/observed names).
5. `https://www.semrush.com/bot/` — SemrushBot family
   (SemrushBot, SiteAuditBot, SemrushBot-BA/-SI/-SWA/-OCOB/-FT/-ESI,
   SplitSignalBot, RyteBot).

Monperrus lags on newer AI bots (no ClaudeBot/SemrushBot entries at build
time), which is why the vendor supplement exists; overlapping patterns were
kept once (monperrus copy wins).

## Refresh

```bash
curl -sL https://raw.githubusercontent.com/monperrus/crawler-user-agents/master/crawler-user-agents.json -o /tmp/opencode/crawlers-monperrus.json
python3 -c "import json; print(len(json.load(open('/tmp/opencode/crawlers-monperrus.json'))))"
```

Then re-run the compile step and bump `meta.built`. Re-check the vendor
docs for new agents (OpenAI and Anthropic publish theirs; Cloudflare keeps
the widest AI directory).
