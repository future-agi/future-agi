"""Fail-closed component boundaries and stack-safe expression text (test only)."""

import ast
import hashlib


def scan_safely(scan, failure):
    """Keep yielded evidence; a failed constructor/iterator adds a rejection.

    Exception includes parser errors, recursion failures and synthetic MemoryError.
    Process-control BaseExceptions are deliberately not converted to admission.
    No exception text/source contents enter the diagnostic.
    """
    try:
        yield from scan()
    except Exception as exc:
        yield failure(exc)


def unscannable(exc):
    line = getattr(exc, "lineno", None) or 1
    node = ast.Constant(
        value=None, lineno=line, col_offset=0, end_lineno=line, end_col_offset=0
    )
    node.unscannable = type(exc).__name__
    return node, "unscannable: " + node.unscannable


class StackSafeText:
    """Unparser spelling/precedence, with an explicit expression worklist.

    CPython's formatter only visits bounded-height chunks. Its traversal frontier
    emits (node, precedence) tasks instead of recursing into deep children. This
    retains exact shallow and deep unary/attribute/call spellings, without changing
    the process recursion limit. Deep f-string formatting has a separate recursive
    implementation in CPython: retain bounded legacy formatting when possible,
    otherwise use an opaque structural key, never an arbitrary-depth unparse.
    Keys exclude locations so equivalent expressions retain identical alias keys.
    Diagnostics append exact original coordinates to that deterministic excerpt.
    """

    CHUNK_HEIGHT = 24

    def __init__(self):
        self.heights = {}
        self.fingerprints = {}
        self.opaque_nodes = set()
        self.cache = {}

    def measure(self, tree):
        pending = [(tree, False)]
        while pending:
            node, ready = pending.pop()
            if node in self.heights:
                continue
            if not ready:
                pending.append((node, True))
                pending.extend((child, False) for child in ast.iter_child_nodes(node))
            else:
                self.heights[node] = 1 + max(
                    (self.heights[child] for child in ast.iter_child_nodes(node)),
                    default=0,
                )

    def opaque(self, tree):
        self.opaque_nodes.add(tree)
        pending = [(tree, False)]
        while pending:
            node, ready = pending.pop()
            if node in self.fingerprints:
                continue
            if not ready:
                pending.append((node, True))
                pending.extend((child, False) for child in ast.iter_child_nodes(node))
                continue
            digest = hashlib.sha256(type(node).__name__.encode())
            for field, value in ast.iter_fields(node):
                digest.update(field.encode())
                values = value if isinstance(value, list) else [value]
                for item in values:
                    token = (
                        self.fingerprints[item]
                        if isinstance(item, ast.AST)
                        else repr(item)
                    )
                    digest.update(str(len(token)).encode() + b":" + token.encode())
            self.fingerprints[node] = digest.hexdigest()
        return "<" + type(tree).__name__ + ":" + self.fingerprints[tree] + ">"

    def text(self, tree):
        if tree in self.cache:
            return self.cache[tree]
        if isinstance(tree, ast.Name):
            return tree.id
        # AST walks encounter the longest attribute chain first. Populate all its
        # prefixes once; rendering each prefix independently would be quadratic
        # work on top of the unavoidable total size of the string path keys.
        if isinstance(tree, ast.Attribute):
            chain, base = [], tree
            while isinstance(base, ast.Attribute) and base not in self.cache:
                chain.append(base)
                base = base.value
            if isinstance(base, ast.Name) or (
                isinstance(base, ast.Attribute) and base in self.cache
            ):
                result = self.cache.get(base, getattr(base, "id", ""))
                for node in reversed(chain):
                    result += "." + node.attr
                    self.cache[node] = result
                return self.cache[tree]
        self.measure(tree)
        heights, ceiling = self.heights, self.CHUNK_HEIGHT

        def fstring(node):
            # Preserve previously renderable moderate-depth f-string spelling.
            # This is a rendering bound only: all identity edges remain present.
            if heights[node] <= 256:
                try:
                    return ast.unparse(node)
                except RecursionError:
                    pass
            return self.opaque(node)

        class Chunk(ast._Unparser):
            def __init__(self, root, precedence):
                super().__init__()
                self.root = root
                self.set_precedence(precedence, root)

            def traverse(self, node):
                if isinstance(node, list):
                    for item in node:
                        self.traverse(item)
                elif heights[node] > ceiling and node is not self.root:
                    self._source.append((node, self.get_precedence(node)))
                elif heights[node] > ceiling and isinstance(
                    node, (ast.JoinedStr, ast.FormattedValue)
                ):
                    self.write(fstring(node))
                else:
                    super().traverse(node)

            def visit_FormattedValue(self, node):
                # CPython constructs type(self)() inside this formatter; shallow
                # f-strings are bounded, so use the ordinary formatter unchanged.
                self.write(ast.unparse(node))

        output = []
        pending = [(tree, ast._Precedence.TEST)]
        while pending:
            task = pending.pop()
            if isinstance(task, str):
                output.append(task)
                continue
            node, precedence = task
            chunk = Chunk(node, precedence)
            chunk.traverse(node)
            pending.extend(reversed(chunk._source))
        result = "".join(output)
        self.cache[tree] = result
        return result

    def diagnostic(self, node):
        result = self.text(node)
        # Test AST identity, never source text that could imitate an opaque key.
        # All normally rendered messages retain historical ast.unparse spelling.
        if self.opaque_nodes and any(
            part in self.opaque_nodes for part in ast.walk(node)
        ):
            return (
                result[:160] + f" [at {node.lineno}:{node.col_offset}-"
                f"{node.end_lineno}:{node.end_col_offset}]"
            )
        return result
