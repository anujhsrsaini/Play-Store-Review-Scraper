---
_harness_template: "CLAUDE.md.template"
_harness_version: "4.15.0"
---

# CLAUDE.md - Claude Code Instructions

> **Project**: Play-Store-Review-Scraper
> **Created**: 2026-06-08
> **Setup locale**: en

---

## Read This First

Read `AGENTS.md` before starting work.

- Development flow overview
- Role boundaries
- Prohibited actions

This file contains only Claude Code-specific instructions.

---

## 0. Project Overview

Python tool that scrapes Google Play Store app reviews via `google-play-scraper`.
The entry point is `fetch_reviews.py`, which searches the top apps in a target
country (default: India), fetches app metadata, and writes reviews to per-app
CSV files.

Run it with:

```bash
python fetch_reviews.py
```

Dependencies are pinned in `requirements.txt` (install into a venv).

---

## 1. Claude Code Scope

### Work You Own

- Implement changes that span 4+ files or more than 100 lines
- Commit and push scoped changes
- Confirm CI is green, with up to 3 automatic fix attempts

### Work You Must Not Do

- Do not work outside the requested scope.
- Do not change security settings unless explicitly requested.
- Do not commit scraped CSV output or secrets.

---

## 2. Commit Message Convention

```text
feat: add a new feature
fix: fix a bug
docs: update documentation
refactor: refactor code
test: add or update tests
chore: maintenance work
```

Example: `feat: add CSV export for app metadata`

---

## 3. CI Failure Handling

### Flow

1. Detect the CI failure.
2. Read the error log.
3. Fix -> commit -> rerun CI.
4. After 3 failed attempts, stop and escalate with a summary.

---

## 4. Session Routine

### At Session Start

```bash
git status -sb
cat Plans.md
head -50 AGENTS.md
```

You can also type "session start" to trigger the session skill.

### At Completion

```bash
git add -A
git commit -m "feat: [change summary]"
git push
```

---

## 5. Available Commands

| Command | Purpose |
|---------|---------|
| `/sync-status` | Inspect status and propose next actions |
| `/work` | Execute tasks and update work status |

---

## 6. Troubleshooting

| Symptom | Action |
|---------|--------|
| Task not found | Check `Plans.md` |
| CI keeps failing | Try 3 fixes, then escalate |
| Scope is unclear | Ask for clarification |

---

*Use this file together with `AGENTS.md`.*
