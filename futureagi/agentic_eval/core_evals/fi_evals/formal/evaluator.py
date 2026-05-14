import json
import time
from typing import Any

try:
    import z3
    Z3_AVAILABLE = True
except ImportError:
    Z3_AVAILABLE = False

from agentic_eval.core_evals.fi_metrics.metric_type import MetricType
from agentic_eval.core_evals.fi_utils.evals_result import EvalResult, EvalResultMetric

from ..base_evaluator import BaseEvaluator


class FormalConstraintEvaluator(BaseEvaluator):
    """
    Evaluates agent responses against formally specified constraints using Z3 SMT solver.

    Unlike LLM-as-judge, this evaluator produces a mathematical certificate:
    either a proof that the agent's assignment satisfies all constraints,
    or identifies exactly which constraints are violated.

    Constraints are specified as a JSON schema with typed variables and constraint
    expressions. Agent responses must be a JSON dict mapping variable names to values.

    Supported constraint types:
        equals, not_equals, less_than, greater_than, less_than_or_equal,
        greater_than_or_equal, sum_equals, sum_less_than, sum_greater_than,
        all_different, product_equals

    Example constraints spec:
        {
            "variables": {
                "x": {"type": "int", "min": 1, "max": 9},
                "y": {"type": "int", "min": 1, "max": 9}
            },
            "constraints": [
                {"type": "sum_equals", "vars": ["x", "y"], "value": 10},
                {"type": "not_equals", "vars": ["x", "y"]}
            ]
        }

    Example agent_response: '{"x": 3, "y": 7}'
    """

    @property
    def name(self) -> str:
        return "FormalConstraintEvaluator"

    @property
    def display_name(self) -> str:
        return "Formal Constraint Verification"

    @property
    def metric_ids(self) -> list[str]:
        return [MetricType.PASSED.value, "formal_correctness"]

    @property
    def required_args(self) -> list[str]:
        return ["task_description", "agent_response", "constraints"]

    @property
    def examples(self):
        return [
            {
                "task_description": "Find two different integers x and y, each between 1 and 9, that sum to 10.",
                "agent_response": '{"x": 3, "y": 7}',
                "constraints": json.dumps({
                    "variables": {
                        "x": {"type": "int", "min": 1, "max": 9},
                        "y": {"type": "int", "min": 1, "max": 9},
                    },
                    "constraints": [
                        {"type": "sum_equals", "vars": ["x", "y"], "value": 10},
                        {"type": "not_equals", "vars": ["x", "y"]},
                    ],
                }),
            }
        ]

    def is_failure(self, *args) -> bool | None:
        if args:
            return not bool(args[0])
        return None

    def to_config(self) -> dict | None:
        return {
            "solver": "z3",
            "version": z3.get_version_string() if Z3_AVAILABLE else "unavailable",
        }

    # --- Z3 model building ---

    def _build_z3_var(self, name: str, spec: dict) -> Any:
        vtype = spec.get("type", "int")
        if vtype == "int":
            return z3.Int(name)
        elif vtype == "bool":
            return z3.Bool(name)
        elif vtype == "real":
            return z3.Real(name)
        raise ValueError(f"Unknown variable type '{vtype}' for variable '{name}'")

    def _domain_constraints(self, name: str, spec: dict, z3_var: Any) -> list:
        vtype = spec.get("type", "int")
        result = []
        if vtype in ("int", "real"):
            if "min" in spec:
                result.append(z3_var >= spec["min"])
            if "max" in spec:
                result.append(z3_var <= spec["max"])
        return result

    def _build_z3_constraint(self, c: dict, z3_vars: dict) -> Any:
        ctype = c["type"]
        operands = [z3_vars[v] for v in c.get("vars", [])]

        if ctype == "equals":
            return operands[0] == c["value"]
        elif ctype == "not_equals":
            if len(operands) == 2:
                return operands[0] != operands[1]
            return operands[0] != c["value"]
        elif ctype == "less_than":
            return operands[0] < c["value"]
        elif ctype == "greater_than":
            return operands[0] > c["value"]
        elif ctype == "less_than_or_equal":
            return operands[0] <= c["value"]
        elif ctype == "greater_than_or_equal":
            return operands[0] >= c["value"]
        elif ctype == "sum_equals":
            return z3.Sum(operands) == c["value"]
        elif ctype == "sum_less_than":
            return z3.Sum(operands) < c["value"]
        elif ctype == "sum_greater_than":
            return z3.Sum(operands) > c["value"]
        elif ctype == "all_different":
            return z3.Distinct(operands)
        elif ctype == "product_equals":
            product = operands[0]
            for op in operands[1:]:
                product = product * op
            return product == c["value"]
        raise ValueError(f"Unknown constraint type: '{ctype}'")

    # --- Evaluation ---

    def _evaluate(self, **kwargs) -> EvalResult:
        self.validate_args(**kwargs)
        start = time.perf_counter()

        if not Z3_AVAILABLE:
            return self._error_result(kwargs, start, "z3-solver not installed: pip install z3-solver")

        task_description = kwargs["task_description"]
        agent_response = kwargs["agent_response"]
        constraints_raw = kwargs["constraints"]

        try:
            spec = json.loads(constraints_raw) if isinstance(constraints_raw, str) else constraints_raw
        except json.JSONDecodeError as e:
            return self._error_result(kwargs, start, f"constraints is not valid JSON: {e}")

        try:
            assignment = json.loads(agent_response) if isinstance(agent_response, str) else agent_response
        except json.JSONDecodeError as e:
            return self._error_result(
                kwargs, start,
                f"agent_response is not valid JSON — expected a dict of variable assignments. Error: {e}",
            )

        variables_spec = spec.get("variables", {})
        constraints_spec = spec.get("constraints", [])

        try:
            z3_vars = {n: self._build_z3_var(n, s) for n, s in variables_spec.items()}
        except ValueError as e:
            return self._error_result(kwargs, start, str(e))

        # Build domain + problem constraints
        base_constraints = []
        for name, vspec in variables_spec.items():
            base_constraints.extend(self._domain_constraints(name, vspec, z3_vars[name]))

        built = []
        try:
            for c in constraints_spec:
                built.append(self._build_z3_constraint(c, z3_vars))
        except (ValueError, KeyError, IndexError) as e:
            return self._error_result(kwargs, start, f"Invalid constraint spec: {e}")

        # Sanity check: is the problem itself satisfiable?
        feasibility = z3.Solver()
        for bc in base_constraints:
            feasibility.add(bc)
        for bc in built:
            feasibility.add(bc)
        if feasibility.check() == z3.unsat:
            return self._error_result(
                kwargs, start,
                "The constraint specification is unsatisfiable — no valid solution exists. Check your constraints.",
            )

        # Check missing variables in assignment
        missing = [n for n in variables_spec if n not in assignment]
        if missing:
            return self._error_result(kwargs, start, f"Agent response missing variables: {missing}")

        # Verify: do constraints + assignment yield SAT?
        verifier = z3.Solver()
        for bc in base_constraints:
            verifier.add(bc)
        for bc in built:
            verifier.add(bc)
        for name, value in assignment.items():
            if name in z3_vars:
                verifier.add(z3_vars[name] == value)

        result = verifier.check()
        runtime_ms = int((time.perf_counter() - start) * 1000)

        if result == z3.sat:
            model = verifier.model()
            certificate = {v: str(model.eval(z3_vars[v])) for v in z3_vars}
            reason = (
                f"VERIFIED: {assignment} satisfies all {len(constraints_spec)} constraint(s). "
                f"Z3 certificate: {certificate}"
            )
            passed = True
        else:
            # Identify which constraints fail individually
            violations = []
            for i, (c_spec, bc) in enumerate(zip(constraints_spec, built)):
                probe = z3.Solver()
                for dc in base_constraints:
                    probe.add(dc)
                for name, value in assignment.items():
                    if name in z3_vars:
                        probe.add(z3_vars[name] == value)
                probe.add(bc)
                if probe.check() == z3.unsat:
                    violations.append(f"[{i}] {c_spec}")
            reason = (
                f"REFUTED: {assignment} violates {len(violations)} constraint(s): {violations}"
            )
            passed = False

        return {
            "name": self.name,
            "display_name": self.display_name,
            "data": {
                "task_description": task_description,
                "agent_response": str(agent_response),
                "constraints": str(constraints_raw),
            },
            "failure": not passed,
            "reason": reason,
            "runtime": runtime_ms,
            "model": "z3-solver",
            "metadata": None,
            "metrics": [
                EvalResultMetric(id=MetricType.PASSED.value, value=float(passed)),
                EvalResultMetric(id="formal_correctness", value=1.0 if passed else 0.0),
            ],
            "datapoint_field_annotations": None,
        }

    def _error_result(self, kwargs: dict, start: float, message: str) -> EvalResult:
        runtime_ms = int((time.perf_counter() - start) * 1000)
        return {
            "name": self.name,
            "display_name": self.display_name,
            "data": {k: str(v) for k, v in kwargs.items()},
            "failure": True,
            "reason": f"ERROR: {message}",
            "runtime": runtime_ms,
            "model": "z3-solver",
            "metadata": None,
            "metrics": [
                EvalResultMetric(id=MetricType.PASSED.value, value=0.0),
                EvalResultMetric(id="formal_correctness", value=0.0),
            ],
            "datapoint_field_annotations": None,
        }
