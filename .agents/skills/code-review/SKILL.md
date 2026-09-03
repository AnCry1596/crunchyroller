---
name: code-review
description: >-
  Reviews pull requests for the crunchyroller project. Use when asked to review
  a PR, audit new code, or post feedback as GitHub review comments. Covers
  security checks, logic correctness, style consistency, and posting inline
  comments via the GitHub MCP server.
---

# Code Review Skill — crunchyroller

## Repo Context

- **Repo:** `Vure-sh/crunchyroller`
- **Stack:** Python (downloader, DRM, API, HTTP client), vanilla JS + HTML/CSS (web GUI), `web_gui.py` (Python HTTP server)
- **Key files:**
  - `crunchyroll/downloader.py` — main download orchestration, SegmentBase + segmented paths
  - `crunchyroll/api.py` — Crunchyroll API calls, playback stream fetch
  - `crunchyroll/session_pool.py` — HTTP session pool, retry logic, parallel range downloads
  - `crunchyroll/mpd.py` — DASH manifest parsing (SegmentTimeline, SegmentBase, KID extraction)
  - `crunchyroll/drm.py` — Widevine DRM / license key handling
  - `crunchyroll/integrity.py` — ffprobe-based stream validation + A/V drift detection
  - `crunchyroll/merger.py` — ffmpeg mux invocation
  - `crunchyroll/http_client.py` — authenticated HTTP client with rate-limit retry
  - `web_gui.py` — local Python HTTP server exposing `/api/*` endpoints
  - `web/js/app.js` — frontend logic (CheckboxDropdown, polling, config save/restore)
  - `web/css/style.css` — dark monochrome theme
  - `web/index.html` — single-page UI

---

## Review Process

### Step 1 — Fetch the PR

Use the GitHub MCP tool to:
1. Get PR details: `get_pull_request` (owner=`Vure-sh`, repo=`crunchyroller`, pull_number=`<N>`)
2. Get changed files: `get_pull_request_files`
3. Read the full diff for each file

If the PR is already fetched locally via git (`git fetch origin refs/pull/<N>/head:pr<N>`), use `git diff master...pr<N>` and `git show pr<N>:<file>` to read the full file in context.

### Step 2 — Security Scan (Always First)

Check every line added (`+`) for:
- Outbound HTTP calls to non-Crunchyroll domains (exfil, telemetry, webhooks)
- `subprocess`, `eval`, `exec`, `os.system`, `__import__`
- Tokens/credentials being logged or sent anywhere unexpected
- External URLs hardcoded that aren't `*.crunchyroll.com`, `*.crunchyroll.com`, Widevine license endpoints

If anything suspicious found → **block merge immediately**, report to user.

### Step 3 — Logic Review

Key patterns to check for this codebase:

**Python / downloader:**
- Progress callbacks must use `f"{track_type}-file"` suffix for SegmentBase (complete-file) paths so `web_gui.py` sets `complete_file=True` correctly
- Rate-limit waits (420/429) must respect `Retry-After` header
- `_invoke_progress_cb` must pass `total_bytes / total_bytes` (not `1/1`) for file-based progress so MB display works
- `get_kids()` in `mpd.py` should return all KIDs, not just one
- `r=-1` in DASH SegmentTimeline means repeat-until-period-end — not 1 segment
- Widevine key matching should normalize to bytes and handle UUID byte-order swap
- `force_download` flag must be threaded all the way from CLI/GUI → `_run_download` → `download_episode`

**JavaScript / web UI:**
- Always null-guard `document.getElementById('force-download')` — use `(el || {}).checked || false`
- `applyState()` reads config on load; `saveCfg()` writes on change; `startDl()` sends on download start — all three must be consistent
- `CheckboxDropdown` single-select mode closes on selection; multi-select stays open
- Cache-busting: CSS/JS imports must have `?v=X` query param

**Python HTTP server (`web_gui.py`):**
- All responses must include `Cache-Control: no-cache, no-store, must-revalidate`
- Config keys persisted: `video_quality`, `audio_quality`, `audio_lang`, `subs_lang`, `force_download`
- `complete_file` state in `STATE["download"]` must be reset to `False` at download start

### Step 4 — Style & Consistency

- Python: follow existing docstring style (short one-liner)
- No `print()` statements for debug output that aren't guarded by `debug=True` or `flush=True` heartbeat pattern
- JS: `const`/`let` only, no `var`
- CSS: use existing CSS variables (`--border`, `--text`, `--dim`, `--bg`, etc.) — no hardcoded colors except `#111111` for popup backgrounds

### Step 5 — Post Review via GitHub MCP

Use `create_pull_request_review` to post:

```
owner: "Vure-sh"
repo: "crunchyroller"
pull_number: <N>
event: "COMMENT" | "APPROVE" | "REQUEST_CHANGES"
body: "<overall summary>"
comments: [
  {
    path: "<file path>",
    line: <line number in new file>,
    body: "<inline comment>"
  }
]
```

**Comment tone:** Direct and technical. No fluff. Reference the exact variable/function. If it's a bug give the fix inline. If it's a nit, prefix with `nit:`.

**Approval criteria:**
- No security issues
- No broken progress/UI state bugs
- No DASH parsing regressions
- Minor nits don't block merge

---

## Quick Checklist

Before approving any PR, verify:
- [ ] No outbound calls to non-Crunchyroll domains
- [ ] SegmentBase progress callback uses `-file` suffix
- [ ] JS `force-download` element null-guarded in all 3 places
- [ ] `r=-1` DASH segments handled correctly if `mpd.py` was touched
- [ ] Rate-limit retry uses `Retry-After` if `session_pool.py` or `http_client.py` was touched
- [ ] No debug `print()` left in hot paths without `debug` guard
- [ ] Cache-Control headers intact if `web_gui.py` was touched
