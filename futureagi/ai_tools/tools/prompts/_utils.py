from typing import Any

from ai_tools.base import ToolContext, ToolResult
from ai_tools.formatting import format_datetime, format_status, markdown_table, section, truncate
from ai_tools.resolvers import is_uuid, resolve_prompt_template


def prompt_template_candidates(context: ToolContext, search: str = ""):
    from model_hub.models.run_prompt import PromptTemplate

    qs = PromptTemplate.objects.filter(
        organization=context.organization,
        deleted=False,
    )
    if context.workspace:
        qs = qs.filter(workspace=context.workspace)

    search = str(search or "").strip()
    if search and not is_uuid(search):
        qs = qs.filter(name__icontains=search)

    return list(qs.order_by("-updated_at")[:10])


def candidate_prompt_templates_result(
    context: ToolContext,
    title: str = "Prompt Template Required",
    detail: str = "",
    search: str = "",
) -> ToolResult:
    templates = prompt_template_candidates(context, search)
    rows = [
        [
            f"`{template.id}`",
            truncate(template.name, 40),
            format_datetime(template.updated_at),
        ]
        for template in templates
    ]
    body = detail or ""
    if rows:
        body = (body + "\n\n" if body else "") + markdown_table(
            ["Template ID", "Name", "Updated"],
            rows,
        )
    else:
        body = body or "No prompt templates found."
    return ToolResult(
        content=section(title, body),
        data={
            "requires_template_id": True,
            "templates": [
                {"id": str(template.id), "name": template.name}
                for template in templates
            ],
        },
    )


def resolve_prompt_template_for_tool(
    template_ref: Any,
    context: ToolContext,
    title: str = "Prompt Template Required",
) -> tuple[Any | None, ToolResult | None]:
    ref = str(template_ref or "").strip()
    if not ref:
        return None, candidate_prompt_templates_result(context, title)

    template, error = resolve_prompt_template(
        ref,
        context.organization,
        context.workspace,
    )
    if error:
        return None, candidate_prompt_templates_result(
            context,
            "Prompt Template Not Found",
            f"{error} Use one of these template IDs or exact names.",
            search="" if is_uuid(ref) else ref,
        )
    return template, None


def prompt_version_candidates(template, search: str = ""):
    from model_hub.models.run_prompt import PromptVersion

    qs = PromptVersion.objects.filter(
        original_template=template,
        deleted=False,
    ).order_by("-created_at")
    search = str(search or "").strip()
    if search and not is_uuid(search):
        qs = qs.filter(template_version__icontains=search)
    return list(qs[:20])


def candidate_prompt_versions_result(
    template,
    title: str = "Prompt Version Required",
    detail: str = "",
    search: str = "",
) -> ToolResult:
    versions = prompt_version_candidates(template, search)
    rows = [
        [
            f"`{version.id}`",
            version.template_version,
            "Yes" if version.is_default else "-",
            "Draft" if version.is_draft else "Committed",
            format_datetime(version.created_at),
        ]
        for version in versions
    ]
    body = detail or ""
    if rows:
        body = (body + "\n\n" if body else "") + markdown_table(
            ["Version ID", "Version", "Default", "Status", "Created"],
            rows,
        )
    else:
        body = body or f"No versions found for prompt template `{template.name}`."
    return ToolResult(
        content=section(title, body),
        data={
            "requires_version_id": True,
            "template_id": str(template.id),
            "versions": [
                {
                    "id": str(version.id),
                    "version": version.template_version,
                    "is_default": version.is_default,
                    "is_draft": version.is_draft,
                }
                for version in versions
            ],
        },
    )


def resolve_prompt_version(
    template,
    version_ref: Any,
    title: str = "Prompt Version Required",
) -> tuple[Any | None, ToolResult | None]:
    ref = str(version_ref or "").strip()
    if not ref:
        return None, candidate_prompt_versions_result(template, title)

    from model_hub.models.run_prompt import PromptVersion

    if ref.lower() in {"default", "current"}:
        version = PromptVersion.objects.filter(
            original_template=template,
            deleted=False,
            is_default=True,
        ).first()
        if version:
            return version, None
    if ref.lower() == "latest":
        version = (
            PromptVersion.objects.filter(original_template=template, deleted=False)
            .order_by("-created_at")
            .first()
        )
        if version:
            return version, None

    candidates = prompt_version_candidates(template)
    matched = None
    if is_uuid(ref):
        matched = next((version for version in candidates if str(version.id) == ref), None)
    if not matched:
        exact = [
            version
            for version in candidates
            if (version.template_version or "").lower() == ref.lower()
        ]
        if len(exact) == 1:
            matched = exact[0]
        elif len(exact) > 1:
            detail = (
                f"Multiple versions match `{ref}`. Use one of these version IDs."
            )
            return None, candidate_prompt_versions_result(
                template,
                "Prompt Version Ambiguous",
                detail,
                search=ref,
            )
    if matched:
        return matched, None

    return None, candidate_prompt_versions_result(
        template,
        "Prompt Version Not Found",
        f"Version `{ref}` was not found for prompt template `{template.name}`.",
        search="" if is_uuid(ref) else ref,
    )


def prompt_eval_config_candidates(template, search: str = ""):
    from model_hub.models.run_prompt import PromptEvalConfig

    qs = (
        PromptEvalConfig.objects.filter(prompt_template=template, deleted=False)
        .select_related("eval_template", "eval_group")
        .order_by("-created_at")
    )
    search = str(search or "").strip()
    if search and not is_uuid(search):
        qs = qs.filter(name__icontains=search)
    return list(qs[:20])


def candidate_prompt_eval_configs_result(
    template,
    title: str = "Prompt Eval Config Required",
    detail: str = "",
    search: str = "",
) -> ToolResult:
    configs = prompt_eval_config_candidates(template, search)
    rows = []
    for config in configs:
        eval_name = config.eval_template.name if config.eval_template else "-"
        rows.append(
            [
                f"`{config.id}`",
                config.name or eval_name,
                eval_name,
                config.eval_group.name if config.eval_group else "-",
                format_status("active"),
            ]
        )
    body = detail or ""
    if rows:
        body = (body + "\n\n" if body else "") + markdown_table(
            ["Config ID", "Name", "Eval Template", "Group", "Status"],
            rows,
        )
    else:
        body = body or f"No eval configs found for prompt template `{template.name}`."
    return ToolResult(
        content=section(title, body),
        data={
            "requires_prompt_eval_config_ids": True,
            "template_id": str(template.id),
            "configs": [
                {
                    "id": str(config.id),
                    "name": config.name,
                    "eval_template": (
                        config.eval_template.name if config.eval_template else None
                    ),
                    "eval_group": config.eval_group.name if config.eval_group else None,
                }
                for config in configs
            ],
        },
    )


def resolve_prompt_eval_configs(template, config_refs: list[Any]):
    candidates = prompt_eval_config_candidates(template)
    resolved = []
    missing = []
    seen = set()
    for ref in config_refs:
        ref_str = str(ref or "").strip()
        matched = None
        if not ref_str:
            missing.append("empty eval config reference")
            continue
        if is_uuid(ref_str):
            matched = next((config for config in candidates if str(config.id) == ref_str), None)
        if not matched:
            ref_lower = ref_str.lower()
            exact = [
                config
                for config in candidates
                if (config.name or "").lower() == ref_lower
                or (
                    config.eval_template
                    and (config.eval_template.name or "").lower() == ref_lower
                )
            ]
            if len(exact) == 1:
                matched = exact[0]
            elif len(exact) > 1:
                missing.append(
                    f"{ref_str}: multiple eval configs match; use one of "
                    + ", ".join(f"`{config.name}` ({config.id})" for config in exact[:5])
                )
                continue
        if matched:
            key = str(matched.id)
            if key not in seen:
                resolved.append(matched)
                seen.add(key)
        else:
            missing.append(ref_str)
    return resolved, missing


def prompt_simulation_candidates(template, search: str = ""):
    from simulate.models import RunTest

    qs = (
        RunTest.objects.filter(
            prompt_template=template,
            source_type="prompt",
            organization=template.organization,
            deleted=False,
        )
        .select_related("prompt_version")
        .order_by("-created_at")
    )
    search = str(search or "").strip()
    if search and not is_uuid(search):
        qs = qs.filter(name__icontains=search)
    return list(qs[:20])


def candidate_prompt_simulations_result(
    template,
    title: str = "Prompt Simulation Required",
    detail: str = "",
    search: str = "",
) -> ToolResult:
    simulations = prompt_simulation_candidates(template, search)
    rows = []
    for simulation in simulations:
        version_name = (
            simulation.prompt_version.template_version
            if simulation.prompt_version
            else "-"
        )
        rows.append(
            [
                f"`{simulation.id}`",
                truncate(simulation.name, 40),
                version_name,
                format_datetime(simulation.created_at),
            ]
        )
    body = detail or ""
    if rows:
        body = (body + "\n\n" if body else "") + markdown_table(
            ["Simulation ID", "Name", "Version", "Created"],
            rows,
        )
    else:
        body = body or f"No prompt simulations found for template `{template.name}`."
    return ToolResult(
        content=section(title, body),
        data={
            "requires_simulation_id": True,
            "template_id": str(template.id),
            "simulations": [
                {
                    "id": str(simulation.id),
                    "name": simulation.name,
                    "version": (
                        simulation.prompt_version.template_version
                        if simulation.prompt_version
                        else None
                    ),
                }
                for simulation in simulations
            ],
        },
    )


def resolve_prompt_simulation(
    template,
    simulation_ref: Any,
    title: str = "Prompt Simulation Required",
) -> tuple[Any | None, ToolResult | None]:
    ref = str(simulation_ref or "").strip()
    if not ref:
        return None, candidate_prompt_simulations_result(template, title)

    candidates = prompt_simulation_candidates(template)
    matched = None
    if is_uuid(ref):
        matched = next(
            (simulation for simulation in candidates if str(simulation.id) == ref),
            None,
        )
    if not matched:
        exact = [
            simulation
            for simulation in candidates
            if (simulation.name or "").lower() == ref.lower()
        ]
        if len(exact) == 1:
            matched = exact[0]
        elif len(exact) > 1:
            return None, candidate_prompt_simulations_result(
                template,
                "Prompt Simulation Ambiguous",
                f"More than one simulation matched `{ref}`. Use one of these IDs.",
                search=ref,
            )
    if matched:
        return matched, None

    return None, candidate_prompt_simulations_result(
        template,
        "Prompt Simulation Not Found",
        f"Simulation `{ref}` was not found for prompt template `{template.name}`.",
        search="" if is_uuid(ref) else ref,
    )
