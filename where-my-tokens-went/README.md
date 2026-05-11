# where-my-tokens-went

Data and analysis script for [Where My Tokens Actually Went](https://haowjy.github.io/blog/where-my-tokens-went).

## Contents

- `analyze.py` — script that walks raw session logs and produces the aggregates in `data/`
- `session-read-analyzer.py` — shared parser for Claude Code, Codex, OpenCode, and Meridian sessions
- `track-reads-over-time.py` — weekly redundancy and read-after-edit tracker
- `april3-before-after-by-role.py` — Claude Code before/after cut at the April 3, 2026 v2.1.91 release date
- `data/` — derived/aggregate datasets (CSV)
- `images/` — generated charts used for review and publication

## What's not here

Raw conversation transcripts and file contents are excluded for privacy and size.
The aggregates here are derived from those raw logs and cannot be reconstructed back.

## Scrubbing checklist

Before committing anything to `data/`, sweep for:

- [ ] **Absolute paths** — `/Users/<you>/...`, `/home/<you>/...` → strip to relative or `<HOME>` placeholder
- [ ] **API keys / tokens** — env-var dumps, auth headers, OAuth blobs
- [ ] **Filenames** that reveal repo names, project codenames, client names → keep only what the post needs
- [ ] **User IDs / hostnames** in session metadata
- [ ] **URLs to private repos / internal endpoints**
- [ ] **Email addresses** in commit metadata or git config
- [ ] **Verbatim chat content** — even in "preview" or "first 200 chars" columns; keep counts, drop text
- [ ] **Cost values that imply pricing tier or contract details** beyond what the post discloses

If in doubt, hash or bucket. A column like `repo_hash` is safer than `repo_name`.

## Reproducing

Drop your own session log directories in and run:

```
python analyze.py --claude-code-dir <path> --codex-dir <path> --opencode-dir <path>
python track-reads-over-time.py --summary
python april3-before-after-by-role.py --min-edits 25 --min-side-edits 25
```
