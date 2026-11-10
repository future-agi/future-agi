"""Common HTTP boundary for the two individual-entity tag assignment actions."""

from tfc.utils.general_methods import GeneralMethods
from tracer.services.clickhouse.v2.entity_tag_targets import TagTargetError
from tracer.services.entity_tag_assignments import update_entity_tags

_MESSAGES = {
    "tag_scope_required": "Refetch the selected entity and supply its complete tag target and revision.",
    "tag_write_forbidden": "Write access to the current workspace is required.",
    "tag_target_not_found": "Tag target not found in the selected project.",
    "tag_target_changed": "The selected source has changed. Refetch before saving tags.",
    "tag_revision_conflict": "The tags have changed. Refetch before saving.",
    "tag_target_unavailable": "The telemetry store is unavailable. Please retry.",
    "tag_assignment_unavailable": "Tag assignments are unavailable. Please retry.",
}


def tag_update_response(request, *, trace_id=None):
    gm = GeneralMethods(request)
    try:
        result = update_entity_tags(
            request=request, data=request.validated_data, trace_id=trace_id
        )
    except TagTargetError as exc:
        return gm.custom_error_response(
            exc.status_code, _MESSAGES[exc.code], code=exc.code
        )
    return gm.success_response(result)
