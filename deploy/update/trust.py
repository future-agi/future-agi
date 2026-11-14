"""Fetch a release manifest and prove Future AGI's release workflow signed it.

The trust anchor lives here, not in the manifest: by default the signer must
be https://github.com/future-agi/future-agi/.github/workflows/
release-update-manifest.yml@refs/tags/<the manifest's own version>, issued by
GitHub Actions. An operator may pin another identity or a public key through
settings (forks, distributors), never through the data being verified.

The manifest is read exactly once, into a directory only bin/update writes,
and the copy that cosign verifies is the copy that gets parsed.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
import shutil
import stat
import subprocess
import time
from dataclasses import dataclass, field
from http.client import HTTPException
from pathlib import Path
from typing import Any, Callable, Dict, FrozenSet, List, Mapping, Optional, Tuple
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlparse, urlsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener, urlopen

try:
    import pwd
except ImportError:  # not on Windows, where bin/update runs only inside WSL
    pwd = None  # type: ignore[assignment]

from .manifest import (BUNDLE_ASSET, ManifestError, is_prerelease, is_version, parse_version,
                       validate_manifest)
from .settings import Settings

MAX_DOWNLOAD_BYTES = 4 * 1024 * 1024
_GITHUB_LATEST_RE = re.compile(r"^https://github\.com/([^/]+)/([^/]+)/releases/latest/download/[^/?#]+$")
_GITHUB_API = "https://api.github.com"

COSIGN_INSTALL_URL = "https://docs.sigstore.dev/cosign/system_config/installation/"
# The release is signed by cosign v3 (.github/actions/setup-helm-tools), which
# writes the protobuf Sigstore bundle. cosign 2.x before 2.4.2 cannot read it at
# all, and older verifiers have known verify-blob bugs, so a cosign on PATH is
# used only from this major version on; otherwise the pinned image runs it.
MIN_COSIGN_MAJOR = 3
# Each number has at most 18 digits (manifest.VERSION_NUMBER_DIGITS): a longer
# one is no release number, and int() refuses more than 4300 (Python 3.11+).
_SEMVER_RE = re.compile(r"^v?([0-9]{1,18})\.([0-9]{1,18})\.([0-9]{1,18})(?![0-9])")
_GIT_VERSION_RE = re.compile(r"^\s*GitVersion:\s*(\S+)", re.M)
# cosign is a Go program: these are the proxy variables it reads. They are
# passed to the cosign container by name (-e NAME), so a proxy URL carrying
# credentials never appears on a command line.
PROXY_VARIABLES = ("HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY", "https_proxy", "http_proxy", "no_proxy")
# The fields of a config.json "proxies" entry the docker CLI turns into
# container variables (both letter cases), as far as cosign reads them.
_DOCKER_PROXY_FIELDS = (("httpsProxy", "HTTPS_PROXY"), ("httpProxy", "HTTP_PROXY"), ("noProxy", "NO_PROXY"))
# The daemon address the docker CLI uses when nothing names one, and the
# defaults it fills in (opts.ParseHost).
_DOCKER_DEFAULT_HOST = "unix:///var/run/docker.sock"
_DOCKER_DEFAULT_SOCKETS = {"unix": "/var/run/docker.sock", "npipe": "//./pipe/docker_engine"}
_DOCKER_TCP_PORT = 2375
_DOCKER_TLS_PORT = 2376
# How to give containers a proxy in the docker CLI's config.json ("proxies").
DOCKER_PROXY_DOCS_URL = "https://docs.docker.com/engine/cli/proxy/"
# What Go's HTTP client says when it could not use its proxy (cosign is a Go
# program), when the proxy answered 407 (it wants credentials, or refused the
# ones it got), and when it could not reach a server at all.
_PROXY_ERROR_RE = re.compile(r"proxyconnect|socks connect", re.IGNORECASE)
_PROXY_AUTH_RE = re.compile(r"proxy authentication required", re.IGNORECASE)
_NETWORK_ERROR_RE = re.compile(
    r"dial tcp|no such host|i/o timeout|connection refused|network is unreachable|tls handshake timeout"
    r"|context deadline exceeded|connection reset by peer", re.IGNORECASE)
# A trusted identity of this form is a GitHub Actions workflow file of
# <owner>/<repo> at the release tag. See workflow_claims.
_GITHUB_TAG_IDENTITY_RE = re.compile(
    r"^https://github\.com/([A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?)/([A-Za-z0-9._-]+)"
    r"/\.github/workflows/[^/@\s]+@refs/tags/\{version\}$"
)
# What the docker CLI (or daemon) prints when `docker run` itself failed,
# before cosign ran: exit statuses 125-127 are docker's, never cosign's.
_DOCKER_EXIT_STATUSES = (125, 126, 127)
_DOCKER_ERRORS = ("error response from daemon", "cannot connect to the docker daemon",
                  "failed to connect to the docker api", "permission denied while trying to connect to the docker",
                  "error during connect")
_DOCKER_NOISE_RE = re.compile(  # help hints and pull progress (Docker's, then Podman's)
    r"^(?:Run|See) 'docker run --help'|^Unable to find image .* locally$|: Pulling from |^Digest: sha256:|^Status: "
    r"|: (?:Pulling fs layer|Waiting|Verifying Checksum|Download complete|Pull complete|Already exists)$"
    r"|^Trying to pull |^Getting image source signatures$|^Copying (?:blob|config) |^Writing manifest to image"
)
# A warning printed before the error that stopped `docker run` (Docker's WARNING:, Podman's WARN[0000],
# a daemon's level=warning): named only when nothing else was printed.
_DOCKER_WARNING_RE = re.compile(r"^WARN(?:ING)?\b|\blevel=warning\b", re.IGNORECASE)


def workflow_claims(identity_template: str, version: str) -> Optional[Tuple[str, str]]:
    """The GitHub repository and ref a keyless signature of `version` must carry, or None.

    For GitHub Actions, Fulcio's certificate identity (the SAN) is the workflow
    file that ran, at the ref the caller named. A reusable workflow called from
    any other repository therefore gets a certificate whose SAN is exactly
    https://github.com/future-agi/future-agi/.github/workflows/
    release-update-manifest.yml@refs/tags/<version>, so the SAN alone cannot
    tell Future AGI's release run from a run in someone else's repository. The
    repository and ref the run belonged to are separate certificate claims.

    When the trusted identity has the form https://github.com/<owner>/<repo>/
    .github/workflows/<file>@refs/tags/{version} (the default does), cosign
    must also find <owner>/<repo> as the workflow repository and
    refs/tags/<version> as the workflow ref: only a run in that repository,
    started from that tag, verifies. An identity of any other form (a branch,
    another CI, an e-mail address) names no repository and tag to pin, so
    nothing is added and that identity alone is the anchor.
    """

    match = _GITHUB_TAG_IDENTITY_RE.fullmatch(identity_template)
    if not match:
        return None
    owner, repo = match.groups()
    return f"{owner}/{repo}", f"refs/tags/{version}"


def _parse_json(raw: bytes, what: str) -> Any:
    """json.loads for downloaded bytes: whatever it cannot parse is a ManifestError, never a traceback."""

    try:
        return json.loads(raw)
    except RecursionError as exc:
        raise ManifestError(f"{what} is not valid JSON: it is nested too deeply") from exc
    except ValueError as exc:  # JSONDecodeError, UnicodeDecodeError, an integer literal too long to convert
        raise ManifestError(f"{what} is not valid JSON: {str(exc).split('; ')[0]}") from exc


def _loopback_proxy(value: str) -> bool:
    """Whether a proxy setting (a URL, or host:port as Go reads it) names this host's loopback interface.

    localhost, 127.0.0.0/8, ::1 and 0.0.0.0 inside the cosign container are the
    container itself, not the host where that proxy listens.
    """

    text = value.strip()
    if not text:
        return False
    try:
        host = urlsplit(text if "://" in text else f"http://{text}").hostname
    except ValueError:  # not a URL at all: cosign cannot use it either
        return False
    host = (host or "").rstrip(".")
    if host == "localhost" or host.endswith(".localhost"):
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    mapped = getattr(address, "ipv4_mapped", None)  # ::ffff:127.0.0.1
    if mapped is not None:
        address = mapped
    return address.is_loopback or address.is_unspecified


def _docker_config_file(environ: Mapping[str, str]) -> Optional[Path]:
    """The docker CLI's config.json: in $DOCKER_CONFIG, else ~/.docker, finding ~ the way the CLI does."""

    directory = environ.get("DOCKER_CONFIG")
    if directory:
        return Path(directory) / "config.json"
    home = environ.get("HOME")
    if not home and pwd is not None and hasattr(os, "getuid"):
        try:
            home = pwd.getpwuid(os.getuid()).pw_dir
        except KeyError:
            home = None
    return Path(home) / ".docker" / "config.json" if home else None


def _json_field(document: Any, name: str) -> Any:
    """A field of a JSON object read the way Go's encoding/json fills a struct: the name in any letter case.

    The docker CLI reads config.json into Go structs, which take a key in any
    case ("HTTPSProxy" is httpsProxy) and keep the last key that matches.
    """

    found = None
    if isinstance(document, dict):
        for key, value in document.items():
            if isinstance(key, str) and key.lower() == name.lower():
                found = value
    return found


def _read_json_file(path: Path, what: str) -> Any:
    """A local JSON file the docker CLI reads, or None when it is missing or not JSON (the CLI ignores it then)."""

    try:
        return _parse_json(_read_local(path, what), f"{what} {path}")
    except ManifestError:
        return None


def _docker_host_url(value: str, tls: bool) -> Optional[str]:
    """DOCKER_HOST as the docker CLI normalizes it (opts.ParseHost), or None for one it refuses.

    The default socket when empty (tcp://localhost:2376 with TLS: DOCKER_TLS or
    DOCKER_TLS_VERIFY), tcp:// for host:port, no user name, and the default
    port (2375) when a tcp address has none; the case and any path are kept.
    """

    host = value.strip()
    if not host:
        return f"tcp://localhost:{_DOCKER_TLS_PORT}" if tls else _DOCKER_DEFAULT_HOST
    scheme, separator, rest = host.partition("://")
    if not separator:
        scheme, rest = "tcp", host
    if scheme == "tcp":
        address, slash, path = rest.partition("/")
        address = address.rsplit("@", 1)[-1]
        if address.startswith("["):
            name, _, port = address[1:].partition("]")
            if not port.startswith(":"):
                return None  # the CLI refuses [address] without a port
            port = port[1:]
        else:
            name, _, port = address.rpartition(":") if ":" in address else (address, "", "")
        name, port = name or "localhost", port or str(_DOCKER_TCP_PORT)
        return f"tcp://{f'[{name}]' if ':' in name else name}:{port}{slash}{path}"
    if scheme in ("unix", "npipe"):
        if "://" in rest:
            return None
        return f"{scheme}://{rest or _DOCKER_DEFAULT_SOCKETS[scheme]}"
    if scheme in ("fd", "ssh"):
        return host
    return None


def _docker_daemon_host(environ: Mapping[str, str], config: Any, directory: Path) -> Optional[str]:
    """The daemon address the docker CLI's "proxies" section is keyed by, or None when it cannot be told.

    The CLI's own choice: DOCKER_HOST (the default context, normalized), else
    the context DOCKER_CONTEXT names, else config.json's currentContext,
    whose endpoint is read from the CLI's context store as it was written.
    An endpoint without a Host (`docker context create NAME --docker host=`
    writes none) is the default socket, as for the CLI, which then gives its
    client no host at all. TLS (DOCKER_TLS, or DOCKER_TLS_VERIFY, which
    implies it) moves only the default context's default address. An ssh://
    daemon is reached through a connection helper and is known to the CLI as
    http://docker.example.com.
    """

    raw = environ.get("DOCKER_HOST") or ""
    name = environ.get("DOCKER_CONTEXT") or _json_field(config, "currentContext")
    if raw or not isinstance(name, str) or name in ("", "default"):
        host = _docker_host_url(raw, bool(environ.get("DOCKER_TLS") or environ.get("DOCKER_TLS_VERIFY")))
    else:
        try:
            digest = hashlib.sha256(name.encode("utf-8")).hexdigest()
        except UnicodeError:  # invalid bytes in DOCKER_CONTEXT, a lone surrogate in config.json: no such context
            return None
        meta = _read_json_file(directory / "contexts" / "meta" / digest / "meta.json", "Docker context")
        endpoints = _json_field(meta, "Endpoints")
        endpoint = endpoints.get("docker") if isinstance(endpoints, dict) else None
        if not isinstance(endpoint, dict):
            return None  # no such context: `docker run` fails before any container starts
        host = _json_field(endpoint, "Host")
        if host is None:  # no Host key, or JSON null: Go reads both as ""
            host = ""
        if not isinstance(host, str):
            return None  # a Host the CLI cannot read: it fails to load the context
        host = host or _DOCKER_DEFAULT_HOST
    if host is not None and host.startswith("ssh://"):
        return "http://docker.example.com"
    return host


def _docker_config_proxies(environ: Mapping[str, str]) -> FrozenSet[str]:
    """The proxy variables the docker CLI sets in every container it runs, from config.json's "proxies".

    The CLI uses the entry keyed by the address of the daemon it talks to,
    else the "default" entry, and only that one: its httpsProxy, httpProxy and
    noProxy become HTTPS_PROXY, HTTP_PROXY and NO_PROXY (in both letter cases)
    when they are not empty, but never over a variable `-e` already sets.
    Returned upper-case. A value of only spaces counts as empty: the host's
    variable then reaches the container, where Docker's blank one would only
    break it. A config.json that is missing or cannot be read gives none, as
    for the CLI.
    """

    path = _docker_config_file(environ)
    if path is None:
        return frozenset()
    config = _read_json_file(path, "Docker config")
    proxies = _json_field(config, "proxies")
    if not isinstance(proxies, dict):
        return frozenset()
    chosen = "default"
    if any(key != "default" for key in proxies):
        host = _docker_daemon_host(environ, config, path.parent)
        if host is not None and host in proxies:
            chosen = host
    entry = proxies.get(chosen)
    variables = set()
    for field_name, variable in _DOCKER_PROXY_FIELDS:
        value = _json_field(entry, field_name)
        if isinstance(value, str) and value.strip():
            variables.add(variable)
    return frozenset(variables)


def _docker_failure(returncode: int, output: str) -> Optional[str]:
    """Why `docker run` failed before cosign could run, or None when cosign itself answered."""

    lines = [line.strip() for line in output.splitlines() if line.strip()]
    for line in lines:
        if line.lower().startswith("docker: "):
            return line[len("docker: "):].strip() or line
        if any(error in line.lower() for error in _DOCKER_ERRORS):
            return line
    if returncode in _DOCKER_EXIT_STATUSES:
        meaningful = [line for line in lines if not _DOCKER_NOISE_RE.search(line)]
        errors = [line for line in meaningful if not _DOCKER_WARNING_RE.search(line)] or meaningful
        return errors[0] if errors else f"docker run exited with status {returncode}"
    return None


class VerificationError(RuntimeError):
    """The manifest's signature could not be verified against the trust anchor."""


class NotFound(ManifestError):
    """The server answered HTTP 404: the file is not there (or not yet)."""


class NoCompleteRelease(ManifestError):
    """The release list was read, and no stable release in it has both the manifest and its signature bundle."""


@dataclass
class FetchedManifest:
    source: str  # where the manifest was actually read from
    directory: Path
    manifest_path: Path
    raw: bytes
    data: Dict[str, Any]
    bundle_path: Optional[Path] = None
    bundle_source: Optional[str] = None
    bundle_error: Optional[str] = None
    note: Optional[str] = None  # why the manifest came from somewhere other than the source asked for
    # The newest stable release, when it has no manifest yet and is newer than
    # the release whose manifest was used (see _newest_release_with_manifest).
    newer_release: Optional[str] = None


@dataclass
class Verification:
    verified: bool
    method: str = ""
    signer: str = ""
    issuer: str = ""
    tool: str = ""
    error: str = ""
    details: Dict[str, str] = field(default_factory=dict)
    public_key_sha256: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return {"verified": self.verified, "method": self.method, "signer": self.signer,
                "issuer": self.issuer, "tool": self.tool, "error": self.error}


@dataclass(frozen=True, repr=False)
class HttpResponse:
    """Untrusted transport bytes and validators; never a public status object."""

    status: int
    body: bytes = b""
    etag: Optional[str] = None
    last_modified: Optional[str] = None
    final_url: str = ""
    retry_after: Optional[str] = None
    link: Optional[str] = None


def https_url(url: str) -> str:
    """Normalize an HTTPS endpoint without accepting credentials or control bytes."""
    try:
        if (not isinstance(url, str) or not url.startswith("https://") or len(url) > 8192
                or any(ord(c) <= 32 or ord(c) >= 127 for c in url) or "\\" in url):
            raise ValueError()
        parsed = urlsplit(url)
        if (not parsed.hostname or parsed.username is not None or parsed.password is not None
                or parsed.fragment or parsed.port == 0):
            raise ValueError()
        host = parsed.hostname.lower()
        if ":" in host:
            host = f"[{host}]"
        authority = host + (f":{parsed.port}" if parsed.port not in (None, 443) else "")
        return f"https://{authority}{parsed.path or '/'}" + (f"?{parsed.query}" if parsed.query else "")
    except ValueError:
        raise ManifestError("unsafe HTTPS endpoint") from None


class _HTTPSRedirect(HTTPRedirectHandler):
    def __init__(self, deadline: float):
        self.deadline = deadline
        self.redirects = 0

    def http_error_302(self, req, fp, code, msg, headers):
        # urllib's default handler drains redirect bodies without a size cap.
        # Close instead; every hop shares the original request's time budget.
        fp.close()
        target = urljoin(req.full_url, headers.get("location") or headers.get("uri") or "")
        https_url(target)
        self.redirects += 1
        remaining = self.deadline - time.monotonic()
        if self.redirects > 5 or remaining <= 0 or target == req.full_url:
            raise ManifestError("unsafe HTTPS redirect")
        redirected = self.redirect_request(req, fp, code, msg, headers, target)
        return self.parent.open(redirected, timeout=remaining)

    http_error_301 = http_error_303 = http_error_307 = http_error_308 = http_error_302

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        https_url(newurl)  # Reject a downgrade BEFORE urllib sends the redirected request.
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirected is not None and urlsplit(req.full_url).netloc != urlsplit(newurl).netloc:
            # Neither source credentials nor endpoint validators belong to a CDN.
            for name in ("Authorization", "Cookie", "If-none-match", "If-modified-since"):
                redirected.remove_header(name)
        return redirected


def http_get(url: str, headers: Mapping[str, str], timeout: float = 30.0, *,
             max_bytes: int = MAX_DOWNLOAD_BYTES, ssl_context=None) -> HttpResponse:
    """Conditional bounded HTTPS; an explicit host CA hook never disables TLS checks."""
    https_url(url)
    timeout = min(timeout, 30.0)
    handlers = [_HTTPSRedirect(time.monotonic() + timeout)]
    if ssl_context is not None:
        import ssl
        if not isinstance(ssl_context, ssl.SSLContext) or not ssl_context.check_hostname or ssl_context.verify_mode != ssl.CERT_REQUIRED:
            raise ManifestError("TLS verification required")
        handlers.append(HTTPSHandler(context=ssl_context))
    return _download(url, timeout, headers=headers, response_metadata=True, max_bytes=max_bytes,
                     opener=build_opener(*handlers).open)


def _download(url: str, timeout: float = 30.0, *, accept: str = "application/json",
              headers: Optional[Mapping[str, str]] = None, response_metadata: bool = False,
              opener=None, max_bytes: int = MAX_DOWNLOAD_BYTES):
    if type(max_bytes) is not int or not 0 < max_bytes <= MAX_DOWNLOAD_BYTES:
        raise ManifestError("invalid download bound")
    if not url.startswith("https://"):
        raise ManifestError(f"refusing to download over a non-https URL: {url}")
    try:
        request = Request(url, headers={"Accept": accept, "User-Agent": "future-agi-bin-update", **(headers or {})})
        with (opener or urlopen)(request, timeout=timeout) as response:
            final = response.geturl()
            if not final.startswith("https://"):
                raise ManifestError(f"{url} redirected to a non-https URL")
            if response_metadata:
                https_url(final)
            payload = response.read(max_bytes + 1)
            metadata = getattr(response, "headers", {})
            length = metadata.get("Content-Length")
            if length is not None and (not length.isdigit() or int(length) != len(payload)):
                raise ManifestError("incomplete HTTP body")
            result = HttpResponse(getattr(response, "status", 200), payload, metadata.get("ETag"),
                                  metadata.get("Last-Modified"), final, metadata.get("Retry-After"), metadata.get("Link"))
    except ManifestError:
        raise
    except HTTPError as exc:
        if response_metadata:
            try:
                return HttpResponse(exc.code, etag=exc.headers.get("ETag"),
                                    last_modified=exc.headers.get("Last-Modified"), final_url=exc.geturl(),
                                    retry_after=exc.headers.get("Retry-After"), link=exc.headers.get("Link"))
            finally:
                if exc.fp is not None:
                    exc.close()
        error = NotFound if exc.code == 404 else ManifestError
        raise error(f"could not download {url}: HTTP {exc.code}") from exc
    except (URLError, TimeoutError, OSError, HTTPException, ValueError) as exc:
        # HTTPException: a malformed URL (InvalidURL) or a body cut short
        # (IncompleteRead); ValueError: a URL urllib cannot parse at all.
        raise ManifestError(f"could not download {url}: {getattr(exc, 'reason', exc)}") from exc
    if len(payload) > max_bytes:
        raise ManifestError(f"{url} is larger than {max_bytes} bytes")
    return result if response_metadata else payload


def _special_kind(mode: int) -> Optional[str]:
    """What a file that is not a regular file is, for an error message; None for a regular file."""

    if stat.S_ISREG(mode):
        return None
    if stat.S_ISDIR(mode):
        return "a directory"
    if stat.S_ISFIFO(mode):
        return "a named pipe"
    if stat.S_ISCHR(mode) or stat.S_ISBLK(mode):
        return "a device"
    if stat.S_ISSOCK(mode):
        return "a socket"
    return "a special file"


def _read_local(path: Path, what: str = "") -> bytes:
    """Read a local regular file, at most MAX_DOWNLOAD_BYTES + 1 bytes of it.

    A named pipe, device or directory is refused before it is opened, and again
    on the opened descriptor in case the path was swapped in between (opened
    non-blocking, so a pipe cannot hang bin/update). Reading stops one byte past
    the cap, so a huge file is never read whole into memory.
    """

    name = f"{what} {path}" if what else str(path)
    data = b""
    try:
        kind = _special_kind(os.stat(str(path)).st_mode)
        if kind is None:
            fd = os.open(str(path), os.O_RDONLY | getattr(os, "O_NONBLOCK", 0))
            with os.fdopen(fd, "rb") as handle:
                kind = _special_kind(os.fstat(handle.fileno()).st_mode)
                if kind is None:
                    data = handle.read(MAX_DOWNLOAD_BYTES + 1)
    except OSError as exc:
        raise ManifestError(f"cannot read {name}: {exc.strerror or exc}") from exc
    if kind is not None:
        raise ManifestError(f"cannot read {name}: it is {kind}, not a regular file")
    if len(data) > MAX_DOWNLOAD_BYTES:
        raise ManifestError(f"{name} is larger than {MAX_DOWNLOAD_BYTES} bytes")
    return data


def _write_private(path: Path, data: bytes) -> None:
    """Create bin/update's own copy of `data` at `path`: never through a planted file or link.

    The copies (manifest, bundle, public key) are public data, not secrets, and
    the cosign container must be able to read them as whatever uid it runs
    under: rootless Docker and userns-remap map the host user to another uid
    inside the container. So the file is 0644 whatever the umask: readable by
    all, writable only by its owner. The workdir's parent (the 0700 state
    directory) still keeps other host users out.
    """

    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    with os.fdopen(fd, "wb") as handle:
        if hasattr(os, "fchmod"):
            os.fchmod(handle.fileno(), 0o644)  # the mode given to os.open is cut by the umask
        handle.write(data)


def _is_url(source: str) -> bool:
    return "://" in source


def release_records(releases: Any, channel: str = "stable") -> List[Dict[str, Any]]:
    """Shared legacy/scheduled channel filter. The API's order is not semver order."""
    result = []
    for release in releases if isinstance(releases, list) else []:
        if not isinstance(release, dict) or release.get("draft"):
            continue
        tag = release.get("tag_name")
        if not is_version(tag):
            continue
        pre = is_prerelease(tag)
        if pre != (channel == "beta") or bool(release.get("prerelease")) != pre:
            continue
        result.append(release)
    return sorted(result, key=lambda record: parse_version(record["tag_name"]), reverse=True)


def has_release_assets(release: Mapping[str, Any], *required: str) -> bool:
    assets = release.get("assets")
    names = {a.get("name") for a in assets if isinstance(a, dict) and isinstance(a.get("name"), str)} \
        if isinstance(assets, list) else set()
    return all(name in names for name in required)


def _newest_release_with_manifest(latest_url: str, owner: str, repo: str,
                                  asset: str, *, required_bundle: Optional[str] = None, download=None
                                  ) -> Tuple[str, str, Optional[str]]:
    """The URL of `asset` in the newest stable release that has it and (when required) its signature bundle.

    The newer release is the newest stable release when it has no complete
    manifest/bundle pair yet (it is newer than the chosen one), else None:
    GitHub's latest release is then an ad-hoc release or an older backport,
    and no newer manifest is coming.

    GitHub's releases/latest/download/<asset> answers 404 while the newest
    release has no manifest: it is attached only once the release's images are
    published (30-90 minutes after the release is created, never if that
    workflow failed), and an ad-hoc release that is not a platform version can
    be the latest one too. The release list comes from the GitHub REST API,
    with the same https-only, size-capped download as the manifest.

    With `required_bundle` (the manifest was found, its bundle was not) the
    errors say only that: NoCompleteRelease when no release in the list has
    both, else why the list could not be read, without the manifest-404 text.
    """

    if required_bundle is None:
        missing = (f"{latest_url} was not found: the newest release of {owner}/{repo} has no update manifest yet "
                   "(it is attached once the release's images are published). Retry later, or pass --manifest "
                   "with the manifest URL of a specific release")
    else:
        missing = (f"No stable release of {owner}/{repo} has both {asset} and its signature bundle "
                   f"{required_bundle}. Retry later, or pass --manifest and --bundle for a specific release")
    download = download or _download
    api = f"{_GITHUB_API}/repos/{owner}/{repo}/releases?per_page=30"
    try:
        releases = _parse_json(download(api, accept="application/vnd.github+json"), f"the release list {api}")
    except ManifestError as exc:  # the download failed, or the body is not JSON
        if required_bundle is not None:
            raise  # the caller says what it was looking for
        raise ManifestError(f"{missing}. Looking for an older release that has one failed: {exc}") from exc

    newest: Optional[str] = None  # the newest stable release, with or without the manifest
    chosen: Optional[str] = None
    for release in release_records(releases):
        tag = release["tag_name"]
        if newest is None:
            newest = tag
        required = (asset, required_bundle) if required_bundle else (asset,)
        if chosen is None and has_release_assets(release, *required):
            chosen = tag
    if chosen is None:
        raise ManifestError(missing) if required_bundle is None else NoCompleteRelease(missing)
    newer = newest if newest is not None and parse_version(newest) > parse_version(chosen) else None
    if newer is not None:
        note = (f"the newest release {newer} has no complete update manifest and signature bundle yet"
                if required_bundle else f"the newest release {newer} has no update manifest yet")
    else:  # GitHub's latest release is not a platform release, or not the newest one
        note = ("the latest GitHub release has no complete update manifest and signature bundle"
                if required_bundle else "the latest GitHub release has no update manifest")
    note += f"; using {chosen}, the newest release that has one"
    return f"https://github.com/{owner}/{repo}/releases/download/{chosen}/{asset}", note, newer


def fetch_manifest(source: str, workdir: Path, *, cwd: Path, bundle: Optional[str] = None,
                   download=None) -> FetchedManifest:
    """Copy the manifest (and its bundle, when found) into `workdir`.

    `workdir` is 0755 (see _write_private): the cosign container reads it
    through a read-only bind mount, as a uid that need not own it.
    """

    download = download or _download
    workdir.mkdir(mode=0o755, parents=True, exist_ok=True)
    os.chmod(str(workdir), 0o755)  # the mode given to mkdir is cut by the umask
    note = newer = None
    requested = source  # `source` becomes the release the manifest came from
    if _is_url(source):
        try:
            parsed = urlparse(source)
        except ValueError as exc:
            raise ManifestError(f"invalid manifest URL {source}: {exc}") from exc
        if parsed.scheme != "https":
            raise ManifestError(f"manifest URL must use https, not {parsed.scheme}://")
        try:
            raw = download(source)
        except NotFound:
            latest = _GITHUB_LATEST_RE.fullmatch(source)
            if not latest:
                raise
            owner, repo = latest.groups()
            source, note, newer = _newest_release_with_manifest(source, owner, repo, source.rsplit("/", 1)[1],
                                                               download=download)
            raw = download(source)
    else:
        path = Path(source)
        if not path.is_absolute():
            path = cwd / path
        raw = _read_local(path, "manifest")
    manifest_path = workdir / "manifest.json"
    _write_private(manifest_path, raw)
    data = validate_manifest(_parse_json(raw, "manifest"))
    if newer is not None and is_version(data["version"]) and parse_version(newer) <= parse_version(data["version"]):
        newer = None  # the manifest is of a release at least as new: nothing newer is waiting for one
    fetched = FetchedManifest(source, workdir, manifest_path, raw, data, note=note, newer_release=newer)

    bundle_name = data["signature"]["bundle"]
    if bundle:
        bundle_source = bundle
    elif _is_url(source):
        latest = _GITHUB_LATEST_RE.fullmatch(source)
        if latest:
            # Fetch the bundle from the release the manifest names, so a newer
            # release published between the two downloads cannot mix them.
            owner, repo = latest.groups()
            bundle_source = f"https://github.com/{owner}/{repo}/releases/download/{data['version']}/{bundle_name}"
        else:
            bundle_source = urljoin(source, bundle_name)
    else:
        local = Path(source)
        if not local.is_absolute():
            local = cwd / local
        bundle_source = str(local.parent / bundle_name)
    fetched.bundle_source = bundle_source
    try:
        if _is_url(bundle_source):
            bundle_bytes = download(bundle_source)
        else:
            bundle_path = Path(bundle_source)
            if not bundle_path.is_absolute():
                bundle_path = cwd / bundle_path
            bundle_bytes = _read_local(bundle_path)
        copy = workdir / "bundle.json"
        _write_private(copy, bundle_bytes)
        fetched.bundle_path = copy  # only once the private copy is complete
    except NotFound as exc:
        fetched.bundle_error = f"signature bundle not available ({bundle_source}): {exc}"
        latest = _GITHUB_LATEST_RE.fullmatch(requested)
        # A manifest without its bundle (one deleted by hand, or a release published before the workflow uploaded the
        # bundle first), whether it is the latest release's or the one the manifest-404 fallback chose by its
        # manifest: use the newest release that has both, rather than hand cosign a manifest it cannot verify. An
        # explicit bundle is the operator's choice and is never replaced.
        if latest and not bundle:
            try:
                return _complete_pair(requested, *latest.groups(), fetched, download=download) or fetched
            except NoCompleteRelease as nothing:
                fetched.bundle_error += f". {nothing}"
            except (ManifestError, OSError) as fallback_error:
                fetched.bundle_error += (". Looking for an older release that has a complete manifest and signature "
                                         f"bundle failed: {fallback_error}")
    except (ManifestError, OSError) as exc:
        fetched.bundle_error = f"signature bundle not available ({bundle_source}): {exc}"
    return fetched


def _complete_pair(latest_url: str, owner: str, repo: str, fetched: FetchedManifest, *,
                   download=None) -> Optional[FetchedManifest]:
    """The manifest and bundle of the newest stable release that has both, in fetched's workdir (see fetch_manifest).

    None when that is the release `fetched` already holds (its bundle was
    attached between the two requests): nothing better is there to use.
    """

    download = download or _download
    bundle_name = fetched.data["signature"]["bundle"]
    source, note, newer = _newest_release_with_manifest(latest_url, owner, repo, latest_url.rsplit("/", 1)[1],
                                                        required_bundle=bundle_name, download=download)
    version = source.split("/releases/download/", 1)[1].split("/", 1)[0]
    if version == fetched.data["version"]:
        return None
    raw = download(source)
    data = validate_manifest(_parse_json(raw, "manifest"))
    bundle_source = (f"https://github.com/{owner}/{repo}/releases/download/{data['version']}/"
                     f"{data['signature']['bundle']}")
    bundle_bytes = download(bundle_source)
    # The latest release's manifest copy is replaced: bin/update created it, and nothing has read it yet.
    fetched.manifest_path.unlink()
    _write_private(fetched.manifest_path, raw)
    copy = fetched.directory / "bundle.json"
    _write_private(copy, bundle_bytes)
    return FetchedManifest(source, fetched.directory, fetched.manifest_path, raw, data, bundle_path=copy,
                           bundle_source=bundle_source, note=note, newer_release=newer)

Runner = Callable[..., Any]


@dataclass
class _CosignRun:
    """How Verifier runs cosign: the command, and what explains its failures."""

    command: List[str]
    tool: str  # the cosign binary, or the image cosign runs from
    in_docker: bool = False
    # Why this tool: the image was chosen over the cosign on PATH, or the only
    # cosign there is did not report a usable version.
    note: str = ""
    host_cosign: str = ""  # the cosign on PATH the image was chosen over, and its version (for advice)
    loopback: List[str] = field(default_factory=list)  # proxy variables not passed to the container
    forwarded: List[str] = field(default_factory=list)  # this host's proxy variables passed to the container
    # The proxy variables (upper-case) the docker CLI sets in the container from config.json.
    docker_proxies: FrozenSet[str] = frozenset()


class Verifier:
    """Runs cosign verify-blob on a fetched manifest: a cosign v3+ on PATH, else the pinned image in Docker.

    `runner` runs the verification; `probe` runs `cosign version` (a separate
    hook, so a test's runner sees only the verification); `environ` is where
    the proxy settings forwarded to the cosign container are looked up, the
    docker CLI's config.json is found (DOCKER_CONFIG, HOME), and the daemon it
    talks to is named (DOCKER_HOST, DOCKER_CONTEXT, DOCKER_TLS, DOCKER_TLS_VERIFY).
    """

    def __init__(self, settings: Settings, *, docker: Optional[List[str]] = None,
                 runner: Optional[Runner] = None, which: Callable[[str], Optional[str]] = shutil.which,
                 probe: Optional[Runner] = None, environ: Optional[Mapping[str, str]] = None,
                 host_only: bool = False, deadline: Optional[float] = None) -> None:
        self.settings = settings
        self.docker = docker or ["docker"]
        self.runner = runner or subprocess.run
        self.which = which
        self.probe = probe or subprocess.run
        self.environ = os.environ if environ is None else environ
        self._versions: Dict[str, Tuple[Optional[Tuple[int, int, int]], str]] = {}
        self.host_only = host_only
        self.deadline = deadline

    def begin_discovery(self, deadline: float) -> None:
        """A verifier instance belongs to one serialized host tick at a time."""
        self.deadline = deadline
        self._versions.clear()  # Re-probe the registered binary each tick.
        self.require_host()

    def timeout(self, maximum: float) -> float:
        remaining = maximum if self.deadline is None else min(maximum, self.deadline - time.monotonic())
        if remaining <= 0:
            raise VerificationError("discovery_incomplete")
        return remaining

    def require_host(self) -> str:
        """Never start a Docker helper for scheduled observation or verification."""
        cosign = self.which("cosign")
        if not self.host_only or not cosign:
            raise VerificationError("host_verifier_required")
        version, _ = self.cosign_version(cosign)
        if version is None or version[0] < MIN_COSIGN_MAJOR:
            raise VerificationError("host_verifier_required")
        return cosign

    def trust_fingerprint(self, fetched: Optional[FetchedManifest] = None) -> str:
        """Pin content, mode and claim rules, not a public-key filename.

        With fetched, fingerprint the exact key copy used by cosign as well as
        the current claim policy, so a key swap during acquisition fails closed.
        """
        key = self.settings.get("FUTURE_AGI_UPDATE_COSIGN_PUBLIC_KEY")
        key_path = fetched.directory / "trusted-key.pub" if fetched is not None else Path(key)
        fields = {name: self.settings.get(name) for name in (
            "FUTURE_AGI_UPDATE_TRUSTED_IDENTITY", "FUTURE_AGI_UPDATE_TRUSTED_ISSUER",
            "FUTURE_AGI_UPDATE_COSIGN_OFFLINE")}
        fields.update(key_sha256=hashlib.sha256(_read_local(key_path)).hexdigest() if key else "",
                      mode="public-key" if key else "keyless", claims="exact-github-repository-and-tag-v1",
                      minimum_cosign_major=MIN_COSIGN_MAJOR, host_only=self.host_only)
        return hashlib.sha256(json.dumps(fields, sort_keys=True).encode("utf-8")).hexdigest()

    def public_key_sha256(self) -> str:
        """Host-configured anchor for Controls, never sourced from a candidate."""
        key = self.settings.get("FUTURE_AGI_UPDATE_COSIGN_PUBLIC_KEY")
        return hashlib.sha256(_read_local(Path(key))).hexdigest() if key else ""

    def expected_signer(self, version: str) -> str:
        template = self.settings.get("FUTURE_AGI_UPDATE_TRUSTED_IDENTITY")
        return template.replace("{version}", version)

    def cosign_version(self, cosign: str) -> Tuple[Optional[Tuple[int, int, int]], str]:
        """The (major, minor, patch) of the cosign at `cosign` and the version it reported.

        (None, reported) when no version could be read: the binary did not run,
        or reported something that is not a release number (a "devel" build).
        Asked once per binary: `cosign version --json` (its gitVersion), then
        the plain `cosign version` text (its GitVersion: line).
        """

        if cosign not in self._versions:
            reported = ""
            for args in ([cosign, "version", "--json"], [cosign, "version"]):
                try:
                    result = self.probe(args, capture_output=True, text=True, check=False, timeout=self.timeout(60))
                except (OSError, subprocess.TimeoutExpired):
                    break  # it does not run at all: the text form will not either
                stdout = result.stdout if isinstance(result.stdout, str) else ""
                stderr = result.stderr if isinstance(result.stderr, str) else ""
                try:
                    answer = json.loads(stdout)
                except (ValueError, RecursionError):
                    answer = None
                found = answer.get("gitVersion") if isinstance(answer, dict) else None
                if not isinstance(found, str) or not found.strip():
                    match = _GIT_VERSION_RE.search(stdout) or _GIT_VERSION_RE.search(stderr)
                    found = match.group(1) if match else ""
                reported = found.strip()
                if reported:
                    break
            match = _SEMVER_RE.match(reported)
            parsed = (int(match.group(1)), int(match.group(2)), int(match.group(3))) if match else None
            self._versions[cosign] = (parsed, reported)
        return self._versions[cosign]

    def _command(self, fetched: FetchedManifest, args: List[str]) -> _CosignRun:
        """How cosign runs: a cosign v3+ on PATH, else the pinned image, and why that tool when notable."""

        if self.host_only:
            cosign = self.require_host()
            return _CosignRun([cosign, *args], cosign)
        image = self.settings.get("FUTURE_AGI_UPDATE_COSIGN_IMAGE")
        cosign = self.which("cosign")
        note = host_cosign = ""
        if cosign:
            version, reported = self.cosign_version(cosign)
            if version is not None and version[0] >= MIN_COSIGN_MAJOR:
                return _CosignRun([cosign, *args], cosign)
            if version is not None:  # what that cosign is, then why it is not used
                state, reason = f"is {reported}", f"is {reported}, older than v{MIN_COSIGN_MAJOR}"
            elif reported:
                state = f"reports version {reported!r}"
                reason = f"{state}, not a release number"
            else:
                state = reason = "did not report its version"
            if not self.which(self.docker[0]):
                if version is not None:
                    raise VerificationError(
                        f"cosign {cosign} {state}, which cannot verify the release signature: bin/update needs "
                        f"cosign v{MIN_COSIGN_MAJOR} or later. Upgrade cosign ({COSIGN_INSTALL_URL}), or make "
                        f"Docker available so bin/update runs cosign from {image}")
                # The only cosign there is, of a version that cannot be read:
                # try it (a failure is still a failure) and say why it may fail.
                return _CosignRun([cosign, *args], cosign,
                                  note=f"cosign {cosign} {reason}; bin/update needs cosign v{MIN_COSIGN_MAJOR} or later")
            note = f"bin/update used {image} because the cosign on PATH, {cosign}, {reason}"
            host_cosign = f"{cosign}, which {state}"
        elif not self.which(self.docker[0]):
            raise VerificationError(
                f"cosign is needed to verify the release signature: install cosign v{MIN_COSIGN_MAJOR} or later "
                f"({COSIGN_INSTALL_URL}) or make Docker available"
            )
        offline = self._offline()
        mapped = [a.replace(str(fetched.directory), "/work") for a in args]
        command = [
            *self.docker, "run", "--rm", "--read-only",
            "--user", f"{os.getuid()}:{os.getgid()}" if hasattr(os, "getuid") else "0:0",
            "--tmpfs", "/tmp:rw,size=64m", "-e", "HOME=/tmp",
        ]
        loopback: List[str] = []
        forwarded: List[str] = []
        docker_proxies: FrozenSet[str] = frozenset()
        if not offline:
            # Keyless verification fetches Sigstore's trusted root (TUF) on
            # every run: behind an egress proxy the container needs it too.
            # Not a kind of proxy config.json gives the containers of this
            # daemon (the docker CLI sets it unless -e does, so -e would
            # replace it), and never a proxy on this host's loopback
            # interface, which the container cannot reach. Every other one is.
            docker_proxies = _docker_config_proxies(self.environ)
            for name in PROXY_VARIABLES:
                if name not in self.environ or name.upper() in docker_proxies:
                    continue
                if name.lower() != "no_proxy" and _loopback_proxy(self.environ[name]):
                    loopback.append(name)
                else:
                    command += ["-e", name]
                    forwarded.append(name)
        # Read-only, and relabelled (z) so an SELinux host lets the container
        # read it; the files are world-readable (see _write_private).
        command += ["-v", f"{fetched.directory}:/work:ro,z"]
        if offline:
            command += ["--network", "none"]
        return _CosignRun([*command, image, *mapped], image, in_docker=True, note=note, host_cosign=host_cosign,
                          loopback=loopback, forwarded=forwarded, docker_proxies=docker_proxies)

    def _offline(self) -> bool:
        """Verify without the network: only with a public key.

        Keyless verification checks Sigstore's transparency log, so it always
        keeps the network. For the commands that verify (check, apply),
        load_settings already refuses OFFLINE without a key.
        """

        return bool(self.settings.get("FUTURE_AGI_UPDATE_COSIGN_PUBLIC_KEY")) and self.settings.boolean(
            "FUTURE_AGI_UPDATE_COSIGN_OFFLINE")

    @staticmethod
    def _cosign_advice(run: _CosignRun) -> str:
        """How to get a cosign bin/update runs on the host, without Docker."""

        if run.host_cosign:
            return (f"replace the cosign on PATH, {run.host_cosign}, with cosign v{MIN_COSIGN_MAJOR} or later "
                    f"({COSIGN_INSTALL_URL})")
        return f"install cosign v{MIN_COSIGN_MAJOR} or later ({COSIGN_INSTALL_URL})"

    def _proxy_failure(self, run: _CosignRun, lines: List[str]) -> Optional[str]:
        """Why cosign in its container could not reach Sigstore because of a proxy, or None."""

        config = _docker_config_file(self.environ)
        section = f"the \"proxies\" section of {config or '~/.docker/config.json'}"
        direct = (f"{self._cosign_advice(run)}: bin/update runs a cosign v{MIN_COSIGN_MAJOR} on PATH directly, "
                  "with this host's proxy settings")
        advice = f"Give containers a proxy they can reach in {section} ({DOCKER_PROXY_DOCS_URL}), or {direct}"
        refused = [line.strip() for line in lines if _PROXY_AUTH_RE.search(line)]
        if refused:
            # The proxy answered: it is reachable, and wants credentials.
            credentials = "the proxy URL with its credentials (http://user:password@proxy:3128)"
            https = [name for name in run.forwarded if name.upper() == "HTTPS_PROXY"]
            if "HTTPS_PROXY" in run.docker_proxies:
                where = (f"The container's HTTPS proxy is the httpsProxy in {section}, which Docker sets in every "
                         f"container. Put {credentials} there ({DOCKER_PROXY_DOCS_URL}), or {direct}")
            elif https:
                names = " and ".join(https)
                where = (f"bin/update passed this host's {names} to the container. Put {credentials} in {names}, "
                         f"or give containers that proxy URL in {section} ({DOCKER_PROXY_DOCS_URL}), or {direct}")
            else:
                where = (f"Put {credentials} in {section} ({DOCKER_PROXY_DOCS_URL}) or in this host's HTTPS_PROXY, "
                         f"or {direct}")
            return (f"cosign, run from {run.tool}, reached its proxy, but the proxy refused it: {refused[-1]}. The "
                    f"proxy needs credentials that cosign did not send, or rejected the ones it sent. {where}")
        through = [line.strip() for line in lines if _PROXY_ERROR_RE.search(line)]
        if through:
            return (f"cosign, run from {run.tool}, could not connect through its proxy: {through[-1]}. The proxy "
                    "must be reachable from inside a container, where localhost and 127.0.0.1 are the container "
                    f"itself. {advice}")
        unreachable = [line.strip() for line in lines if _NETWORK_ERROR_RE.search(line)]
        if run.loopback and unreachable:
            names = ", ".join(run.loopback)
            point, them = ("points", "it") if len(run.loopback) == 1 else ("point", "them")
            return (f"cosign, run from {run.tool}, could not reach Sigstore: {unreachable[-1]}. {names} on this "
                    f"host {point} at a loopback address, which inside a container is the container itself, so "
                    f"bin/update did not pass {them} to the cosign container. {advice}")
        return None

    def verify(self, fetched: FetchedManifest) -> Verification:
        if fetched.bundle_path is None:
            return Verification(False, error=fetched.bundle_error or "no signature bundle")
        version = fetched.data["version"]
        key = self.settings.get("FUTURE_AGI_UPDATE_COSIGN_PUBLIC_KEY")
        offline = self._offline()
        if key:
            key_path = Path(key)
            try:
                key_copy = fetched.directory / "trusted-key.pub"
                if not key_copy.exists():
                    # Read like the manifest: a named pipe or a device is refused, never waited on or read endlessly.
                    _write_private(key_copy, _read_local(key_path, "FUTURE_AGI_UPDATE_COSIGN_PUBLIC_KEY"))
            except ManifestError as exc:  # the key itself (_read_local turns every error reading it into one)
                return Verification(False, error=str(exc))
            except OSError as exc:  # bin/update's copy of it: a full disk, a file already there
                return Verification(False, error=f"cannot copy FUTURE_AGI_UPDATE_COSIGN_PUBLIC_KEY {key} to "
                                                 f"{key_copy}: {exc.strerror or exc}")
            args = ["verify-blob", "--key", str(key_copy), "--bundle", str(fetched.bundle_path)]
            if offline:
                args.append("--insecure-ignore-tlog=true")
            method, signer, issuer = "public key", str(key_path), ""
        else:
            signer = self.expected_signer(version)
            issuer = self.settings.get("FUTURE_AGI_UPDATE_TRUSTED_ISSUER")
            declared = fetched.data["signature"]
            for name, pinned in (("identity", signer), ("issuer", issuer)):
                if name in declared and declared[name] != pinned:
                    return Verification(False, method="keyless", signer=signer, issuer=issuer, error=(
                        f"manifest says it was signed by {name} {declared[name]!r}, "
                        f"but bin/update only trusts {pinned!r}"))
            args = ["verify-blob", "--bundle", str(fetched.bundle_path),
                    "--certificate-identity", signer, "--certificate-oidc-issuer", issuer]
            claims = workflow_claims(self.settings.get("FUTURE_AGI_UPDATE_TRUSTED_IDENTITY"), version)
            if claims:
                args += ["--certificate-github-workflow-repository", claims[0],
                         "--certificate-github-workflow-ref", claims[1]]
            method = "keyless"
        args.append(str(fetched.manifest_path))
        try:
            run = self._command(fetched, args)
        except VerificationError as exc:
            return Verification(False, method=method, signer=signer, issuer=issuer, error=str(exc))
        tool = run.tool
        # Why this tool. The image's reason is said only once cosign ran in it:
        # a `docker run` that failed (or could not start) ran no cosign at all.
        why = f" ({run.note})" if run.note else ""
        try:
            result = self.runner(run.command, capture_output=True, text=True, check=False, timeout=self.timeout(300))
        except (OSError, subprocess.TimeoutExpired) as exc:
            return Verification(False, method=method, signer=signer, issuer=issuer, tool=tool,
                                error=f"could not run cosign: {exc}{'' if run.in_docker else why}")
        if result.returncode != 0:
            output = (result.stderr or result.stdout or "").strip()
            lines = [line for line in output.splitlines() if line.strip()]
            failure = _docker_failure(result.returncode, output) if run.in_docker else None
            if failure is not None:
                return Verification(False, method=method, signer=signer, issuer=issuer, tool=tool, error=(
                    f"could not run cosign from {tool}: {failure}. Make sure Docker can pull and run that image, "
                    f"or {self._cosign_advice(run)}"))
            if run.in_docker and "/work/" in output and "no such file" in output.lower():
                error = (f"Docker cannot see {fetched.directory}, so cosign (run from {tool}) cannot read the "
                         "manifest. Share the checkout with the Docker VM (./bin/install checks the same thing) "
                         f"or install cosign v{MIN_COSIGN_MAJOR} or later")
            else:
                proxy = self._proxy_failure(run, lines) if run.in_docker else None
                error = proxy or "signature verification failed" + (f": {lines[-1]}" if lines else "")
            return Verification(False, method=method, signer=signer, issuer=issuer, tool=tool, error=error + why)
        return Verification(True, method=method, signer=signer, issuer=issuer, tool=tool,
                            public_key_sha256=hashlib.sha256(_read_local(key_copy)).hexdigest() if key else "",
                            details={"note": run.note} if run.note else {})


def matches_trust(verification, version: str, *, identity: str, issuer: str,
                  public_key_sha256: str = "") -> bool:
    """Evaluate actual verifier output; never turn a filename or boolean into trust.

    Callers must also bind raw bytes and Verifier.trust_fingerprint(). Public-key
    mode is explicit host configuration; an empty key keeps exact keyless defaults.
    This predicate does not perform cryptography or establish freshness.
    """
    if not verification.verified or not verification.tool:
        return False
    if public_key_sha256:
        return (re.fullmatch(r"[0-9a-f]{64}", public_key_sha256) is not None
                and verification.method == "public key" and verification.issuer == ""
                and verification.public_key_sha256 == public_key_sha256)
    return (verification.method == "keyless" and verification.signer == identity.format(version=version)
            and verification.issuer == issuer and not verification.public_key_sha256)


def remove_workdir(workdir: Path) -> None:
    shutil.rmtree(workdir, ignore_errors=True)


__all__ = ["BUNDLE_ASSET", "FetchedManifest", "Verification", "VerificationError", "Verifier",
           "fetch_manifest", "remove_workdir"]
