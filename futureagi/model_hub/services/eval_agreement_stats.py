"""Statistics over stored verdicts; no model calls or live-definition rescoring."""

import ast
import json
import math
from collections import Counter
from decimal import Decimal


def nonblank(value):
    return isinstance(value, str) and bool(value.strip())


def scalar(value, label_type):
    if label_type == "categorical":
        selected = value.get("selected") if isinstance(value, dict) else value
        if isinstance(selected, str):
            selected = [selected]
        if not isinstance(selected, list) or len(selected) != 1:
            return None
        # Queue validation compares str(item) with configured option values.
        choice = str(selected[0])
        return choice if nonblank(choice) else None
    if label_type == "text":
        text = value.get("text") if isinstance(value, dict) else value
        return text if nonblank(text) else None
    if label_type == "thumbs_up_down":
        thumb = value.get("value") if isinstance(value, dict) else value
        return thumb if thumb in ("up", "down") else None
    number = (
        value.get("rating" if label_type == "star" else "value")
        if isinstance(value, dict)
        else value
    )
    if isinstance(number, str):
        try:
            number = float(number)
        except (ValueError, OverflowError):
            return None
    return finite(number)


def finite(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except OverflowError:
        return None


def stored_verdict(raw, numeric, allowed_choices=None):
    if not isinstance(raw, str) or len(raw) > 100000:
        return None
    literal_choice = (
        raw
        if not numeric and allowed_choices is not None and raw in allowed_choices
        else None
    )
    # Plain categorical choices may themselves look like JSON primitives.
    if not numeric and not raw.lstrip().startswith(("{", "[", '"')):
        return raw if nonblank(raw) else None
    # Dataset writers persist TextField values as plain text, JSON, or Python
    # dict literals. Decode only real literals; never repair or infer a verdict.
    try:
        value = json.loads(raw)
    except RecursionError:
        return literal_choice
    except (ValueError, TypeError):
        if raw.lstrip().startswith(("{", "[")):
            try:
                value = ast.literal_eval(raw)
            except (ValueError, SyntaxError, RecursionError):
                return literal_choice
        else:
            value = raw
    if isinstance(value, dict):
        value = value.get("score") if numeric else value.get("choice")
    if numeric:
        return finite(value)
    if literal_choice is not None:
        # Legacy text cells have no encoding discriminator. If both meanings are
        # configured options, exclude the ambiguity rather than invent a verdict.
        if nonblank(value) and value != raw and value in allowed_choices:
            return None
        return literal_choice
    if raw.lstrip().startswith('"') and (
        allowed_choices is None or value not in allowed_choices
    ):
        return None
    return value if nonblank(value) else None


def numeric_mean(values):
    if not values:
        return None
    return finite(float(sum(values) / len(values)))


def mean_absolute_difference(pairs):
    return numeric_mean(
        [abs(Decimal(str(p["judge"])) - Decimal(str(p["human"]))) for p in pairs]
    )


def consensus(scores, label_type):
    # Ordered by value_updated_at by the reader, never by notes' updated_at.
    # Only the latest vote per reviewer
    # contributes, even when that reviewer scored through several queues.
    latest = {}
    for score in scores:
        latest.setdefault(score.annotator_id, scalar(score.value, label_type))
    votes = [value for value in latest.values() if value is not None]
    if not votes:
        return None
    if label_type in {"numeric", "star"}:
        votes.sort()
        return numeric_mean(
            [
                Decimal(str(votes[(len(votes) - 1) // 2])),
                Decimal(str(votes[len(votes) // 2])),
            ]
        )
    value, n = Counter(votes).most_common(1)[0]
    return value if n > len(votes) / 2 else None


def spearman(pairs):
    def ranks(values):
        counts = Counter(values)
        rank, lookup = 1, {}
        for value in sorted(counts):
            count = counts[value]
            lookup[value] = rank + (count - 1) / 2
            rank += count
        return [lookup[value] for value in values]

    if len(pairs) < 2:
        return None
    x, y = ranks([p["judge"] for p in pairs]), ranks([p["human"] for p in pairs])
    mx, my = sum(x) / len(x), sum(y) / len(y)
    dx, dy = [v - mx for v in x], [v - my for v in y]
    denom = math.sqrt(sum(v * v for v in dx) * sum(v * v for v in dy))
    return sum(a * b for a, b in zip(dx, dy, strict=True)) / denom if denom else None


def statistics(pairs, numeric):
    n = len(pairs)
    if numeric:
        return {
            "n": n,
            "numeric": True,
            "accuracy": None,
            "kappa": None,
            "labels": [],
            "matrix": [],
            "per_label": [],
            "mad": mean_absolute_difference(pairs),
            "spearman": spearman(pairs),
        }
    labels = sorted({p[side] for p in pairs for side in ("judge", "human")})
    if len(labels) > 100:
        raise ValueError("Too many distinct verdicts for a categorical report.")
    counts = Counter((p["human"], p["judge"]) for p in pairs)
    human = Counter(p["human"] for p in pairs)
    judge = Counter(p["judge"] for p in pairs)
    accuracy = sum(p["human"] == p["judge"] for p in pairs) / n if n else None
    chance = sum(human[label] * judge[label] for label in labels) / (n * n) if n else 1
    per_label = []
    for label in labels:
        tp = counts[label, label]
        precision = tp / judge[label] if judge[label] else None
        recall = tp / human[label] if human[label] else None
        per_label.append(
            {
                "label": label,
                "precision": precision,
                "recall": recall,
                "f1": 2 * tp / (judge[label] + human[label]),
                "support": human[label],
            }
        )
    return {
        "n": n,
        "numeric": False,
        "accuracy": accuracy,
        "kappa": (accuracy - chance) / (1 - chance) if n and chance < 1 else None,
        "labels": labels,
        "matrix": [[counts[h, j] for j in labels] for h in labels],
        "per_label": per_label,
        "mad": None,
        "spearman": None,
    }
