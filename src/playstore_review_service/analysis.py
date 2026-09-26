"""Analysis engine pieces that do NOT require an LLM.

- Star-based sentiment over the FULL corpus (free, deterministic — spec §4.3: never pay
  the LLM for percentages, and never compute the headline number from a sample).
- Context curation: most-recent + most-helpful + star-stratified sample under a token
  budget (spec §4.3), with PII never entering the prompt.
- Quote verification: every quote the LLM returns must be a real (normalized) substring
  of the review it cites, or it is dropped (spec §4.3, non-negotiable).
- A stub analyzer (n-gram themes by star band) used when no LLM_API_KEY is set, so
  the whole flow is testable without spending money.
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime, timedelta
from typing import Any

# Review time-window options. We only analyze RECENT reviews (no lifetime view):
# 30 / 60 / 90 days, each capped at MAX_REVIEWS_PER_ANALYSIS newest reviews.
PERIOD_DAYS: dict[str, int] = {"30d": 30, "60d": 60, "90d": 90}
PERIOD_LABELS = {
    "30d": "the last 30 days",
    "60d": "the last 60 days",
    "90d": "the last 90 days",
}
DEFAULT_PERIOD = "90d"

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


def canonical_pair_hash(app_a_id: str, app_b_id: str) -> str:
    """Order-invariant hash identifying an app pair (Battle Lens dedupe key).

    ``(a, b)`` and ``(b, a)`` hash identically so the same comparison is never
    computed or cached twice. Ids are used verbatim (package ids are case-sensitive).
    """
    first, second = sorted((app_a_id, app_b_id))
    return hashlib.sha256(f"{first}\0{second}".encode()).hexdigest()


def snapshots_hash(
    app_a_id: str,
    snapshot_a_id: int,
    fetched_a_iso: str,
    app_b_id: str,
    snapshot_b_id: int,
    fetched_b_iso: str,
) -> str:
    """Order-invariant hash of the two snapshots a comparison was computed from.

    Follows the same canonical app order as :func:`canonical_pair_hash` so a
    re-scrape of either side (new snapshot id / fetch time) invalidates the cache.
    """
    pairs = sorted(
        (
            (app_a_id, snapshot_a_id, fetched_a_iso),
            (app_b_id, snapshot_b_id, fetched_b_iso),
        )
    )
    joined = "|".join(f"{app}:{sid}:{fetched}" for app, sid, fetched in pairs)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def custom_focus_hash(custom_focus: str | None) -> str:
    """Hash of the optional free-form compare focus. ``None``/blank hashes as empty,
    matching the ``ComparisonResult.custom_focus_hash`` default."""
    return hashlib.sha256(normalize_question(custom_focus or "").encode("utf-8")).hexdigest()


def lookback_cutoff_iso(lookback_days: int, now: datetime) -> str:
    """ISO cutoff ``lookback_days`` before ``now`` for Battle Lens fetches.

    ``now`` is injected for testability (mirrors :func:`period_cutoff_iso`).
    Non-positive values fall back to the default 90-day window — there is no
    unbounded option.
    """
    days = lookback_days if lookback_days > 0 else PERIOD_DAYS[DEFAULT_PERIOD]
    return (now - timedelta(days=days)).isoformat()


def normalize_text(text: str) -> str:
    cleaned = text.replace("[", "(").replace("]", ")")
    return re.sub(r"\s+", " ", cleaned.strip().lower())


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
    return _sentiment_pcts(pos, neu, neg, source="star_ratings_sample")


def average_score(scores: Iterable[int | float | None]) -> float | None:
    """Mean star rating over a corpus (e.g. the analyzed sample), or None if no usable scores."""
    vals = [float(s) for s in scores if isinstance(s, int | float) and s]
    return round(sum(vals) / len(vals), 2) if vals else None


def _sentiment_pcts(pos: int, neu: int, neg: int, *, source: str) -> dict[str, Any]:
    total = pos + neg + neu

    def pct(n: int) -> float:
        return round(100 * n / total, 1) if total else 0.0

    return {
        "positive_pct": pct(pos),
        "neutral_pct": pct(neu),
        "negative_pct": pct(neg),
        "counted": total,
        "source": source,
    }


def sentiment_from_histogram(histogram: Any) -> dict[str, Any] | None:
    """Headline sentiment from the app's LIFETIME [1★..5★] rating counts (true population),
    not the recency-biased text sample (N2). Returns None if the histogram is unusable."""
    if not (isinstance(histogram, list) and len(histogram) == 5):
        return None
    try:
        c = [int(x) for x in histogram]
    except (TypeError, ValueError):
        return None
    if sum(c) <= 0:
        return None
    return _sentiment_pcts(c[3] + c[4], c[2], c[0] + c[1], source="lifetime_histogram")


def period_cutoff_iso(period: str, now: datetime) -> str:
    """ISO cutoff for a period key. Unknown keys fall back to the default window — there is
    no unbounded/lifetime option. `now` is injected for testability."""
    days = PERIOD_DAYS.get(period) or PERIOD_DAYS[DEFAULT_PERIOD]
    return (now - timedelta(days=days)).isoformat()


def in_period(
    reviews: Sequence[Mapping[str, Any]], cutoff_iso: str | None
) -> list[Mapping[str, Any]]:
    """Reviews with created_at >= cutoff (ISO strings compare lexicographically). cutoff
    None → all. Reviews lacking a date are excluded from a specific window."""
    if cutoff_iso is None:
        return list(reviews)
    return [r for r in reviews if (r.get("created_at") or "") >= cutoff_iso]


def date_range(reviews: Sequence[Mapping[str, Any]]) -> tuple[str | None, str | None]:
    """(earliest, latest) created_at (date part) across reviews that have one."""
    dates = sorted((r.get("created_at") or "")[:10] for r in reviews if r.get("created_at"))
    return (dates[0], dates[-1]) if dates else (None, None)


def question_keywords(question: str) -> list[str]:
    """Salient content words from the question, for relevance boosting."""
    words = [
        w for w in _WORD_RE.findall((question or "").lower()) if w not in _STOPWORDS and len(w) > 2
    ]
    seen: set[str] = set()
    out: list[str] = []
    for w in words:  # de-dup, preserve order
        if w not in seen:
            seen.add(w)
            out.append(w)
    return out


def curate_reviews(
    reviews: Sequence[Mapping[str, Any]],
    *,
    question: str | None = None,
    token_budget: int = DEFAULT_CONTEXT_TOKEN_BUDGET,
    recent_n: int = 150,
    helpful_n: int = 100,
    per_star_n: int = 40,
    relevant_n: int = 120,
) -> list[Mapping[str, Any]]:
    """Stratified sample (spec §4.3): question-relevant + recent + most-helpful + per-star
    buckets, de-duplicated, capped by an approximate token budget.

    The question-relevant bucket (N3) is the cheap, no-RAG fix for the previously
    question-blind sample: reviews whose text mentions the question's keywords are pulled
    in first, ordered by how many keywords they hit then recency."""
    with_text = [r for r in reviews if (r.get("text") or "").strip()]
    by_recency = sorted(with_text, key=lambda r: r.get("created_at") or "", reverse=True)
    by_thumbs = sorted(with_text, key=lambda r: r.get("thumbs_up") or 0, reverse=True)

    chosen: dict[str, Mapping[str, Any]] = {}

    def take(pool: Iterable[Mapping[str, Any]], n: int) -> None:
        if n <= 0:
            return
        added = 0
        for r in pool:
            rid = str(r.get("review_id") or "")
            if not rid or rid in chosen:
                continue
            chosen[rid] = r
            added += 1
            if added >= n:
                break

    kws = question_keywords(question or "")
    if kws:

        def hits(r: Mapping[str, Any]) -> int:
            text = (r.get("text") or "").lower()
            return sum(1 for k in kws if k in text)

        relevant = sorted(
            (r for r in by_recency if hits(r) > 0),
            key=lambda r: (hits(r), r.get("created_at") or ""),
            reverse=True,
        )
        take(relevant, relevant_n)  # question-relevant reviews first

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
        raw_text = (r.get("text") or "").replace("\n", " ").replace("[", "(").replace("]", ")")
        text = _DELIMITER_RE.sub(" ", raw_text).strip()
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
    orphan_themes = 0
    for theme in payload.get("themes") or []:
        ids = [i for i in (theme.get("supporting_quote_ids") or []) if str(i) in kept_ids]
        if not ids:
            orphan_themes += 1  # no surviving evidence → drop, don't render as fact (N5)
            continue
        themes.append({**theme, "supporting_quote_ids": ids})
    out = {**payload, "supporting_quotes": verified, "themes": themes}
    # Relevance floor (N5): nothing survived verification → not a grounded answer.
    if not verified and not themes:
        out["not_enough_data"] = True
    caveats = list(payload.get("caveats") or [])
    if dropped:
        caveats.append(f"{dropped} unverifiable quote(s) removed.")
    if orphan_themes:
        caveats.append(f"{orphan_themes} theme(s) without supporting evidence removed.")
    out["caveats"] = caveats
    return out


def verify_comparison_quotes(
    payload: dict[str, Any],
    text_by_id_a: Mapping[str, str],
    text_by_id_b: Mapping[str, str],
    app_a_id: str = "app_a",
    app_b_id: str = "app_b",
) -> dict[str, Any]:
    """Per-side quote verification for Battle Lens (spec §4.3, non-negotiable).

    Each quote must be a real, non-trivial substring of a review from the corpus
    of its DECLARED side — a genuine quote from app A cited as side ``b`` is a
    misattribution and is dropped just like a fabrication. Themes survive only on
    surviving quote ids. When exactly one side ends up groundless, that is a
    caveat naming the app (the other side's answer still stands); when neither
    side has evidence, ``not_enough_data`` is set like :func:`verify_quotes`.
    """
    corpora = {"a": text_by_id_a, "b": text_by_id_b}
    verified: list[dict[str, Any]] = []
    dropped = 0
    for quote in payload.get("supporting_quotes") or []:
        side = quote.get("side")
        rid = str(quote.get("id") or "")
        source = corpora.get(side, {}).get(rid) if side in corpora else None
        norm = normalize_text(str(quote.get("quote") or ""))
        if source and len(norm) >= MIN_QUOTE_CHARS and norm in normalize_text(source):
            verified.append({**quote, "side": side})
        else:
            dropped += 1
    kept_ids = {str(q.get("id")) for q in verified}

    def _id_sides(rid: str) -> set[str]:
        """Which corpora contain this review id (ids are app-scoped, so usually one)."""
        sides = set()
        if rid in text_by_id_a:
            sides.add("a")
        if rid in text_by_id_b:
            sides.add("b")
        return sides

    themes = []
    orphan_themes = 0
    for theme in payload.get("themes") or []:
        ids = [i for i in (theme.get("supporting_quote_ids") or []) if str(i) in kept_ids]
        if not ids:
            orphan_themes += 1
            continue
        if str(theme.get("side")) == "both":
            # A both-sides claim needs surviving evidence from EACH corpus.
            covered: set[str] = set().union(*[_id_sides(str(i)) for i in ids])
            if covered != {"a", "b"}:
                orphan_themes += 1
                continue
        themes.append({**theme, "supporting_quote_ids": ids})
    sides_ok = {
        side
        for side in ("a", "b")
        if any(str(q.get("side")) == side for q in verified)
        or any(str(t.get("side")) in (side, "both") for t in themes)
    }
    out = {**payload, "supporting_quotes": verified, "themes": themes}
    caveats = list(payload.get("caveats") or [])
    if dropped:
        caveats.append(f"{dropped} unverifiable or misattributed quote(s) removed.")
    if orphan_themes:
        caveats.append(f"{orphan_themes} theme(s) without supporting evidence removed.")
    if not verified and not themes:
        out["not_enough_data"] = True
    elif len(sides_ok) < 2:
        # Exactly one side is grounded (empty sides_ok implies empty evidence, handled
        # above): name the missing app, the other side's answer still stands.
        for side_key in ("a", "b"):
            if side_key not in sides_ok:
                app = {"a": app_a_id, "b": app_b_id}[side_key]
                caveats.append(f"Insufficient grounded data for {app}; comparison is one-sided.")
    out["caveats"] = caveats
    return out


def stub_compare(
    app_a_id: str,
    app_b_id: str,
    curated_a: list[Mapping[str, Any]],
    curated_b: list[Mapping[str, Any]],
    custom_focus: str = "",
) -> dict[str, Any]:
    """Keyword-based Battle Lens fallback (no LLM): per-side stub analyses with the
    schema's ``side`` attribution filled in, so the shape matches the LLM path and
    the same verifier + renderer consume both."""
    question = custom_focus.strip() or f"compare {app_a_id} vs {app_b_id}"
    payload_a = stub_analysis(question, curated_a)
    payload_b = stub_analysis(question, curated_b)
    themes: list[dict[str, Any]] = []
    quotes: list[dict[str, Any]] = []
    for side, payload in (("a", payload_a), ("b", payload_b)):
        for theme in payload.get("themes") or []:
            themes.append({**theme, "side": side})
        for quote in payload.get("supporting_quotes") or []:
            quotes.append({**quote, "side": side})
    caveats = [
        *(payload_a.get("caveats") or []),
        *(payload_b.get("caveats") or []),
        "Keyword-based fallback comparison; connect an LLM provider for deeper analysis.",
    ]
    return {
        "summary": (
            f"Keyword-based comparison (no LLM). {app_a_id}: {payload_a.get('summary', '')} "
            f"{app_b_id}: {payload_b.get('summary', '')}"
        ),
        "not_enough_data": bool(payload_a.get("not_enough_data"))
        and bool(payload_b.get("not_enough_data")),
        "themes": themes,
        "supporting_quotes": quotes,
        "caveats": caveats,
    }


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
            "Set LLM_API_KEY for a full LLM analysis."
        ),
        "not_enough_data": len(curated) == 0,
        "themes": theme_block(neg, "negative") + theme_block(pos, "positive"),
        "supporting_quotes": quotes,
        "caveats": ["Generated WITHOUT an LLM (no LLM_API_KEY set) — keyword frequency only."],
    }
