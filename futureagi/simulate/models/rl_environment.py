import uuid

from django.conf import settings
from django.db import models
from django.db.models import Q

from accounts.models import Organization
from accounts.models.workspace import Workspace
from tfc.utils.base_model import BaseModel


class RLEnvironment(BaseModel):
    """An RL harness workspace: one target agent moving through understand/build/scenarios."""

    class Phase(models.TextChoices):
        UNDERSTAND = "understand", "Understand"
        BUILD = "build", "Build"
        SCENARIOS = "scenarios", "Scenarios"

    class Status(models.TextChoices):
        IDLE = "idle", "Idle"
        WORKING = "working", "Working"
        FAILED = "failed", "Failed"
        ARCHIVED = "archived", "Archived"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="rl_environments",
        help_text="Organization this RL environment belongs to",
    )
    # Internal-service writes set no context vars, so BaseModel's auto-assignment
    # of workspace never fires; nullable here would land NULL rows that are
    # invisible to users in non-default workspaces.
    workspace = models.ForeignKey(
        Workspace,
        on_delete=models.CASCADE,
        related_name="rl_environments",
        help_text="Workspace this RL environment belongs to",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="rl_environments_created",
        help_text="User who created this RL environment",
    )
    title = models.CharField(max_length=255, help_text="Display title for the environment")
    source_kind = models.CharField(
        max_length=32, blank=True, default="", help_text="Kind of the understand-stage source"
    )
    source_ref = models.CharField(
        max_length=500, blank=True, default="", help_text="Reference to the understand-stage source"
    )
    phase = models.CharField(
        max_length=20,
        choices=Phase.choices,
        default=Phase.UNDERSTAND,
        help_text="Current harness stage",
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.IDLE,
        help_text="Current environment status",
    )
    simulator_prompt = models.TextField(
        blank=True, default="", help_text="System prompt driving the simulator agent"
    )
    agent_definition = models.ForeignKey(
        "simulate.AgentDefinition",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="rl_environments",
        help_text="Agent definition under test",
    )
    agent_version = models.ForeignKey(
        "simulate.AgentVersion",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="rl_environments",
        help_text="Agent version under test",
    )
    run_config = models.JSONField(
        default=dict,
        blank=True,
        help_text="Run-time config, e.g. accepts_dispatch_metadata, modality",
    )
    last_error = models.TextField(blank=True, default="", help_text="Last error encountered")

    class Meta:
        db_table = "simulate_rl_environment"
        verbose_name = "RL Environment"
        verbose_name_plural = "RL Environments"
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["organization", "status"], name="idx_rl_env_org_status"),
        ]

    def __str__(self):
        return self.title


class RLContract(BaseModel):
    """A versioned agent profile extracted during the understand stage."""

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        ACTIVE = "active", "Active"
        SUPERSEDED = "superseded", "Superseded"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="rl_contracts",
        help_text="Organization this contract belongs to",
    )
    environment = models.ForeignKey(
        RLEnvironment,
        on_delete=models.CASCADE,
        related_name="contracts",
        help_text="RL environment this contract belongs to",
    )
    version = models.PositiveIntegerField(help_text="Contract version within the environment")
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.DRAFT,
        help_text="Current contract status",
    )
    data = models.JSONField(default=dict, blank=True, help_text="The extracted agent profile")
    amendments = models.JSONField(
        default=list, blank=True, help_text="Amendments applied on top of the extracted profile"
    )

    class Meta:
        db_table = "simulate_rl_contract"
        verbose_name = "RL Contract"
        verbose_name_plural = "RL Contracts"
        ordering = ("-created_at",)
        constraints = [
            models.UniqueConstraint(
                fields=["environment", "version"],
                condition=Q(deleted=False),
                name="uniq_rl_contract_env_version",
            ),
        ]

    def __str__(self):
        return f"{self.environment_id} v{self.version}"


class RLEnvironmentMessage(BaseModel):
    """A single turn in an RL environment's understand-stage conversation."""

    class Role(models.TextChoices):
        USER = "user", "User"
        ASSISTANT = "assistant", "Assistant"
        SYSTEM = "system", "System"
        TOOL = "tool", "Tool"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="rl_environment_messages",
        help_text="Organization this message belongs to",
    )
    environment = models.ForeignKey(
        RLEnvironment,
        on_delete=models.CASCADE,
        related_name="messages",
        help_text="RL environment this message belongs to",
    )
    # Client-supplied idempotency key: MAX(seq)+1 alone duplicates under retry.
    turn_id = models.UUIDField(help_text="Client-supplied idempotency key for this turn")
    seq = models.PositiveIntegerField(help_text="Ordering sequence within the environment")
    role = models.CharField(max_length=20, choices=Role.choices, help_text="Speaker role")
    text = models.TextField(blank=True, default="", help_text="Message text")
    tools = models.JSONField(default=list, blank=True, help_text="Tool calls made in this turn")
    phase = models.CharField(
        max_length=20,
        choices=RLEnvironment.Phase.choices,
        blank=True,
        default="",
        help_text="Harness stage this message was recorded in",
    )

    class Meta:
        db_table = "simulate_rl_environment_message"
        verbose_name = "RL Environment Message"
        verbose_name_plural = "RL Environment Messages"
        ordering = ("seq",)
        constraints = [
            models.UniqueConstraint(
                fields=["environment", "turn_id"],
                condition=Q(deleted=False),
                name="uniq_rl_env_message_turn",
            ),
        ]
        indexes = [
            models.Index(fields=["environment", "seq"], name="idx_rl_msg_env_seq"),
        ]

    def __str__(self):
        return f"{self.environment_id}#{self.seq}"


class RLWorld(BaseModel):
    """A built, materializable state for an RL environment's contract."""

    class Status(models.TextChoices):
        BUILDING = "building", "Building"
        SAVED = "saved", "Saved"
        FAILED = "failed", "Failed"

    class StoreKind(models.TextChoices):
        POSTGRES = "postgres", "Postgres"
        INPROCESS = "inprocess", "In-process"
        SQLITE = "sqlite", "SQLite"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="rl_worlds",
        help_text="Organization this world belongs to",
    )
    environment = models.ForeignKey(
        RLEnvironment,
        on_delete=models.CASCADE,
        related_name="worlds",
        help_text="RL environment this world belongs to",
    )
    contract = models.ForeignKey(
        RLContract,
        on_delete=models.CASCADE,
        related_name="worlds",
        help_text="Contract this world was built from",
    )
    version = models.PositiveIntegerField(help_text="World version within the environment")
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.BUILDING,
        help_text="Current world status",
    )
    store_kind = models.CharField(
        max_length=20,
        choices=StoreKind.choices,
        default=StoreKind.POSTGRES,
        help_text="Backing store for the world",
    )
    schema_scripts = models.JSONField(
        default=list, blank=True, help_text="Ordered SQL scripts the build stage applied"
    )
    snapshot = models.JSONField(
        default=dict,
        blank=True,
        help_text='Held snapshot format: {"rows": {...}, "counters": {...}}',
    )
    state = models.JSONField(default=dict, blank=True, help_text="World state")
    handlers = models.JSONField(default=dict, blank=True, help_text="Registered handlers")
    tool_specs = models.JSONField(default=list, blank=True, help_text="Tool specifications")
    # The harness produces {name: python_source}, not a list.
    world_checks = models.JSONField(default=dict, blank=True, help_text="World-level checks")
    refusal_signature = models.TextField(
        blank=True, default="", help_text="Signature used to detect agent refusals"
    )
    # 63 = postgres identifier limit.
    master_db_name = models.CharField(
        max_length=63,
        blank=True,
        default="",
        help_text="Name of the materialized master database",
    )
    master_materialized_at = models.DateTimeField(
        null=True, blank=True, help_text="When the master database was materialized"
    )

    class Meta:
        db_table = "simulate_rl_world"
        verbose_name = "RL World"
        verbose_name_plural = "RL Worlds"
        ordering = ("-created_at",)
        constraints = [
            models.UniqueConstraint(
                fields=["environment", "version"],
                condition=Q(deleted=False),
                name="uniq_rl_world_env_version",
            ),
        ]
        indexes = [
            models.Index(fields=["environment", "status"], name="idx_rl_world_env_status"),
        ]

    def __str__(self):
        return f"{self.environment_id} v{self.version}"


class RLScenario(BaseModel):
    """A scenario proved against a built world."""

    class GateStatus(models.TextChoices):
        UNPROVEN = "unproven", "Unproven"
        PROVING = "proving", "Proving"
        PASSED = "passed", "Passed"
        FAILED = "failed", "Failed"
        STALE = "stale", "Stale"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="rl_scenarios",
        help_text="Organization this scenario belongs to",
    )
    environment = models.ForeignKey(
        RLEnvironment,
        on_delete=models.CASCADE,
        related_name="scenarios",
        help_text="RL environment this scenario belongs to",
    )
    world = models.ForeignKey(
        RLWorld,
        on_delete=models.CASCADE,
        related_name="scenarios",
        help_text="World this scenario was proved against",
    )
    name = models.CharField(max_length=255, help_text="Scenario name")
    instruction = models.TextField(blank=True, default="", help_text="Simulator instruction")
    persona = models.JSONField(default=dict, blank=True, help_text="Persona driving the simulator")
    variables = models.JSONField(default=dict, blank=True, help_text="Scenario variables")
    # The harness produces [{"tool": str, "arguments": dict}], not a dict.
    solution = models.JSONField(default=list, blank=True, help_text="Reference solution")
    sub_goals = models.JSONField(default=list, blank=True, help_text="Scenario sub-goals")
    setup_code = models.TextField(blank=True, default="", help_text="Code run before the scenario")
    ready_code = models.TextField(
        blank=True, default="", help_text="Code that determines scenario readiness"
    )
    # A mapping of expectation -> value (e.g. {"cart.count": 1}), not a list.
    checks = models.JSONField(default=dict, blank=True, help_text="Scenario-level checks")
    # Matches the harness's own Scenario default; the harness is the producer.
    max_turns = models.PositiveIntegerField(
        default=10, help_text="Maximum number of turns allowed"
    )
    gate_status = models.CharField(
        max_length=20,
        choices=GateStatus.choices,
        default=GateStatus.UNPROVEN,
        help_text="Proving-gate status",
    )
    gate_results = models.JSONField(default=dict, blank=True, help_text="Proving-gate results")
    proved_at = models.DateTimeField(null=True, blank=True, help_text="When the gate last passed")
    # Shadow-graph idempotency key, created lazily at run initiation.
    platform_scenario = models.OneToOneField(
        "simulate.Scenarios",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="rl_scenario",
        help_text="Platform scenario shadowing this RL scenario",
    )

    class Meta:
        db_table = "simulate_rl_scenario"
        verbose_name = "RL Scenario"
        verbose_name_plural = "RL Scenarios"
        ordering = ("-created_at",)
        constraints = [
            models.UniqueConstraint(
                fields=["environment", "name"],
                condition=Q(deleted=False),
                name="uniq_rl_scenario_env_name",
            ),
        ]
        indexes = [
            models.Index(fields=["environment", "gate_status"], name="idx_rl_scen_env_gate"),
        ]

    def __str__(self):
        return self.name


class RLWorldCopy(BaseModel):
    """A leased, per-run copy of a world's materialized state."""

    class Purpose(models.TextChoices):
        GATE = "gate", "Gate"
        VOICE = "voice", "Voice"
        CHAT = "chat", "Chat"

    class Status(models.TextChoices):
        PROVISIONING = "provisioning", "Provisioning"
        READY = "ready", "Ready"
        IN_CALL = "in_call", "In Call"
        GRADING = "grading", "Grading"
        GRADED = "graded", "Graded"
        DROPPED = "dropped", "Dropped"
        EXPIRED = "expired", "Expired"
        FAILED = "failed", "Failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="rl_world_copies",
        help_text="Organization this world copy belongs to",
    )
    # A collision with a soft-deleted row is just as unacceptable, so this is a
    # plain unique constraint rather than one conditioned on deleted=False.
    token = models.UUIDField(
        default=uuid.uuid4,
        unique=True,
        editable=False,
        help_text="Opaque token handed to the harness for this copy",
    )
    environment = models.ForeignKey(
        RLEnvironment,
        on_delete=models.CASCADE,
        related_name="world_copies",
        help_text="RL environment this copy belongs to",
    )
    world = models.ForeignKey(
        RLWorld,
        on_delete=models.CASCADE,
        related_name="copies",
        help_text="World this copy was leased from",
    )
    scenario = models.ForeignKey(
        RLScenario,
        on_delete=models.CASCADE,
        related_name="copies",
        help_text="Scenario this copy was leased for",
    )
    run_test = models.ForeignKey(
        "simulate.RunTest",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="rl_world_copies",
        help_text="Run test this copy was leased for",
    )
    call_execution = models.ForeignKey(
        "simulate.CallExecution",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="rl_world_copies",
        help_text="Call execution this copy was leased for",
    )
    purpose = models.CharField(max_length=10, choices=Purpose.choices, help_text="Lease purpose")
    db_name = models.CharField(
        max_length=63, blank=True, default="", help_text="Name of the leased database"
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PROVISIONING,
        help_text="Current lease status",
    )
    call_log = models.JSONField(default=list, blank=True, help_text="Call log entries")
    # Written by the world service alongside call-log appends so a restart can
    # rehydrate mutated state instead of rewinding to the build-time snapshot.
    state = models.JSONField(default=dict, blank=True, help_text="The copy's live, mutated state")
    verdicts = models.JSONField(default=list, blank=True, help_text="Grading verdicts")
    # Computed backend-side; the harness cannot read the run's max duration.
    expires_at = models.DateTimeField(null=True, blank=True, help_text="When the lease expires")
    error = models.TextField(blank=True, default="", help_text="Error encountered on this copy")

    class Meta:
        db_table = "simulate_rl_world_copy"
        verbose_name = "RL World Copy"
        verbose_name_plural = "RL World Copies"
        ordering = ("-created_at",)
        constraints = [
            # A retried prepare must find the existing copy, not double-stamp.
            models.UniqueConstraint(
                fields=["call_execution"],
                condition=Q(deleted=False, call_execution__isnull=False),
                name="uniq_rl_world_copy_call_exec",
            ),
        ]
        indexes = [
            models.Index(fields=["status", "expires_at"], name="idx_rl_copy_status_expires"),
        ]

    def __str__(self):
        return str(self.token)
