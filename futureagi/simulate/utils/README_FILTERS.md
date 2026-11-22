"""
Docstring update for test_execution_utils.py

Number filtering now fully supports `is_null` and `is_not_null` operators:
- Keeps rows where field is null or not null respectively.
- For multi-field checks (e.g. cost), `is_null` requires all fields to be null.
- `responseTime` null filters do not attempt to cast empty values to float.
"""
