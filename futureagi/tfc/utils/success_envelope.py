"""Static detection of view actions that answer through the success envelope.

At runtime ``GeneralMethods.success_response(result, status=200)`` and
``GeneralMethods.create_response(result)`` (tfc/utils/general_methods.py) are
the only helpers that build the ``{"status": true, "result": <result>}``
success envelope. OpenAPI generation cannot execute a view, so this module
reads the view action's source and works out, return statement by return
statement, how its successful responses are built.

The analysis is conservative. An action is ``ENVELOPE`` only when *every*
``return`` that can produce a 2xx response is proven to be one of the envelope
helpers: directly, through a local variable, or through a ``self`` method,
``super()`` method or module function it calls (arguments are followed into
the callee). A return that cannot be proven makes the action ``UNKNOWN``; a mix
of enveloped and bare 2xx returns makes it ``MIXED``. Only ``ENVELOPE`` changes
generated OpenAPI output, so every unproven action keeps exactly the schema
drf-yasg infers for it today.

Classification is a pure function of the source code. It never runs a view,
touches the database or the network, or depends on a request, so OpenAPI
generation stays deterministic.
"""

from __future__ import annotations

import ast
import inspect
import re
import textwrap
from dataclasses import dataclass, field

ENVELOPE = "envelope"
RAW = "raw"
MIXED = "mixed"
UNKNOWN = "unknown"
NO_SUCCESS = "no_success"

_ERROR = "error"
_NONE = "none"

# GeneralMethods helpers that build the success envelope, with their default
# HTTP status. ``success_response(result, status=...)`` takes the status as its
# second argument; ``create_response`` always answers 201.
ENVELOPE_HELPERS = {"success_response": 200, "create_response": 201}
_ENVELOPE_STATUS_ARG = {"success_response": (1, "status")}

# GeneralMethods helpers that only build non-2xx error responses.
ERROR_HELPERS = frozenset(
    {
        "bad_request",
        "custom_error_response",
        "forbidden_response",
        "internal_server_error_response",
        "not_found",
        "param_missing_response",
        "too_many_requests",
        "unauthorized_response",
        "usage_limit_response",
    }
)

# A paginator's ``get_paginated_response`` answers with the bare DRF page
# (``{count, next, previous, results}``), never the success envelope. A view's
# own ``self.get_paginated_response`` is still analysed like any other method.
BARE_PAGINATION_HELPERS = frozenset({"get_paginated_response"})

_HTTP_STATUS_NAME = re.compile(r"HTTP_(\d{3})_[A-Z0-9_]+")
_MAX_DEPTH = 8


@dataclass(frozen=True)
class Classification:
    """How an action builds its successful responses."""

    kind: str
    statuses: tuple = ()
    reasons: tuple = field(default=(), compare=False)

    @property
    def is_envelope(self):
        return self.kind == ENVELOPE


@dataclass(frozen=True)
class _Outcome:
    kind: str  # ENVELOPE, RAW, _ERROR, _NONE or UNKNOWN
    status: int | None = None
    reason: str = ""


@dataclass
class _Frame:
    func: object
    node: ast.AST
    owner: type | None
    view_cls: type | None
    bindings: dict = field(default_factory=dict)
    defaults: dict = field(default_factory=dict)

    @property
    def where(self):
        return _qualname(self.func)


def _is_success(status):
    return status is not None and 200 <= status < 300


def _qualname(func):
    return f"{getattr(func, '__module__', '?')}.{getattr(func, '__qualname__', '?')}"


def unwrap_view_function(func):
    """Return the user-written function behind decorators and ``@api_view``."""

    seen = set()
    while func is not None and id(func) not in seen:
        seen.add(id(func))
        func = getattr(func, "__func__", func)
        unwrapped = inspect.unwrap(func)
        if unwrapped is not func:
            func = unwrapped
            continue
        # DRF's ``@api_view`` installs ``handler(self, *args, **kwargs)``, which
        # closes over the decorated view function as ``func``.
        if (
            getattr(func, "__name__", "") == "handler"
            and getattr(func, "__module__", "") == "rest_framework.decorators"
        ):
            try:
                inner = inspect.getclosurevars(func).nonlocals.get("func")
            except (TypeError, ValueError):
                inner = None
            if inner is not None:
                func = inner
                continue
        break
    return func


_PARSED = {}


def _function_node(func):
    code = getattr(func, "__code__", None)
    if code is None:
        return None
    if code not in _PARSED:
        node = None
        try:
            module = ast.parse(textwrap.dedent(inspect.getsource(func)))
        except (OSError, TypeError, SyntaxError):
            module = None
        if module is not None:
            for candidate in module.body:
                if isinstance(candidate, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    node = candidate
                    break
        _PARSED[code] = node
    return _PARSED[code]


def _own_nodes(function_node):
    """Yield the nodes of a function body without entering nested scopes."""

    stack = list(function_node.body)
    while stack:
        node = stack.pop()
        yield node
        for child in ast.iter_child_nodes(node):
            if not isinstance(
                child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)
            ):
                stack.append(child)


def _binds(target, name):
    """True when an assignment target rebinds ``name`` (not ``name.x = ...``)."""

    return any(
        isinstance(node, ast.Name)
        and node.id == name
        and isinstance(node.ctx, ast.Store)
        for node in ast.walk(target)
    )


def _parameters(node):
    args = node.args
    return [a.arg for a in [*args.posonlyargs, *args.args]], [
        a.arg for a in args.kwonlyargs
    ]


def _all_parameters(node):
    positional, keyword_only = _parameters(node)
    args = node.args
    variadic = [a.arg for a in (args.vararg, args.kwarg) if a is not None]
    return {*positional, *keyword_only, *variadic}


def _parameter_defaults(node):
    positional, keyword_only = _parameters(node)
    defaults = {}
    for name, value in zip(
        positional[len(positional) - len(node.args.defaults) :],
        node.args.defaults,
        strict=True,
    ):
        defaults[name] = value
    for name, value in zip(keyword_only, node.args.kw_defaults, strict=True):
        if value is not None:
            defaults[name] = value
    return defaults


def _call_bindings(node, call, caller, skip_first):
    """Map callee parameter names to the caller's argument expressions."""

    positional, keyword_only = _parameters(node)
    if skip_first:
        positional = positional[1:]
    bindings = {}
    for name, arg in zip(positional, call.args, strict=False):
        if isinstance(arg, ast.Starred):
            break
        bindings[name] = (arg, caller)
    known = set(positional) | set(keyword_only)
    for kw in call.keywords:
        if kw.arg in known:
            bindings[kw.arg] = (kw.value, caller)
    return bindings


def _defining_class(view_cls, name, func):
    for klass in getattr(view_cls, "__mro__", ()):
        attr = klass.__dict__.get(name)
        if attr is None:
            continue
        if unwrap_view_function(getattr(attr, "__func__", attr)) is func:
            return klass
    return None


def _call_arg(call, position, keyword):
    for kw in call.keywords:
        if kw.arg == keyword:
            return kw.value, True
    if position is not None and len(call.args) > position:
        return call.args[position], True
    return None, False


class _Analyzer:
    def __init__(self):
        self.active = set()

    # Functions -----------------------------------------------------------

    def function_outcomes(self, func, owner, view_cls, depth, call=None, caller=None):
        func = unwrap_view_function(func)
        node = _function_node(func)
        if node is None:
            return [_Outcome(UNKNOWN, reason=f"no source for {_qualname(func)}")]
        key = (func, owner)
        if key in self.active or depth > _MAX_DEPTH:
            return [_Outcome(UNKNOWN, reason=f"recursion at {_qualname(func)}")]
        skip_first = False
        if owner is not None:
            raw = owner.__dict__.get(getattr(func, "__name__", ""))
            skip_first = not isinstance(raw, staticmethod)
        frame = _Frame(
            func=func,
            node=node,
            owner=owner,
            view_cls=view_cls,
            bindings=_call_bindings(node, call, caller, skip_first) if call else {},
            defaults=_parameter_defaults(node),
        )
        self.active.add(key)
        try:
            returns = [n for n in _own_nodes(node) if isinstance(n, ast.Return)]
            if not returns:
                return [_Outcome(UNKNOWN, reason=f"no return in {frame.where}")]
            outcomes = []
            for ret in returns:
                outcomes.extend(self.expr_outcomes(ret.value, frame, depth, set()))
            return outcomes
        finally:
            self.active.discard(key)

    # Expressions ---------------------------------------------------------

    def expr_outcomes(self, expr, frame, depth, names_seen):
        if expr is None or (isinstance(expr, ast.Constant) and expr.value is None):
            # ``return None`` never answers a request; helpers use it to mean
            # "no early response" (``if error := self._check(): return error``).
            return [_Outcome(_NONE)]
        if isinstance(expr, ast.Await):
            return self.expr_outcomes(expr.value, frame, depth, names_seen)
        if isinstance(expr, ast.IfExp):
            return self.expr_outcomes(
                expr.body, frame, depth, names_seen
            ) + self.expr_outcomes(expr.orelse, frame, depth, names_seen)
        if isinstance(expr, ast.BoolOp):
            outcomes = []
            for value in expr.values:
                outcomes.extend(self.expr_outcomes(value, frame, depth, names_seen))
            return outcomes
        if isinstance(expr, ast.NamedExpr):
            return self.expr_outcomes(expr.value, frame, depth, names_seen)
        if isinstance(expr, ast.Name):
            return self.name_outcomes(expr.id, frame, depth, names_seen)
        if isinstance(expr, ast.Call):
            return self.call_outcomes(expr, frame, depth)
        return [
            _Outcome(UNKNOWN, reason=f"{type(expr).__name__} return in {frame.where}")
        ]

    def name_values(self, name, frame):
        """Return (expressions, frame) bound to ``name``, or a failure reason."""

        values = []
        for child in _own_nodes(frame.node):
            if isinstance(child, ast.Assign):
                for target in child.targets:
                    if isinstance(target, ast.Name) and target.id == name:
                        values.append(child.value)
                    elif _binds(target, name):
                        return None, f"unpacked {name} in {frame.where}"
            elif isinstance(child, ast.AnnAssign):
                if _binds(child.target, name):
                    if child.value is None:
                        continue
                    values.append(child.value)
            elif isinstance(child, ast.AugAssign):
                if _binds(child.target, name):
                    return None, f"augmented {name} in {frame.where}"
            elif isinstance(child, (ast.For, ast.AsyncFor)):
                if _binds(child.target, name):
                    return None, f"loop-bound {name} in {frame.where}"
            elif isinstance(child, (ast.With, ast.AsyncWith)):
                if any(
                    item.optional_vars is not None and _binds(item.optional_vars, name)
                    for item in child.items
                ):
                    return None, f"with-bound {name} in {frame.where}"
            elif isinstance(child, ast.NamedExpr) and child.target.id == name:
                values.append(child.value)
            elif isinstance(child, ast.ExceptHandler) and child.name == name:
                return None, f"exception-bound {name} in {frame.where}"
        pairs = [(value, frame) for value in values]
        # A parameter keeps the caller's argument (or its default) on every
        # path that does not reassign it, so that value stays possible next to
        # any in-body assignment.
        if name in frame.bindings:
            pairs.append(frame.bindings[name])
        elif name in frame.defaults:
            callee = _Frame(frame.func, frame.node, frame.owner, frame.view_cls)
            pairs.append((frame.defaults[name], callee))
        elif values and name in _all_parameters(frame.node):
            return None, f"reassigned parameter {name} in {frame.where}"
        if pairs:
            return pairs, None
        return None, f"unassigned {name} in {frame.where}"

    def name_outcomes(self, name, frame, depth, names_seen):
        key = (name, id(frame))
        if key in names_seen:
            return [_Outcome(UNKNOWN, reason=f"cyclic {name} in {frame.where}")]
        values, reason = self.name_values(name, frame)
        if values is None:
            return [_Outcome(UNKNOWN, reason=reason)]
        outcomes = []
        for value, value_frame in values:
            outcomes.extend(
                self.expr_outcomes(value, value_frame, depth, names_seen | {key})
            )
        return outcomes

    def status_value(self, node, frame, depth=0):
        if isinstance(node, ast.Constant) and isinstance(node.value, int):
            return node.value
        if isinstance(node, ast.Attribute):
            match = _HTTP_STATUS_NAME.fullmatch(node.attr)
            return int(match.group(1)) if match else None
        if isinstance(node, ast.Name):
            match = _HTTP_STATUS_NAME.fullmatch(node.id)
            if match:
                return int(match.group(1))
            if depth > _MAX_DEPTH:
                return None
            values, _ = self.name_values(node.id, frame)
            if not values:
                return None
            statuses = {self.status_value(v, f, depth + 1) for v, f in values}
            return statuses.pop() if len(statuses) == 1 else None
        return None

    # Calls ---------------------------------------------------------------

    def call_outcomes(self, call, frame, depth):
        target = call.func
        if isinstance(target, ast.Attribute):
            return self.method_call_outcomes(call, target, frame, depth)
        if isinstance(target, ast.Name):
            resolved = getattr(frame.func, "__globals__", {}).get(target.id)
            if resolved is None:
                return [_Outcome(UNKNOWN, reason=f"unresolved {target.id}()")]
            response = self.response_class_outcome(resolved, call, frame)
            if response is not None:
                return [response]
            if inspect.isfunction(resolved):
                return self.function_outcomes(
                    resolved, None, None, depth + 1, call=call, caller=frame
                )
            return [_Outcome(UNKNOWN, reason=f"call to {target.id}() in {frame.where}")]
        return [_Outcome(UNKNOWN, reason=f"indirect call in {frame.where}")]

    def method_call_outcomes(self, call, target, frame, depth):
        attr = target.attr
        if attr in ENVELOPE_HELPERS:
            status = ENVELOPE_HELPERS[attr]
            if attr in _ENVELOPE_STATUS_ARG:
                status_node, given = _call_arg(call, *_ENVELOPE_STATUS_ARG[attr])
                if given:
                    status = self.status_value(status_node, frame)
                    if status is None:
                        return [
                            _Outcome(
                                UNKNOWN,
                                reason=f"unresolved {attr} status in {frame.where}",
                            )
                        ]
            return [_Outcome(ENVELOPE, status)]
        if attr in ERROR_HELPERS:
            return [_Outcome(_ERROR)]
        receiver = target.value
        view_cls = frame.view_cls
        is_self = isinstance(receiver, ast.Name) and receiver.id in ("self", "cls")
        if attr in BARE_PAGINATION_HELPERS and not is_self:
            return [_Outcome(RAW, 200)]
        if is_self:
            resolved = getattr(view_cls, attr, None) if view_cls else None
            if resolved is None or not callable(resolved):
                return [_Outcome(UNKNOWN, reason=f"unresolved self.{attr}()")]
        elif (
            isinstance(receiver, ast.Call)
            and isinstance(receiver.func, ast.Name)
            and receiver.func.id == "super"
            and view_cls is not None
            and frame.owner is not None
        ):
            resolved = None
            mro = view_cls.__mro__
            for klass in mro[mro.index(frame.owner) + 1 :]:
                if attr in klass.__dict__:
                    resolved = getattr(klass, attr)
                    break
            if resolved is None:
                return [_Outcome(UNKNOWN, reason=f"unresolved super().{attr}()")]
        else:
            return [_Outcome(UNKNOWN, reason=f"call to .{attr}() in {frame.where}")]
        resolved_func = unwrap_view_function(resolved)
        return self.function_outcomes(
            resolved_func,
            _defining_class(view_cls, attr, resolved_func),
            view_cls,
            depth + 1,
            call=call,
            caller=frame,
        )

    def response_class_outcome(self, klass, call, frame):
        from django.http import HttpResponseBase
        from rest_framework.response import Response

        if not (isinstance(klass, type) and issubclass(klass, HttpResponseBase)):
            return None
        # DRF's Response takes ``status`` second; Django responses take it by
        # keyword in practice.
        position = 1 if issubclass(klass, Response) else None
        status_node, given = _call_arg(call, position, "status")
        if not given:
            if position is None and len(call.args) > 2:
                return _Outcome(UNKNOWN, reason=f"positional {klass.__name__} args")
            return _Outcome(RAW, getattr(klass, "status_code", 200))
        status = self.status_value(status_node, frame)
        if status is None:
            return _Outcome(
                UNKNOWN,
                reason=f"unresolved {klass.__name__} status in {frame.where}",
            )
        return _Outcome(RAW if _is_success(status) else _ERROR, status)


_CACHE = {}


def classify_view_function(view_cls, func):
    """Classify how ``func`` (an action of ``view_cls``) builds 2xx responses."""

    func = unwrap_view_function(func)
    key = (view_cls, func)
    if key not in _CACHE:
        name = getattr(func, "__name__", "")
        owner = _defining_class(view_cls, name, func) if view_cls else None
        outcomes = _Analyzer().function_outcomes(func, owner, view_cls, 0)
        unknown = sorted({o.reason for o in outcomes if o.kind == UNKNOWN})
        envelope = sorted({o.status for o in outcomes if o.kind == ENVELOPE})
        raw = sorted({o.status for o in outcomes if o.kind == RAW})
        if unknown:
            result = Classification(UNKNOWN, reasons=tuple(unknown))
        elif not all(_is_success(status) for status in envelope):
            result = Classification(UNKNOWN, reasons=("non-2xx envelope status",))
        elif envelope and any(_is_success(status) for status in raw):
            result = Classification(MIXED, tuple(envelope), ("bare and enveloped",))
        elif envelope:
            result = Classification(ENVELOPE, tuple(envelope))
        elif raw:
            result = Classification(RAW, tuple(raw))
        else:
            result = Classification(NO_SUCCESS)
        _CACHE[key] = result
    return _CACHE[key]


def view_action_function(view, method):
    """Return the user-written function DRF dispatches ``method`` to."""

    action = getattr(view, "action", None)
    handler = getattr(view, action, None) if action else None
    if handler is None:
        handler = getattr(view, method.lower(), None)
    if handler is None:
        return None
    return unwrap_view_function(handler)


def classify_view_action(view, method):
    """Classify the action ``view`` runs for the HTTP ``method``."""

    func = view_action_function(view, method)
    if func is None:
        return Classification(UNKNOWN, reasons=("no handler",))
    return classify_view_function(type(view), func)
