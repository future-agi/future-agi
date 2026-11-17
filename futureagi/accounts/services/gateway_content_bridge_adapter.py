"""Construct exact canonical references, without copying canonical policy.

Main supplies the accepted canonical module and explicit TargetKind mapping.
That module is absent on the pinned c2.6 base; this adapter never invents it.
M7 generation must be installed by main in that module's transaction. A post-
transaction revision read here would race removals, so there is no such read.
"""

from .gateway_content_bridge_types import Decision, PortUnavailable


class CanonicalAccounts:
    def __init__(self, api, target_kinds):
        self.api = api
        self.target_kinds = dict(target_kinds)

    def authorize(self, identity, request, resolve_target, arguments):
        api = self.api
        actor = api.ActorRef(person_id=identity.person_id)
        if identity.kind == "personal_key":
            credential = api.PersonalKeyCredential(key_id=identity.credential_id)
        elif identity.kind == "session":
            credential = api.SessionCredential(token_id=identity.credential_id)
        else:
            raise PortUnavailable()

        def resolve(handle, org, workspace):
            target = resolve_target(handle, org, workspace)
            if target is None:
                return None
            kind = self.target_kinds.get(target.kind)
            if kind is None:
                raise PortUnavailable()
            schema = None
            if target.prompt_names is not None or target.prompt_required is not None:
                if (
                    type(target.prompt_names) is not frozenset
                    or type(target.prompt_required) is not frozenset
                    or not target.prompt_required <= target.prompt_names
                ):
                    raise PortUnavailable()
                schema = api.PromptSchema(
                    names=target.prompt_names, required=target.prompt_required
                )
            return api.TargetBinding(
                organization_id=target.organization_id,
                kind=kind,
                server_id=target.server_id,
                prompt_schema=schema,
                tool_surface=target.tool_surface,
            )

        result = api.authorize_gateway_content(
            actor,
            credential,
            request.org_id,
            request.workspace_id,
            api.Action(request.action),
            api.TargetRef(handle=request.target, resolve=resolve),
            phase=api.Phase(request.phase),
            arguments=arguments,
        )
        return Decision(
            result.decision.value, result.reason_code.value, result.membership_revision
        )
