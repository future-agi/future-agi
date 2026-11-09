"""Person and workspace authorization for console batch reads and cancellation."""

from accounts.models.service_grant import ServiceGrant
from accounts.services.envelope_principals import live_person
from agentcc.models import AgentccAPIKey
from agentcc.services.batch_jobs import job_for
from tfc.constants.levels import Level
from tfc.routers import primary_required
from tfc.service_envelope import EnvelopeRejected


@primary_required
def authorized_job(request, batch_id, *, cancel=False):
    organization = getattr(request, "organization", None)
    user = getattr(request, "user", None)
    if organization is None or user is None or not user.is_authenticated:
        return None
    job = job_for(organization, batch_id)
    if job is None:
        return None
    # The credential, not the submitting session or a caller-supplied key_id,
    # determines ownership. Retain terminal/revoked rows for historical reads.
    if job.gateway_key_id and not job.gateway_key_id.startswith("dlg_"):
        credential = AgentccAPIKey._base_manager.filter(
            organization_id=organization.pk,
            gateway_key_id=job.gateway_key_id,
        ).first()
        owner = getattr(credential, "user_id", None)
        workspace = getattr(credential, "workspace_id", None)
    else:
        grants = ServiceGrant.objects.filter(
            kind="batch", batch_ref=job.pk, org=organization.pk
        )
        if job.gateway_key_id:
            grants = grants.filter(jti=job.gateway_key_id[4:])
        credential = grants.first()
        owner = getattr(credential, "user", None)
        workspace = getattr(credential, "workspace", None)
    if credential is None:
        return None
    if workspace is not None and workspace != getattr(
        getattr(request, "workspace", None), "pk", None
    ):
        return None
    try:
        live_person(str(organization.pk), str(user.pk), str(workspace or "-"))
    except EnvelopeRejected:
        return None
    level = user.get_membership_level(organization)
    if level is None or (cancel and level < Level.MEMBER):
        return None
    if level < Level.ADMIN and owner != user.pk:
        return None
    return job
