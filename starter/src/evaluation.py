"""Evaluate retrieved evidence and preserve per-query traces."""
from typing import Any


def _as_list(value: Any) -> list[Any]:
    """Normalize a possibly-scalar or None value into a list."""
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _hit_matches_gold(
    hit: dict[str, Any],
    gold: dict[str, Any],
) -> bool:
    """Check whether a hit covers a gold evidence item.

    A hit is considered a match when its source and page match a gold
    item, and the gold text (if provided) is contained in the hit text.
    Accepts any documented alternative gold evidence, i.e. gold may be
    a list of candidates; matching any one is enough.
    """
    hit_source = hit.get("source")
    hit_page = hit.get("page") or hit.get("page_number")
    hit_text = (hit.get("text") or "").strip()

    gold_source = gold.get("source")
    gold_page = gold.get("page") or gold.get("page_number")
    gold_text = (gold.get("text") or "").strip()

    if gold_source is not None and hit_source != gold_source:
        return False
    if gold_page is not None and hit_page != gold_page:
        return False
    # Nếu gold có text, yêu cầu hit chứa đoạn text đó.
    # Không dùng exact string match để suy ra nhãn grounding hay coherence.
    if gold_text and gold_text not in hit_text:
        return False
    return True


def evaluate_query(
    question: dict[str, Any],
    hits: list[dict[str, Any]],
) -> dict[str, Any]:
    """Check source/page and enough gold evidence; keep hit@1 and hit@3 trace.

    Accept any documented alternative gold evidence. Do not infer human
    grounding or chunk coherence labels from exact string match.
    """
    gold_items = _as_list(question.get("gold_evidence"))
    # Hỗ trợ trường hợp gold evidence được khai báo dưới tên khác
    if not gold_items:
        gold_items = _as_list(question.get("evidence")) or _as_list(
            question.get("gold")
        )

    top1 = hits[:1]
    top3 = hits[:3]

    def _any_hit_matches(candidates: list[dict[str, Any]]) -> bool:
        for hit in candidates:
            for gold in gold_items:
                if _hit_matches_gold(hit, gold):
                    return True
        return False

    hit_at_1 = _any_hit_matches(top1)
    hit_at_3 = _any_hit_matches(top3)

    # Đếm số gold item được phủ bởi top-3, để biết "đủ bằng chứng" hay chưa
    covered_gold = 0
    for gold in gold_items:
        if any(_hit_matches_gold(hit, gold) for hit in top3):
            covered_gold += 1
    enough_evidence = covered_gold == len(gold_items) and len(gold_items) > 0

    trace = {
        "query_id": question.get("id") or question.get("query_id"),
        "question": question.get("question") or question.get("text"),
        "split": question.get("split", "unknown"),
        "strategy": question.get("strategy", "unknown"),
        "gold_count": len(gold_items),
        "covered_gold": covered_gold,
        "hit_at_1": hit_at_1,
        "hit_at_3": hit_at_3,
        "enough_evidence": enough_evidence,
        "top1_sources": [
            {
                "source": h.get("source"),
                "page": h.get("page") or h.get("page_number"),
            }
            for h in top1
        ],
        "top3_sources": [
            {
                "source": h.get("source"),
                "page": h.get("page") or h.get("page_number"),
            }
            for h in top3
        ],
    }
    return trace


def summarize(traces: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate Hit@1 and Evidence Hit@3 by strategy and dev/test split."""
    if not traces:
        return {"overall": {}, "by_strategy": {}, "by_split": {}}

    def _aggregate(items: list[dict[str, Any]]) -> dict[str, Any]:
        n = len(items)
        if n == 0:
            return {"count": 0, "hit_at_1": 0.0, "hit_at_3": 0.0,
                    "evidence_hit_at_3": 0.0}
        return {
            "count": n,
            "hit_at_1": sum(1 for t in items if t["hit_at_1"]) / n,
            "hit_at_3": sum(1 for t in items if t["hit_at_3"]) / n,
            # Evidence Hit@3: top-3 phủ đủ toàn bộ gold evidence
            "evidence_hit_at_3": sum(
                1 for t in items if t["enough_evidence"]
            ) / n,
        }

    by_strategy: dict[str, list[dict[str, Any]]] = {}
    by_split: dict[str, list[dict[str, Any]]] = {}
    by_strategy_split: dict[str, dict[str, list[dict[str, Any]]]] = {}

    for t in traces:
        s = t.get("strategy", "unknown")
        sp = t.get("split", "unknown")
        by_strategy.setdefault(s, []).append(t)
        by_split.setdefault(sp, []).append(t)
        by_strategy_split.setdefault(s, {}).setdefault(sp, []).append(t)

    return {
        "overall": _aggregate(traces),
        "by_strategy": {
            s: _aggregate(items) for s, items in by_strategy.items()
        },
        "by_split": {
            sp: _aggregate(items) for sp, items in by_split.items()
        },
        "by_strategy_split": {
            s: {
                sp: _aggregate(items)
                for sp, items in splits.items()
            }
            for s, splits in by_strategy_split.items()
        },
    }