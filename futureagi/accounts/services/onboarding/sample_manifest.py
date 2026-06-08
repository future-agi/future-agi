from copy import deepcopy
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from django.core.exceptions import ImproperlyConfigured

CONFIG_PATH = Path(__file__).with_name("sample_project.yml")
OPTIONAL_SAMPLE_PATHS = frozenset({"prompt", "evals", "agent", "gateway", "voice"})
OPTIONAL_SAMPLE_LAYOUTS = frozenset(
    {"promptDiff", "evalRun", "agentTrace", "gatewayLog", "voiceCall"}
)


def _config_error(message: str) -> ImproperlyConfigured:
    return ImproperlyConfigured(f"Invalid onboarding sample project config: {message}")


def _mapping(value: Any, path: str) -> dict:
    if not isinstance(value, dict):
        raise _config_error(f"{path} must be a mapping.")
    return value


def _required_text(mapping: dict, key: str, path: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value.strip():
        raise _config_error(f"{path}.{key} must be a non-empty string.")
    return value


def _optional_text(mapping: dict, key: str, path: str) -> str | None:
    value = mapping.get(key)
    if value in {None, ""}:
        return None
    if not isinstance(value, str):
        raise _config_error(f"{path}.{key} must be a string.")
    return value


def _optional_mapping(mapping: dict, key: str, path: str) -> dict:
    value = mapping.get(key) or {}
    return _mapping(value, f"{path}.{key}")


def _optional_sequence(mapping: dict, key: str, path: str) -> list:
    value = mapping.get(key) or []
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise _config_error(f"{path}.{key} must be a list of strings.")
    return value


def _internal_route(mapping: dict, key: str, path: str) -> str:
    value = _required_text(mapping, key, path)
    if not value.startswith("/") or value.startswith("//"):
        raise _config_error(f"{path}.{key} must be an internal route.")
    return value


def _load_config_file() -> dict:
    try:
        raw = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    except OSError as exc:
        raise _config_error(f"{CONFIG_PATH.name} could not be read.") from exc
    except yaml.YAMLError as exc:
        raise _config_error(f"{CONFIG_PATH.name} is not valid YAML.") from exc

    return _mapping(raw, CONFIG_PATH.name)


def _validate_timing(span: dict, path: str) -> None:
    timing = _mapping(span.get("timing_ms"), f"{path}.timing_ms")
    for key in ("start", "end", "latency"):
        value = timing.get(key)
        if not isinstance(value, int) or value < 0:
            raise _config_error(f"{path}.timing_ms.{key} must be a positive integer.")
    if timing["end"] < timing["start"]:
        raise _config_error(f"{path}.timing_ms.end must be after start.")


def _validate_artifacts(manifest: dict) -> None:
    artifacts = _mapping(manifest.get("artifacts"), "artifacts")
    project = _mapping(artifacts.get("observe_project"), "artifacts.observe_project")
    _required_text(project, "stable_key", "artifacts.observe_project")
    _required_text(project, "display_name", "artifacts.observe_project")
    _optional_mapping(project, "metadata", "artifacts.observe_project")
    _optional_sequence(project, "tags", "artifacts.observe_project")

    session = _mapping(artifacts.get("trace_session"), "artifacts.trace_session")
    _required_text(session, "stable_key", "artifacts.trace_session")
    _required_text(session, "display_name", "artifacts.trace_session")

    trace = _mapping(artifacts.get("sample_trace"), "artifacts.sample_trace")
    _required_text(trace, "stable_key", "artifacts.sample_trace")
    _required_text(trace, "display_name", "artifacts.sample_trace")
    _required_text(trace, "external_id", "artifacts.sample_trace")
    _mapping(trace.get("input"), "artifacts.sample_trace.input")
    _mapping(trace.get("output"), "artifacts.sample_trace.output")
    _optional_mapping(trace, "metadata", "artifacts.sample_trace")
    _optional_sequence(trace, "tags", "artifacts.sample_trace")

    spans = _mapping(artifacts.get("spans"), "artifacts.spans")
    if not spans:
        raise _config_error("artifacts.spans cannot be empty.")
    seen = set()
    for span_key, span in spans.items():
        path = f"artifacts.spans.{span_key}"
        if not isinstance(span_key, str) or not span_key:
            raise _config_error("artifacts.spans contains an invalid key.")
        span = _mapping(span, path)
        _required_text(span, "stable_key", path)
        _required_text(span, "name", path)
        _required_text(span, "observation_type", path)
        _mapping(span.get("input"), f"{path}.input")
        _mapping(span.get("output"), f"{path}.output")
        _optional_mapping(span, "metadata", path)
        _optional_sequence(span, "tags", path)
        _validate_timing(span, path)
        parent = _optional_text(span, "parent", path)
        if parent and parent not in seen:
            raise _config_error(f"{path}.parent must reference an earlier span.")
        seen.add(span_key)


def _validate_optional_path_preview(
    preview: dict,
    *,
    path_key: str,
    path: str,
) -> None:
    primary_path = _required_text(preview, "primaryPath", path)
    if primary_path != path_key:
        raise _config_error(f"{path}.primaryPath must match {path_key}.")
    layout = _required_text(preview, "layout", path)
    if layout not in OPTIONAL_SAMPLE_LAYOUTS:
        raise _config_error(f"{path}.layout is unsupported.")
    for key in ("eyebrow", "headline", "summary", "ctaLabel", "takeaway"):
        _required_text(preview, key, path)
    starter = _mapping(preview.get("starterAction"), f"{path}.starterAction")
    for key in ("label", "resultLabel", "description"):
        _required_text(starter, key, f"{path}.starterAction")


def _validate_optional_paths(manifest: dict) -> None:
    optional_paths = _mapping(manifest.get("optional_paths"), "optional_paths")
    for path_key, config in optional_paths.items():
        path = f"optional_paths.{path_key}"
        if path_key not in OPTIONAL_SAMPLE_PATHS:
            raise _config_error(f"{path} is not a supported optional sample path.")
        config = _mapping(config, path)
        _required_text(config, "stable_key", path)
        _required_text(config, "display_name", path)
        _required_text(config, "artifact_type", path)
        _internal_route(config, "route", path)
        preview = _mapping(config.get("preview"), f"{path}.preview")
        _validate_optional_path_preview(
            preview,
            path_key=path_key,
            path=f"{path}.preview",
        )


def _validate_manifest(manifest: dict) -> None:
    _required_text(manifest, "schema_version", CONFIG_PATH.name)
    _required_text(manifest, "manifest_id", CONFIG_PATH.name)
    _required_text(manifest, "manifest_version", CONFIG_PATH.name)
    _required_text(manifest, "display_name", CONFIG_PATH.name)
    _required_text(manifest, "domain", CONFIG_PATH.name)
    _required_text(manifest, "sample_label", CONFIG_PATH.name)
    _required_text(manifest, "release_0_entry_path", CONFIG_PATH.name)
    _mapping(manifest.get("story"), "story")
    _mapping(manifest.get("defaults", {}), "defaults")
    _validate_optional_paths(manifest)
    _validate_artifacts(manifest)


@lru_cache(maxsize=1)
def get_default_sample_manifest() -> dict:
    manifest = _load_config_file()
    _validate_manifest(manifest)
    return manifest


DEFAULT_SAMPLE_MANIFEST_ID = get_default_sample_manifest()["manifest_id"]
DEFAULT_SAMPLE_MANIFEST_VERSION = get_default_sample_manifest()["manifest_version"]


def get_sample_manifest(manifest_id=None, manifest_version=None):
    manifest = deepcopy(get_default_sample_manifest())
    expected_id = manifest["manifest_id"]
    expected_version = manifest["manifest_version"]
    if manifest_id and manifest_id != expected_id:
        return None
    if manifest_version and manifest_version != expected_version:
        return None
    return manifest


def sample_idempotency_key(workspace, manifest=None):
    manifest = manifest or get_default_sample_manifest()
    workspace_id = getattr(workspace, "id", workspace)
    return (
        f"sample:{workspace_id}:{manifest['manifest_id']}:"
        f"{manifest['manifest_version']}"
    )
