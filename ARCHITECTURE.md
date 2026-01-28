# Heatmap of Fascism — Architecture & Code Structure

**Status:** Canonical specification  
**Scope:** Bot, delete-runner, support tooling, CLI boundaries  
**Audience:** Maintainers, reviewers, agents

This document defines the *non‑negotiable* architecture of the Heatmap of Fascism project.
It exists to prevent regressions, ambiguity, and accidental data corruption.

---

## 0) Core Principles (Non‑Negotiable)

- Separation of concerns over convenience.
- Domain logic is **pure** (no IO, no APIs, no logging).
- Delete logic is **isolated and pausable**.
- Logs reflect **execution time**, never object creation time.
- `ox` CLI contains **zero business logic**.
- No guessing: unknown entities stay unknown.

If a change violates one of these rules, it is a **bug**.

---

## 1) High‑Level Structure

```text
hm/
  core/
  adapters/
  domain/
  support/
  utils/

support/
  support.log
  delete_runner.log
  support_state.json
  deleted_*.json
  PAUSE_DELETES
```

- `hm/` contains **all executable logic**
- top‑level `support/` contains **runtime data only** (gitignored)

---

## 2) Directory Responsibilities

### 2.1 hm/core/

```text
hm/core/
  main_loop.py
  pipeline.py
  models.py
  constants.py
  errors.py
```

**Purpose:** orchestration only.

`main_loop.py`
- polling loop
- adaptive backoff
- startup banner
- emits `START / RUNNING / CHECKS / SUMMARY`
- calls `pipeline.process_posts()`

**Must NOT**
- parse posts
- validate data
- normalize values
- talk to APIs directly

---

`pipeline.py`  
Fixed execution order:

1. ingest posts  
2. parse  
3. validate  
4. normalize  
5. decide  
6. output  

Exports:

```python
process_posts(posts) -> PipelineResult
```

`PipelineResult` is a **dataclass**, never a dict.

---

`models.py`
- Dataclasses: `ParsedPost`, `Report`, `Stats`, `PipelineResult`
- Enums: `Kind`, `Decision`, `Status`

Enums are **domain models**, not constants.

---

`constants.py`
Contains ONLY:
- regex patterns
- filenames
- numeric limits
- timeouts

No enums. No logic.

---

### 2.2 hm/adapters/

```text
hm/adapters/
  mastodon_api.py
  umap_api.py
  git_ops.py
```

**Purpose:** external systems only.

`mastodon_api.py`
- HTTP GET / POST / DELETE
- Retry‑After handling (429)
- timeouts

**Hard rule:**  
No business logic. No logging.

---

`git_ops.py`
- ONLY add/commit/push `reports.geojson`
- never pull or rebase
- logs: `OK / SKIP / ERROR`

Exports:
```python
auto_git_push_reports(reason: str) -> bool
```

---

### 2.3 hm/domain/

```text
hm/domain/
  parse_post.py
  validate.py
  normalize.py
  dedup.py
  entities.py
  decide.py
```

**Purpose:** define what a post *means*.

Rules:
- no IO
- no APIs
- no logging
- pure functions only

`decide.py` outputs:
- publish
- pending
- ignore
- reject

---

### 2.4 hm/support/

```text
hm/support/
  delete_runner.py
  check_bot.py
  support_replies.py
  state.py
  audit.py
```

**Purpose:** operational tooling.

---

`delete_runner.py`
- processes audit files (`deleted_*.json`)
- deletes posts
- handles 429 / Retry‑After
- verifies ownership
- respects kill‑switch

**Kill switch**
```text
support/PAUSE_DELETES
```

If present → exit cleanly.

Persistent state (`support_state.json`):
- deleted_ids
- fail_ids
- deleted_total
- audit_name
- last_action_ts

Logs to:
```text
support/delete_runner.log
```

**Important:**  
Delete logs reflect *deletion time*, never `created_at`.

---

`check_bot.py`
- produces a 60‑minute operational summary from:
  - `bot.launchd.log`
  - `delete_runner.log`
  - latest audit files

Never reads:
- `support.log` for deletes

Exports:
```python
check(window_minutes=60)
```

---

### 2.5 hm/utils/

```text
hm/utils/
  log.py
  time.py
  files.py
  rate.py
```

Shared helpers only:
- `log.py` → formatting + rotation
- `time.py` → `now_berlin()`, `today_iso()`
- `files.py` → backups + atomic writes
- `rate.py` → rate counters

---

## 3) Runtime Files (top‑level support/)

```text
support/
  support.log
  delete_runner.log
  support_state.json
  deleted_*.json
  PAUSE_DELETES
```

Rules:
- `support.log` ≠ delete log
- `created_at` ≠ `deleted_at`

---

## 4) Logging Rules (Critical)

- Europe/Berlin timezone everywhere
- execution time only
- payload timestamps are never used for monitoring

---

## 5) ox CLI Rules

Allowed:
```bash
python -m hm.support.delete_runner
python -m hm.support.check_bot
```

Forbidden:
- heredoc Python
- parsing
- regex
- business logic

`ox` is a thin launcher. Nothing more.

---

## 6) Data Model: Sticker / Graffiti

Canonical model:
- `kind`: `"sticker"` | `"graffiti"` (exclusive)
- `category`: free string / code / name
- optional:
  - `details`
  - `entity_key`
  - `entity_display`

Rules:
- exactly one kind
- category is flexible, kind is strict

---

## 7) Migration Strategy

Step 0 — Freeze
- `touch support/PAUSE_DELETES`
- refactor only

Step A
- create `hm/`
- extract utils

Step B
- extract `MastodonClient`

Step C
- move delete runner out of `bot.py`

Step D
- stabilize `check_bot`

Step E
- extract domain + pipeline

---

## 8) Done Criteria

The system is clean when:
- `bot.py` is short and readable
- delete logic is isolated
- deletes are pausable
- logs are unambiguous
- `check_bot` reports correctly

---

This document is **canonical**.
Any deviation must update this file first.
