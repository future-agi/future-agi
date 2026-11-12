"""Execute a validated decision tree on one output and record the path taken.

Check nodes are deterministic and free. Judge nodes ask an LLM judge through
the ordinary evaluator (``run_eval_func``) with the node's question as the
rubric; eval nodes run another workspace eval as it scores on its own. The
walk is bounded by the definition's validated depth and judge-call budget.
"""

import json
import re
import time
import uuid

from model_hub.services.dag_eval_definition import VAR_RE, is_choice, numbers_of

MAX_CHECK_TEXT = 20000
REGEX_TIMEOUT_S = 0.5
MAX_REASON = 1000


def _text(value):
    if value is None:
        return ""
    return value if isinstance(value, str) else json.dumps(value)


def _search(pattern, text, flags):
    try:
        import regex
    except ImportError:  # pragma: no cover - regex ships with the backend lock
        return re.search(pattern, text[:2000], flags)
    return regex.search(pattern, text, flags, timeout=REGEX_TIMEOUT_S)


def _json_at(obj, path):
    cur = obj
    for part in [
        p for p in re.split(r"\.|\[(\d+)\]", re.sub(r"^\$\.?", "", path)) if p
    ]:
        if isinstance(cur, list) and part.isdigit() and int(part) < len(cur):
            cur = cur[int(part)]
        elif isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return False, None
    return True, cur


def run_check(check, output, values):
    """One deterministic check: {passed: bool|None, reason, skipped?, match?}."""
    on = check.get("on") or (
        "tool_calls" if check["type"] == "tool_called" else "output"
    )
    text = _text(output if on == "output" else values.get(on))
    # Dataset cells can exceed the typed-input limit; never score a prefix.
    if len(text) > MAX_CHECK_TEXT:
        reason = f"{on} exceeds the {MAX_CHECK_TEXT}-character check limit"
        return {"passed": None, "skipped": reason, "reason": reason}
    sensitive = bool(check.get("case_sensitive"))

    def fold(value):
        return value if sensitive else value.lower()

    kind = check["type"]
    if kind in ("contains", "not_contains"):
        needle = check.get("value", "")
        has = fold(needle) in fold(text)
        verb = "contains" if has else "does not contain"
        return {
            "passed": has if kind == "contains" else not has,
            "reason": f'{on} {verb} "{needle}"',
        }
    if kind == "regex":
        pattern = check.get("pattern", "")
        try:
            match = _search(pattern, text, 0 if sensitive else re.IGNORECASE)
        except Exception as exc:  # noqa: BLE001 - timeout or engine refusal
            reason = f"{pattern} could not be checked ({type(exc).__name__})"
            return {"passed": None, "skipped": reason, "reason": reason}
        if match:
            return {
                "passed": True,
                "reason": f'{on} matches {pattern} ("{match.group(0)[:200]}")',
                "match": match.group(0)[:200],
            }
        return {"passed": False, "reason": f"{on} does not match {pattern}"}
    if kind == "equals_expected":
        expected = values.get("expected")
        if expected is None or not _text(expected).strip():
            return {
                "passed": None,
                "skipped": "no expected value",
                "reason": "no expected value",
            }
        same = fold(text.strip()) == fold(_text(expected).strip())
        return {
            "passed": same,
            "reason": f"{on} equals expected"
            if same
            else f"{on} differs from expected",
        }
    if kind == "json_path":
        try:
            obj = json.loads(text)
        except ValueError:
            return {"passed": False, "reason": f"{on} is not JSON"}
        found, value = _json_at(obj, check.get("path", ""))
        path = check.get("path", "")
        if check.get("op") == "equals":
            same = found and _text(value) == str(check.get("value", ""))
            return {
                "passed": same,
                "reason": f"{path} is {_text(value)}"
                if found
                else f"{path} is missing",
            }
        return {
            "passed": found,
            "reason": f"{path} exists" if found else f"{path} is missing",
        }
    if kind == "tool_called":
        calls = output if on == "output" else values.get(on)
        if isinstance(calls, str):
            try:
                calls = json.loads(calls)
            except ValueError:
                calls = None
        if not isinstance(calls, list) or any(not isinstance(c, dict) for c in calls):
            reason = f"{on} must be a JSON array of tool calls"
            return {"passed": None, "skipped": reason, "reason": reason}
        names = [
            c.get("name")
            or c.get("tool")
            or (
                c["function"].get("name")
                if isinstance(c.get("function"), dict)
                else None
            )
            for c in calls
        ]
        called = check.get("value") in names
        verb = "was" if called else "was not"
        return {"passed": called, "reason": f"{check.get('value')} {verb} called"}
    n, length = int(check.get("n", 0)), len(text)
    passed = length >= n if check.get("op") == "gte" else length <= n
    sign = "≥" if check.get("op") == "gte" else "≤"
    return {"passed": passed, "reason": f"{on} is {length} characters ({sign} {n})"}


def judge_prompt(question):
    """The judge rubric: the question, plus the output when it does not read it."""
    keys = list(dict.fromkeys(m.group(1) for m in VAR_RE.finditer(question)))
    if "output" not in keys:
        question = f"{question}\n\nOutput:\n{{{{output}}}}"
        keys.append("output")
    return question, keys


def ask_judge(*, question, inputs, model, org, workspace, tree_name):
    """One pass/fail judge call through the ordinary evaluator."""
    from model_hub.models.choices import OwnerChoices
    from model_hub.models.evals_metric import EvalTemplate
    from model_hub.utils.scoring import score_eval_output
    from model_hub.views.utils import evals as eval_utils

    prompt, keys = judge_prompt(question)
    template = EvalTemplate(
        id=uuid.uuid4(),
        name=f"{tree_name}-question"[:2000],
        organization=org,
        workspace=workspace,
        owner=OwnerChoices.USER.value,
        eval_type="llm",
        template_type="single",
        visible_ui=False,
        criteria=prompt,
        model=model,
        output_type_normalized="pass_fail",
        pass_threshold=0.5,
        config={
            "output": "Pass/Fail",
            "eval_type_id": "CustomPromptEvaluator",
            "required_keys": keys,
            "rule_prompt": prompt,
            "system_prompt": None,
            "custom_eval": True,
            "check_internet": False,
            "few_shot_examples": [],
            "template_format": "mustache",
            "pass_threshold": 0.5,
        },
    )
    started = time.monotonic()
    result = eval_utils.run_eval_func(
        {},
        {key: _text(inputs.get(key)) for key in keys},
        template,
        org,
        model=model,
        workspace=workspace,
        source="dag_eval",
        input_data_types=dict.fromkeys(keys, "text"),
    )
    return {
        "score": score_eval_output(result.get("output"), template, default_score=None),
        "reason": _text(result.get("reason"))[:MAX_REASON],
        "judge_model": model,
        "latency_ms": round((time.monotonic() - started) * 1000),
    }


class Walk:
    """One execution; ``judge`` and ``evaluate`` are the external calls."""

    def __init__(self, dag, *, output, values, judge, evaluate, tree_model):
        self.dag = dag
        self.by_id = {n["id"]: n for n in dag["nodes"]}
        self.numbers = numbers_of(dag)
        self.values = {**values, "output": _text(output)}
        self.judge = judge
        self.evaluate = evaluate
        self.tree_model = tree_model
        self.calls = 0

    def render(self, text):
        return VAR_RE.sub(
            lambda m: _text(self.values.get(m.group(1), m.group(0))), text
        )

    def ask(self, question, model):
        self.calls += 1
        try:
            return self.judge(question=question, inputs=self.values, model=model)
        except Exception as exc:  # noqa: BLE001 - one failed call skips the row
            return {"score": None, "reason": f"the judge failed: {str(exc)[:200]}"}

    def answer(self, node):
        if node["kind"] == "check":
            result = run_check(node["check"], self.values["output"], self.values)
            if result["passed"] is None:
                return None, result["skipped"], {}
            edge = node["answers"][0 if result["passed"] else 1]
            step = {"check": node["check"], "reason": result["reason"]}
            if result.get("match"):
                step["match"] = result["match"]
            return edge, None, step
        if node["kind"] == "eval":
            self.calls += 1
            result = self.evaluate(node["template_id"], self.values)
            if result.get("passed") is None:
                return None, result.get("reason") or "not scored", {}
            edge = node["answers"][0 if result["passed"] else 1]
            return (
                edge,
                None,
                {
                    "template": result.get("name"),
                    "template_id": node["template_id"],
                    "value": result.get("score"),
                    "reason": result.get("reason", ""),
                    "latency_ms": result.get("latency_ms", 0),
                },
            )
        model = node.get("judge_model") or self.tree_model
        if is_choice(node):
            reads = []
            for option in node["options"]:
                statement = f"{node['question']}\n\nThe answer is: {self.render(option['label'])}"
                reads.append((option, self.ask(statement, model)))
            scored = [(o, r) for o, r in reads if r.get("score") is not None]
            if not scored:
                return None, reads[0][1].get("reason") or "not scored", {}
            top = max(r["score"] for _, r in scored)
            best, best_read = next((o, r) for o, r in scored if r["score"] == top)
            return (
                best,
                None,
                {
                    "question": self.render(node["question"]),
                    "reason": best_read.get("reason", ""),
                    "judge_model": model,
                    "latency_ms": sum(r.get("latency_ms", 0) for _, r in reads),
                    "options": [
                        {"label": o["label"], "value": r.get("score")} for o, r in reads
                    ],
                },
            )
        read = self.ask(node["question"], model)
        if read.get("score") is None:
            return None, read.get("reason") or "not scored", {}
        edge = node["answers"][0 if read["score"] >= 0.5 else 1]
        return (
            edge,
            None,
            {
                "question": self.render(node["question"]),
                "reason": read.get("reason", ""),
                "judge_model": model,
                "latency_ms": read.get("latency_ms", 0),
            },
        )

    def run(self, threshold):
        path, node, skip = [], self.by_id[self.dag["root"]], None
        seen = set()
        while node["kind"] != "leaf":
            number = self.numbers[node["id"]]
            if node["id"] in seen:
                skip = f"the tree loops at question {number}"
                break
            seen.add(node["id"])
            edge, why, step = self.answer(node)
            if edge is None:
                skip = f"{why} at question {number}"
                break
            path.append(
                {
                    "node_id": node["id"],
                    "n": number,
                    "kind": node["kind"],
                    "answer": edge["label"],
                    "cost_usd": None,
                    "latency_ms": 0,
                    **step,
                }
            )
            node = self.by_id[edge["next"]]
        return self.result(path, None if skip else node, skip, threshold)

    def result(self, path, leaf_node, skip, threshold):
        leaf = (
            {
                "id": leaf_node["id"],
                "label": leaf_node["label"],
                "score": leaf_node["score"],
            }
            if leaf_node
            else None
        )
        key = ">".join(
            [f"{s['node_id']}:{s['answer']}" for s in path]
            + [leaf["id"] if leaf else "skipped"]
        )
        label = " → ".join(
            [f"{s['n']} {s['answer']}" for s in path]
            + ([leaf["label"]] if leaf else [])
        )
        base = {
            "path": path,
            "path_key": key,
            "path_label": label,
            "leaf": leaf,
            "judge_calls": self.calls,
            "judge_cost_usd": None,
            "latency_ms": sum(s.get("latency_ms") or 0 for s in path),
        }
        if leaf is None:
            return {
                **base,
                "value": None,
                "passed": None,
                "score": None,
                "skipped": skip or "no score reached",
                "reason": f"Skipped · {skip or 'no score reached'}",
            }
        score = leaf["score"]
        pass_fail = self.dag["output_type"] == "pass_fail"
        passed = score >= (0.5 if pass_fail else threshold)
        return {
            **base,
            "value": (1 if passed else 0) if pass_fail else score,
            "passed": passed,
            "score": score,
            "skipped": None,
            "reason": f"Path: {label} ({score:.2f})",
        }


def execute(dag, *, output, values, threshold, judge, evaluate, tree_model):
    walk = Walk(
        dag,
        output=output,
        values=values,
        judge=judge,
        evaluate=evaluate,
        tree_model=tree_model,
    )
    return walk.run(threshold)
