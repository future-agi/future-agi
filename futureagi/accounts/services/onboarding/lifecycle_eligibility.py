import uuid
from dataclasses import dataclass
from datetime import timedelta
from urllib.parse import urlencode

from django.utils import timezone

from accounts.models import (
    OnboardingActivationEvent,
    OnboardingGoal,
    OnboardingLifecycleEvaluationLog,
    OnboardingLifecyclePreference,
)
from accounts.services.onboarding.flow_config import configured_action
from accounts.services.onboarding.lifecycle_completion import lifecycle_target_completed
from accounts.services.onboarding.lifecycle_digest_preview import (
    build_lifecycle_digest_preview,
)
from accounts.services.onboarding.lifecycle_frequency import frequency_cap_suppression
from accounts.services.onboarding.lifecycle_registry import lifecycle_campaigns
from tracer.models.project import Project
from tracer.models.trace import Trace


@dataclass(frozen=True)
class LifecycleDecision:
    run_id: uuid.UUID
    user: object
    organization: object
    workspace: object
    status: str
    campaign: dict | None
    activation_state: dict
    target_url: str | None
    eligible_at: object | None
    suppression_reason: str | None = None
    suppression_details: dict | None = None
    evaluated_at: object | None = None
    metadata: dict | None = None

    def to_payload(self):
        campaign = self.campaign or {}
        recommended_action = self.activation_state.get("recommended_action") or {}
        return {
            "run_id": str(self.run_id),
            "status": self.status,
            "campaign_key": campaign.get("campaign_key"),
            "campaign_group": campaign.get("campaign_group"),
            "template_key": campaign.get("template_key"),
            "template_version": campaign.get("template_version"),
            "activation_stage": self.activation_state.get("stage") or "",
            "primary_path": self.activation_state.get("primary_path"),
            "recommendation_id": recommended_action.get("id"),
            "target_action_id": campaign.get("target_action_id"),
            "target_success_event": campaign.get("target_success_event"),
            "target_url": self.target_url,
            "suppression_reason": self.suppression_reason,
            "suppression_details": self.suppression_details or {},
            "eligible_at": self.eligible_at,
            "evaluated_at": self.evaluated_at,
            "activation_state_snapshot": activation_state_snapshot(
                self.activation_state
            ),
            "registry_snapshot": campaign,
            "metadata": self.metadata or {},
        }


def activation_state_snapshot(activation_state):
    recommended_action = activation_state.get("recommended_action") or {}
    fallback_action = activation_state.get("fallback_action") or {}
    return {
        "stage": activation_state.get("stage"),
        "primary_path": activation_state.get("primary_path"),
        "goal": activation_state.get("goal"),
        "is_activated": activation_state.get("is_activated"),
        "recommended_action_id": recommended_action.get("id"),
        "recommended_action_href": recommended_action.get("href"),
        "fallback_action_id": fallback_action.get("id"),
        "sample_project_status": (
            (activation_state.get("sample_project") or {}).get("status")
        ),
    }


def choose_lifecycle_campaign(
    activation_state,
    registry=None,
    *,
    campaign_key=None,
    started_at=None,
    now=None,
):
    campaigns = registry or lifecycle_campaigns()
    stage = activation_state.get("stage")
    primary_path = activation_state.get("primary_path")
    matches = []
    for campaign in campaigns:
        if campaign_key and campaign["campaign_key"] != campaign_key:
            continue
        if stage not in campaign["entry_stages"]:
            continue
        campaign_path = campaign.get("primary_path")
        if campaign_path not in {"any", primary_path}:
            continue
        daily_quality_modes = campaign.get("daily_quality_modes")
        if daily_quality_modes is not None:
            daily_quality_mode = (activation_state.get("daily_quality") or {}).get(
                "mode"
            )
            if daily_quality_mode not in daily_quality_modes:
                continue
        matches.append(campaign)
    if not matches:
        return None
    if started_at and now:
        eligible = [
            campaign
            for campaign in matches
            if now >= started_at + timedelta(minutes=campaign["wait_window_minutes"])
        ]
        if eligible:
            return sorted(
                eligible,
                key=lambda item: (
                    -item["wait_window_minutes"],
                    -item["priority"],
                    item["campaign_key"],
                ),
            )[0]
        return sorted(
            matches,
            key=lambda item: (
                item["wait_window_minutes"],
                -item["priority"],
                item["campaign_key"],
            ),
        )[0]
    return sorted(matches, key=lambda item: (-item["priority"], item["campaign_key"]))[
        0
    ]


def _latest_goal_selected_at(organization, workspace):
    goal = OnboardingGoal.no_workspace_objects.filter(
        organization=organization,
        workspace=workspace,
        is_active=True,
    ).first()
    return goal.selected_at if goal else None


def _first_observe_created_at(activation_state, workspace):
    observe_id = (activation_state.get("signals") or {}).get("first_observe_id")
    if not observe_id:
        return None
    project = Project.no_workspace_objects.filter(
        id=observe_id,
        workspace=workspace,
    ).first()
    return project.created_at if project else None


def _first_trace_created_at(activation_state, workspace):
    trace_id = (activation_state.get("signals") or {}).get("first_trace_id")
    if not trace_id:
        return None
    trace = Trace.no_workspace_objects.filter(
        id=trace_id,
        project__workspace=workspace,
    ).first()
    return trace.created_at if trace else None


def _first_prompt_created_at(activation_state, workspace):
    prompt_id = (activation_state.get("prompt") or {}).get("prompt_id") or (
        activation_state.get("signals") or {}
    ).get("latest_prompt_id")
    if not prompt_id:
        return None
    from model_hub.models.run_prompt import PromptTemplate

    prompt = PromptTemplate.no_workspace_objects.filter(
        id=prompt_id,
        workspace=workspace,
        is_sample=False,
    ).first()
    return prompt.created_at if prompt else None


def _first_agent_created_at(activation_state, workspace):
    agent_state = activation_state.get("agent") or {}
    agent_id = agent_state.get("agent_id") or (
        activation_state.get("signals") or {}
    ).get("agent_id")
    agent_source = agent_state.get("agent_source") or (
        activation_state.get("signals") or {}
    ).get("agent_source")
    if not agent_id:
        return None
    if agent_source == "simulate":
        from simulate.models.agent_definition import AgentDefinition

        agent = AgentDefinition.no_workspace_objects.filter(
            id=agent_id,
            workspace=workspace,
        ).first()
    else:
        from agent_playground.models.graph import Graph

        agent = Graph.no_workspace_objects.filter(
            id=agent_id,
            workspace=workspace,
            is_template=False,
        ).first()
    return agent.created_at if agent else None


def _first_gateway_provider_created_at(activation_state, organization, workspace):
    gateway_state = activation_state.get("gateway") or {}
    provider_id = gateway_state.get("provider_credential_id") or (
        activation_state.get("signals") or {}
    ).get("gateway_provider_credential_id")
    if not provider_id:
        return _latest_event_at(
            organization,
            workspace,
            "gateway_provider_added",
            is_sample=False,
        )
    from agentcc.models.provider_credential import AgentccProviderCredential

    provider = AgentccProviderCredential.no_workspace_objects.filter(
        id=provider_id,
        organization=organization,
    ).first()
    return provider.created_at if provider else None


def _first_gateway_key_created_at(activation_state, organization):
    gateway_state = activation_state.get("gateway") or {}
    key_id = gateway_state.get("gateway_key_id") or (
        activation_state.get("signals") or {}
    ).get("gateway_key_id")
    if not key_id:
        return None
    from agentcc.models import AgentccAPIKey

    key = AgentccAPIKey.no_workspace_objects.filter(
        organization=organization,
        gateway_key_id=key_id,
    ).first()
    return key.created_at if key else None


def _first_gateway_request_created_at(activation_state, organization, workspace):
    gateway_state = activation_state.get("gateway") or {}
    request_log_id = gateway_state.get("request_log_id") or (
        activation_state.get("signals") or {}
    ).get("gateway_request_log_id")
    request_id = gateway_state.get("request_id") or (
        activation_state.get("signals") or {}
    ).get("gateway_request_id")
    from agentcc.models import AgentccRequestLog

    queryset = AgentccRequestLog.no_workspace_objects.filter(organization=organization)
    if request_log_id:
        request = queryset.filter(id=request_log_id).first()
    elif request_id:
        request = queryset.filter(request_id=request_id).first()
    else:
        request = None
    if request:
        return request.started_at or request.created_at
    return _latest_event_at(
        organization,
        workspace,
        "gateway_request_seen",
        is_sample=False,
    )


def _first_eval_source_created_at(activation_state, workspace):
    eval_state = activation_state.get("eval") or {}
    source_id = eval_state.get("source_id") or (
        activation_state.get("signals") or {}
    ).get("eval_source_id")
    source_type = eval_state.get("source_type") or (
        activation_state.get("signals") or {}
    ).get("eval_source_type")
    if not source_id:
        return None
    if source_type == "dataset":
        from model_hub.models.develop_dataset import Dataset

        source = Dataset.no_workspace_objects.filter(
            id=source_id,
            workspace=workspace,
        ).first()
    elif source_type == "trace_project":
        source = Project.no_workspace_objects.filter(
            id=source_id,
            workspace=workspace,
        ).first()
    else:
        source = None
    return getattr(source, "created_at", None) if source else None


def _first_eval_scorer_created_at(activation_state, organization, workspace):
    eval_state = activation_state.get("eval") or {}
    scorer_id = eval_state.get("scorer_id") or (
        activation_state.get("signals") or {}
    ).get("eval_scorer_id")
    scorer_template_id = eval_state.get("scorer_template_id") or (
        activation_state.get("signals") or {}
    ).get("eval_scorer_template_id")
    if scorer_id:
        from model_hub.models.evals_metric import UserEvalMetric

        metric = UserEvalMetric.no_workspace_objects.filter(
            id=scorer_id,
            organization=organization,
            workspace=workspace,
        ).first()
        if metric:
            return metric.created_at
    if scorer_template_id:
        from tracer.models.custom_eval_config import CustomEvalConfig

        config = CustomEvalConfig.no_workspace_objects.filter(
            eval_template_id=scorer_template_id,
            project__organization=organization,
            project__workspace=workspace,
        ).first()
        if config:
            return config.created_at
    return _latest_event_at(
        organization,
        workspace,
        "eval_scorer_created",
        is_sample=False,
    )


def _first_voice_agent_created_at(activation_state, workspace):
    voice_state = activation_state.get("voice") or {}
    agent_id = voice_state.get("agent_id") or (
        activation_state.get("signals") or {}
    ).get("voice_agent_id")
    if not agent_id:
        return None
    from simulate.models.agent_definition import AgentDefinition

    agent = AgentDefinition.no_workspace_objects.filter(
        id=agent_id,
        workspace=workspace,
    ).first()
    return agent.created_at if agent else None


def _latest_event_at(organization, workspace, event_name, *, is_sample=False):
    event = (
        OnboardingActivationEvent.no_workspace_objects.filter(
            organization=organization,
            workspace=workspace,
            event_name=event_name,
            is_sample=is_sample,
        )
        .order_by("-occurred_at", "-created_at")
        .first()
    )
    return event.occurred_at if event else None


def stage_started_at(*, activation_state, organization, workspace, now):
    stage = activation_state.get("stage")
    if stage == "choose_goal":
        return getattr(workspace, "created_at", None)
    if stage == "connect_observability":
        return _latest_goal_selected_at(organization, workspace) or getattr(
            workspace, "created_at", None
        )
    if stage in {
        "waiting_for_first_trace",
        "waiting_for_first_trace_sample_available",
    }:
        return _first_observe_created_at(activation_state, workspace)
    if stage == "review_first_trace":
        return _first_trace_created_at(activation_state, workspace)
    if stage == "create_trace_evaluator":
        return _latest_event_at(
            organization,
            workspace,
            "trace_reviewed",
            is_sample=False,
        )
    if stage == "start_prompt":
        return _latest_goal_selected_at(organization, workspace) or getattr(
            workspace,
            "created_at",
            None,
        )
    if stage == "run_prompt_test":
        return _first_prompt_created_at(
            activation_state,
            workspace,
        ) or _latest_event_at(
            organization,
            workspace,
            "prompt_created",
            is_sample=False,
        )
    if stage == "save_prompt_version":
        return _latest_event_at(
            organization,
            workspace,
            "prompt_test_run_completed",
            is_sample=False,
        )
    if stage == "create_second_prompt_version":
        return _latest_event_at(
            organization,
            workspace,
            "prompt_version_created",
            is_sample=False,
        )
    if stage == "compare_prompt_versions":
        return _latest_event_at(
            organization,
            workspace,
            "prompt_version_created",
            is_sample=False,
        )
    if stage == "prompt_next_loop":
        return _latest_event_at(
            organization,
            workspace,
            "prompt_comparison_completed",
            is_sample=False,
        )
    if stage == "create_agent":
        return _latest_goal_selected_at(organization, workspace) or getattr(
            workspace,
            "created_at",
            None,
        )
    if stage == "run_agent_scenario":
        return _first_agent_created_at(activation_state, workspace) or _latest_event_at(
            organization,
            workspace,
            "agent_created",
            is_sample=False,
        )
    if stage == "review_agent_trace":
        return _latest_event_at(
            organization,
            workspace,
            "agent_prototype_run_completed",
            is_sample=False,
        ) or ((activation_state.get("agent") or {}).get("run_completed_at"))
    if stage in {"save_agent_eval", "agent_create_eval"}:
        return _latest_event_at(
            organization,
            workspace,
            "agent_trace_reviewed",
            is_sample=False,
        )
    if stage == "configure_gateway_provider":
        return _latest_goal_selected_at(organization, workspace) or getattr(
            workspace,
            "created_at",
            None,
        )
    if stage == "create_gateway_key":
        return _first_gateway_provider_created_at(
            activation_state,
            organization,
            workspace,
        ) or _latest_event_at(
            organization,
            workspace,
            "gateway_provider_added",
            is_sample=False,
        )
    if stage == "run_gateway_request":
        return _first_gateway_key_created_at(
            activation_state,
            organization,
        ) or _latest_event_at(
            organization,
            workspace,
            "gateway_key_created",
            is_sample=False,
        )
    if stage in {"review_gateway_log", "fix_gateway_failure"}:
        return _first_gateway_request_created_at(
            activation_state,
            organization,
            workspace,
        ) or _latest_event_at(
            organization,
            workspace,
            "gateway_request_seen",
            is_sample=False,
        )
    if stage == "add_gateway_policy":
        return _latest_event_at(
            organization,
            workspace,
            "gateway_log_opened",
            is_sample=False,
        )
    if stage == "create_eval_dataset":
        return _latest_goal_selected_at(organization, workspace) or getattr(
            workspace,
            "created_at",
            None,
        )
    if stage == "add_eval_scorer":
        return _first_eval_source_created_at(
            activation_state,
            workspace,
        ) or _latest_event_at(
            organization,
            workspace,
            "eval_dataset_created",
            is_sample=False,
        )
    if stage == "run_eval":
        return _first_eval_scorer_created_at(
            activation_state,
            organization,
            workspace,
        )
    if stage == "review_eval_failures":
        eval_state = activation_state.get("eval") or {}
        return eval_state.get("run_completed_at") or _latest_event_at(
            organization,
            workspace,
            "eval_run_completed",
            is_sample=False,
        )
    if stage == "eval_next_loop":
        eval_state = activation_state.get("eval") or {}
        return eval_state.get("reviewed_at") or _latest_event_at(
            organization,
            workspace,
            "eval_failures_reviewed",
            is_sample=False,
        )
    if stage == "create_voice_agent":
        return _latest_goal_selected_at(organization, workspace) or getattr(
            workspace,
            "created_at",
            None,
        )
    if stage == "run_voice_test_call":
        voice_state = activation_state.get("voice") or {}
        if (
            voice_state.get("call_failed")
            and voice_state.get("has_review")
            and voice_state.get("has_success_criteria")
        ):
            return (
                voice_state.get("success_criteria_at")
                or _latest_event_at(
                    organization,
                    workspace,
                    "voice_success_criteria_added",
                    is_sample=False,
                )
                or voice_state.get("reviewed_at")
                or _latest_event_at(
                    organization,
                    workspace,
                    "voice_call_reviewed",
                    is_sample=False,
                )
            )
        return _first_voice_agent_created_at(
            activation_state,
            workspace,
        ) or _latest_event_at(
            organization,
            workspace,
            "voice_agent_created",
            is_sample=False,
        )
    if stage == "review_voice_call":
        voice_state = activation_state.get("voice") or {}
        return voice_state.get("call_completed_at") or _latest_event_at(
            organization,
            workspace,
            "voice_test_call_completed",
            is_sample=False,
        )
    if stage == "add_voice_success_criteria":
        voice_state = activation_state.get("voice") or {}
        return voice_state.get("reviewed_at") or _latest_event_at(
            organization,
            workspace,
            "voice_call_reviewed",
            is_sample=False,
        )
    if stage == "voice_monitor_calls":
        voice_state = activation_state.get("voice") or {}
        return voice_state.get("success_criteria_at") or _latest_event_at(
            organization,
            workspace,
            "voice_success_criteria_added",
            is_sample=False,
        )
    if stage in {"activated", "daily_review"}:
        last_event = activation_state.get("last_meaningful_event") or {}
        return last_event.get("occurred_at") or now
    return getattr(workspace, "created_at", None)


def _preference_for(user, organization, workspace):
    return (
        OnboardingLifecyclePreference.no_workspace_objects.filter(
            user=user,
            organization=organization,
            workspace=workspace,
        ).first()
        or OnboardingLifecyclePreference.no_workspace_objects.filter(
            user=user,
            organization=organization,
            workspace__isnull=True,
        ).first()
    )


def _target_event_complete(organization, workspace, campaign):
    return lifecycle_target_completed(
        organization=organization,
        workspace=workspace,
        campaign=campaign,
    )


def _target_url(activation_state, campaign):
    if not campaign:
        return None

    strategy = campaign["route_strategy"]
    route_availability = activation_state.get("route_availability") or {}
    recommended_action = activation_state.get("recommended_action") or {}

    if strategy == "home_choose_goal":
        return _url_with_campaign_params(
            "/dashboard/home?onboarding=choose-goal",
            campaign,
        )
    if strategy == "activation_recommendation":
        if recommended_action.get("id") != campaign["target_action_id"]:
            return None
        return _url_with_campaign_params(recommended_action.get("href"), campaign)
    if strategy == "sample_project":
        sample = activation_state.get("sample_project") or {}
        if sample.get("available") and not sample.get("is_hidden"):
            return _url_with_campaign_params(
                sample.get("entry_route") or sample.get("href"),
                campaign,
            )
        route = route_availability.get("sample_trace") or {}
        href = _available_route_href(route)
        return _url_with_campaign_params(href, campaign)
    if strategy == "artifact_deep_link":
        if recommended_action.get("id") == campaign["target_action_id"]:
            return _url_with_campaign_params(recommended_action.get("href"), campaign)
        route_key = (configured_action(campaign["target_action_id"]) or {}).get(
            "route_key"
        )
        route = route_availability.get(route_key) or {}
        href = _available_route_href(route)
        return _url_with_campaign_params(href, campaign)
    if strategy == "daily_quality":
        route = route_availability.get("daily_quality_home") or {}
        href = _available_route_href(route)
        return _url_with_campaign_params(href, campaign)
    return None


def _safe_internal_url(url):
    if not isinstance(url, str):
        return None
    url = url.strip()
    if not url or not url.startswith("/") or url.startswith("//"):
        return None
    return url


def _available_route_href(route):
    if not route or not route.get("is_available"):
        return None
    return _safe_internal_url(route.get("href"))


def _url_with_campaign_params(url, campaign):
    url = _safe_internal_url(url)
    if not url:
        return None
    params = urlencode(
        {
            "source": "onboarding_email",
            "campaign_key": campaign["campaign_key"],
            "target_event": campaign["target_success_event"],
        }
    )
    separator = "&" if "?" in url else "?"
    return f"{url}{separator}{params}"


def _campaign_permission_suppression(activation_state, campaign):
    if not campaign:
        return None
    permissions = activation_state.get("permissions") or {}
    try:
        action = configured_action(campaign["target_action_id"])
    except KeyError:
        action = {}
    requires_permission = action.get("requires_permission")
    if requires_permission and not permissions.get("can_write"):
        return "permission_limited"
    if activation_state.get("stage") == "permission_limited":
        return "permission_limited"
    return None


def _sample_suppression(activation_state, campaign):
    if not campaign:
        return None
    sample_project = activation_state.get("sample_project") or {}
    signals = activation_state.get("signals") or {}
    if campaign["sample_policy"] == "sample_only":
        if campaign.get("primary_path") == "gateway":
            gateway_state = activation_state.get("gateway") or {}
            if signals.get("gateway_requests", 0) > 0 or gateway_state.get(
                "has_request"
            ):
                return "target_event_complete"
            if not (
                gateway_state.get("is_sample")
                or signals.get("gateway_sample_request_count", 0) > 0
            ):
                return "sample_not_allowed"
            return None
        if sample_project.get("is_hidden"):
            return "sample_hidden"
        if not sample_project.get("available"):
            return "sample_not_allowed"
        if signals.get("traces", 0) > 0:
            return "target_event_complete"
    if campaign["sample_policy"] == "real_only":
        last_event = activation_state.get("last_meaningful_event") or {}
        if activation_state.get("is_activated") and last_event.get("is_sample"):
            return "sample_not_allowed"
    return None


def _path_changed(activation_state, campaign):
    if not campaign:
        return False
    campaign_path = campaign.get("primary_path")
    return campaign_path not in {"any", activation_state.get("primary_path")}


def apply_lifecycle_suppressions(
    *,
    user,
    organization,
    workspace,
    activation_state,
    campaign,
    now,
    flags,
    target_url,
    eligible_at,
    skip_frequency=False,
):
    stage = activation_state.get("stage")
    if stage == "feature_disabled":
        return "feature_disabled", {}
    if stage == "workspace_missing":
        return "workspace_suppressed", {}
    if not getattr(user, "is_active", False) or not getattr(
        workspace,
        "is_active",
        False,
    ):
        return "workspace_inactive", {}
    if not getattr(user, "email", ""):
        return "missing_email", {}

    preference = _preference_for(user, organization, workspace)
    if preference:
        if not preference.onboarding_enabled or preference.unsubscribed_at:
            return "user_unsubscribed", {"preference_id": str(preference.id)}
        if preference.snoozed_until and preference.snoozed_until > now:
            return "user_snoozed", {
                "snoozed_until": preference.snoozed_until.isoformat()
            }
        if campaign:
            group_field = {
                "welcome": "first_action_recovery_enabled",
                "recovery": "first_action_recovery_enabled",
                "sample": "sample_bridge_enabled",
                "first_signal": "first_action_recovery_enabled",
                "prompt": "first_action_recovery_enabled",
                "agent": "first_action_recovery_enabled",
                "gateway": "first_action_recovery_enabled",
                "eval": "first_action_recovery_enabled",
                "voice": "first_action_recovery_enabled",
                "next_loop": "next_loop_enabled",
                "activation_success": "daily_digest_enabled",
            }.get(campaign["campaign_group"])
            if group_field and not getattr(preference, group_field):
                return "user_unsubscribed", {"preference_id": str(preference.id)}

    if not flags.get("onboarding_lifecycle_dry_run_enabled"):
        return "dry_run_flag_off", {}
    if not campaign:
        return "no_matching_campaign", {}
    if not flags.get(campaign["dry_run_flag"]):
        return "dry_run_flag_off", {"flag": campaign["dry_run_flag"]}
    if _target_event_complete(organization, workspace, campaign):
        return "target_event_complete", {"event": campaign["target_success_event"]}
    if _path_changed(activation_state, campaign):
        return "path_changed", {"campaign_path": campaign.get("primary_path")}

    permission_reason = _campaign_permission_suppression(activation_state, campaign)
    if permission_reason:
        return permission_reason, {}

    sample_reason = _sample_suppression(activation_state, campaign)
    if sample_reason:
        return sample_reason, {}
    if not target_url:
        return "route_unavailable", {"route_strategy": campaign["route_strategy"]}
    if eligible_at is None:
        return "activation_state_error", {"reason": "stage_start_missing"}
    if now < eligible_at:
        return "wait_window_open", {"eligible_at": eligible_at.isoformat()}

    if not skip_frequency:
        frequency_reason = frequency_cap_suppression(
            user=user,
            workspace=workspace,
            campaign=campaign,
            now=now,
        )
        if frequency_reason:
            return frequency_reason, {}
    return None, {}


def evaluate_lifecycle_decision(
    *,
    user,
    organization,
    workspace,
    activation_state,
    flags,
    now=None,
    run_id=None,
    source="preview",
    campaign_key=None,
    skip_frequency=False,
):
    now = now or timezone.now()
    run_id = run_id or uuid.uuid4()
    started_at = (
        stage_started_at(
            activation_state=activation_state,
            organization=organization,
            workspace=workspace,
            now=now,
        )
        if workspace
        else None
    )
    campaign = choose_lifecycle_campaign(
        activation_state,
        campaign_key=campaign_key,
        started_at=started_at,
        now=now,
    )
    eligible_at = (
        started_at + timedelta(minutes=campaign["wait_window_minutes"])
        if started_at and campaign
        else None
    )
    target_url = _target_url(activation_state, campaign)
    reason, details = apply_lifecycle_suppressions(
        user=user,
        organization=organization,
        workspace=workspace,
        activation_state=activation_state,
        campaign=campaign,
        now=now,
        flags=flags,
        target_url=target_url,
        eligible_at=eligible_at,
        skip_frequency=skip_frequency,
    )
    status = (
        OnboardingLifecycleEvaluationLog.STATUS_ELIGIBLE
        if reason is None
        else (
            OnboardingLifecycleEvaluationLog.STATUS_SKIPPED
            if reason in {"no_matching_campaign", "workspace_inactive", "missing_email"}
            else OnboardingLifecycleEvaluationLog.STATUS_SUPPRESSED
        )
    )
    metadata = {
        "source": source,
        "send_enabled": bool(flags.get("onboarding_lifecycle_send_enabled")),
    }
    digest_preview = build_lifecycle_digest_preview(
        organization=organization,
        workspace=workspace,
        activation_state=activation_state,
        campaign=campaign,
        now=now,
    )
    if digest_preview:
        metadata["digest_preview"] = digest_preview

    return LifecycleDecision(
        run_id=run_id,
        user=user,
        organization=organization,
        workspace=workspace,
        status=status,
        campaign=campaign,
        activation_state=activation_state,
        target_url=target_url,
        eligible_at=eligible_at,
        suppression_reason=reason,
        suppression_details=details,
        evaluated_at=now,
        metadata=metadata,
    )


def write_lifecycle_evaluation(decision):
    payload = decision.to_payload()
    campaign = decision.campaign or {}
    return OnboardingLifecycleEvaluationLog.no_workspace_objects.create(
        run_id=decision.run_id,
        user=decision.user,
        organization=decision.organization,
        workspace=decision.workspace,
        campaign_key=campaign.get("campaign_key"),
        campaign_group=campaign.get("campaign_group"),
        template_key=campaign.get("template_key"),
        template_version=campaign.get("template_version"),
        activation_stage=payload["activation_stage"],
        primary_path=payload["primary_path"],
        recommendation_id=payload["recommendation_id"],
        target_action_id=payload["target_action_id"],
        target_success_event=payload["target_success_event"],
        target_url=payload["target_url"],
        status=decision.status,
        suppression_reason=decision.suppression_reason,
        suppression_details=payload["suppression_details"],
        eligible_at=decision.eligible_at,
        evaluated_at=decision.evaluated_at,
        activation_state_snapshot=payload["activation_state_snapshot"],
        registry_snapshot=payload["registry_snapshot"],
        metadata=payload["metadata"],
    )


def lifecycle_preview_from_decision(decision, *, flags):
    campaign = decision.campaign or {}
    metadata = decision.metadata or {}
    return {
        "dry_run_enabled": bool(flags.get("onboarding_lifecycle_dry_run_enabled")),
        "send_enabled": bool(flags.get("onboarding_lifecycle_send_enabled")),
        "status": decision.status,
        "next_campaign_key": campaign.get("campaign_key"),
        "template_key": campaign.get("template_key"),
        "eligible_at": decision.eligible_at,
        "suppressed": decision.status
        != OnboardingLifecycleEvaluationLog.STATUS_ELIGIBLE,
        "suppression_reason": decision.suppression_reason,
        "target_success_event": campaign.get("target_success_event"),
        "target_action_id": campaign.get("target_action_id"),
        "target_url": decision.target_url,
        "digest_preview": metadata.get("digest_preview"),
        "dry_run_only": not bool(flags.get("onboarding_lifecycle_send_enabled")),
    }
