from dataclasses import dataclass, field


@dataclass(frozen=True)
class CertificationWorkflowInput:
    campaign_id: str


@dataclass(frozen=True)
class CertificationCaseInput:
    case_id: str


@dataclass(frozen=True)
class CertificationCasesOutput:
    case_ids: list[str] = field(default_factory=list)
    max_active_cases: int = 4


@dataclass(frozen=True)
class CertificationSubmitOutput:
    hosted_harness_job_id: str


@dataclass(frozen=True)
class CertificationPollOutput:
    done: bool
    job_state: str


@dataclass(frozen=True)
class CertificationReconcileOutput:
    ok: bool
    missing: list[str]
    failure_class: str | None


@dataclass(frozen=True)
class CertificationResultInput:
    case_id: str
    reconcile_ok: bool
    failure_class: str | None


@dataclass(frozen=True)
class CertificationCampaignInput:
    campaign_id: str


@dataclass(frozen=True)
class CertificationNotifyOutput:
    delivered: int
    failed: int
    skipped: int


@dataclass(frozen=True)
class CertificationFinalizeOutput:
    state: str


@dataclass(frozen=True)
class CertificationWorkflowOutput:
    campaign_id: str
    state: str
    cases_completed: int
    cases_failed: int
