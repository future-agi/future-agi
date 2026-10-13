# MCP schema-handler contract for the Python ai_tools bridge

Future AGI exposes platform tools through the Python MCP server by deriving an MCP-visible function signature from each `ai_tools` Pydantic `input_model`, then forwarding runtime kwargs into `BaseTool.run()`.

Related issue: [#1314](https://github.com/future-agi/future-agi/issues/1314)

## Contract

For every registered tool, these surfaces should stay in sync:

```text
Pydantic input_model
  -> generated MCP tool schema / inspect.Signature
  -> handler kwargs
  -> BaseTool.run(raw_params)
  -> input_model.model_validate(...)
```

If this contract drifts, an MCP client may plan against a schema that is not actually accepted at runtime, or runtime may accept/forward fields that were not represented in the advertised schema.

## Failure modes

- A tool adds a required `input_model` field, but the MCP signature does not expose it.
- Required/optional status differs between the advertised schema and Pydantic validation.
- Optional defaults such as `False`, `0`, `[]`, or `{}` are dropped or changed.
- Runtime kwargs contain fields not represented in the advertised schema.
- A refactor leaves FastMCP schema generation working while `BaseTool.run()` receives a different parameter shape.

## Recommended regression test shape

A focused test should register or monkeypatch a synthetic tool with an input model like:

```python
class ContractInput(BaseModel):
    required_name: str
    enabled: bool = False
    limit: int = 0

    model_config = ConfigDict(extra="forbid")
```

Assertions:

1. `handler.__signature__` includes exactly `required_name`, `enabled`, and `limit`.
2. `required_name` is required in the generated signature.
3. `enabled=False` and `limit=0` are preserved as defaults.
4. A valid handler call forwards the same kwargs to `tool.run()`.
5. A missing required field returns a validation error and does not execute tool side effects.
6. An unknown field is rejected when the Pydantic model forbids extras.

## Operator-facing observability

When schema validation fails at an MCP boundary, logs or usage records should make the rejection distinguishable from upstream tool failure:

- tool name
- schema or tool version/hash if available
- violation path / reason
- redacted argument preview
- whether the tool body executed

This mirrors the same principle as gateway-side MCP validation: **tool schema validation is an external-effect boundary and should fail closed before dispatch.**
