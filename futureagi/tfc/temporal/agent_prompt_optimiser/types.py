from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class RunAgentPromptOptimiserWorkflowInput:
    run_id: str
    task_queue: str = "tasks_xl"
    resume: bool = True


@dataclass
class RunAgentPromptOptimiserWorkflowOutput:
    run_id: str
    status: str
    best_prompt: Optional[str] = None
    best_score: Optional[float] = None
    trials_completed: int = 0
    error: Optional[str] = None


@dataclass
class SetupRunOutput:
    run_id: str
    total_trials: int
    current_trial_number: int
    scenario_manifest: List[str] = field(default_factory=list)
    optimizer_state: Optional[Dict[str, Any]] = None
    best_prompt: Optional[str] = None
    best_score: Optional[float] = None


# ----- Evaluation Workflow Types -----


@dataclass
class SerializedEvalTemplate:
    """Serialized eval template for Temporal activities."""

    id: str
    name: str
    type_id: str  # The eval class identifier (e.g., "LlmEvaluator")
    config: Dict[str, Any] = field(default_factory=dict)
    model: Optional[str] = None


@dataclass
class SerializedEvalConfig:
    """Serialized eval config for Temporal activities."""

    id: str
    name: str
    config: Dict[str, Any] = field(default_factory=dict)
    mapping: Dict[str, Any] = field(default_factory=dict)
    model: Optional[str] = None
    error_localizer: bool = False
    kb_id: Optional[str] = None
    eval_template: Optional[SerializedEvalTemplate] = None


@dataclass
class EvaluatorConfig:
    """Complete evaluator configuration for serialization."""

    eval_configs: List[SerializedEvalConfig] = field(default_factory=list)
    issues: List[Dict[str, Any]] = field(default_factory=list)
    use_synthetic: bool = True
    simulator_model: str = "gemini-3.5-flash-lite"
    customer_model: str = "gemini-3.5-flash-lite"
    max_parallel_evals: int = 5
    use_issues: bool = True
    use_evals: bool = True
    use_dual_llm_sim: bool = False
    is_inbound: bool = True
    initial_agent_prompt: Optional[str] = None
    organization_id: Optional[str] = None
    workspace_id: Optional[str] = None
    eval_source: str = "fix_your_agent"


@dataclass
class ScenarioInput:
    """Single scenario to evaluate in an activity."""

    call_execution_id: str
    agent_prompt: str
    persona: str = ""
    situation: str = ""
    expected_outcome: str = ""
    existing_transcript: Optional[str] = None
    customer_system_prompt: Optional[str] = None
    # Extra fields from scenario dict
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ScenarioResult:
    """Result from evaluating a single scenario."""

    call_execution_id: str
    score: float
    reason: str = ""
    transcript: str = ""
    component_evals: Dict[str, Any] = field(default_factory=dict)


@dataclass
class EvaluateTrialWorkflowInput:
    """Input for EvaluateTrialWorkflow child workflow."""

    run_id: str
    trial_number: int
    agent_prompt: str
    scenarios: List[ScenarioInput] = field(default_factory=list)
    evaluator_config: Optional[EvaluatorConfig] = None


@dataclass
class EvaluateTrialWorkflowOutput:
    """Output from EvaluateTrialWorkflow."""

    trial_number: int
    average_score: float
    results: List[ScenarioResult] = field(default_factory=list)
