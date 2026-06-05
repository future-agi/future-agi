# Formal Specifications

Machine-checkable models of critical system properties.

## Tools

### TLA+ / TLC (eval pipeline)

`eval_pipeline.tla` models the `run_eval()` state machine and verifies:
- Every run terminates in `Done` or `Failed` (no stuck states)
- `Failed` always carries a non-empty reason
- `Done` always has `duration` set
- The protect shortcut always routes to `DeterministicEvaluator`

**Run:**
```bash
# Download TLA+ tools
curl -L https://github.com/tlaplus/tlaplus/releases/latest/download/tla2tools.jar -o tla2tools.jar

# Check the spec
java -jar tla2tools.jar -config eval_pipeline.cfg eval_pipeline.tla
```

Or open in the [TLA+ Toolbox](https://lamport.azurewebsites.net/tla/toolbox.html) IDE.

### Alloy 6 (eval registry)

`eval_registry.als` models the evaluator registry as a relational structure and verifies:
- `get_eval_class` never returns `None` for registered names
- `is_registered` and `get_eval_class` are consistent
- `list_registered` is complete
- Registry is deterministic

**Run:**
```bash
# Download Alloy 6
curl -L https://github.com/AlloyTools/org.alloytools.alloy/releases/latest/download/org.alloytools.alloy.dist.jar -o alloy6.jar

# Headless check (all assertions)
java -cp alloy6.jar edu.mit.csail.sdg.alloy4whole.ExampleUsingTheAPI eval_registry.als
```

Or open `eval_registry.als` in the [Alloy Analyzer](https://alloytools.org) GUI.

## Relationship to tests

These specs model the *logical* properties. The pytest tests in
`evaluations/tests/` enforce them at runtime:

| Property | Spec | Test |
|----------|------|------|
| Formatter is total | `eval_pipeline.tla` (Format phase) | `test_formatter_hypothesis.py` |
| Force-choices invariant | — | `test_formatter_z3.py` |
| Registry never returns None | `eval_registry.als` | `test_registry.py` |
| is_registered consistent | `eval_registry.als` | `test_registry.py` |
| Protect routes to Deterministic | `eval_pipeline.tla` | `test_runner.py` (planned) |
