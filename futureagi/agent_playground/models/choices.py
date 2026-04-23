import re

from django.db import models

# Characters reserved for dot-notation variable syntax (e.g. {{Node1.response[0]}}).
# Forbidden in node names and output port display_names.
RESERVED_NAME_CHARS = frozenset(".[]{}")
RESERVED_NAME_RE = re.compile(r"[.\[\]{}]")


class NodeType(models.TextChoices):
    """Node type choices"""

    SUBGRAPH = "subgraph", "Subgraph"
    ATOMIC = "atomic", "Atomic"


class GraphVersionStatus(models.TextChoices):
    """GraphVersion status choices"""

    DRAFT = "draft", "Draft"
    ACTIVE = "active", "Active"
    INACTIVE = "inactive", "Inactive"


class PortDirection(models.TextChoices):
    """Port direction choices"""

    INPUT = "input", "Input"
    OUTPUT = "output", "Output"


class PortMode(models.TextChoices):
    """Port creation mode for NodeTemplate"""

    STRICT = "strict", "Strict"
    EXTENSIBLE = "extensible", "Extensible"
    DYNAMIC = "dynamic", "Dynamic"


class GraphExecutionStatus(models.TextChoices):
    """GraphExecution specific status choices"""

    PENDING = "pending", "Pending"
    RUNNING = "running", "Running"
    SUCCESS = "success", "Success"
    FAILED = "failed", "Failed"
    CANCELLED = "cancelled", "Cancelled"


class NodeExecutionStatus(models.TextChoices):
    """NodeExecution specific status choices"""

    PENDING = "pending", "Pending"
    RUNNING = "running", "Running"
    SUCCESS = "success", "Success"
    FAILED = "failed", "Failed"
    SKIPPED = "skipped", "Skipped"
