from uuid import uuid4

from accounts.services.onboarding.context import OnboardingContext
from accounts.services.onboarding.route_availability import resolve_route_availability
from accounts.services.onboarding.signal_resolver import OnboardingSignals


def _context(*, can_write=True):
    return OnboardingContext(
        user=None,
        organization=None,
        workspace=None,
        organization_role="Owner" if can_write else "Viewer",
        workspace_role="workspace_admin" if can_write else "workspace_viewer",
        organization_level=15 if can_write else 1,
        workspace_level=8 if can_write else 1,
        selected_goal="monitor_production_ai_app",
        primary_path="observe",
        persona="developer",
        source="test",
        email_context=None,
        permissions={
            "role": "Owner" if can_write else "Viewer",
            "can_read": True,
            "can_write": can_write,
            "can_manage_workspace": can_write,
            "missing_permissions": [] if can_write else ["workspace:write"],
            "request_access_href": "/dashboard/settings/user-management",
            "permission_limited": not can_write,
        },
        warnings=[],
    )


def test_trace_detail_route_requires_owned_ids():
    routes = resolve_route_availability(
        context=_context(),
        flags={
            "onboarding_sample_project": False,
            "onboarding_daily_quality_home": False,
        },
        signals=OnboardingSignals(first_checks={}),
    )

    assert routes["observe_trace_detail"]["is_available"] is False
    assert routes["observe_trace_detail"]["reason"] == "missing_id"


def test_trace_detail_route_uses_signal_ids():
    observe_id = uuid4()
    trace_id = uuid4()
    routes = resolve_route_availability(
        context=_context(),
        flags={
            "onboarding_sample_project": False,
            "onboarding_daily_quality_home": False,
        },
        signals=OnboardingSignals(
            first_checks={},
            first_observe_id=str(observe_id),
            first_trace_id=str(trace_id),
        ),
    )

    assert routes["observe_trace_detail"] == {
        "href": f"/dashboard/observe/{observe_id}/trace/{trace_id}",
        "is_available": True,
        "reason": None,
    }


def test_observe_focus_routes_use_signal_ids():
    observe_id = uuid4()
    routes = resolve_route_availability(
        context=_context(),
        flags={
            "onboarding_observe_route_modes": True,
            "onboarding_sample_project": False,
            "onboarding_daily_quality_home": False,
        },
        signals=OnboardingSignals(
            first_checks={},
            first_observe_id=str(observe_id),
        ),
    )

    assert routes["observe_project"] == {
        "href": f"/dashboard/observe/{observe_id}/llm-tracing",
        "is_available": True,
        "reason": None,
    }
    assert routes["observe_send_first_trace"] == {
        "href": (
            f"/dashboard/observe/{observe_id}/llm-tracing?"
            "source=onboarding&onboarding=send-first-trace"
        ),
        "is_available": True,
        "reason": None,
    }
    assert routes["observe_create_trace_evaluator"] == {
        "href": (
            f"/dashboard/observe/{observe_id}/llm-tracing?"
            "source=onboarding&onboarding=create-evaluator"
        ),
        "is_available": True,
        "reason": None,
    }


def test_eval_source_fix_route_uses_trace_project_context():
    observe_id = uuid4()
    eval_id = uuid4()
    run_id = uuid4()
    routes = resolve_route_availability(
        context=_context(),
        flags={
            "onboarding_eval_path": True,
            "onboarding_eval_route_modes": True,
            "onboarding_sample_project": False,
            "onboarding_daily_quality_home": False,
        },
        signals=OnboardingSignals(
            first_checks={},
            eval_has_failures=True,
            eval_has_review=True,
            eval_run_id=str(run_id),
            eval_scorer_template_id=str(eval_id),
            eval_source_id=str(observe_id),
            eval_source_type="trace_project",
        ),
    )

    assert routes["eval_next_loop"] == {
        "href": (
            f"/dashboard/observe/{observe_id}/llm-tracing?"
            "source=onboarding&step=fix-eval-failure"
            f"&source_type=trace_project&source_id={observe_id}"
            f"&eval_id={eval_id}&run_id={run_id}"
        ),
        "is_available": True,
        "reason": None,
    }


def test_write_route_is_unavailable_for_read_only_user():
    routes = resolve_route_availability(
        context=_context(can_write=False),
        flags={
            "onboarding_sample_project": False,
            "onboarding_daily_quality_home": False,
        },
        signals=OnboardingSignals(first_checks={}),
    )

    assert routes["observe_setup"]["is_available"] is False
    assert routes["observe_setup"]["reason"] == "missing_permission"
