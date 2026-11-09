"""Authenticated, explicitly tenant-scoped HTTP adapters for the Skills services."""

from __future__ import annotations

import math
from inspect import signature

from django.core.files.uploadedfile import UploadedFile
from django.db.models import F
from django.http import HttpResponse
from django.utils.http import parse_etags
from drf_yasg import openapi
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.renderers import JSONRenderer
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.authentication import APIKeyAuthentication
from model_hub.models.skills_hub import HubSkillVersion
from model_hub.selectors import skills_hub as selectors
from model_hub.serializers import skills_hub as s
from model_hub.services.skills_hub import (
    checks,
    compare,
    drafts,
    labels,
    limits,
    packages,
    resolve,
    templates_registry,
    usage,
    versions,
)
from model_hub.services.skills_hub.errors import SkillsHubError
from model_hub.services.skills_hub.types import MISSING, Actor
from model_hub.services.skills_hub.v11.principals import (
    principal_from_request,
    require_private_principal,
    require_scope_access,
)
from model_hub.views.skills_uploads import SkillMultipartParser
from tfc.utils.api_contracts import validated_request
from tfc.utils.general_methods import GeneralMethods


class SkillsPermission(BasePermission):
    """Also enforce the central write rule for in-process/forced-auth callers."""

    def has_permission(self, request, view):
        principal = principal_from_request(request)
        request.skill_principal = principal
        if getattr(view, "private_resource", False) or (
            isinstance(view, SkillListView) and request.method == "POST"
        ):
            require_private_principal(principal=principal)
        require_scope_access(
            principal=principal, write=request.method not in {"GET", "HEAD", "OPTIONS"}
        )
        return True


def domain_error(error):
    response = GeneralMethods().custom_error_response(
        error.status, error.message, code=error.code
    )
    # Domain conflict/check payloads are structured, not flattened validation text.
    if error.detail or error.status in {409, 422}:
        response.data["detail"] = error.detail
    return response


def invalid_request(errors):
    if any(
        getattr(error, "code", None) == "max_length" for error in errors.get("note", [])
    ):
        return GeneralMethods().custom_error_response(
            413, errors, code="payload_too_large"
        )
    code = "invalid_selector"
    for field, candidate in (
        ("name", "invalid_name"),
        ("description", "invalid_name"),
        ("tags", "invalid_tags"),
        ("labels", "invalid_label"),
    ):
        if field in errors:
            code = candidate
            break
    return GeneralMethods().custom_error_response(400, errors, code=code)


def invalid_import(errors):
    return GeneralMethods().custom_error_response(400, errors, code="import_invalid")


def contract(output=None, *, body=None, query=None, status=200, many=False, **kwargs):
    responses = dict.fromkeys((400, 401, 403, 404, 410, 413, 428), s.SkillsError)
    responses.update({409: s.SkillsConflictError, 422: s.SkillsChecksError})
    if output:
        responses[status] = s.envelope(output, many=many)
        if status == 201:
            responses[200] = responses[status]
    else:
        responses[status] = openapi.Response(
            "No content" if status == 204 else "ZIP archive",
            schema=None if status == 204 else openapi.Schema(type=openapi.TYPE_FILE),
        )
    responses.update(kwargs.pop("responses", {}))

    def decorate(handler):
        parameters = list(kwargs.pop("manual_parameters", []))
        for name in signature(handler).parameters:
            if name in {"skill_id", "resource_id", "credential_id"}:
                parameters.append(
                    openapi.Parameter(
                        name,
                        openapi.IN_PATH,
                        type=openapi.TYPE_STRING,
                        format="uuid",
                        required=True,
                    )
                )
            elif name == "number":
                parameters.append(
                    openapi.Parameter(
                        name,
                        openapi.IN_PATH,
                        type=openapi.TYPE_INTEGER,
                        minimum=1,
                        required=True,
                    )
                )
            elif name == "label":
                parameters.append(
                    openapi.Parameter(
                        name, openapi.IN_PATH, type=openapi.TYPE_STRING, required=True
                    )
                )
        return validated_request(
            body,
            query_serializer=query,
            responses=responses,
            reject_unknown_fields=True,
            validation_error_response=kwargs.pop(
                "validation_error_response", invalid_request
            ),
            tags=["Skills"],
            manual_parameters=parameters,
            **kwargs,
        )(handler)

    return decorate


class SkillsView(APIView):
    authentication_classes = [APIKeyAuthentication]
    permission_classes = [IsAuthenticated, SkillsPermission]

    def handle_exception(self, exc):
        if isinstance(exc, SkillsHubError):
            return domain_error(exc)
        return super().handle_exception(exc)

    def skill(self, request, skill_id):
        return selectors.get_skill_for_scope(
            request.organization, request.workspace, skill_id
        )

    def actor(self, request):
        return Actor(
            user=None if request.skill_principal.kind == "system_key" else request.user,
            api_key=getattr(request, "org_api_key", None),
        )

    def output(
        self, request, serializer, value, *, status=200, many=False, **context
    ) -> Response:
        data = serializer(
            value, many=many, context={"request": request, **context}
        ).data
        response: Response = GeneralMethods(request).success_response(
            data, status=status
        )
        return response

    def published(self, request, result):
        return self.output(
            request, s.PublishResult, result, status=200 if result.unchanged else 201
        )


def page(queryset, query):
    count = queryset.count()
    start = (query["page"] - 1) * query["page_size"]
    return {
        "results": list(queryset[start : start + query["page_size"]]),
        "count": count,
        "page": query["page"],
        "page_size": query["page_size"],
        "total_pages": math.ceil(count / query["page_size"]),
    }


def fetch(request, version, channel=None):
    key = getattr(request, "org_api_key", None)
    # DjangoAPIExecutor force-authenticates its APIRequestFactory request. This
    # private Python attribute cannot be set by an HTTP header from a client.
    in_process = getattr(request, "_force_auth_user", None) is not None
    usage.record_fetch(
        version=version,
        channel=channel or ("mcp" if in_process else "api"),
        consumer_key=f"key:{key.pk}" if key else f"user:{request.user.pk}",
        consumer_label=(key.name or "")[:200] if key else request.user.email[:200],
    )


def bundle(skill, version, label=None):
    return {
        "name": skill.name,
        "version": version.number,
        "version_id": str(version.pk),
        "digest": version.digest,
        "label": label,
        "files": [
            {"path": row.path, "content": row.blob.content}
            for row in version.files.select_related("blob").order_by("path")
        ],
    }


def zip_response(request, bundles, *, skipped=None):
    content = packages.build_zip(
        bundles, origin=request.build_absolute_uri("/").rstrip("/"), skipped=skipped
    )
    response = HttpResponse(content, content_type="application/zip")
    response["Content-Disposition"] = 'attachment; filename="futureagi-skills.zip"'
    return response


class SkillListView(SkillsView):
    @contract(s.SkillPage, query=s.LibraryQuery)
    def get(self, request):
        query = request.validated_query_data
        filters = {k: v for k, v in query.items() if k not in {"page", "page_size"}}
        result = page(
            selectors.list_skills(request.organization, request.workspace, **filters),
            query,
        )
        ids = [skill.pk for skill in result["results"]]
        latest = (
            HubSkillVersion.objects.filter(
                skill_id__in=ids, number=F("skill__latest_version_number")
            )
            .select_related("created_by")
            .prefetch_related("labels")
        )
        result["stats"] = selectors.workspace_stats(
            request.organization,
            request.workspace,
            None if request.skill_principal.kind == "system_key" else request.user,
        )
        return self.output(
            request,
            s.SkillPage,
            result,
            fetch_counts=usage.fetches_7d_map(ids),
            latest_versions={v.skill_id: v for v in latest},
        )

    @contract(s.SkillDetail, body=s.CreateSkillRequest, status=201)
    def post(self, request):
        skill = versions.create_skill(
            organization=request.organization,
            workspace=request.workspace,
            actor=self.actor(request),
            **request.validated_data,
        )
        return self.output(request, s.SkillDetail, skill, status=201)


class SkillTemplatesView(SkillsView):
    @contract(s.Template, many=True)
    def get(self, request):
        return self.output(
            request, s.Template, templates_registry.list_templates(), many=True
        )


class SkillChecksView(SkillsView):
    @contract(s.ChecksResult, body=s.ChecksRequest)
    def post(self, request):
        data = request.validated_data
        return self.output(
            request,
            s.ChecksResult,
            checks.run_checks(data["files"], expected_name=data.get("name")),
        )


class SkillPushView(SkillsView):
    @contract(s.PushResult, body=s.PushRequest, status=201)
    def post(self, request):
        result = versions.publish_by_name(
            organization=request.organization,
            workspace=request.workspace,
            actor=self.actor(request),
            **request.validated_data,
        )
        return self.output(
            request,
            s.PushResult,
            {
                "skill": result.version.skill,
                "version": result.version,
                "unchanged": result.unchanged,
                "release_revision": result.release_revision,
                "evidence": result.evidence,
            },
            status=200 if result.unchanged else 201,
        )


class SkillResolveView(SkillsView):
    @contract(
        s.Manifest,
        query=s.ResolveQuery,
        responses={304: openapi.Response("Not modified")},
        operation_description='Resolve an immutable skill manifest. Returns ETag: "<digest>". Send If-None-Match with that value for HTTP 304; a missing label never falls back. Optional file_offset/file_limit page the file inventory; follow next_file_offset with the returned version pinned. MCP defaults to 25 files per page and omits frontmatter and file contents; read SKILL.md in chunks for the original metadata.',
    )
    def get(self, request):
        query = dict(request.validated_query_data)
        in_process = getattr(request, "_force_auth_user", None) is not None
        include_content = query.pop("include_content") and not in_process
        file_offset = query.pop("file_offset")
        file_limit = query.pop(
            "file_limit", resolve.MANIFEST_PAGE_SIZE if in_process else None
        )
        resolved = resolve.resolve(
            organization=request.organization, workspace=request.workspace, **query
        )
        version = resolved.version
        etag = f'"{version.digest}"'
        candidates = parse_etags(request.headers.get("If-None-Match", ""))
        if "*" in candidates or any(
            candidate.removeprefix("W/") == etag for candidate in candidates
        ):
            response = HttpResponse(status=304)
        else:
            response = self.output(
                request,
                s.Manifest,
                resolve.manifest(
                    resolved,
                    include_content=include_content,
                    # YAML aliases can expand even a small frontmatter block. MCP
                    # reads its original bytes from SKILL.md, not an unbounded DTO.
                    include_frontmatter=not in_process,
                    file_offset=file_offset,
                    file_limit=file_limit,
                ),
            )
        fetch(request, version)
        response["ETag"] = etag
        response["Cache-Control"] = "private, no-cache"
        response["Vary"] = (
            "Authorization, X-Api-Key, X-Secret-Key, X-Workspace-Id, X-Organization-Id"
        )
        return response


class ZipRenderer(JSONRenderer):
    """Negotiate ZIP downloads while keeping error envelopes JSON.

    Successful archives are HttpResponse objects and bypass DRF rendering. Only
    JSON metadata/errors reach this renderer after an Accept: application/zip.
    """

    media_type = "application/zip"
    format = "zip"

    def render(self, data, accepted_media_type=None, renderer_context=None):
        if renderer_context:
            renderer_context["response"]["Content-Type"] = "application/json"
        return super().render(data, accepted_media_type, renderer_context)


class SkillDownloadView(SkillsView):
    renderer_classes = [JSONRenderer, ZipRenderer]

    @contract(query=s.DownloadQuery, produces=["application/zip", "application/json"])
    def get(self, request):
        query = request.validated_query_data
        names = list(dict.fromkeys(filter(None, query.get("names", "").split(","))))
        if query.get("tag"):
            scoped = (
                selectors.skills_for_request_scope(
                    request.organization, request.workspace
                )
                .filter(tags__contains=[query["tag"]])
                .order_by("name")
            )
            if names:
                scoped = scoped.filter(name__in=names)
            names = list(
                scoped.values_list("name", flat=True)[: limits.MAX_DOWNLOAD_SKILLS + 1]
            )
        if len(names) > limits.MAX_DOWNLOAD_SKILLS:
            raise SkillsHubError(
                "payload_too_large", "Download up to 50 skills at a time.", status=413
            )
        bundles, skipped, fetched = [], [], []
        for name in names:
            try:
                selected = resolve.resolve(
                    organization=request.organization,
                    workspace=request.workspace,
                    name=name,
                    label=query.get("label"),
                    version=query.get("version"),
                )
            except SkillsHubError as exc:
                if exc.status != 404:
                    raise
                skipped.append({"name": name, "reason": exc.code})
                continue
            bundles.append(
                bundle(
                    selected.skill, selected.version, selected.requested.get("label")
                )
            )
            fetched.append(selected.version)
        if not bundles:
            raise SkillsHubError(
                "nothing_to_download", "No skills matched this download.", status=404
            )
        response = zip_response(request, bundles, skipped=skipped)
        for version in fetched:
            fetch(request, version, "download")
        return response


class SkillDetailView(SkillsView):
    @contract(s.SkillDetail)
    def get(self, request, skill_id):
        return self.output(request, s.SkillDetail, self.skill(request, skill_id))

    @contract(s.SkillDetail, body=s.TagsRequest)
    def patch(self, request, skill_id):
        skill = versions.update_tags(
            skill=self.skill(request, skill_id),
            actor=self.actor(request),
            **request.validated_data,
        )
        return self.output(request, s.SkillDetail, skill)

    @contract(body=s.ArchiveRequest, status=204)
    def delete(self, request, skill_id):
        versions.archive_skill(
            skill=self.skill(request, skill_id),
            actor=self.actor(request),
            **request.validated_data,
        )
        return Response(status=204)


class SkillVersionsView(SkillsView):
    @contract(s.VersionPage, query=s.PageQuery)
    def get(self, request, skill_id):
        queryset = (
            self.skill(request, skill_id)
            .versions.select_related("created_by")
            .prefetch_related("labels")
            .order_by("-number")
        )
        return self.output(
            request, s.VersionPage, page(queryset, request.validated_query_data)
        )

    @contract(s.PublishResult, body=s.PublishVersionRequest, status=201)
    def post(self, request, skill_id):
        return self.published(
            request,
            versions.publish_files(
                skill=self.skill(request, skill_id),
                actor=self.actor(request),
                source={"type": "api"},
                **request.validated_data,
            ),
        )


class SkillVersionView(SkillsView):
    @contract(s.VersionDetail)
    def get(self, request, skill_id, number):
        version = selectors.version_for_skill(self.skill(request, skill_id), number)
        return self.output(request, s.VersionDetail, version)


class SkillFileView(SkillsView):
    @contract(s.FileChunk, query=s.FileQuery)
    def get(self, request, skill_id, number):
        version = selectors.version_for_skill(self.skill(request, skill_id), number)
        query = dict(request.validated_query_data)
        if getattr(request, "_force_auth_user", None) is not None:
            query["max_bytes"] = min(query["max_bytes"], limits.MCP_CHUNK_BYTES)
        chunk = resolve.read_file_chunk(version, **query)
        if chunk["offset"] == 0:
            fetch(request, version)
        return self.output(request, s.FileChunk, chunk)


class SkillVersionDownloadView(SkillsView):
    renderer_classes = [JSONRenderer, ZipRenderer]

    @contract(produces=["application/zip", "application/json"])
    def get(self, request, skill_id, number):
        skill = self.skill(request, skill_id)
        version = selectors.version_for_skill(skill, number)
        from model_hub.services.skills_hub.v12.resolution_leases import (
            require_distribution_eligibility,
        )

        require_distribution_eligibility(version)
        response = zip_response(request, [bundle(skill, version)])
        fetch(request, version, "download")
        return response


class SkillRestoreView(SkillsView):
    @contract(s.PublishResult, body=s.RestoreRequest, status=201)
    def post(self, request, skill_id, number):
        return self.published(
            request,
            versions.restore_version(
                skill=self.skill(request, skill_id),
                actor=self.actor(request),
                number=number,
                **request.validated_data,
            ),
        )


class SkillCompareView(SkillsView):
    @contract(s.CompareResult, query=s.CompareQuery)
    def get(self, request, skill_id):
        query = request.validated_query_data
        if "draft" in (query["from"], query["to"]):
            require_private_principal(principal=request.skill_principal)
        result = compare.compare(
            skill=self.skill(request, skill_id),
            from_ref=query["from"],
            to_ref=query["to"],
            user=request.user,
        )
        return self.output(request, s.CompareResult, result)


class SkillLabelsView(SkillsView):
    @contract(s.LabelOut, many=True)
    def get(self, request, skill_id):
        rows = (
            self.skill(request, skill_id)
            .labels.select_related("version", "updated_by")
            .order_by("name")
        )
        return self.output(request, s.LabelOut, rows, many=True)


class SkillLabelView(SkillsView):
    @contract(s.LabelOut, body=s.SetLabelRequest)
    def put(self, request, skill_id, label):
        data = dict(request.validated_data)
        number = data.pop("version")
        result = labels.set_label(
            skill=self.skill(request, skill_id),
            actor=self.actor(request),
            name=label,
            version_number=number,
            **data,
        )
        return self.output(request, s.LabelOut, result)

    @contract(body=s.RemoveLabelRequest, status=204)
    def delete(self, request, skill_id, label):
        labels.remove_label(
            skill=self.skill(request, skill_id),
            actor=self.actor(request),
            name=label,
            expected_version=request.validated_data.get("expected_version", MISSING),
            expected_release_revision=request.validated_data.get(
                "expected_release_revision", MISSING
            ),
        )
        return Response(status=204)


class SkillLabelHistoryView(SkillLabelView):
    # The literal history route also names a valid mutable label. Keep GET for
    # audit events and delegate writes to exactly the ordinary label policy.
    @contract(s.LabelOut, body=s.SetLabelRequest)
    def put(self, request, skill_id):
        return super().put(request, skill_id, label="history")

    @contract(body=s.RemoveLabelRequest, status=204)
    def delete(self, request, skill_id):
        return super().delete(request, skill_id, label="history")

    @contract(s.HistoryPage, query=s.HistoryQuery)
    def get(self, request, skill_id):
        query = request.validated_query_data
        rows = labels.label_history(
            skill=self.skill(request, skill_id), label=query.get("label")
        )
        return self.output(request, s.HistoryPage, page(rows, query))


class SkillDraftView(SkillsView):
    private_resource = True

    @contract(s.DraftOut)
    def get(self, request, skill_id):
        return self.output(
            request,
            s.DraftOut,
            drafts.get_draft(skill=self.skill(request, skill_id), user=request.user),
        )

    @contract(s.DraftOut, body=s.DraftRequest)
    def put(self, request, skill_id):
        result = drafts.save_draft(
            skill=self.skill(request, skill_id),
            user=request.user,
            actor=self.actor(request),
            **request.validated_data,
        )
        return self.output(request, s.DraftOut, result)

    @contract(body=s.EmptyRequest, status=204)
    def delete(self, request, skill_id):
        drafts.discard_draft(skill=self.skill(request, skill_id), user=request.user)
        return Response(status=204)


class SkillDraftPublishView(SkillsView):
    private_resource = True

    @contract(s.PublishResult, body=s.PublishDraftRequest, status=201)
    def post(self, request, skill_id):
        return self.published(
            request,
            drafts.publish_draft(
                skill=self.skill(request, skill_id),
                user=request.user,
                actor=self.actor(request),
                **request.validated_data,
            ),
        )


class SkillUsageView(SkillsView):
    @contract(s.Usage, query=s.UsageQuery)
    def get(self, request, skill_id):
        return self.output(
            request,
            s.Usage,
            usage.usage_summary(
                self.skill(request, skill_id), request.validated_query_data["days"]
            ),
        )


def parse_uploads(uploads: list[UploadedFile]):
    total = 0
    loose_count = 0
    for upload in uploads:
        if upload.name is None or upload.size is None:
            raise SkillsHubError(
                "import_invalid",
                "Each uploaded file needs a name and byte size.",
                status=400,
            )
        is_zip = upload.name.lower().endswith(".zip")
        loose_count += not is_zip
        maximum = limits.MAX_ZIP_BYTES if is_zip else limits.MAX_FILE_BYTES
        total += upload.size
        if (
            upload.size > maximum
            or total > limits.MAX_UNZIPPED_BYTES
            or loose_count > limits.MAX_LOOSE_FILES
        ):
            raise SkillsHubError(
                "payload_too_large",
                "The upload exceeds the file or import size limit.",
                status=413,
            )
    parts: list[tuple[str, bytes]] = []
    for upload in uploads:
        if upload.name is None or upload.size is None:
            raise SkillsHubError(
                "import_invalid",
                "Each uploaded file needs a name and byte size.",
                status=400,
            )
        content = upload.read(upload.size + 1)
        if len(content) > upload.size:
            raise SkillsHubError(
                "payload_too_large", "The upload exceeds its declared size.", status=413
            )
        parts.append((getattr(upload, "skill_relative_path", upload.name), content))
    return packages.parse_upload(parts)


class SkillImportPreviewView(SkillsView):
    parser_classes = [SkillMultipartParser]

    @contract(
        s.ImportPreview,
        body=s.ImportPreviewRequest,
        validation_error_response=invalid_import,
    )
    def post(self, request):
        parsed = parse_uploads(request.validated_data["files"])
        scope = selectors.skills_for_request_scope(
            request.organization, request.workspace
        )
        existing = {
            skill.name: skill
            for skill in scope.filter(
                name__in=[item["name"] for item in parsed["bundles"]]
            )
        }
        bundles = []
        for item in parsed["bundles"]:
            skill = existing.get(item["name"])
            state = "new"
            if item["checks"]["summary"]["errors"]:
                state = "invalid"
            elif skill:
                latest = skill.versions.filter(
                    number=skill.latest_version_number
                ).first()
                state = (
                    "unchanged"
                    if latest
                    and latest.digest == packages.manifest_digest(item["files"])
                    else "changed"
                )
            bundles.append(
                {
                    "key": item["key"],
                    "root": item["root"],
                    "name": item["name"],
                    "file_count": len(item["files"]),
                    "total_bytes": sum(
                        len(file["content"].encode("utf-8")) for file in item["files"]
                    ),
                    "status": state,
                    "checks": item["checks"],
                    **(
                        {
                            "skill_id": skill.pk,
                            "latest_version": skill.latest_version_number or None,
                        }
                        if skill
                        else {}
                    ),
                }
            )
        return self.output(request, s.ImportPreview, {**parsed, "bundles": bundles})


class SkillImportView(SkillsView):
    parser_classes = [SkillMultipartParser]

    @contract(
        s.ImportResult, body=s.ImportRequest, validation_error_response=invalid_import
    )
    def post(self, request):
        data = request.validated_data
        parsed = parse_uploads(data["files"])
        bundles = {item["key"]: item for item in parsed["bundles"]}
        if any(key not in bundles for key in data["selected"]):
            raise SkillsHubError(
                "import_invalid",
                "A selected bundle was not found in the upload.",
                status=400,
            )
        result: dict[str, list[dict]] = {
            "created": [],
            "updated": [],
            "unchanged": [],
            "failed": [],
        }
        scope = selectors.skills_for_request_scope(
            request.organization, request.workspace
        )
        for key in dict.fromkeys(data["selected"]):
            item = bundles[key]
            existing = scope.filter(name=item["name"]).first()
            try:
                published = versions.publish_by_name(
                    organization=request.organization,
                    workspace=request.workspace,
                    actor=self.actor(request),
                    files=item["files"],
                    note="",
                    labels=data["labels"],
                    tags=existing.tags if existing else [],
                    acknowledge_warnings=data["acknowledge_warnings"],
                    expected_release_revision=data["expected_release_revisions"].get(
                        key, MISSING
                    ),
                    source={
                        "type": "import",
                        "filename": item["filename"],
                        "root": item["root"],
                    },
                )
            except SkillsHubError as exc:
                result["failed"].append(
                    {
                        "key": key,
                        "name": item["name"],
                        "code": exc.code,
                        "message": exc.message,
                    }
                )
                continue
            bucket = (
                "unchanged"
                if published.unchanged
                else "updated" if existing else "created"
            )
            version = published.version
            result[bucket].append(
                {
                    "key": key,
                    "name": version.skill.name,
                    "skill_id": version.skill_id,
                    "version": version.number,
                    "version_id": version.pk,
                    "digest": version.digest,
                }
            )
        return self.output(request, s.ImportResult, result)
