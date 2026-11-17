"""Custom Python code columns are disabled (Capability contract).

The executor never runs user code: ``exec()`` in ExecutePythonCodeView stays
commented out, and its regex screen is not a sandbox. Until code runs in a
real sandbox, every request that would test, add, edit or re-run custom code
is refused with one fixed message after authentication, request validation
and the scoped dataset/column lookup, but before any metadata, status or cell
write and before any task is queued. A queued or direct worker call refuses
before its first read or write and ends as a non-retryable failure, so
Temporal neither retries it nor records a success.

Nothing here can turn code back on.
"""

from tfc.utils.error_codes import get_error_message

CODE_EXECUTION_DISABLED = "CODE_EXECUTION_DISABLED"
CODE_EXECUTION_DISABLED_TYPE = "CodeExecutionDisabled"

# The conditional branch node type that runs custom code
# (ConditionalColumnView._process_branch).
CODE_BRANCH_TYPE = "extract_code"
EXECUTE_CODE_OPERATION = "execute_code"
CONDITIONAL_OPERATION = "conditional"


def conditional_config_uses_code(config):
    """True when a conditional column's branch list has a code branch.

    Follows the shape ConditionalColumnView._process_row and _process_branch
    dispatch on: the config is a list of branch dicts, and a branch runs code
    when its ``branch_node_config`` dict has ``type == "extract_code"``. Text
    anywhere else (a condition, a static value, another key) never counts.
    """
    if not isinstance(config, list):
        return False
    for branch in config:
        if not isinstance(branch, dict):
            continue
        node = branch.get("branch_node_config")
        if isinstance(node, dict) and node.get("type") == CODE_BRANCH_TYPE:
            return True
    return False


def operation_uses_code(operation_type, conditional_config=None):
    """True for execute_code, and for a conditional whose config has a code
    branch."""
    if operation_type == EXECUTE_CODE_OPERATION:
        return True
    if operation_type == CONDITIONAL_OPERATION:
        return conditional_config_uses_code(conditional_config)
    return False


def code_execution_disabled_message():
    """The fixed refusal. It never repeats anything the caller sent."""
    return get_error_message(CODE_EXECUTION_DISABLED)


def refuse_disabled_code_task():
    """End a code task as a non-retryable failure before it reads or writes.

    The drop-in activity wrapper re-raises it; Temporal does not retry a
    non-retryable ApplicationError, and a direct call raises it to the caller.
    """
    from temporalio.exceptions import ApplicationError

    raise ApplicationError(
        code_execution_disabled_message(),
        type=CODE_EXECUTION_DISABLED_TYPE,
        non_retryable=True,
    )
