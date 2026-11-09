"""The canonical bundle (`omega-hub-bundle/v1`, SPEC §4.6) as Django reads it.

Django only ever opens bundles the checker produced (and re-hashes them first); it still refuses
anything that is not a plain file under `entry/` or `source/`, so a bad bundle cannot write or link.
`pack_canonical` builds the same layout for tests and fixtures.
"""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import posixpath
import tarfile

from omega_hub.errors import HubError

# Above the checker's own caps (entry: 60 files, 512 KiB; Official source folder: 400 files, 4 MiB),
# so nothing the checker accepts is refused here.
MAX_FILES = 600
MAX_UNPACKED = 6 * 1024 * 1024
LANGS = {
    ".ts": "typescript", ".mts": "typescript", ".tsx": "typescript", ".js": "javascript",
    ".mjs": "javascript", ".json": "json", ".md": "markdown", ".html": "html", ".css": "css",
    ".yml": "yaml", ".yaml": "yaml", ".txt": "text", ".csv": "csv", ".cast": "cast", ".svg": "svg",
    ".png": "image", ".jpg": "image", ".jpeg": "image", ".gif": "image", ".webp": "image",
}
CODE_EXTENSIONS = (".ts", ".mts", ".mjs", ".js", ".tsx")


def lang_of(path: str) -> str:
    base = posixpath.basename(path)
    if base in (".env.example", ".gitignore", "LICENSE"):
        return "text"
    return LANGS.get(posixpath.splitext(base)[1].lower(), "text")


def read_canonical(data: bytes) -> dict[str, dict[str, bytes]]:
    """`{"entry": {rel: bytes}, "source": {rel: bytes}}` from a canonical bundle."""
    out: dict[str, dict[str, bytes]] = {"entry": {}, "source": {}}
    total = 0
    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
            for member in archive.getmembers():
                if not member.isfile():
                    raise HubError("OMEGA_HUB_BUNDLE_MISMATCH", 400, f"{member.name}: not a regular file")
                name = member.name
                top, _, rel = name.partition("/")
                clean = posixpath.normpath(rel)
                if top not in out or not rel or clean != rel or rel.startswith("/") or ".." in rel.split("/"):
                    raise HubError("OMEGA_HUB_BUNDLE_MISMATCH", 400, f"{name}: outside entry/ and source/")
                total += member.size
                if total > MAX_UNPACKED or sum(len(v) for v in out.values()) >= MAX_FILES:
                    raise HubError("OMEGA_HUB_BUNDLE_MISMATCH", 400, "the bundle is larger than allowed")
                handle = archive.extractfile(member)
                out[top][rel] = handle.read() if handle else b""
    except (tarfile.TarError, OSError, EOFError) as exc:
        raise HubError("OMEGA_HUB_BUNDLE_MISMATCH", 400, f"the bundle does not open: {exc}") from exc
    if "omega.hub.json" not in out["entry"]:
        raise HubError("OMEGA_HUB_BUNDLE_MISMATCH", 400, "the bundle has no entry/omega.hub.json")
    return out


def pack_canonical(entry: dict[str, bytes], source: dict[str, bytes] | None = None) -> bytes:
    """Byte-sorted paths, mode 0644, uid/gid 0, mtime 0, no directories, gzip mtime 0."""
    files = {f"entry/{rel}": data for rel, data in entry.items()}
    files.update({f"source/{rel}": data for rel, data in (source or {}).items()})
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w", format=tarfile.USTAR_FORMAT) as archive:
        for name in sorted(files, key=lambda value: value.encode("utf-8")):
            info = tarfile.TarInfo(name)
            info.size = len(files[name])
            info.mode = 0o644
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            info.mtime = 0
            archive.addfile(info, io.BytesIO(files[name]))
    out = io.BytesIO()
    with gzip.GzipFile(fileobj=out, mode="wb", mtime=0) as gz:
        gz.write(raw.getvalue())
    return out.getvalue()


def manifest_of(files: dict[str, dict[str, bytes]]) -> dict:
    try:
        manifest = json.loads(files["entry"]["omega.hub.json"].decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise HubError("OMEGA_HUB_BUNDLE_MISMATCH", 400, "omega.hub.json does not parse") from exc
    if not isinstance(manifest, dict):
        raise HubError("OMEGA_HUB_BUNDLE_MISMATCH", 400, "omega.hub.json is not an object")
    return manifest


def files_root(files: dict[str, dict[str, bytes]], manifest: dict) -> tuple[str, dict[str, bytes]]:
    """Where the copyable files live: `source/` for an Official entry with `source`, else `entry/`."""
    if manifest.get("source") and files["source"]:
        return "source", files["source"]
    return "entry", files["entry"]


def digest_files(files: dict[str, bytes]) -> str:
    """`@omega/hub` digestFiles: sha256 over sorted `rel\\0sha256(file)` lines."""
    lines = sorted(f"{rel}\u0000{hashlib.sha256(data).hexdigest()}" for rel, data in files.items())
    return "sha256:" + hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def catalog_digest(files: dict[str, dict[str, bytes]], manifest: dict) -> str:
    """The catalog digest: every copyable file, without the recording when it is in the entry folder."""
    root, chosen = files_root(files, manifest)
    demo = manifest.get("demo") if isinstance(manifest.get("demo"), dict) else {}
    cast = demo.get("cast") if isinstance(demo.get("cast"), str) else "demo.cast"
    copyable = {rel: data for rel, data in chosen.items() if not (root == "entry" and rel == cast)}
    return digest_files(copyable)


def file_list(files: dict[str, dict[str, bytes]]) -> list[dict]:
    rows = []
    for top in ("entry", "source"):
        for rel in sorted(files[top]):
            data = files[top][rel]
            rows.append({
                "path": rel if top == "entry" else f"source/{rel}",
                "bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
                "lang": lang_of(rel),
            })
    return rows


def readme_of(files: dict[str, dict[str, bytes]]) -> str:
    data = files["entry"].get("README.md", b"")
    return data.decode("utf-8", errors="replace")


def file_bytes(files: dict[str, dict[str, bytes]], path: str) -> bytes | None:
    if path.startswith("source/"):
        return files["source"].get(path[len("source/"):])
    return files["entry"].get(path)
