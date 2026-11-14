"""Discover a two-level category hierarchy from lens answers (Clio-style),
or classify into a saved one.

Discovery: vectorise each unit's lens sentence (MiniLM embeddings when model
serving is up, TF-IDF otherwise), k-means into base clusters (k ~ sqrt(n)),
name each cluster with the thinking model against its nearest outside items,
then have the thinking model organise the clusters into top-level categories.
Classification: the fast model sorts units into a saved taxonomy, leaving
"none" for anything that does not fit so new categories can still emerge.
"""

from __future__ import annotations

import math
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

import numpy as np
import structlog

from ee.agenthub.insights.llm import FAST, THINKING, Spend, chat_json
from ee.agenthub.insights.summary import model_list

logger = structlog.get_logger(__name__)

SEED = 17
MAX_BASE_CLUSTERS = 30
NAMING_INSIDE = 24
NAMING_OUTSIDE = 10
CLASSIFY_BATCH = 20
NONE = "none"


@dataclass
class Item:
    key: str
    text: str


@dataclass
class Node:
    id: str
    name: str
    description: str
    level: int
    parent_id: str | None = None
    members: list[str] = field(default_factory=list)
    status: str = "active"

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "level": self.level,
            "parent_id": self.parent_id,
            "status": self.status,
        }


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


def vectorise(texts: list[str]) -> tuple[np.ndarray, str]:
    """Unit-normalised vectors and the method used."""
    try:
        from agentic_eval.core.embeddings.serving_client import serving_available

        if serving_available():
            from tracer.queries.eval_clustering import embed_texts

            vectors = np.asarray(embed_texts(texts), dtype=float)
            if vectors.ndim == 2 and len(vectors) == len(texts):
                norms = np.linalg.norm(vectors, axis=1, keepdims=True)
                return vectors / np.where(norms == 0, 1, norms), "minilm"
    except Exception:
        logger.info("insight_embeddings_unavailable_using_tfidf")
    from sklearn.decomposition import TruncatedSVD
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.preprocessing import normalize

    matrix = TfidfVectorizer(
        ngram_range=(1, 2), sublinear_tf=True, stop_words="english", min_df=1
    ).fit_transform(texts)
    components = min(64, matrix.shape[1] - 1, len(texts) - 1)
    if components >= 2:
        matrix = TruncatedSVD(n_components=components, random_state=SEED).fit_transform(
            matrix
        )
    else:
        matrix = matrix.toarray()
    return normalize(matrix), "tfidf"


def base_cluster_count(n: int) -> int:
    if n < 8:
        return 1
    return int(min(MAX_BASE_CLUSTERS, max(3, round(math.sqrt(n)))))


def kmeans(vectors: np.ndarray, k: int, min_size: int) -> np.ndarray:
    from sklearn.cluster import KMeans

    if k <= 1 or len(vectors) <= k:
        return np.zeros(len(vectors), dtype=int)
    labels = KMeans(n_clusters=k, n_init=10, random_state=SEED).fit_predict(vectors)
    # Dissolve clusters too small to describe into their nearest neighbour.
    for _ in range(3):
        sizes = np.bincount(labels, minlength=k)
        small = [c for c in range(k) if 0 < sizes[c] < min_size]
        keep = [c for c in range(k) if sizes[c] >= min_size]
        if not small or not keep:
            break
        centroids = np.stack([vectors[labels == c].mean(axis=0) for c in keep])
        for c in small:
            for i in np.where(labels == c)[0]:
                labels[i] = keep[int(np.argmax(centroids @ vectors[i]))]
    _, labels = np.unique(labels, return_inverse=True)
    return labels


_NAME_SYSTEM = (
    "You name clusters of short statements about how people use an AI agent. "
    "A good name is specific and actionable (at most 7 words, Title Case, no "
    "trailing punctuation) and would let someone sort a new statement into or "
    "out of the cluster. Never include personal data. Reply with JSON only."
)


def name_cluster(
    inside: list[str], outside: list[str], *, context: str, spend: Spend
) -> tuple[str, str]:
    prompt = (
        f"{context}\n\nStatements IN the cluster:\n"
        + "\n".join(f"- {t}" for t in inside)
        + "\n\nNearby statements NOT in the cluster (name what separates them):\n"
        + "\n".join(f"- {t}" for t in outside or ["(none)"])
        + '\n\nReturn {"name": "...", "description": "one or two sentences '
        'saying what belongs here and what does not"}. If the statements share '
        'no clear theme, use the name "Mixed".'
    )
    raw = chat_json(_NAME_SYSTEM, prompt, tier=THINKING, max_tokens=300, spend=spend)
    # A reply that isn't an object names nothing: the cluster is "Unnamed"
    # rather than a crash that retries the whole discovery at model cost.
    raw = raw if isinstance(raw, dict) else {}
    name = " ".join(str(raw.get("name", "")).split())[:80] or "Unnamed"
    description = " ".join(str(raw.get("description", "")).split())[:400]
    return name, description


def _name_all(
    groups: list[list[int]],
    items: list[Item],
    vectors: np.ndarray,
    context: str,
    spend: Spend,
) -> list[tuple[str, str]]:
    rng = np.random.default_rng(SEED)
    centroids = [vectors[g].mean(axis=0) for g in groups]

    def one(index: int):
        members = groups[index]
        chosen = (
            members
            if len(members) <= NAMING_INSIDE
            else list(rng.choice(members, NAMING_INSIDE, replace=False))
        )
        others = [i for g, group in enumerate(groups) if g != index for i in group]
        nearest = sorted(others, key=lambda i: -float(vectors[i] @ centroids[index]))[
            :NAMING_OUTSIDE
        ]
        return name_cluster(
            [items[i].text for i in chosen],
            [items[i].text for i in nearest],
            context=context,
            spend=spend,
        )

    with ThreadPoolExecutor(max_workers=6) as pool:
        return list(pool.map(one, range(len(groups))))


_ORGANISE_SYSTEM = (
    "You organise categories of how people use an AI agent into a small set of "
    "broader top-level categories for a product report. Top-level names are "
    "specific (at most 5 words, Title Case) and together cover everything. Reply "
    "with JSON only."
)


def organise(subs: list[Node], context: str, spend: Spend) -> list[Node]:
    """Group subcategories under 3-7 top-level categories (thinking model)."""
    target = top_target(len(subs))
    listing = "\n".join(
        f'- id "{s.id}": {s.name} ({len(s.members)} items): {s.description}'
        for s in subs
    )
    prompt = (
        f"{context}\n\nSubcategories:\n{listing}\n\nGroup them into about {target} "
        "top-level categories. Every subcategory id must appear in exactly one "
        "group. Only group subcategories that share a real theme a product team "
        "would act on together; a subcategory with no close sibling can be its own "
        "group. Never make a grab-bag group, and never name a group by listing its "
        'children (no "X, Y and Z"). Return {"categories": [{"name": "...", "description": "one '
        'sentence", "children": ["<id>", ...]}]}.'
    )
    raw = chat_json(
        _ORGANISE_SYSTEM, prompt, tier=THINKING, max_tokens=1500, spend=spend
    )
    by_id = {s.id: s for s in subs}
    tops: list[Node] = []
    placed: set[str] = set()
    for entry in model_list(raw.get("categories")) if isinstance(raw, dict) else []:
        if not isinstance(entry, dict):
            continue
        children = [
            c
            for c in map(str, model_list(entry.get("children")))
            if c in by_id and c not in placed
        ]
        if not children:
            continue
        top = Node(
            id=_new_id(),
            name=" ".join(str(entry.get("name", "")).split())[:80] or "Other",
            description=" ".join(str(entry.get("description", "")).split())[:400],
            level=1,
        )
        for child in children:
            by_id[child].parent_id = top.id
            top.members.extend(by_id[child].members)
            placed.add(child)
        tops.append(top)
    leftovers = [s for s in subs if s.id not in placed]
    if leftovers:
        other = Node(id=_new_id(), name="Other", description="Smaller themes.", level=1)
        for sub in leftovers:
            sub.parent_id = other.id
            other.members.extend(sub.members)
        tops.append(other)
    return tops


def top_target(n_subs: int) -> int:
    return max(3 if n_subs >= 6 else 2, min(7, round(n_subs / 2.5)))


def naming_payload(
    groups: list[list[int]], items: list[Item], vectors: np.ndarray
) -> list[dict]:
    """Per cluster: a naming sample, held-out members for the namer's
    verifier, and the nearest items outside it (for contrastive names)."""
    rng = np.random.default_rng(SEED)
    centroids = [vectors[g].mean(axis=0) for g in groups]
    clusters = []
    for index, members in enumerate(groups):
        shuffled = list(rng.permutation(members))
        # Hold back about a quarter of every cluster (2 to 8 members) so the
        # namer's blind audit always has members the name was not written from.
        held = min(8, max(2, len(members) // 4)) if len(members) >= 6 else 0
        holdout = shuffled[:held]
        sample = shuffled[held : held + NAMING_INSIDE]
        others = [i for g, group in enumerate(groups) if g != index for i in group]
        nearest = sorted(others, key=lambda i: -float(vectors[i] @ centroids[index]))[
            :NAMING_OUTSIDE
        ]
        clusters.append(
            {
                "id": f"c{index}",
                "size": len(members),
                "sample": [
                    {"key": items[i].key, "text": items[i].text} for i in sample
                ],
                "holdout": [
                    {"key": items[i].key, "text": items[i].text} for i in holdout
                ],
                "nearest_outside": [
                    {"key": items[i].key, "text": items[i].text} for i in nearest
                ],
            }
        )
    return clusters


def _named_by(
    namer, groups, items, vectors
) -> tuple[list[tuple[str, str]], list[dict], list[dict]]:
    """Names from an external namer (the Insights agent), in group order,
    plus its proposed top-level grouping and audit."""
    clusters = naming_payload(groups, items, vectors)
    reply = namer(clusters, top_target(len(groups))) or {}
    by_id = {
        str(s.get("id")): s
        for s in model_list(reply.get("subs"))
        if isinstance(s, dict)
    }
    names = []
    for index in range(len(groups)):
        sub = by_id.get(f"c{index}", {})
        name = " ".join(str(sub.get("name", "")).split())[:80] or f"Group {index + 1}"
        names.append((name, " ".join(str(sub.get("description", "")).split())[:400]))
    audit = [
        {"cluster": sid, **(sub.get("audit") or {})}
        for sid, sub in by_id.items()
        if isinstance(sub.get("audit"), dict)
    ]
    return (
        names,
        [t for t in model_list(reply.get("tops")) if isinstance(t, dict)],
        audit,
    )


def _tops_from(
    proposal: list[dict], subs: list[Node], cluster_to_sub: dict[str, Node]
) -> list[Node]:
    tops: list[Node] = []
    placed: set[str] = set()
    for entry in proposal:
        children = [
            cluster_to_sub[c]
            for c in map(str, model_list(entry.get("children")))
            if c in cluster_to_sub and cluster_to_sub[c].id not in placed
        ]
        if not children:
            continue
        top = Node(
            id=_new_id(),
            name=" ".join(str(entry.get("name", "")).split())[:80] or "Other",
            description=" ".join(str(entry.get("description", "")).split())[:400],
            level=1,
        )
        for child in children:
            child.parent_id = top.id
            top.members.extend(child.members)
            placed.add(child.id)
        tops.append(top)
    leftovers = [s for s in subs if s.id not in placed]
    if leftovers:
        other = Node(id=_new_id(), name="Other", description="Smaller themes.", level=1)
        for sub in leftovers:
            sub.parent_id = other.id
            other.members.extend(sub.members)
        tops.append(other)
    return tops


def discover(
    items: list[Item], *, context: str, spend: Spend, namer=None
) -> tuple[list[Node], list[Node], dict]:
    """Return (top-level categories, subcategories, info) for the items.

    ``namer(clusters, target_top_count)`` names and organises the clusters
    (the Insights agent); without one, the built-in model calls do it."""
    if not items:
        return [], [], {"method": "none", "k": 0}
    vectors, method = vectorise([item.text for item in items])
    n = len(items)
    k = base_cluster_count(n)
    labels = kmeans(vectors, k, min_size=3 if n < 300 else 5)
    groups = [list(np.where(labels == c)[0]) for c in range(int(labels.max()) + 1)]
    proposal, audit = None, []
    if namer is not None:
        names, proposal, audit = _named_by(namer, groups, items, vectors)
    else:
        names = _name_all(groups, items, vectors, context, spend)
    subs: list[Node] = []
    by_name: dict[str, Node] = {}
    cluster_to_sub: dict[str, Node] = {}
    for index, (group, (name, description)) in enumerate(
        zip(groups, names, strict=True)
    ):
        members = [items[i].key for i in group]
        existing = by_name.get(name.lower())
        if existing:  # two clusters the model saw as one theme
            existing.members.extend(members)
            cluster_to_sub[f"c{index}"] = existing
            continue
        node = Node(
            id=_new_id(), name=name, description=description, level=2, members=members
        )
        by_name[name.lower()] = node
        cluster_to_sub[f"c{index}"] = node
        subs.append(node)
    info = {
        "method": method,
        "k": len(groups),
        "namer": "agent" if namer else "builtin",
    }
    if audit:
        info["audit"] = audit
    if len(subs) <= 3:
        tops = []
        for sub in subs:
            top = Node(
                id=_new_id(),
                name=sub.name,
                description=sub.description,
                level=1,
                members=list(sub.members),
            )
            sub.parent_id = top.id
            tops.append(top)
    elif proposal:
        tops = _tops_from(proposal, subs, cluster_to_sub)
    else:
        tops = organise(subs, context, spend)
    return tops, subs, info


_CLASSIFY_SYSTEM = (
    "You sort short statements about an AI agent's conversations into an "
    "existing set of categories. Use a category only when its description "
    f'clearly fits; otherwise answer "{NONE}". Reply with JSON only.'
)


def classify(
    items: list[Item], subs: list[dict], *, context: str, spend: Spend
) -> dict[str, str]:
    """{item key: sub id or NONE} using the saved taxonomy (fast model)."""
    listing = "\n".join(f'- "{s["id"]}": {s["name"]}: {s["description"]}' for s in subs)
    valid = {s["id"] for s in subs}
    batches = [
        items[i : i + CLASSIFY_BATCH] for i in range(0, len(items), CLASSIFY_BATCH)
    ]

    def one(batch: list[Item]) -> dict[str, str]:
        numbered = "\n".join(
            f"{i}. {item.text}" for i, item in enumerate(batch, start=1)
        )
        prompt = (
            f"{context}\n\nCategories:\n{listing}\n\nStatements:\n{numbered}\n\n"
            'Return {"assignments": {"<statement number>": "<category id or none>"}} '
            "covering every statement."
        )
        raw = chat_json(
            _CLASSIFY_SYSTEM, prompt, tier=FAST, max_tokens=800, spend=spend
        )
        assigned = raw.get("assignments") if isinstance(raw, dict) else None
        # Anything but an object of answers leaves the batch unsorted ("none").
        assigned = assigned if isinstance(assigned, dict) else {}
        out = {}
        for i, item in enumerate(batch, start=1):
            choice = str(assigned.get(str(i), NONE)).strip().strip('"')
            out[item.key] = choice if choice in valid else NONE
        return out

    result: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=6) as pool:
        for part in pool.map(one, batches):
            result.update(part)
    return result
