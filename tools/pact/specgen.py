"""
pact spec gen -- synthesize a TLA+ skeleton from Python source.

Extraction rules:
  Django model class     -> VARIABLES + TypeInvariant entries
  @shared_task function  -> TLA+ Action stub
  cache.get / cache.set  -> non-atomic step comment (split-step hazard)
  unique_together / Meta.constraints -> UniqueConstraint INVARIANT

70% of a valid spec can be derived from the AST.  The remaining 30% --
temporal liveness properties and domain-specific invariants -- require human
annotation.  Generated stubs are clearly marked with TODO comments.
"""

from __future__ import annotations

import ast
import re
import textwrap
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class _Field:
    name: str
    django_type: str   # e.g. "CharField", "ForeignKey"
    nullable: bool = False
    required: bool = True

    @property
    def tla_type(self) -> str:
        _MAP = {
            "CharField": "STRING",
            "TextField": "STRING",
            "SlugField": "STRING",
            "EmailField": "STRING",
            "URLField": "STRING",
            "UUIDField": "STRING",
            "IntegerField": "Int",
            "SmallIntegerField": "Int",
            "BigIntegerField": "Int",
            "PositiveIntegerField": "Nat",
            "PositiveSmallIntegerField": "Nat",
            "FloatField": "Real",
            "DecimalField": "Real",
            "BooleanField": "BOOLEAN",
            "NullBooleanField": "BOOLEAN",
            "DateTimeField": "Nat",
            "DateField": "Nat",
            "TimeField": "Nat",
            "DurationField": "Nat",
            "ForeignKey": "STRING",
            "OneToOneField": "STRING",
            "ManyToManyField": "SUBSET STRING",
            "JSONField": "STRING",
            "ArrayField": "Seq(STRING)",
            "FileField": "STRING",
            "ImageField": "STRING",
        }
        base = _MAP.get(self.django_type, "STRING")
        if self.nullable:
            return f"{base} \\union {{\"NULL\"}}"
        return base


@dataclass
class _Model:
    name: str
    fields: list[_Field] = field(default_factory=list)
    unique_constraints: list[list[str]] = field(default_factory=list)


@dataclass
class _Task:
    name: str
    args: list[str] = field(default_factory=list)
    has_cache_get: bool = False
    has_cache_set: bool = False
    has_db_write: bool = False


# ---------------------------------------------------------------------------
# AST visitors
# ---------------------------------------------------------------------------

class _SpecVisitor(ast.NodeVisitor):
    def __init__(self):
        self.models: list[_Model] = []
        self.tasks: list[_Task] = []
        self._current_model: Optional[_Model] = None
        self._current_task: Optional[_Task] = None

    def visit_ClassDef(self, node: ast.ClassDef):
        is_model = any(
            (isinstance(b, ast.Attribute) and b.attr == "Model") or
            (isinstance(b, ast.Name) and b.id in ("Model", "BaseModel", "TimestampedModel", "SoftDeleteModel"))
            for b in node.bases
        )
        if is_model:
            prev = self._current_model
            self._current_model = _Model(name=node.name)
            self.generic_visit(node)
            self.models.append(self._current_model)
            self._current_model = prev
        else:
            self.generic_visit(node)

    def visit_Assign(self, node: ast.Assign):
        if self._current_model is None:
            self.generic_visit(node)  # still recurse so visit_Call fires inside task bodies
            return
        if not (len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)):
            return
        fname = node.targets[0].id
        if fname.startswith("_") or fname == "objects":
            return
        call = node.value
        if not isinstance(call, ast.Call):
            return
        func = call.func
        if isinstance(func, ast.Attribute) and func.attr.endswith("Field"):
            django_type = func.attr
        elif isinstance(func, ast.Name) and func.id.endswith("Field"):
            django_type = func.id
        else:
            return

        nullable = any(
            isinstance(kw.value, ast.Constant) and kw.value.value is True
            for kw in call.keywords if kw.arg in ("null", "blank")
        )
        # FKs and M2Ms aren't "required" at create time in the usual sense
        required = django_type not in ("ForeignKey", "ManyToManyField", "OneToOneField")
        if nullable:
            required = False

        f = _Field(name=fname, django_type=django_type, nullable=nullable, required=required)
        self._current_model.fields.append(f)

    _TASK_DECORATOR_NAMES = frozenset({
        "shared_task", "task",           # Celery
        "temporal_activity",             # Temporal
        "activity_method",               # Temporal SDK variants
    })

    def visit_FunctionDef(self, node: ast.FunctionDef):
        is_task = any(
            (isinstance(d, ast.Attribute) and d.attr in self._TASK_DECORATOR_NAMES) or
            (isinstance(d, ast.Name) and d.id in self._TASK_DECORATOR_NAMES) or
            (isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute) and d.func.attr in self._TASK_DECORATOR_NAMES) or
            (isinstance(d, ast.Call) and isinstance(d.func, ast.Name) and d.func.id in self._TASK_DECORATOR_NAMES)
            for d in node.decorator_list
        )
        if is_task:
            args = [
                a.arg for a in node.args.args
                if a.arg not in ("self", "cls")
            ] + [a.arg for a in (node.args.posonlyargs or [])]
            task = _Task(name=node.name, args=args)
            prev = self._current_task
            self._current_task = task
            self.generic_visit(node)
            self.tasks.append(task)
            self._current_task = prev
        else:
            prev_task = self._current_task
            if self._current_task is not None:
                self.generic_visit(node)
            else:
                self.generic_visit(node)
            self._current_task = prev_task

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Call(self, node: ast.Call):
        if self._current_task is not None:
            func = node.func
            # cache.get / cache.set / cache_client.get etc.
            if isinstance(func, ast.Attribute):
                if func.attr == "get" and _is_cache_obj(func.value):
                    self._current_task.has_cache_get = True
                elif func.attr in ("set", "setex", "rpush", "lpush") and _is_cache_obj(func.value):
                    self._current_task.has_cache_set = True
                elif func.attr in ("save", "create", "update", "delete", "bulk_create", "bulk_update"):
                    self._current_task.has_db_write = True
        self.generic_visit(node)

    def _extract_unique_constraints(self, node: ast.ClassDef):
        """Extract unique_together and UniqueConstraint from inner Meta class."""
        for item in node.body:
            if isinstance(item, ast.ClassDef) and item.name == "Meta":
                for stmt in item.body:
                    if not isinstance(stmt, ast.Assign):
                        continue
                    if not (len(stmt.targets) == 1 and isinstance(stmt.targets[0], ast.Name)):
                        continue
                    attr = stmt.targets[0].id
                    if attr == "unique_together":
                        val = stmt.value
                        groups = _extract_string_tuples(val)
                        self._current_model.unique_constraints.extend(groups)
                    elif attr == "constraints":
                        # UniqueConstraint(fields=[...], ...)
                        if isinstance(stmt.value, ast.List):
                            for elt in stmt.value.elts:
                                if isinstance(elt, ast.Call):
                                    for kw in elt.keywords:
                                        if kw.arg == "fields" and isinstance(kw.value, ast.List):
                                            flds = [
                                                c.value for c in kw.value.elts
                                                if isinstance(c, ast.Constant) and isinstance(c.value, str)
                                            ]
                                            if flds:
                                                self._current_model.unique_constraints.append(flds)


def _is_cache_obj(node: ast.expr) -> bool:
    if isinstance(node, ast.Name):
        return "cache" in node.id.lower() or "redis" in node.id.lower()
    if isinstance(node, ast.Attribute):
        return "cache" in node.attr.lower() or "redis" in node.attr.lower()
    return False


def _extract_string_tuples(node: ast.expr) -> list[list[str]]:
    result = []
    if isinstance(node, (ast.List, ast.Tuple)):
        for elt in node.elts:
            if isinstance(elt, (ast.List, ast.Tuple)):
                group = [
                    e.value for e in elt.elts
                    if isinstance(e, ast.Constant) and isinstance(e.value, str)
                ]
                if group:
                    result.append(group)
            elif isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                result.append([elt.value])
    return result


# ---------------------------------------------------------------------------
# Synthesis
# ---------------------------------------------------------------------------

def _tla_ident(name: str) -> str:
    """Convert snake_case to CamelCase for TLA+ identifiers."""
    return "".join(w.capitalize() for w in name.split("_"))


def _tla_var(name: str) -> str:
    """Variable names stay lowercase in TLA+ by convention."""
    return name.lower()


def synthesize(source: str, module_name: str = "Module") -> str:
    tree = ast.parse(source)
    v = _SpecVisitor()
    # Also extract Meta constraints from model classes (second pass via same visitor)
    v.visit(tree)
    # Back-fill unique_constraints for each model (we need to re-walk ClassDef bodies)
    for cls_node in ast.walk(tree):
        if isinstance(cls_node, ast.ClassDef):
            for m in v.models:
                if m.name == cls_node.name:
                    v._current_model = m
                    v._extract_unique_constraints(cls_node)
                    v._current_model = None

    return _render(v.models, v.tasks, module_name)


def _render(models: list[_Model], tasks: list[_Task], module_name: str) -> str:
    lines: list[str] = []

    bar = "-" * 28
    lines.append(f"{bar} MODULE {module_name} {bar}")
    lines.append("EXTENDS Naturals, Sequences, FiniteSets, TLC")
    lines.append("")

    if not models and not tasks:
        lines.append("\\* pact spec gen: no Django models or Celery tasks found in this file.")
        lines.append("\\* Point at a models.py or tasks.py file.")
        lines.append(f"{'=' * (len(module_name) + 62)}")
        return "\n".join(lines)

    # VARIABLES
    var_names: list[str] = []
    var_comments: dict[str, str] = {}
    for model in models:
        v = _tla_var(model.name) + "s"
        var_names.append(v)
        var_comments[v] = f"SET OF {model.name} records"
    for task in tasks:
        if task.has_db_write or task.has_cache_set:
            pass  # state captured via model vars

    if var_names:
        lines.append("VARIABLES")
        for i, v in enumerate(var_names):
            comma = "," if i < len(var_names) - 1 else ""
            lines.append(f"  {v}{comma}  \\* {var_comments[v]}")
        lines.append("")

    all_vars = ", ".join(f"<<{', '.join(var_names)}>>") if var_names else "<<>>"

    # TypeInvariant
    lines.append("TypeInvariant ==")
    for i, model in enumerate(models):
        v = _tla_var(model.name) + "s"
        prefix = "  /\\" if i > 0 else "  /\\"
        lines.append(f"{prefix} \\A r \\in {v} :")
        for f in model.fields:
            lines.append(f"       /\\ r.{f.name} \\in {f.tla_type}")
    if not models:
        lines.append("  TRUE  \\* TODO: add type constraints")
    lines.append("")

    # UniqueConstraint invariants
    for model in models:
        if model.unique_constraints:
            for uc in model.unique_constraints:
                v = _tla_var(model.name) + "s"
                inv_name = _tla_ident("_".join(uc)) + "Unique"
                field_tuple = ", ".join(f"r.{f}" for f in uc)
                lines.append(f"\\* Derived from unique_together / UniqueConstraint")
                lines.append(f"{inv_name} ==")
                lines.append(f"  \\A r1, r2 \\in {v} :")
                lines.append(f"    r1 # r2 => <<{field_tuple.replace('r.', 'r1.')}>> # <<{field_tuple.replace('r.', 'r2.')}>>")
                lines.append("")

    # Init
    lines.append("Init ==")
    for i, model in enumerate(models):
        v = _tla_var(model.name) + "s"
        lines.append(f"  {'  ' if i > 0 else ''}/\\ {v} = {{}}")
    if not models:
        lines.append("  TRUE  \\* TODO")
    lines.append("")

    # Actions from @shared_task
    for task in tasks:
        action_name = _tla_ident(task.name)
        param_str = "".join(f", {_tla_ident(a)}" for a in task.args)
        lines.append(f"\\* Corresponds to: @shared_task {task.name}()")
        if task.has_cache_get and task.has_cache_set:
            lines.append(f"\\* WARNING: non-atomic -- cache.get + cache.set is a split step.")
            lines.append(f"\\* Model as two separate actions or use atomic Redis primitive.")
        lines.append(f"{action_name}({param_str.lstrip(', ')}) ==")
        lines.append(f"  \\* TODO: specify precondition (ENABLED guard)")
        lines.append(f"  /\\ TRUE")
        if models:
            other_vars = [_tla_var(m.name) + "s" for m in models]
            lines.append(f"  /\\ UNCHANGED <<{', '.join(other_vars)}>>")
        lines.append("")

    # Next
    if tasks:
        lines.append("Next ==")
        for i, task in enumerate(tasks):
            action_name = _tla_ident(task.name)
            params = "".join(f", _{a}" for a in task.args)
            quantified = "".join(f"\\E _{a} \\in STRING : " for a in task.args)
            prefix = "  \\/" if i > 0 else "  \\/",
            if task.args:
                lines.append(f"  \\/ {quantified}{action_name}({', '.join('_' + a for a in task.args)})")
            else:
                lines.append(f"  \\/ {action_name}")
        lines.append("")

    # Next (only when tasks present; without tasks the spec is pure model typing)
    if not tasks:
        lines.append("\\* No task functions found (expected @shared_task, @temporal_activity, etc.)")
        lines.append("\\* Define actions manually or point at a tasks.py / activities.py file.")
        lines.append("Next == FALSE  \\* placeholder")
        lines.append("")

    # Spec
    if var_names:
        vars_tuple = f"<<{', '.join(var_names)}>>"
        vars_def = vars_tuple
    else:
        # No models found -- emit a placeholder variable so the spec is syntactically valid
        lines.append("\\* TODO: add state variables (point pact spec gen at a models.py too)")
        lines.append("VARIABLES task_state  \\* placeholder")
        lines.append("")
        var_names = ["task_state"]
        vars_tuple = "<<task_state>>"
        vars_def = vars_tuple
    lines.append(f"vars == {vars_def}")
    lines.append("")
    lines.append("Spec ==")
    lines.append(f"  Init")
    lines.append(f"  /\\ [][Next]_{vars_tuple}")
    if tasks:
        lines.append(f"  /\\ WF_{vars_tuple}(Next)  \\* TODO: refine liveness per action")
    lines.append("")

    # INVARIANTS
    lines.append("INVARIANT TypeInvariant")
    for model in models:
        for uc in model.unique_constraints:
            inv_name = _tla_ident("_".join(uc)) + "Unique"
            lines.append(f"INVARIANT {inv_name}")
    lines.append("")
    lines.append(f"{'=' * (len(module_name) + 62)}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def spec_gen(path: Path, output: Optional[Path] = None) -> str:
    """
    Generate a TLA+ skeleton for the Python file at `path`.
    Returns the spec string; writes to `output` if provided.
    """
    source = path.read_text(encoding="utf-8")
    module_name = _tla_ident(path.stem)
    spec = synthesize(source, module_name)
    if output:
        output.write_text(spec, encoding="utf-8")
    return spec
