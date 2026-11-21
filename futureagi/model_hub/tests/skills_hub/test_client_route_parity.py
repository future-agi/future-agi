"""Client registrations must resolve against the mounted URL tree, not just Swagger."""

import json
import re
from pathlib import Path

import pytest
from django.urls import include, path, resolve

ROOT = Path(__file__).resolve().parents[4]
PREFIX = "/model-hub/skills/"
urlpatterns = [path("model-hub/skills/", include("model_hub.urls_skills_hub"))]


def concrete_path(template):
    def substitute(match):
        name = match.group(1)
        if name == "number":
            return "1"
        if name == "label":
            return "production"
        if name == "source":
            return "skills_text"
        assert name.endswith("_id"), (template, name)
        return "11111111-1111-4111-8111-111111111111"

    return re.sub(r"\{([^}]+)\}", substitute, template)


@pytest.mark.parametrize("suffix", ["", "annotations/"])
def test_evidence_source_paths_are_mounted(suffix):
    template = PREFIX + "evidence/{source}/{source_id}/" + suffix
    match = resolve(concrete_path(template), urlconf=__name__)
    assert match.kwargs["source"] == "skills_text"
    assert str(match.kwargs["source_id"]) == "11111111-1111-4111-8111-111111111111"


@pytest.mark.parametrize(
    "template,method,name",
    [
        ("archived/", "get", "skills-archived"),
        (
            "archived/{resource_id}/restore-review/",
            "get",
            "skills-archived-restore-review",
        ),
        ("archived/{resource_id}/restore/", "post", "skills-archived-restore"),
    ],
)
def test_archived_skill_routes_are_mounted(template, method, name):
    # These routes must resolve before the integrator regenerates client contracts.
    match = resolve(concrete_path(PREFIX + template), urlconf=__name__)
    assert match.url_name == name
    view = match.func.view_class
    assert method in view.http_method_names
    assert callable(getattr(view, method, None))
    assert view.private_resource is True


def test_unknown_path_parameters_are_not_silently_substituted():
    with pytest.raises(AssertionError):
        concrete_path(PREFIX + "{unknown}/")


def client_sources(directory):
    # Negative contract fixtures deliberately name unmounted historical routes.
    # Audit shipped registrations, not strings in the tests of those registrations.
    return sorted(
        source
        for source in directory.glob("*.js")
        if not source.name.endswith((".test.js", ".spec.js"))
    )


def test_client_source_scan_excludes_negative_test_fixtures(tmp_path):
    for name in ("endpoints.js", "runtime.js", "endpoints.test.js", "runtime.spec.js"):
        (tmp_path / name).write_text("")
    assert [source.name for source in client_sources(tmp_path)] == [
        "endpoints.js",
        "runtime.js",
    ]


def test_every_frontend_skills_registration_is_mounted():
    files = client_sources(ROOT / "frontend/src/api/skills")
    assert files, "The frontend client sources must be present for this gate."
    templates = {
        template
        for source in files
        for template in re.findall(
            r"['\"](/model-hub/skills/[^'\"\s]*)['\"]", source.read_text()
        )
    }
    assert len(templates) >= 70, "Do not silently shrink the inspected client surface."
    for template in sorted(templates):
        match = resolve(concrete_path(template), urlconf=__name__)
        assert getattr(match.func, "view_class", None) is not None, template


def test_every_generated_skills_operation_has_a_mounted_method():
    contract = json.loads((ROOT / "api_contracts/openapi/swagger.json").read_text())
    paths = {
        route: item
        for route, item in contract["paths"].items()
        if route.startswith(PREFIX)
    }
    assert len(paths) >= 129
    for template, operations in paths.items():
        match = resolve(concrete_path(template), urlconf=__name__)
        view = getattr(match.func, "view_class", None)
        assert view is not None, template
        for method in {"get", "post", "put", "patch", "delete"}.intersection(
            operations
        ):
            assert method in view.http_method_names and callable(
                getattr(view, method, None)
            ), (template, method)
