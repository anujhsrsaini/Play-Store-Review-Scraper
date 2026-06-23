---
_harness_template: "AGENTS.md.template"
_harness_version: "4.15.0"
---

# AGENTS.md - Development Flow Overview

> **Project**: Play-Store-Review-Scraper
> **Created**: 2026-06-08
> **Setup locale**: en

---

## 0. Project Overview

Python tool that scrapes Google Play Store app reviews via `google-play-scraper`.
Entry point: `fetch_reviews.py`. Output: per-app review CSV files.

---

## 1. File Map

| File | Purpose | Readers |
|------|---------|---------|
| `AGENTS.md` | Shared development flow overview | All agents |
| `CLAUDE.md` | Claude Code-specific instructions | Claude Code |
| `Plans.md` | Task tracking | All agents |

---

## 2. Task Tracking (`Plans.md`)

### Status Markers

These markers are internal protocol values. Keep them as written unless the
project explicitly adds tested aliases.

| Marker | Meaning | Set by |
|--------|---------|--------|
| `pm:requested` | Work requested | PM |
| `cc:todo` | Not started by Claude Code | Either agent |
| `cc:wip` | Claude Code is working | Claude Code |
| `cc:done` | Claude Code completed the work | Claude Code |
| `pm:approved` | Completion confirmed | PM |
| `blocked` | Blocked | Either agent |

### State Flow

```
pm:requested -> cc:wip -> cc:done -> pm:approved
```

---

## 3. Commit Message Convention

```text
feat: add a new feature
fix: fix a bug
docs: update documentation
refactor: refactor code
test: add or update tests
chore: maintenance work
```

---

## 4. Prohibited Actions

- Do not force-push (`--force` / `--force-with-lease`)
- Do not commit secrets or scraped CSV output
- Do not change security-sensitive settings unless explicitly requested

> **Note**: This is a solo project, so direct pushes to `main` are allowed.

---

## 5. Troubleshooting

| Symptom | Action |
|---------|--------|
| CI keeps failing | Try 3 fixes, then escalate |
| Task scope is unclear | Ask for clarification |
| `Plans.md` is out of date | Run `/sync-status` |

---

*All agents may read this document.*
