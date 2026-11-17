"""Decision-tree eval definitions: normalization, validation and the outline.

A definition (schema ``fi-dag-v1``) is a rooted graph of question nodes
(``judge``, ``check``, ``eval``) whose answers lead to other nodes or to
``leaf`` scores. The server is the authority on validity: no cycles, every
answer leads somewhere, every path ends in a score, and the tree stays within
fixed size, depth and judge-call bounds so one execution has a known cost.
Messages match the editor's wording so the UI can show them verbatim.
"""

import math
import re

DAG_SCHEMA = "fi-dag-v1"
MAX_NODES = 40
MAX_DEPTH = 12
MAX_JUDGE_CALLS = 24
MAX_TEXT = 2000
MAX_LABEL = 200
MAX_PATTERN = 200
MAX_MODEL = 255
CHOICE_MIN, CHOICE_MAX = 2, 10
NODE_KINDS = ("judge", "check", "eval", "leaf")
CHECK_TYPES = (
    "contains",
    "not_contains",
    "regex",
    "equals_expected",
    "json_path",
    "tool_called",
    "length",
)
OUTPUT_TYPES = ("score", "pass_fail")
NODE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,40}$")
VAR_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]{0,63}$")
VAR_RE = re.compile(r"{{\s*([^{}]+?)\s*}}")
UUID_RE = re.compile(r"^[0-9a-fA-F-]{36}$")


class DagInvalid(Exception):
    """A definition that cannot be saved or executed; ``errors`` are verbatim."""

    def __init__(self, errors):
        super().__init__("; ".join(errors))
        self.errors = list(errors)


def edges_of(node):
    if node.get("kind") == "judge" and node.get("options"):
        return node["options"]
    return node.get("answers") or []


def is_choice(node):
    return node.get("kind") == "judge" and bool(node.get("options"))


def numbers_of(dag):
    numbers, i = {}, 0
    for node in dag.get("nodes", []):
        if node.get("kind") != "leaf":
            i += 1
            numbers[node["id"]] = i
    return numbers


def _text(value, limit):
    return value if isinstance(value, str) and len(value) <= limit else None


def _edges(raw, errors, where):
    if not isinstance(raw, list) or len(raw) > CHOICE_MAX:
        errors.append(f"{where} has answers that are not valid")
        return []
    edges = []
    for edge in raw:
        label = _text(edge.get("label"), MAX_TEXT) if isinstance(edge, dict) else None
        target = edge.get("next", "") if isinstance(edge, dict) else None
        if label is None or not isinstance(target, str) or len(target) > 40:
            errors.append(f"{where} has answers that are not valid")
            return []
        edges.append({"label": label, "next": target})
    return edges


def _check(raw, errors, where):
    if not isinstance(raw, dict) or raw.get("type") not in CHECK_TYPES:
        errors.append(f"{where} has a check that is not valid")
        return {}
    on = raw.get("on") or ("tool_calls" if raw["type"] == "tool_called" else "output")
    if not isinstance(on, str) or (on != "output" and not VAR_NAME_RE.match(on)):
        errors.append(f"{where} checks a variable that is not valid")
        on = "output"
    check = {"type": raw["type"], "on": on}
    for key, limit in (("value", MAX_TEXT), ("pattern", MAX_PATTERN), ("path", 200)):
        if raw.get(key) not in (None, ""):
            if _text(raw[key], limit) is None:
                errors.append(f"{where} has a check {key} that is too long")
            else:
                check[key] = raw[key]
    if raw.get("op") not in (None, ""):
        if raw["op"] not in ("gte", "lte", "equals", "exists"):
            errors.append(f"{where} has a check that is not valid")
        else:
            check["op"] = raw["op"]
    if raw.get("n") not in (None, ""):
        n = raw["n"]
        if isinstance(n, bool) or not isinstance(n, (int, float, str)):
            errors.append(f"{where} has a length that is not valid")
        else:
            try:
                check["n"] = int(float(n))
            except ValueError:
                errors.append(f"{where} has a length that is not valid")
    if "case_sensitive" in raw:
        check["case_sensitive"] = bool(raw["case_sensitive"])
    return check


def _node(raw, index, errors):
    where = f"Node {index + 1}"
    if not isinstance(raw, dict):
        errors.append(f"{where} is not valid")
        return None
    node_id, kind = raw.get("id"), raw.get("kind")
    if not isinstance(node_id, str) or not NODE_ID_RE.match(node_id):
        errors.append(f"{where} has an id that is not valid")
        return None
    if kind not in NODE_KINDS:
        errors.append(f"{where} has a kind that is not valid")
        return None
    node = {"id": node_id, "kind": kind}
    if kind == "leaf":
        node["label"] = _text(raw.get("label") or "", MAX_LABEL) or ""
        score = raw.get("score")
        try:
            node["score"] = (
                None if score in (None, "") or isinstance(score, bool) else float(score)
            )
        except (TypeError, ValueError):
            node["score"] = None
        return node
    if kind == "judge":
        node["question"] = _text(raw.get("question") or "", MAX_TEXT)
        if node["question"] is None:
            errors.append(f"{where} has a question that is too long")
            node["question"] = ""
        if raw.get("judge_model"):
            model = _text(raw["judge_model"], MAX_MODEL)
            if model is None:
                errors.append(f"{where} has a judge that is not valid")
            else:
                node["judge_model"] = model
        if raw.get("options"):
            node["options"] = _edges(raw["options"], errors, where)
            return node
    if kind == "check":
        node["check"] = _check(raw.get("check"), errors, where)
    if kind == "eval":
        template_id = raw.get("template_id") or ""
        if not isinstance(template_id, str) or (
            template_id and not UUID_RE.match(template_id)
        ):
            errors.append(f"{where} names an eval that is not valid")
            template_id = ""
        node["template_id"] = template_id
    node["answers"] = _edges(raw.get("answers") or [], errors, where)
    return node


def normalize(raw):
    """Keep only the known fields of a definition; DagInvalid on bad shapes."""
    if not isinstance(raw, dict) or not isinstance(raw.get("nodes"), list):
        raise DagInvalid(["A decision tree needs a list of nodes"])
    if len(raw["nodes"]) > MAX_NODES:
        raise DagInvalid([f"A decision tree has at most {MAX_NODES} nodes"])
    errors = []
    nodes = [_node(item, i, errors) for i, item in enumerate(raw["nodes"])]
    if errors:
        raise DagInvalid(list(dict.fromkeys(errors)))
    output_type = raw.get("output_type") or "score"
    if output_type not in OUTPUT_TYPES:
        raise DagInvalid(["Output type is score or pass_fail"])
    root = raw.get("root")
    return {
        "schema": DAG_SCHEMA,
        "root": root if isinstance(root, str) else "",
        "output_type": output_type,
        "nodes": nodes,
    }


def variables_of(dag):
    names = []

    def add(name):
        if name not in names:
            names.append(name)

    for node in dag.get("nodes", []):
        if node["kind"] == "judge":
            for text in [node.get("question", "")] + [
                o["label"] for o in node.get("options") or []
            ]:
                for match in VAR_RE.finditer(text):
                    add(match.group(1))
        if node["kind"] == "eval":
            for key in node.get("required_keys", []):
                add(key)
        if node["kind"] == "check":
            check = node["check"]
            on = check.get("on") or (
                "tool_calls" if check["type"] == "tool_called" else "output"
            )
            if on != "output":
                add(on)
            if node["check"].get("type") == "equals_expected":
                add("expected")
    return names


def _own_calls(node):
    if node["kind"] == "judge":
        return len(node["options"]) if is_choice(node) else 1
    return 1 if node["kind"] == "eval" else 0


def _bounds(dag, by_id):
    """Longest path (questions) and most judge calls on any path from the root."""
    memo = {}

    def walk(node_id):
        if node_id in memo:
            return memo[node_id]
        node = by_id[node_id]
        if node["kind"] == "leaf":
            memo[node_id] = (0, 0)
            return memo[node_id]
        nexts = [walk(e["next"]) for e in edges_of(node)]
        memo[node_id] = (
            1 + max(d for d, _ in nexts),
            _own_calls(node) + max(c for _, c in nexts),
        )
        return memo[node_id]

    return walk(dag["root"])


def _check_errors(node, name, errors):
    check = node["check"]
    kind = check["type"]
    if kind == "regex":
        try:
            re.compile(check.get("pattern", ""))
        except re.error:
            errors.append(f"{check.get('pattern', '')} is not a valid pattern")
    if kind in ("contains", "not_contains", "tool_called") and not check.get("value"):
        errors.append(f"{name} needs a value to check")
    if kind == "json_path" and not check.get("path"):
        errors.append(f"{name} needs a JSON path")
    if kind == "length" and (check.get("n") is None or check["n"] < 0):
        errors.append(f"{name} needs a length of 0 or more")


def validate(dag, *, tree_model=None):
    """Every reason a normalized definition cannot be saved; [] when it can."""
    errors = []

    def add(message):
        if message not in errors:
            errors.append(message)

    nodes = dag["nodes"]
    ids = [n["id"] for n in nodes]
    if len(set(ids)) != len(ids):
        add("Node ids must be unique")
    by_id = {n["id"]: n for n in nodes}
    root = by_id.get(dag["root"])
    if root is None or root["kind"] == "leaf":
        add("Start from one question")
    numbers = numbers_of(dag)

    def name(node_id):
        if node_id in numbers:
            return str(numbers[node_id])
        return by_id.get(node_id, {}).get("label") or node_id

    for node in nodes:
        if node["kind"] == "leaf":
            score = node["score"]
            if score is None or not math.isfinite(score) or not 0 <= score <= 1:
                add("Scores are between 0 and 1")
            continue
        edges = edges_of(node)
        if node["kind"] == "judge" and not node["question"].strip():
            add("Write the question")
        if is_choice(node):
            if not CHOICE_MIN <= len(edges) <= CHOICE_MAX:
                add("A choice question has 2 to 10 options")
        elif len(edges) != 2:
            add(
                "Every path must end in a score"
                if not edges
                else "A yes/no question has exactly two answers"
            )
        if any(not e["next"] or e["next"] not in by_id for e in edges):
            add(f"{name(node['id'])} has an answer that leads nowhere")
        if node["kind"] == "check":
            _check_errors(node, name(node["id"]), errors)
        if node["kind"] == "eval" and not node["template_id"]:
            add(f"{name(node['id'])} needs an eval")
        if node["kind"] == "judge" and not (node.get("judge_model") or tree_model):
            add(f"Question {name(node['id'])} needs a judge")
    state, reached = {}, set()

    def walk(node_id):
        reached.add(node_id)
        state[node_id] = "open"
        for edge in edges_of(by_id[node_id]):
            target = edge["next"]
            if target not in by_id:
                continue
            if state.get(target) == "open":
                add(f"{name(node_id)} loops back to {name(target)}")
            elif target not in state:
                walk(target)
        state[node_id] = "done"

    if root is not None:
        walk(root["id"])
        for node in nodes:
            if node["id"] not in reached:
                add(f"{name(node['id'])} can't be reached from the first question")
    if not any(n["kind"] == "leaf" for n in nodes):
        add("Every path must end in a score")
    for variable in variables_of(dag):
        if not VAR_NAME_RE.match(variable):
            add(f"{{{{{variable}}}}} is not a valid variable")
    if not errors:
        depth, calls = _bounds(dag, by_id)
        if depth > MAX_DEPTH:
            add(f"A path is at most {MAX_DEPTH} questions deep")
        if calls > MAX_JUDGE_CALLS:
            add(f"A path asks at most {MAX_JUDGE_CALLS} judge calls")
    return errors


def check_text(check):
    on = check.get("on", "output")
    kind = check.get("type")
    if kind == "regex":
        return f"{on} matches {check.get('pattern', '')}"
    if kind in ("contains", "not_contains"):
        verb = "contains" if kind == "contains" else "does not contain"
        return f'{on} {verb} "{check.get("value", "")}"'
    if kind == "json_path":
        tail = (
            f"equals {check.get('value', '')}"
            if check.get("op") == "equals"
            else "exists"
        )
        return f"{on} {check.get('path', '')} {tail}"
    if kind == "tool_called":
        return f"tool {check.get('value', '')} was called"
    if kind == "length":
        sign = "≥" if check.get("op") == "gte" else "≤"
        return f"{on} length {sign} {check.get('n', '')}"
    return f"{on} equals {{{{expected}}}}"


def outline_of(dag, eval_names=None):
    """The definition as numbered readable instructions (the template criteria)."""
    eval_names = eval_names or {}
    numbers = numbers_of(dag)
    by_id = {n["id"]: n for n in dag["nodes"]}

    def target(node_id):
        node = by_id.get(node_id)
        if node is None:
            return "?"
        if node["kind"] == "leaf":
            return f"{node['label']} ({node['score']:.2f})"
        return f"question {numbers[node_id]}"

    kind = "Pass/Fail" if dag["output_type"] == "pass_fail" else "score"
    lines = [f"Decision tree ({kind}) on {{{{output}}}}:"]
    for node in dag["nodes"]:
        if node["kind"] == "leaf":
            continue
        if node["kind"] == "judge":
            head = node["question"]
        elif node["kind"] == "check":
            head = f"Check: {check_text(node['check'])}"
        else:
            head = f"Eval: {eval_names.get(node['template_id'], node['template_id'])}"
        edges = " · ".join(
            f"{e['label']} → {target(e['next'])}" for e in edges_of(node)
        )
        lines.append(f"{numbers[node['id']]}. {head} — {edges}")
    reads = [v for v in variables_of(dag) if v != "output"]
    if reads:
        lines.append("Reads: " + " ".join(f"{{{{{v}}}}}" for v in reads))
    return "\n".join(lines)


def summary_of(dag):
    nodes = dag["nodes"]
    count = {k: sum(1 for n in nodes if n["kind"] == k) for k in NODE_KINDS}
    questions = len(nodes) - count["leaf"]
    parts = [
        f"{count['judge']} judged" if count["judge"] else None,
        f"{count['check']} check{'s' if count['check'] != 1 else ''}"
        if count["check"]
        else None,
        f"{count['eval']} eval{'s' if count['eval'] != 1 else ''}"
        if count["eval"]
        else None,
    ]
    parts = [p for p in parts if p]
    detail = f" ({', '.join(parts)})" if parts else ""
    plural = "s" if questions != 1 else ""
    leaves = "s" if count["leaf"] != 1 else ""
    return (
        f"Decision tree · {questions} question{plural}{detail} · "
        f"{count['leaf']} score{leaves}"
    )


def first_judge_of(dag):
    for node in dag.get("nodes", []):
        if node["kind"] == "judge" and node.get("judge_model"):
            return node["judge_model"]
    return None


def parse(raw, *, tree_model=None):
    """Normalize and validate; returns the definition or raises DagInvalid."""
    dag = normalize(raw)
    errors = validate(dag, tree_model=tree_model or first_judge_of(dag))
    if errors:
        raise DagInvalid(errors)
    return dag
