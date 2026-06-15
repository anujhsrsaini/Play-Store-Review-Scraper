"""Analysis engine pieces that do NOT require an LLM.

- Star-based sentiment over the FULL corpus (free, deterministic — spec §4.3: never pay
  the LLM for percentages, and never compute the headline number from a sample).
- Context curation: most-recent + most-helpful + star-stratified sample under a token
  budget (spec §4.3), with PII never entering the prompt.
- Quote verification: every quote the LLM returns must be a real (normalized) substring
  of the review it cites, or it is dropped (spec §4.3, non-negotiable).
- A stub analyzer (n-gram themes by star band) used when no GEMINI_API_KEY is set, so
  the whole flow is testable without spending money.
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

APPROX_CHARS_PER_TOKEN = 4
DEFAULT_CONTEXT_TOKEN_BUDGET = 40_000  # spec §4.3
_DELIMITER_RE = re.compile(r"<{3,}|>{3,}")  # strip prompt-delimiter look-alikes
_WORD_RE = re.compile(r"[a-z']+")
_STOPWORDS = frozenset(
    """a an and are as at be but by for from has have i if in is it its me my not of on or
    so that the this to was we when which with you your app very really just dont don't im
    i'm too can cant can't will would get got also there their they them than then he she
    his her had do does did been being only even much more most some all no yes out up
    after before now one two it's""".split()
)


def normalize_question(question: str) -> str:
    return re.sub(r"\s+", " ", question.strip().lower()).rstrip("?.! ")


def question_hash(question: str) -> str:
    return hashlib.sha256(normalize_question(question).encode("utf-8")).hexdigest()


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def star_sentiment(scores: Iterable[int | None]) -> dict[str, Any]:
    """Sentiment split from star ratings over the full corpus. 1–2★ neg, 3★ neu, 4–5★ pos."""
    pos = neg = neu = 0
    for score in scores:
        if score is None:
            continue
        if score >= 4:
            pos += 1
        elif score <= 2:
            neg += 1
        else:
            neu += 1
    total = pos + neg + neu

    def pct(n: int) -> float:
        return round(100 * n / total, 1) if total else 0.0

    return {
        "positive_pct": pct(pos),
        "neutral_pct": pct(neu),
        "negative_pct": pct(neg),
        "counted": total,
        "source": "star_ratings",
    }


def curate_reviews(
    reviews: Sequence[Mapping[str, Any]],
    *,
    token_budget: int = DEFAULT_CONTEXT_TOKEN_BUDGET,
    recent_n: int = 150,
    helpful_n: int = 100,
    per_star_n: int = 40,
) -> list[Mapping[str, Any]]:
    """Stratified sample (spec §4.3): recent + most-helpful + per-star buckets,
    de-duplicated, capped by an approximate token budget. Order: recency first."""
    with_text = [r for r in reviews if (r.get("text") or "").strip()]
    by_recency = sorted(with_text, key=lambda r: r.get("created_at") or "", reverse=True)
    by_thumbs = sorted(with_text, key=lambda r: r.get("thumbs_up") or 0, reverse=True)

    chosen: dict[str, Mapping[str, Any]] = {}

    def take(pool: Iterable[Mapping[str, Any]], n: int) -> None:
        added = 0
        for r in pool:
            rid = str(r.get("review_id") or "")
            if not rid or rid in chosen:
                continue
            chosen[rid] = r
            added += 1
            if added >= n:
                break

    take(by_recency, recent_n)
    take(by_thumbs, helpful_n)
    for star in (1, 2, 3, 4, 5):
        take((r for r in by_recency if r.get("score") == star), per_star_n)

    budget_chars = token_budget * APPROX_CHARS_PER_TOKEN
    out: list[Mapping[str, Any]] = []
    used = 0
    for r in sorted(chosen.values(), key=lambda r: r.get("created_at") or "", reverse=True):
        cost = len(r.get("text") or "") + 48  # line overhead
        if used + cost > budget_chars:
            break
        out.append(r)
        used += cost
    return out


def format_review_lines(curated: list[Mapping[str, Any]]) -> str:
    """Render reviews as prompt lines. PII is structurally absent (no name fields), and
    delimiter look-alikes are stripped so review text cannot forge prompt boundaries."""
    lines = []
    for r in curated:
        text = _DELIMITER_RE.sub(" ", (r.get("text") or "").replace("\n", " ")).strip()
        date = (r.get("created_at") or "")[:10] or "?"
        version = r.get("app_version") or "?"
        lines.append(
            f"[id={r.get('review_id')} | ★{r.get('score') or '?'} | {date} | v{version}] {text}"
        )
    return "\n".join(lines)


MIN_QUOTE_CHARS = 8  # reject empty/trivial quotes ("" is a substring of everything)
MAX_SUMMARY_CHARS = 2000
MAX_LABEL_CHARS = 200
MAX_CAVEAT_CHARS = 300
MAX_QUOTE_CHARS = 600


def verify_quotes(payload: dict[str, Any], text_by_id: Mapping[str, str]) -> dict[str, Any]:
    """Drop fabricated quotes/ids; record how many were dropped (spec §4.3)."""
    verified: list[dict[str, Any]] = []
    dropped = 0
    for quote in payload.get("supporting_quotes") or []:
        rid = str(quote.get("id") or "")
        source = text_by_id.get(rid)
        norm = normalize_text(str(quote.get("quote") or ""))
        # Require a real, non-trivial substring match (empty/1-char quotes can't "verify").
        if source and len(norm) >= MIN_QUOTE_CHARS and norm in normalize_text(source):
            verified.append(quote)
        else:
            dropped += 1
    kept_ids = {str(q.get("id")) for q in verified}
    themes = []
    for theme in payload.get("themes") or []:
        ids = [i for i in (theme.get("supporting_quote_ids") or []) if str(i) in kept_ids]
        themes.append({**theme, "supporting_quote_ids": ids})
    out = {**payload, "supporting_quotes": verified, "themes": themes}
    if dropped:
        out["caveats"] = [
            *(payload.get("caveats") or []),
            f"{dropped} unverifiable quote(s) removed.",
        ]
    return out


def _clip(value: Any, limit: int) -> Any:
    return value[:limit] if isinstance(value, str) else value


def clamp_answer(payload: dict[str, Any]) -> dict[str, Any]:
    """Bound free LLM text lengths before storage/render — limits content-injection blast
    radius on the public share page (the question can steer these fields). XSS itself is
    blocked by React escaping; this caps abusive/long injected text."""
    payload["summary"] = _clip(payload.get("summary", ""), MAX_SUMMARY_CHARS)
    payload["caveats"] = [_clip(c, MAX_CAVEAT_CHARS) for c in (payload.get("caveats") or [])]
    for theme in payload.get("themes") or []:
        if isinstance(theme, dict):
            theme["label"] = _clip(theme.get("label", ""), MAX_LABEL_CHARS)
    for q in payload.get("supporting_quotes") or []:
        if isinstance(q, dict):
            q["quote"] = _clip(q.get("quote", ""), MAX_QUOTE_CHARS)
    return payload


def _top_ngrams(texts: list[str], n_terms: int = 8) -> list[str]:
    counts: Counter[str] = Counter()
    for text in texts:
        words = [w for w in _WORD_RE.findall(text.lower()) if w not in _STOPWORDS and len(w) > 2]
        counts.update(words)
        counts.update(" ".join(p) for p in zip(words, words[1:], strict=False))
    return [term for term, c in counts.most_common(n_terms) if c >= 2]


def stub_analysis(question: str, curated: list[Mapping[str, Any]]) -> dict[str, Any]:
    """No-LLM fallback: keyword themes by star band + real quotes. Clearly labeled."""
    neg = [r for r in curated if (r.get("score") or 3) <= 2]
    pos = [r for r in curated if (r.get("score") or 3) >= 4]

    def theme_block(rs: list[Mapping[str, Any]], polarity: str) -> list[dict[str, Any]]:
        terms = _top_ngrams([r.get("text") or "" for r in rs])
        if not terms:
            return []
        return [
            {
                "label": f"frequent {polarity} terms: " + ", ".join(terms[:6]),
                "polarity": polarity,
                "prevalence": "high" if len(rs) > len(curated) / 3 else "medium",
                "supporting_quote_ids": [str(r.get("review_id")) for r in rs[:2]],
            }
        ]

    quotes = [
        {
            "id": str(r.get("review_id")),
            "quote": (r.get("text") or "")[:200],
            "stars": r.get("score"),
            "date": (r.get("created_at") or "")[:10],
        }
        for r in (neg[:2] + pos[:2])
    ]
    return {
        "summary": (
            f"Keyword-level snapshot for: “{question}”. Of {len(curated)} sampled reviews, "
            f"{len(neg)} are negative (1–2★) and {len(pos)} positive (4–5★). "
            "Set GEMINI_API_KEY for a full LLM analysis."
        ),
        "not_enough_data": len(curated) == 0,
        "themes": theme_block(neg, "negative") + theme_block(pos, "positive"),
        "supporting_quotes": quotes,
        "caveats": ["Generated WITHOUT an LLM (no GEMINI_API_KEY set) — keyword frequency only."],
    }
