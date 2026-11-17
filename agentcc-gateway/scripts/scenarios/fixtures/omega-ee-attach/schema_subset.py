"""A small JSON Schema 2020-12 validator for the omega-ee-attach/v1 fixtures (standard library only).

It implements the keywords in KEYWORDS with the specification's semantics and refuses any other keyword,
so a schema can never rely on one it silently ignores. check.sh can cross-check it against the jsonschema
package (OMEGA_EE_ATTACH_JSONSCHEMA_PYTHON). Errors are (JSON pointer, keyword, message); when a oneOf or
anyOf matches no branch, every branch's errors are reported too, so a forbidden fixture can name the one
it is meant to break.
"""

import re

ANNOTATIONS = {"$schema", "$id", "title", "description", "$comment", "$defs"}
KEYWORDS = {
    "$ref",
    "type",
    "enum",
    "const",
    "required",
    "properties",
    "additionalProperties",
    "propertyNames",
    "pattern",
    "minLength",
    "maxLength",
    "minimum",
    "maximum",
    "items",
    "minItems",
    "maxItems",
    "uniqueItems",
    "oneOf",
    "anyOf",
    "allOf",
    "not",
}


def ptr(path, key):
    return path + "/" + str(key).replace("~", "~0").replace("/", "~1")


def json_equal(a, b):
    if isinstance(a, bool) or isinstance(b, bool):
        return isinstance(a, bool) and isinstance(b, bool) and a == b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return a == b
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(
            json_equal(x, y) for x, y in zip(a, b, strict=True)
        )
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(json_equal(a[k], b[k]) for k in a)
    return type(a) is type(b) and a == b


def is_type(v, t):
    if t == "null":
        return v is None
    if t == "boolean":
        return isinstance(v, bool)
    if t == "object":
        return isinstance(v, dict)
    if t == "array":
        return isinstance(v, list)
    if t == "string":
        return isinstance(v, str)
    number = isinstance(v, (int, float)) and not isinstance(v, bool)
    if t == "number":
        return number
    if t == "integer":
        return number and float(v).is_integer()
    raise ValueError(f"unknown type {t!r}")


class Schema:
    def __init__(self, root):
        self.root = root

    def resolve(self, ref):
        if not ref.startswith("#/"):
            raise ValueError(f"only local $ref is supported: {ref!r}")
        node = self.root
        for part in ref[2:].split("/"):
            node = node[part.replace("~1", "/").replace("~0", "~")]
        return node

    def errors(self, v, s, path=""):
        if s is True:
            return []
        if s is False:
            return [(path, "false", "no value is allowed here")]
        unknown = set(s) - KEYWORDS - ANNOTATIONS
        if unknown:
            raise ValueError(f"unsupported schema keywords {sorted(unknown)}")
        out = []
        if "$ref" in s:
            out += self.errors(v, self.resolve(s["$ref"]), path)
        if "type" in s:
            types = s["type"] if isinstance(s["type"], list) else [s["type"]]
            if not any(is_type(v, t) for t in types):
                out.append((path, "type", "is not of type {}".format("/".join(types))))
        if "enum" in s and not any(json_equal(v, e) for e in s["enum"]):
            out.append((path, "enum", "{!r} is not one of {!r}".format(v, s["enum"])))
        if "const" in s and not json_equal(v, s["const"]):
            out.append((path, "const", "{!r} is not {!r}".format(v, s["const"])))
        if isinstance(v, str):
            if "pattern" in s and not re.search(s["pattern"], v):
                out.append(
                    (path, "pattern", "{!r} does not match {}".format(v, s["pattern"]))
                )
            if "minLength" in s and len(v) < s["minLength"]:
                out.append((path, "minLength", f"shorter than {s['minLength']}"))
            if "maxLength" in s and len(v) > s["maxLength"]:
                out.append(
                    (
                        path,
                        "maxLength",
                        f"{len(v)} characters, more than {s['maxLength']}",
                    )
                )
        if is_type(v, "number"):
            if "minimum" in s and v < s["minimum"]:
                out.append(
                    (path, "minimum", "{!r} is below {!r}".format(v, s["minimum"]))
                )
            if "maximum" in s and v > s["maximum"]:
                out.append(
                    (path, "maximum", "{!r} is above {!r}".format(v, s["maximum"]))
                )
        if isinstance(v, dict):
            out += self._object(v, s, path)
        if isinstance(v, list):
            out += self._array(v, s, path)
        out += self._combinators(v, s, path)
        return out

    def _object(self, v, s, path):
        out = []
        for name in s.get("required", []):
            if name not in v:
                out.append((path, "required", f"{name!r} is required"))
        props = s.get("properties", {})
        for name, child in v.items():
            if name in props:
                out += self.errors(child, props[name], ptr(path, name))
            elif "additionalProperties" in s:
                extra = self.errors(child, s["additionalProperties"], ptr(path, name))
                if extra:
                    out.append(
                        (path, "additionalProperties", f"{name!r} is not allowed")
                    )
            if "propertyNames" in s and self.errors(name, s["propertyNames"], path):
                out.append(
                    (path, "propertyNames", f"property name {name!r} is not allowed")
                )
        return out

    def _array(self, v, s, path):
        out = []
        if "items" in s:
            for i, child in enumerate(v):
                out += self.errors(child, s["items"], ptr(path, i))
        if "minItems" in s and len(v) < s["minItems"]:
            out.append((path, "minItems", f"fewer than {s['minItems']} items"))
        if "maxItems" in s and len(v) > s["maxItems"]:
            out.append((path, "maxItems", f"more than {s['maxItems']} items"))
        if s.get("uniqueItems") and any(
            json_equal(v[i], v[j]) for i in range(len(v)) for j in range(i)
        ):
            out.append((path, "uniqueItems", "items are not unique"))
        return out

    def _combinators(self, v, s, path):
        out = []
        for sub in s.get("allOf", []):
            out += self.errors(v, sub, path)
        if "anyOf" in s:
            branches = [self.errors(v, sub, path) for sub in s["anyOf"]]
            if all(branches):
                out.append((path, "anyOf", "matches no branch"))
                out += [e for b in branches for e in b]
        if "oneOf" in s:
            branches = [self.errors(v, sub, path) for sub in s["oneOf"]]
            matched = sum(1 for b in branches if not b)
            if matched == 0:
                out.append((path, "oneOf", "matches no branch"))
                out += [e for b in branches for e in b]
            elif matched > 1:
                out.append((path, "oneOf", f"matches {matched} branches"))
        if "not" in s and not self.errors(v, s["not"], path):
            out.append((path, "not", "must not match the excluded form"))
        return out
