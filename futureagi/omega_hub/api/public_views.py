"""Public reads (SPEC §9.2). Every byte comes from an approved `Version`; anything else is 404."""

from __future__ import annotations

import posixpath
from datetime import date

from django.http import HttpResponseNotModified
from rest_framework.response import Response

from omega_hub import rules
from omega_hub.api.base import PublicView, bytes_response, paginate, viewer
from omega_hub.api.lookups import live_entry, public_version
from omega_hub.api.serializers import cards as card_shapes
from omega_hub.api.serializers.publishers import profile
from omega_hub.errors import HubError
from omega_hub.models import Pick, Version
from omega_hub.permissions import is_hub_token
from omega_hub.services import blocklist, bundles, counts, export, search, storage
from omega_hub.services.community import monday

IMAGE_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}
TEXT_LIMIT = 64 * 1024


class EntriesView(PublicView):
    def get(self, request):
        params = request.query_params.dict()
        entries, sort = search.browse(params)
        response = paginate(
            request, entries, lambda chunk: card_shapes.cards(chunk, viewer(request))
        )
        response.data["facets"] = {"kinds": search.kind_counts(params)}
        response.data["sort"] = (
            sort  # the order applied, so a client that sent none shows the right one
        )
        return response


class EntryView(PublicView):
    def get(self, request, publisher, name):
        entry = live_entry(publisher, name)
        return Response(card_shapes.entry_detail(entry, viewer(request)))


class EntryVersionsView(PublicView):
    def get(self, request, publisher, name):
        entry = live_entry(publisher, name)
        versions = entry.versions.exclude(status=Version.Status.REMOVED).order_by(
            "-semver_key"
        )
        return Response([card_shapes.version_row(version) for version in versions])


class VersionView(PublicView):
    def get(self, request, publisher, name, version):
        return Response(
            card_shapes.version_detail(public_version(publisher, name, version))
        )


class VersionFileView(PublicView):
    hub_install = True

    def get(self, request, publisher, name, version, path):
        found = public_version(publisher, name, version)
        clean = posixpath.normpath(path)
        if clean != path or clean.startswith(("/", "..")):
            raise HubError("OMEGA_HUB_NOT_FOUND", 404)
        files = bundles.read_canonical(storage.read_bytes(found.bundle_key))
        data = bundles.file_bytes(files, path)
        if data is None:
            raise HubError("OMEGA_HUB_NOT_FOUND", 404)
        ext = posixpath.splitext(path)[1].lower()
        csp = {
            "Content-Security-Policy": "default-src 'none'",
            "Cache-Control": "public, max-age=31536000, immutable",
        }
        if ext in IMAGE_TYPES:
            return bytes_response(data, IMAGE_TYPES[ext], csp)
        return bytes_response(data[:TEXT_LIMIT], "text/plain; charset=utf-8", csp)


class VersionBundleView(PublicView):
    hub_install = True

    def get(self, request, publisher, name, version):
        found = public_version(publisher, name, version)
        source = request.query_params.get("source", "cli")
        if source not in ("cli", "studio", "deploy", "console", "web"):
            raise HubError(
                "OMEGA_HUB_INVALID",
                400,
                "source is cli, studio, deploy, console or web",
            )
        data = storage.read_bytes(found.bundle_key)
        # In-app downloads are uncounted until DownloadDaily gains a web source in
        # a later migration. Do not attribute them to CLI or spend a dedupe slot.
        if source != "web":
            session = (
                bool(viewer(request))
                and not is_hub_token(request)
                and getattr(request, "org_api_key", None) is None
            )
            counts.count_download(found, request, source, session=session)
        headers = {
            "X-Omega-Hub-Digest": found.digest,
            "X-Omega-Hub-Bundle-Sha256": found.bundle_sha256,
            "Cache-Control": "no-store",
            "Content-Disposition": f'attachment; filename="{found.entry.name}-{found.version}.tar.gz"',
        }
        if found.status == Version.Status.YANKED:
            headers["X-Omega-Hub-Yanked"] = found.yanked_reason.replace("\n", " ")[:300]
        return bytes_response(data, "application/gzip", headers)


class VersionCastView(PublicView):
    hub_install = True

    def get(self, request, publisher, name, version):
        found = public_version(publisher, name, version)
        if not found.cast_key:
            raise HubError("OMEGA_HUB_NOT_FOUND", 404)
        return bytes_response(
            storage.read_bytes(found.cast_key),
            "text/plain; charset=utf-8",
            {"Cache-Control": "public, max-age=31536000, immutable"},
        )


class EntryRemixesView(PublicView):
    def get(self, request, publisher, name):
        entry = live_entry(publisher, name)
        return Response(card_shapes.cards(search.remixes_of(entry), viewer(request)))


class PublishersView(PublicView):
    def get(self, request):
        publishers, totals = search.list_publishers(request.query_params.dict())
        return paginate(
            request, publishers, lambda chunk: [profile(p, totals[p.pk]) for p in chunk]
        )


class PicksView(PublicView):
    def get(self, request):
        try:
            week = (
                date.fromisoformat(request.query_params["week"])
                if request.query_params.get("week")
                else monday()
            )
        except ValueError as exc:
            raise HubError("OMEGA_HUB_INVALID", 400, "week is YYYY-MM-DD") from exc
        picks = Pick.objects.select_related(
            "entry__publisher", "entry__latest_version"
        ).filter(week=monday(week), entry__status="listed", entry__ranked=True)
        shapes = card_shapes.cards([pick.entry for pick in picks], viewer(request))
        return Response(
            [
                {**shape, "pick": {"week": pick.week.isoformat(), "why": pick.why}}
                for shape, pick in zip(shapes, picks, strict=False)
            ]
        )


class TagsView(PublicView):
    def get(self, request):
        return Response(search.tag_counts())


def _etag_response(request, body: bytes, etag: str, cache_control: str):
    if request.headers.get("If-None-Match") == etag:
        response = HttpResponseNotModified()
        response["ETag"] = etag
        return response
    return bytes_response(
        body, "application/json", {"ETag": etag, "Cache-Control": cache_control}
    )


class ExportView(PublicView):
    def get(self, request):
        body, etag = export.body_and_etag()
        return _etag_response(request, body, etag, "public, max-age=300")


class RulesView(PublicView):
    def get(self, request):
        version = request.query_params.get("version")
        try:
            number = int(version) if version else None
            rules.get_rules(number)
        except (KeyError, ValueError) as exc:
            raise HubError("OMEGA_HUB_NOT_FOUND", 404, "no such rules version") from exc
        return _etag_response(
            request,
            rules.rules_bytes(number),
            rules.rules_etag(number),
            "public, max-age=300",
        )


class BlocklistView(PublicView):
    """Takes no parameters, counts nothing and stores nothing about the request (SPEC §9.2)."""

    throttle_classes: list = []

    def get(self, request):
        body, etag = blocklist.document_and_etag()
        return _etag_response(request, body, etag, "public, max-age=300")
