"""
AST extraction layer.

Walks Python source files and emits three manifests:
  ModelManifest   — Django model classes with their field constraints
  FunctionManifest — function/method signatures with required-arg flags
  CallSite        — call sites with provided kwargs and positional counts
"""

import ast
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

_UNKNOWN = object()


@dataclass
class FieldConstraint:
    name: str
    required: bool  # null=False AND no default= → must be provided in create()
    field_type: str
    null: bool = True
    blank: bool = True
    unique: bool = False
    max_length: Optional[int] = None
    min_value: Optional[int] = None  # explicit or implicit from field type
    max_value: Optional[int] = None
    choices: Optional[list] = None  # literal values only (first elem of 2-tuples)


@dataclass
class ModelManifest:
    name: str
    file: str
    line: int
    fields: list[FieldConstraint] = field(default_factory=list)

    @property
    def required_fields(self) -> list[FieldConstraint]:
        return [f for f in self.fields if f.required]


@dataclass
class ArgConstraint:
    name: str
    required: bool
    has_default: bool = False
    kwonly: bool = False  # keyword-only (after *); cannot be filled by positional args


@dataclass
class FunctionManifest:
    name: str  # qualified: ClassName.method_name or function_name
    file: str
    line: int
    module_path: str
    args: list[ArgConstraint] = field(default_factory=list)

    @property
    def required_args(self) -> list[ArgConstraint]:
        return [a for a in self.args if a.required]


@dataclass
class CallSite:
    callee_name: str
    file: str
    line: int
    provided_kwargs: set[str] = field(default_factory=set)
    kwarg_values: dict[str, object] = field(
        default_factory=dict
    )  # name → literal value
    positional_count: int = 0
    is_create_call: bool = False
    model_name: Optional[str] = None
    caller_name: Optional[str] = (
        None  # qualified name of the enclosing function, if any
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _is_django_field(node: ast.expr) -> Optional[str]:
    if not isinstance(node, ast.Call):
        return None
    func = node.func
    if isinstance(func, ast.Attribute) and func.attr.endswith("Field"):
        return func.attr
    if isinstance(func, ast.Attribute) and func.attr == "ForeignKey":
        return func.attr
    return None


def _get_kwarg(node: ast.Call, name: str) -> Optional[ast.expr]:
    for kw in node.keywords:
        if kw.arg == name:
            return kw.value
    return None


def _is_true_literal(node: ast.expr) -> bool:
    if isinstance(node, ast.Constant):
        return bool(node.value)
    return False


def _int_literal(node: ast.expr) -> Optional[int]:
    if isinstance(node, ast.Constant) and isinstance(node.value, int):
        return node.value
    return None


def _literal_value(node: ast.expr) -> object:
    """Recursively extract a Python literal value."""
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, (ast.List, ast.Tuple)):
        items = [_literal_value(e) for e in node.elts]
        return items if _UNKNOWN not in items else _UNKNOWN
    return _UNKNOWN


def _extract_choices(node: ast.expr) -> Optional[list]:
    """Extract the set of valid values from a choices= argument (literal only)."""
    if not isinstance(node, (ast.List, ast.Tuple)):
        return None
    values = []
    for elt in node.elts:
        # 2-tuple (value, display) — take the first element
            if isinstance(elt, (ast.List, ast.Tuple)) and elt.elts:
                v = _literal_value(elt.elts[0])
                if v is not _UNKNOWN:
                    values.append(v)
            else:
                v = _literal_value(elt)
                if v is not _UNKNOWN:
                    values.append(v)
    return values or None


# Implicit integer bounds by field type
_FIELD_BOUNDS: dict[str, tuple[Optional[int], Optional[int]]] = {
    "PositiveIntegerField": (0, 2_147_483_647),
    "PositiveSmallIntegerField": (0, 32_767),
    "PositiveBigIntegerField": (0, 9_223_372_036_854_775_807),
    "SmallIntegerField": (-32_768, 32_767),
    "IntegerField": (-2_147_483_648, 2_147_483_647),
    "BigIntegerField": (-9_223_372_036_854_775_808, 9_223_372_036_854_775_807),
}

_RELATION_FIELD_TYPES = frozenset(
    {
        "ManyToManyField",
        "ManyToManyRel",
        "ManyToOneRel",
        "OneToOneRel",
    }
)


def _build_field_constraint(
    field_name: str, call: ast.Call, save_assigned: bool
) -> FieldConstraint:
    """Extract all constraint metadata from a Django field declaration AST node."""
    field_type = call.func.attr if isinstance(call.func, ast.Attribute) else ""

    # Relation fields managed via .add(), not create() kwargs
    if field_type in _RELATION_FIELD_TYPES:
        return FieldConstraint(name=field_name, required=False, field_type=field_type)

    null_node = _get_kwarg(call, "null")
    blank_node = _get_kwarg(call, "blank")
    unique_node = _get_kwarg(call, "unique")

    is_null = null_node is not None and _is_true_literal(null_node)
    is_blank = blank_node is not None and _is_true_literal(blank_node)
    is_unique = unique_node is not None and _is_true_literal(unique_node)

    has_default = _get_kwarg(call, "default") is not None
    has_auto = any(
        _get_kwarg(call, kw) is not None and _is_true_literal(_get_kwarg(call, kw))
        for kw in ("auto_now", "auto_now_add")
    )

    required = (
        not is_null
        and not is_blank
        and not has_default
        and not has_auto
        and not save_assigned
    )

    # max_length
    ml_node = _get_kwarg(call, "max_length")
    max_length = _int_literal(ml_node) if ml_node is not None else None

    # min_value / max_value — explicit kwargs first, then implicit from type
    min_v_node = _get_kwarg(call, "min_value")
    max_v_node = _get_kwarg(call, "max_value")
    min_value = _int_literal(min_v_node) if min_v_node is not None else None
    max_value = _int_literal(max_v_node) if max_v_node is not None else None

    implicit = _FIELD_BOUNDS.get(field_type)
    if implicit:
        if min_value is None:
            min_value = implicit[0]
        if max_value is None:
            max_value = implicit[1]

    # choices
    ch_node = _get_kwarg(call, "choices")
    choices = _extract_choices(ch_node) if ch_node is not None else None

    return FieldConstraint(
        name=field_name,
        required=required,
        field_type=field_type,
        null=is_null,
        blank=is_blank,
        unique=is_unique,
        max_length=max_length,
        min_value=min_value,
        max_value=max_value,
        choices=choices,
    )


# ---------------------------------------------------------------------------
# Visitors
# ---------------------------------------------------------------------------


def _class_has_django_fields(node: ast.ClassDef) -> bool:
    """Heuristic: if a class body has any models.XField(...) assignments, treat it as a model.
    This catches custom base classes (BaseModel, TimestampedModel, etc.) without needing
    to resolve the inheritance chain."""
    for stmt in node.body:
        if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1:
            if _is_django_field(stmt.value):
                return True
    return False


def _save_auto_assigned(class_node: ast.ClassDef) -> set[str]:
    """Fields auto-assigned in save() via `if not self.field: self.field = ...` pattern."""
    auto: set[str] = set()

    def assigns_self_attr(statements: list[ast.stmt], attr: str) -> bool:
        for body_stmt in statements:
            targets: list[ast.expr] = []
            if isinstance(body_stmt, ast.Assign):
                targets = list(body_stmt.targets)
            elif isinstance(body_stmt, ast.AnnAssign):
                targets = [body_stmt.target]
            elif isinstance(body_stmt, ast.AugAssign):
                targets = [body_stmt.target]
            for target in targets:
                if (
                    isinstance(target, ast.Attribute)
                    and isinstance(target.value, ast.Name)
                    and target.value.id == "self"
                    and target.attr == attr
                ):
                    return True
        return False

    for item in class_node.body:
        if not isinstance(item, ast.FunctionDef) or item.name != "save":
            continue
        for stmt in ast.walk(item):
            if not isinstance(stmt, ast.If):
                continue
            test = stmt.test
            # match: `not self.field` or `if not self.field`
            negated = isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not)
            attr_test = test.operand if negated else test
            if (
                negated
                and isinstance(attr_test, ast.Attribute)
                and isinstance(attr_test.value, ast.Name)
                and attr_test.value.id == "self"
                and assigns_self_attr(stmt.body, attr_test.attr)
            ):
                auto.add(attr_test.attr)
    return auto


class _ModelVisitor(ast.NodeVisitor):
    def __init__(self, file_path: str) -> None:
        self.file = file_path
        self.models: list[ModelManifest] = []
        self._current: Optional[ModelManifest] = None
        self._auto_assigned: set[str] = set()

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        if not _class_has_django_fields(node):
            self.generic_visit(node)
            return
        manifest = ModelManifest(name=node.name, file=self.file, line=node.lineno)
        prev_model, self._current = self._current, manifest
        prev_auto, self._auto_assigned = self._auto_assigned, _save_auto_assigned(node)
        self.generic_visit(node)
        self._current = prev_model
        self._auto_assigned = prev_auto
        self.models.append(manifest)

    def visit_Assign(self, node: ast.Assign) -> None:
        if self._current is None:
            return
        if len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
            return
        field_name = node.targets[0].id
        if _is_django_field(node.value) is None:
            return
        save_assigned = field_name in self._auto_assigned
        self._current.fields.append(
            _build_field_constraint(field_name, node.value, save_assigned)
        )


class _FunctionVisitor(ast.NodeVisitor):
    def __init__(self, file_path: str, module_path: str) -> None:
        self.file = file_path
        self.module_path = module_path
        self.functions: list[FunctionManifest] = []
        self._class_stack: list[str] = []

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._class_stack.append(node.name)
        self.generic_visit(node)
        self._class_stack.pop()

    def _visit_func(self, node: ast.FunctionDef) -> None:
        args_spec = node.args
        n_defaults = len(args_spec.defaults)
        all_args = args_spec.args
        required_cutoff = len(all_args) - n_defaults

        constraints = []
        for i, arg in enumerate(all_args):
            if arg.arg in ("self", "cls"):
                continue
            required = i < required_cutoff
            constraints.append(
                ArgConstraint(name=arg.arg, required=required, has_default=not required)
            )

        # Keyword-only args (after *)
        kw_defaults = args_spec.kw_defaults  # parallel list; None means no default
        for i, arg in enumerate(args_spec.kwonlyargs):
            if arg.arg in ("self", "cls"):
                continue
            has_default = kw_defaults[i] is not None
            constraints.append(
                ArgConstraint(
                    name=arg.arg,
                    required=not has_default,
                    has_default=has_default,
                    kwonly=True,
                )
            )

        qual = ".".join(self._class_stack + [node.name])
        self.functions.append(
            FunctionManifest(
                name=qual,
                file=self.file,
                line=node.lineno,
                module_path=self.module_path,
                args=constraints,
            )
        )
        self.generic_visit(node)

    visit_FunctionDef = _visit_func
    visit_AsyncFunctionDef = _visit_func


class _CallVisitor(ast.NodeVisitor):
    def __init__(self, file_path: str) -> None:
        self.file = file_path
        self.call_sites: list[CallSite] = []
        self._class_stack: list[str] = []  # class names (mirrors _FunctionVisitor)
        self._func_stack: list[str] = []  # qualified function names within classes

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._class_stack.append(node.name)
        self.generic_visit(node)
        self._class_stack.pop()

    def _enter_func(self, node):
        # Build the same qualified name as _FunctionVisitor: Class.method
        qual = ".".join(self._class_stack + [node.name])
        self._func_stack.append(qual)
        self.generic_visit(node)
        self._func_stack.pop()

    visit_FunctionDef = _enter_func
    visit_AsyncFunctionDef = _enter_func

    def visit_Call(self, node: ast.Call) -> None:
        site = self._make_site(node)
        if site:
            self.call_sites.append(site)
        self.generic_visit(node)

    def _make_site(self, node: ast.Call) -> Optional[CallSite]:
        kwargs = {kw.arg for kw in node.keywords if kw.arg is not None}
        kwarg_values = {
            kw.arg: value
            for kw in node.keywords
            if kw.arg is not None
            for value in [_literal_value(kw.value)]
            if value is not _UNKNOWN
        }
        positional = len(node.args)
        func = node.func

        caller = self._func_stack[-1] if self._func_stack else None

        # Model.objects.create(...)
        if (
            isinstance(func, ast.Attribute)
            and func.attr == "create"
            and isinstance(func.value, ast.Attribute)
            and func.value.attr == "objects"
        ):
            model_name = None
            if isinstance(func.value.value, ast.Name):
                model_name = func.value.value.id
            return CallSite(
                callee_name=(
                    f"{model_name}.objects.create" if model_name else "?.objects.create"
                ),
                file=self.file,
                line=node.lineno,
                provided_kwargs=kwargs,
                kwarg_values=kwarg_values,
                positional_count=positional,
                is_create_call=True,
                model_name=model_name,
                caller_name=caller,
            )

        # Regular call
        name = self._name(func)
        if name:
            return CallSite(
                callee_name=name,
                file=self.file,
                line=node.lineno,
                provided_kwargs=kwargs,
                kwarg_values=kwarg_values,
                positional_count=positional,
                caller_name=caller,
            )
        return None

    def _name(self, node: ast.expr) -> Optional[str]:
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            obj = self._name(node.value)
            return f"{obj}.{node.attr}" if obj else node.attr
        return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

_SKIP_DIRS = frozenset(
    {
        "__pycache__",
        ".git",
        ".venv",
        "venv",
        "node_modules",
        "migrations",
        ".mypy_cache",
        ".uv-cache",
        ".ruff_cache",
    }
)


def iter_python_files(root: Path):
    for path in root.rglob("*.py"):
        if any(part in _SKIP_DIRS for part in path.parts):
            continue
        yield path


def extract_from_file(
    path: Path,
) -> tuple[list[ModelManifest], list[FunctionManifest], list[CallSite]]:
    try:
        source = path.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(source, filename=str(path))
    except SyntaxError:
        return [], [], []

    module_path = ".".join(path.with_suffix("").parts)

    mv = _ModelVisitor(str(path))
    mv.visit(tree)

    fv = _FunctionVisitor(str(path), module_path)
    fv.visit(tree)

    cv = _CallVisitor(str(path))
    cv.visit(tree)

    return mv.models, fv.functions, cv.call_sites


def extract_from_codebase(
    root: Path,
) -> tuple[list[ModelManifest], list[FunctionManifest], list[CallSite]]:
    all_models: list[ModelManifest] = []
    all_funcs: list[FunctionManifest] = []
    all_calls: list[CallSite] = []

    for path in iter_python_files(root):
        models, funcs, calls = extract_from_file(path)
        all_models.extend(models)
        all_funcs.extend(funcs)
        all_calls.extend(calls)

    return all_models, all_funcs, all_calls
