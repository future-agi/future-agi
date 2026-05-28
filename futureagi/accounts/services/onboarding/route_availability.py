from urllib.parse import urlencode

from accounts.services.onboarding.constants import PRODUCT_PATHS


def route_entry(href, is_available=True, reason=None):
    return {
        "href": href,
        "is_available": is_available,
        "reason": None if is_available else reason,
    }


def _available_if(flag, href, reason="feature_disabled"):
    return route_entry(href, is_available=bool(flag), reason=reason)


def _sample_route(flags, sample_project):
    if not flags.get("onboarding_sample_project"):
        return _available_if(False, "/dashboard/home?sample=true")
    if not sample_project:
        return route_entry("/dashboard/home?sample=true")
    if sample_project.get("is_hidden"):
        return route_entry(
            sample_project.get("href") or "/dashboard/home?sample=true",
            is_available=False,
            reason=sample_project.get("blocked_reason") or "sample_hidden",
        )
    if not sample_project.get("available"):
        return route_entry(
            sample_project.get("href") or "/dashboard/home?sample=true",
            is_available=False,
            reason=sample_project.get("blocked_reason") or "sample_artifact_missing",
        )
    return route_entry(
        sample_project.get("entry_route")
        or sample_project.get("href")
        or "/dashboard/home?sample=true"
    )


def _with_query(path, params):
    clean_params = {
        key: value for key, value in params.items() if value not in {None, ""}
    }
    if not clean_params:
        return path
    return f"{path}?{urlencode(clean_params)}"


def resolve_route_availability(*, context, flags, signals, sample_project=None):
    can_write = context.permissions["can_write"]
    first_observe_id = signals.first_observe_id
    first_trace_id = signals.first_trace_id
    observe_route_modes_enabled = bool(flags.get("onboarding_observe_route_modes"))
    prompt_id = signals.latest_prompt_id or signals.first_prompt_id
    prompt_route_modes_enabled = bool(flags.get("onboarding_prompt_route_modes"))
    prompt_path_enabled = bool(flags.get("onboarding_prompt_path"))
    agent_route_modes_enabled = bool(flags.get("onboarding_agent_route_modes"))
    agent_path_enabled = bool(flags.get("onboarding_agent_path"))
    agent_id = signals.agent_id
    agent_source = signals.agent_source
    agent_test_id = signals.agent_test_id
    agent_execution_id = signals.agent_execution_id
    agent_graph_execution_id = signals.agent_graph_execution_id
    gateway_route_modes_enabled = bool(flags.get("onboarding_gateway_route_modes"))
    gateway_path_enabled = bool(flags.get("onboarding_gateway_path"))
    gateway_request_id = signals.gateway_request_id
    gateway_policy_route = signals.gateway_policy_route or "budget"
    eval_route_modes_enabled = bool(flags.get("onboarding_eval_route_modes"))
    eval_path_enabled = bool(flags.get("onboarding_eval_path"))
    eval_source_type = signals.eval_source_type
    eval_source_id = signals.eval_source_id
    eval_scorer_template_id = signals.eval_scorer_template_id
    eval_run_id = signals.eval_run_id
    voice_route_modes_enabled = bool(flags.get("onboarding_voice_route_modes"))
    voice_path_enabled = bool(flags.get("onboarding_voice_path"))
    voice_agent_id = signals.voice_agent_id
    voice_run_test_id = signals.voice_run_test_id
    voice_test_execution_id = signals.voice_test_execution_id
    voice_call_execution_id = signals.voice_call_execution_id

    prompt_workbench_href = "/dashboard/workbench/all?source=onboarding"
    prompt_create_href = (
        f"{prompt_workbench_href}&action=create-prompt"
        if prompt_route_modes_enabled
        else "/dashboard/workbench/all"
    )
    prompt_editor_href = (
        f"/dashboard/workbench/create/{prompt_id}"
        if prompt_id
        else "/dashboard/workbench/all"
    )

    def prompt_route(mode, fallback_reason="missing_id", requires_write=True):
        if not prompt_path_enabled:
            return route_entry(
                prompt_editor_href,
                is_available=False,
                reason="feature_disabled",
            )
        if requires_write and not can_write:
            return route_entry(
                prompt_editor_href,
                is_available=False,
                reason="missing_permission",
            )
        if not prompt_id:
            return route_entry(
                prompt_workbench_href,
                is_available=False,
                reason=fallback_reason,
            )
        suffix = (
            f"?source=onboarding&onboarding={mode}"
            if prompt_route_modes_enabled
            else ""
        )
        return route_entry(f"{prompt_editor_href}{suffix}", is_available=True)

    agent_create_href = (
        "/dashboard/agents?onboarding=create"
        if agent_route_modes_enabled
        else "/dashboard/agents"
    )

    def agent_route(href, *, requires_write=True, is_available=True, reason=None):
        if not agent_path_enabled:
            return route_entry(href, is_available=False, reason="feature_disabled")
        if requires_write and not can_write:
            return route_entry(href, is_available=False, reason="missing_permission")
        return route_entry(href, is_available=is_available, reason=reason)

    def gateway_route(href, *, requires_write=True, is_available=True, reason=None):
        if not gateway_path_enabled:
            return route_entry(href, is_available=False, reason="feature_disabled")
        if not signals.gateway_available or signals.gateway_guard_blocked:
            return route_entry(
                href,
                is_available=False,
                reason=reason or "route_not_implemented",
            )
        if requires_write and not can_write:
            return route_entry(href, is_available=False, reason="missing_permission")
        return route_entry(href, is_available=is_available, reason=reason)

    def eval_route(href, *, requires_write=True, is_available=True, reason=None):
        if not eval_path_enabled:
            return route_entry(href, is_available=False, reason="feature_disabled")
        if requires_write and not can_write:
            return route_entry(href, is_available=False, reason="missing_permission")
        return route_entry(href, is_available=is_available, reason=reason)

    def voice_route(href, *, requires_write=True, is_available=True, reason=None):
        if not voice_path_enabled:
            return route_entry(href, is_available=False, reason="feature_disabled")
        if requires_write and not can_write:
            return route_entry(href, is_available=False, reason="missing_permission")
        return route_entry(href, is_available=is_available, reason=reason)

    if agent_source == "simulate":
        agent_run_href = (
            f"/dashboard/simulate/test/{agent_test_id}?onboarding=run-test"
            if agent_test_id
            else "/dashboard/simulate/test?onboarding=create-test"
        )
    else:
        agent_run_href = (
            f"/dashboard/agents/playground/{agent_id}/build"
            if agent_id
            else agent_create_href
        )
        if agent_route_modes_enabled and agent_id:
            agent_run_href = f"{agent_run_href}?onboarding=run-scenario"

    if agent_source == "simulate" and agent_test_id and agent_execution_id:
        agent_review_href = (
            f"/dashboard/simulate/test/{agent_test_id}/{agent_execution_id}/"
            "call-details?from=onboarding"
        )
    elif agent_id and (agent_graph_execution_id or agent_source == "agent_playground"):
        agent_review_href = f"/dashboard/agents/playground/{agent_id}/executions"
        if agent_route_modes_enabled:
            agent_review_href = f"{agent_review_href}?onboarding=review-run"
    else:
        agent_review_href = "/dashboard/home?reason=route-unavailable"

    agent_eval_href = (
        f"/dashboard/simulate/test/{agent_test_id}"
        if agent_test_id
        else "/dashboard/simulate/test"
    )
    if agent_route_modes_enabled:
        separator = "&" if "?" in agent_eval_href else "?"
        agent_save_eval_href = f"{agent_eval_href}{separator}onboarding=save-eval"
        agent_create_eval_href = f"{agent_eval_href}{separator}onboarding=create-eval"
    else:
        agent_save_eval_href = agent_eval_href
        agent_create_eval_href = agent_eval_href

    agent_quality_href = (
        f"/dashboard/agents/playground/{agent_id}/executions"
        if agent_id and agent_source == "agent_playground"
        else "/dashboard/agents"
    )
    gateway_overview_href = "/dashboard/gateway"
    gateway_provider_href = (
        "/dashboard/gateway/providers?source=onboarding"
        if gateway_route_modes_enabled
        else "/dashboard/gateway/providers"
    )
    gateway_key_href = (
        "/dashboard/gateway/keys?source=onboarding"
        if gateway_route_modes_enabled
        else "/dashboard/gateway/keys"
    )
    gateway_request_href = (
        "/dashboard/gateway?onboarding=test-request"
        if gateway_route_modes_enabled
        else "/dashboard/gateway"
    )
    gateway_log_params = {}
    if gateway_route_modes_enabled:
        gateway_log_params["onboarding"] = (
            "review-request" if gateway_request_id else "first-request"
        )
    if gateway_request_id:
        gateway_log_params["request_id"] = gateway_request_id
    gateway_log_review_href = _with_query("/dashboard/gateway/logs", gateway_log_params)
    gateway_failure_params = {}
    if gateway_route_modes_enabled:
        gateway_failure_params["onboarding"] = "fix-failure"
    if gateway_request_id:
        gateway_failure_params["request_id"] = gateway_request_id
    gateway_failure_href = _with_query(
        "/dashboard/gateway/logs", gateway_failure_params
    )
    gateway_policy_path = {
        "guardrail": "/dashboard/gateway/guardrails",
        "fallback": "/dashboard/gateway/fallbacks",
        "budget": "/dashboard/gateway/budgets",
    }.get(gateway_policy_route, "/dashboard/gateway/budgets")
    gateway_policy_params = {}
    if gateway_route_modes_enabled:
        gateway_policy_params["source"] = "onboarding"
    if gateway_request_id:
        gateway_policy_params["request_id"] = gateway_request_id
    gateway_policy_href = _with_query(gateway_policy_path, gateway_policy_params)

    eval_list_href = "/dashboard/evaluations"
    eval_create_data_href = (
        "/dashboard/evaluations/create?source=onboarding&step=data"
        if eval_route_modes_enabled
        else "/dashboard/evaluations/create"
    )
    eval_scorer_params = {}
    if eval_route_modes_enabled:
        eval_scorer_params.update(
            {
                "source": "onboarding",
                "step": "scorer",
                "source_type": eval_source_type,
                "source_id": eval_source_id,
            }
        )
    eval_add_scorer_href = _with_query(
        "/dashboard/evaluations/create",
        eval_scorer_params,
    )
    eval_run_base = (
        f"/dashboard/evaluations/create/{eval_scorer_template_id}"
        if eval_scorer_template_id
        else "/dashboard/evaluations/create"
    )
    eval_run_params = {}
    if eval_route_modes_enabled:
        eval_run_params.update(
            {
                "source": "onboarding",
                "step": "run",
                "source_type": eval_source_type,
                "source_id": eval_source_id,
            }
        )
    eval_run_href = _with_query(eval_run_base, eval_run_params)
    eval_review_base = (
        f"/dashboard/evaluations/{eval_scorer_template_id}"
        if eval_scorer_template_id
        else "/dashboard/evaluations/usage"
    )
    eval_review_params = {}
    if eval_route_modes_enabled:
        eval_review_params.update(
            {
                "tab": "usage",
                "source": "onboarding",
                "step": "review",
                "run_id": eval_run_id,
            }
        )
    eval_review_href = _with_query(eval_review_base, eval_review_params)
    if eval_source_type == "dataset" and eval_source_id:
        eval_source_fix_base = f"/dashboard/develop/{eval_source_id}"
    elif eval_source_type == "trace_project" and eval_source_id:
        eval_source_fix_base = f"/dashboard/observe/{eval_source_id}/llm-tracing"
    else:
        eval_source_fix_base = "/dashboard/evaluations/usage"
    eval_source_fix_params = {}
    if eval_route_modes_enabled:
        eval_source_fix_params.update(
            {
                "source": "onboarding",
                "step": "fix-eval-failure",
                "source_type": eval_source_type,
                "source_id": eval_source_id,
                "eval_id": eval_scorer_template_id,
                "run_id": eval_run_id,
            }
        )
    eval_source_fix_href = _with_query(
        eval_source_fix_base,
        eval_source_fix_params,
    )
    voice_list_href = "/dashboard/simulate/agent-definitions"
    voice_create_params = {}
    if voice_route_modes_enabled:
        voice_create_params["source"] = "onboarding"
        voice_create_params["onboarding"] = "create-voice-agent"
    voice_create_href = _with_query(
        "/dashboard/simulate/agent-definitions/create-new-agent-definition",
        voice_create_params,
    )
    voice_run_params = {}
    if voice_route_modes_enabled:
        voice_run_params["onboarding"] = (
            "run-test-call" if voice_run_test_id else "create-test-call"
        )
    if voice_agent_id:
        voice_run_params["agent_definition_id"] = voice_agent_id
    voice_run_base = (
        f"/dashboard/simulate/test/{voice_run_test_id}/runs"
        if voice_run_test_id
        else "/dashboard/simulate/test"
    )
    voice_run_href = _with_query(voice_run_base, voice_run_params)
    voice_review_base = (
        f"/dashboard/simulate/test/{voice_run_test_id}/"
        f"{voice_test_execution_id}/call-details"
        if voice_run_test_id and voice_test_execution_id
        else "/dashboard/simulate/test"
    )
    voice_review_params = {}
    if voice_route_modes_enabled:
        voice_review_params["from"] = "onboarding"
        voice_review_params["onboarding"] = "review-voice-call"
    if voice_call_execution_id:
        voice_review_params["call_id"] = voice_call_execution_id
    voice_review_href = _with_query(voice_review_base, voice_review_params)
    voice_criteria_params = {}
    if voice_route_modes_enabled:
        voice_criteria_params["onboarding"] = "success-criteria"
    if voice_call_execution_id:
        voice_criteria_params["call_id"] = voice_call_execution_id
    voice_criteria_base = (
        f"/dashboard/simulate/test/{voice_run_test_id}/runs"
        if voice_run_test_id
        else "/dashboard/simulate/test"
    )
    voice_criteria_href = _with_query(voice_criteria_base, voice_criteria_params)
    voice_monitor_params = {}
    if voice_route_modes_enabled:
        voice_monitor_params["onboarding"] = "monitor-calls"
    voice_monitor_base = (
        f"/dashboard/simulate/test/{voice_run_test_id}/call-logs"
        if voice_run_test_id
        else voice_list_href
    )
    voice_monitor_href = _with_query(voice_monitor_base, voice_monitor_params)

    observe_project_base = (
        f"/dashboard/observe/{first_observe_id}/llm-tracing"
        if first_observe_id
        else "/dashboard/observe"
    )
    observe_project_available = bool(first_observe_id)
    observe_send_trace_href = _with_query(
        observe_project_base,
        (
            {"source": "onboarding", "onboarding": "send-first-trace"}
            if observe_route_modes_enabled
            else {}
        ),
    )
    observe_create_evaluator_href = _with_query(
        observe_project_base,
        (
            {"source": "onboarding", "onboarding": "create-evaluator"}
            if observe_route_modes_enabled
            else {}
        ),
    )
    observe_trace_detail_base = (
        f"/dashboard/observe/{first_observe_id}/trace/{first_trace_id}"
        if first_observe_id and first_trace_id
        else "/dashboard/observe"
    )
    observe_trace_detail_href = _with_query(
        observe_trace_detail_base,
        (
            {"source": "onboarding", "onboarding": "review-first-trace"}
            if observe_route_modes_enabled and first_observe_id and first_trace_id
            else {}
        ),
    )

    routes = {
        "home": route_entry("/dashboard/home"),
        "get_started": route_entry("/dashboard/get-started"),
        "workspace_list": route_entry("/dashboard/settings/user-management"),
        "choose_goal": _available_if(
            flags.get("onboarding_goal_picker"),
            "/dashboard/home?mode=choose-goal",
        ),
        "observe_setup": route_entry(
            "/dashboard/observe?setup=true&source=onboarding",
            is_available=can_write,
            reason="missing_permission",
        ),
        "observe_project": route_entry(
            observe_project_base,
            is_available=observe_project_available,
            reason="missing_id",
        ),
        "observe_send_first_trace": route_entry(
            observe_send_trace_href,
            is_available=observe_project_available,
            reason="missing_id",
        ),
        "observe_create_trace_evaluator": route_entry(
            observe_create_evaluator_href,
            is_available=observe_project_available,
            reason="missing_id",
        ),
        "observe_trace_detail": route_entry(
            observe_trace_detail_href,
            is_available=bool(first_observe_id and first_trace_id),
            reason="missing_id",
        ),
        "observe_dashboard": route_entry(
            f"/dashboard/observe/{first_observe_id}"
            if first_observe_id
            else "/dashboard/observe",
            is_available=True,
        ),
        "sample_trace": _sample_route(flags, sample_project),
        "support": route_entry("/dashboard/get-started?support=true"),
        "daily_quality_home": _available_if(
            flags.get("onboarding_daily_quality_home"),
            "/dashboard/home?mode=daily-quality",
        ),
        "prompt_workbench": _available_if(
            prompt_path_enabled,
            prompt_workbench_href,
        ),
        "prompt_create": route_entry(
            prompt_create_href,
            is_available=prompt_path_enabled and can_write,
            reason="missing_permission" if prompt_path_enabled else "feature_disabled",
        ),
        "prompt_run_test": prompt_route("run-test"),
        "prompt_save_version": prompt_route("save-version"),
        "prompt_compare_versions": prompt_route("compare"),
        "prompt_add_failure": prompt_route("add-failure"),
        "prompt_metrics": prompt_route("metrics", requires_write=False),
        "agent_list": _available_if(agent_path_enabled, "/dashboard/agents"),
        "agent_create": agent_route(agent_create_href),
        "agent_run_scenario": agent_route(
            agent_run_href,
            is_available=bool(agent_id or agent_source == "simulate"),
            reason="missing_id",
        ),
        "agent_review_trace": agent_route(
            agent_review_href,
            requires_write=False,
            is_available=bool(agent_execution_id or agent_graph_execution_id),
            reason="missing_id",
        ),
        "agent_save_eval": agent_route(agent_save_eval_href),
        "agent_create_eval": agent_route(agent_create_eval_href),
        "agent_quality": agent_route(agent_quality_href, requires_write=False),
        "gateway_overview": gateway_route(gateway_overview_href, requires_write=False),
        "gateway_provider": gateway_route(gateway_provider_href),
        "gateway_key": gateway_route(gateway_key_href),
        "gateway_request": gateway_route(gateway_request_href),
        "gateway_log_review": gateway_route(
            gateway_log_review_href,
            requires_write=False,
            is_available=bool(signals.gateway_has_request),
            reason="missing_id",
        ),
        "gateway_failure": gateway_route(
            gateway_failure_href,
            requires_write=False,
            is_available=bool(signals.gateway_has_request),
            reason="missing_id",
        ),
        "gateway_policy": gateway_route(gateway_policy_href),
        "eval_list": _available_if(eval_path_enabled, eval_list_href),
        "eval_create_data": eval_route(eval_create_data_href),
        "eval_add_scorer": eval_route(
            eval_add_scorer_href,
            is_available=bool(signals.eval_has_source),
            reason="missing_id",
        ),
        "eval_run_first": eval_route(
            eval_run_href,
            is_available=bool(signals.eval_has_scorer),
            reason="missing_id",
        ),
        "eval_review_failures": eval_route(
            eval_review_href,
            requires_write=False,
            is_available=bool(signals.eval_has_completed_run),
            reason="missing_id",
        ),
        "eval_next_loop": eval_route(
            eval_source_fix_href,
            is_available=bool(signals.eval_has_review and signals.eval_has_failures),
            reason="missing_id",
        ),
        "voice_list": _available_if(voice_path_enabled, voice_list_href),
        "voice_create_agent": voice_route(voice_create_href),
        "voice_run_test_call": voice_route(
            voice_run_href,
            is_available=bool(signals.voice_has_agent),
            reason="missing_id",
        ),
        "voice_review_call": voice_route(
            voice_review_href,
            requires_write=False,
            is_available=bool(signals.voice_has_call),
            reason="missing_id",
        ),
        "voice_add_success_criteria": voice_route(
            voice_criteria_href,
            is_available=bool(signals.voice_has_review),
            reason="missing_id",
        ),
        "voice_monitor_calls": voice_route(
            voice_monitor_href,
            requires_write=False,
            is_available=bool(signals.voice_has_success_criteria),
            reason="missing_id",
        ),
    }

    for path in PRODUCT_PATHS:
        sample_hidden = (
            path == "sample" and sample_project and sample_project["is_hidden"]
        )
        routes[f"path_{path}"] = route_entry(
            f"/dashboard/home?path={path}",
            is_available=(
                path in {"observe", "sample"}
                or (path == "prompt" and prompt_path_enabled)
                or (path == "agent" and agent_path_enabled)
                or (path == "gateway" and gateway_path_enabled)
                or (path == "evals" and eval_path_enabled)
                or (path == "voice" and voice_path_enabled)
            )
            and not sample_hidden,
            reason=(
                sample_project.get("blocked_reason") or "sample_hidden"
                if sample_hidden
                else "route_not_implemented"
            ),
        )
    return routes
