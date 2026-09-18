"""Read-only calculations over a single investigation's captured evidence."""

import copy
import hashlib
import json
import math
import re

from pydantic import JsonValue


def _digest(value: JsonValue) -> str:
    return hashlib.sha256(
        json.dumps(
            value, ensure_ascii=False, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()


def _equal(left: JsonValue, right: JsonValue) -> bool:
    # Python otherwise considers True and 1 equal, including inside collections.
    if isinstance(left, bool) != isinstance(right, bool):
        return False
    if isinstance(left, dict) and isinstance(right, dict):
        return left.keys() == right.keys() and all(
            _equal(v, right[k]) for k, v in left.items()
        )
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(
            _equal(a, b) for a, b in zip(left, right, strict=True)
        )
    return left == right


_MISSING = object()


def _at_pointer(value: JsonValue, pointer: str) -> JsonValue | object:
    if pointer == "":
        return value
    if not pointer.startswith("/") or re.search(r"~(?![01])", pointer):
        raise ValueError("Invalid JSON pointer")
    for token in pointer[1:].split("/"):
        token = token.replace("~1", "/").replace("~0", "~")
        if isinstance(value, dict) and token in value:
            value = value[token]
        elif isinstance(value, list) and re.fullmatch(r"0|[1-9][0-9]*", token):
            index = int(token)
            if index >= len(value):
                return _MISSING
            value = value[index]
        else:
            return _MISSING
    return value


class EvidenceChecks:
    """No literal operands, external reads, writes, or cross-trace access."""

    def __init__(self, scope: str, records: list[dict], max_checks: int = 8):
        if not scope or type(max_checks) is not int or max_checks < 1:
            raise ValueError("Invalid evidence scope or budget")
        self.scope = scope
        self.max_checks = max_checks
        self._records = {}
        self._receipts = {}
        for record in records:
            source_id = record.get("id")
            if (
                not isinstance(source_id, str)
                or not source_id
                or source_id in self._records
                or "value" not in record
            ):
                raise ValueError("Invalid evidence inventory")
            self._records[source_id] = {
                **copy.deepcopy(record),
                "digest": _digest(record["value"]),
            }

    @property
    def receipts(self) -> list[dict]:
        return copy.deepcopy(list(self._receipts.values()))

    def execute(self, *, scope: str, operation: str, operands: list[dict]) -> dict:
        if scope != self.scope:
            raise ValueError("Evidence scope mismatch")
        if operation not in {"read", "collect", "equal", "set_difference", "sum"}:
            raise ValueError("Unsupported evidence operation")
        count = 2 if operation in {"equal", "set_difference"} else 1
        if not isinstance(operands, list) or (
            not 1 <= len(operands) <= 256
            if operation == "collect"
            else len(operands) != count
        ):
            raise ValueError("Incorrect operand count")
        for ref in operands:
            if (
                not isinstance(ref, dict)
                or set(ref) != {"id", "pointer"}
                or not all(isinstance(v, str) for v in ref.values())
            ):
                raise ValueError("Expected source reference, not a literal operand")
        key = _digest({"operation": operation, "operands": operands, "scope": scope})
        if key in self._receipts:
            return copy.deepcopy(self._receipts[key])
        if len(self._receipts) >= self.max_checks:
            return {"status": "budget_exhausted", "scope": scope}
        resolved = []
        for ref in operands:
            source = self._records.get(ref["id"])
            value = _at_pointer(source["value"], ref["pointer"]) if source else _MISSING
            item = {"status": "unavailable", "ref": ref}
            if value is not _MISSING:
                item = {
                    "status": "observed",
                    "ref": ref,
                    "value": value,
                    "source_digest": source["digest"],
                }
                if "provenance" in source:
                    item["provenance"] = source["provenance"]
            resolved.append(item)
        result = {"status": "unavailable"}
        if all(item["status"] == "observed" for item in resolved):
            values = [item["value"] for item in resolved]
            result = self._calculate(operation, values)
        receipt = {
            **result,
            "scope": scope,
            "operation": operation,
            "operands": resolved,
            "request_digest": key,
            "limitation": "A comparison result does not establish task failure or source completeness.",
        }
        if result["status"] == "observed":
            result_id = f"computed:{key}"
            if result_id in self._records:
                raise ValueError("Computed evidence ID collides with source record")
            provenance = [
                {
                    **item["ref"],
                    "source_digest": item["source_digest"],
                    **({"parents": item["provenance"]} if "provenance" in item else {}),
                }
                for item in resolved
            ]
            receipt.update(
                result_id=result_id,
                provenance=provenance,
                completeness="not_established",
            )
            self._records[result_id] = {
                "value": copy.deepcopy(result["value"]),
                "digest": _digest(result["value"]),
                "provenance": provenance,
            }
        self._receipts[key] = copy.deepcopy(receipt)
        return copy.deepcopy(receipt)

    @staticmethod
    def _calculate(operation: str, values: list[JsonValue]) -> dict:
        if operation == "read":
            value = values[0]
        elif operation == "collect":
            value = values
        elif operation == "equal":
            value = _equal(*values)
        elif operation == "set_difference":
            if not all(isinstance(v, list) for v in values):
                return {
                    "status": "incompatible",
                    "reason": "Set operands must be arrays",
                }
            value = []
            for item in values[0]:
                if not any(_equal(item, other) for other in values[1] + value):
                    value.append(item)
        else:
            numbers = values[0]
            if not isinstance(numbers, list) or not all(
                type(n) in (int, float) and math.isfinite(n) for n in numbers
            ):
                return {
                    "status": "incompatible",
                    "reason": "Sum requires finite numeric values and result",
                }
            value = sum(numbers)
            if not math.isfinite(value):
                return {
                    "status": "incompatible",
                    "reason": "Sum requires finite numeric values and result",
                }
        return {"status": "observed", "value": value}
