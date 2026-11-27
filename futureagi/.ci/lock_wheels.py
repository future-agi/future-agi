"""Fetch the URL-pinned wheels of uv.lock once per Backend CI run.

uv installs a direct-URL dependency (en-core-web-sm, a GitHub release asset)
from its URL on every `uv sync`, so each backend job used to download it
again, and a GitHub 503 failed the job. The plan job now fetches each such
wheel once (bounded retries), and every backend job installs that copy
offline after `uv sync --no-install-package`.

The only trust anchor is the hash uv.lock records for the wheel at the tested
commit. Nothing here carries its own digest: a missing, ambiguous or
malformed lock entry fails, as does any byte stream (cache restore, artifact,
download) whose sha256 differs from the lock. Consumers never download.

  lock_wheels.py key LOCK                    print the cache key (key=...)
  lock_wheels.py fetch LOCK CACHE OUT        verify CACHE or download, copy to OUT
  lock_wheels.py requirements LOCK DIR REQ   verify DIR (no network), write REQ
  lock_wheels.py check LOCK PYTHON           installed versions equal the lock
"""

import argparse
import dataclasses
import hashlib
import http.client
import pathlib
import posixpath
import re
import shutil
import subprocess
import sys
import time
import tomllib
import urllib.error
import urllib.parse
import urllib.request

# The direct-URL packages the backend jobs install. Each must stay a
# `source = { url = ... }` entry with exactly one wheel in uv.lock.
PACKAGES = ("en-core-web-sm",)
# The host uv already downloads these from (its redirects included). No new hosts.
HOST = "github.com"
ATTEMPTS = 5
BACKOFF_SECONDS = (5, 10, 20, 40)
# Per socket operation, and for the whole fetch including pauses; the plan
# job's timeout leaves room for this on top of the Temporal prefetch.
READ_TIMEOUT_SECONDS = 30
DEADLINE_SECONDS = 180
CHUNK = 1 << 20
# With any 5xx, the HTTP statuses worth retrying: GitHub may answer
# throttling with 403 as well as 429, and 408 is a timeout. A 404 or 410
# (asset moved or deleted) cannot change on retry and fails at once.
RETRY_STATUSES = frozenset({403, 408, 429})
WHEEL_NAME = re.compile(r"[A-Za-z0-9_.+-]+\.whl")


class LockError(Exception):
    """uv.lock does not pin the wheel unambiguously."""


class WheelError(Exception):
    """A wheel is missing, could not be fetched, or does not match the lock."""


@dataclasses.dataclass(frozen=True)
class Locked:
    name: str
    version: str
    url: str
    sha256: str
    filename: str


def say(message, stream=None):
    stream = stream or sys.stdout
    stream.write(message + "\n")
    stream.flush()


def _normalize(name):
    return re.sub(r"[-_.]+", "-", name).lower()


def _wheel_names(filename, name, version):
    if not WHEEL_NAME.fullmatch(filename):
        return False
    fields = filename.removesuffix(".whl").split("-")
    if len(fields) not in (5, 6):
        return False
    return _normalize(fields[0]) == name and fields[1] == version


def locked(lock_text, name):
    try:
        lock = tomllib.loads(lock_text)
    except tomllib.TOMLDecodeError as exc:
        raise LockError(f"uv.lock is not TOML: {exc}") from exc
    packages = lock.get("package")
    if not isinstance(packages, list):
        raise LockError("uv.lock has no [[package]] entries")
    entries = [p for p in packages if isinstance(p, dict) and p.get("name") == name]
    if len(entries) != 1:
        raise LockError(f"uv.lock has {len(entries)} entries for {name}, not 1")
    (entry,) = entries
    source = entry.get("source")
    if not isinstance(source, dict) or set(source) != {"url"}:
        raise LockError(f"{name} is not a direct-URL source in uv.lock")
    url = source["url"]
    wheels = entry.get("wheels")
    if not isinstance(wheels, list) or len(wheels) != 1:
        raise LockError(f"{name} must lock exactly one wheel")
    (wheel,) = wheels
    if not isinstance(wheel, dict) or wheel.get("url") != url:
        raise LockError(f"{name}'s locked wheel URL is not its source URL")
    digest = re.fullmatch(r"sha256:([0-9a-f]{64})", str(wheel.get("hash", "")))
    if digest is None:
        raise LockError(f"{name}'s locked wheel has no sha256 hash")
    version = entry.get("version")
    if not isinstance(version, str) or not version:
        raise LockError(f"{name} has no locked version")
    parts = urllib.parse.urlsplit(url)
    remote = parts.scheme == "https" and parts.netloc == HOST
    if not remote or parts.query or parts.fragment:
        raise LockError(f"{name}'s locked URL is not an https://{HOST}/ asset")
    filename = posixpath.basename(parts.path)
    if not _wheel_names(filename, name, version):
        raise LockError(f"{name}'s locked URL does not name its {version} wheel")
    return Locked(name, version, url, digest.group(1), filename)


def locked_all(lock_path):
    text = lock_path.read_text(encoding="utf-8")
    return [locked(text, name) for name in PACKAGES]


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verified(path, item):
    if path.is_symlink() or not path.is_file():
        return False
    return sha256(path) == item.sha256


def key(items):
    lines = [f"{i.name} {i.version} {i.url} sha256:{i.sha256}\n" for i in items]
    digest = hashlib.sha256("".join(lines).encode()).hexdigest()
    return f"backend-lock-wheels-v1-{digest}"


def _permanent(exc):
    """An HTTP answer that a retry cannot change, such as 404 or 410."""
    if not isinstance(exc, urllib.error.HTTPError):
        return False
    return exc.code not in RETRY_STATUSES and not 500 <= exc.code <= 599


def download(
    item, partial, *, opener=urllib.request.urlopen, sleep=time.sleep, clock=None
):
    """Write the asset to `partial`, retrying transient failures only."""
    clock = clock or time.monotonic
    deadline = clock() + DEADLINE_SECONDS
    headers = {"User-Agent": "backend-ci-lock-wheels"}
    request = urllib.request.Request(item.url, headers=headers)
    for attempt in range(1, ATTEMPTS + 1):
        try:
            with opener(request, timeout=READ_TIMEOUT_SECONDS) as response:
                with partial.open("wb") as out:
                    while chunk := response.read(CHUNK):
                        if clock() > deadline:
                            raise TimeoutError("deadline passed mid-transfer")
                        out.write(chunk)
            return
        except (OSError, http.client.HTTPException) as exc:
            # urllib.error.URLError/HTTPError and socket timeouts are OSErrors.
            partial.unlink(missing_ok=True)
            if _permanent(exc):
                message = f"{item.filename}: {exc}; not retried"
                raise WheelError(message) from exc
            delay = BACKOFF_SECONDS[min(attempt, len(BACKOFF_SECONDS)) - 1]
            if attempt == ATTEMPTS or clock() + delay > deadline:
                message = f"{item.filename}: {attempt} attempts failed; last: {exc}"
                raise WheelError(message) from exc
            say(f"::warning::{item.filename} attempt {attempt}/{ATTEMPTS}: {exc}")
            sleep(delay)


def fetch(items, cache, out, **transport):
    cache.mkdir(parents=True, exist_ok=True)
    out.mkdir(parents=True, exist_ok=True)
    if any(out.iterdir()):
        raise WheelError(f"{out} must start empty")
    for item in items:
        cached = cache / item.filename
        if cached.exists() or cached.is_symlink():
            if verified(cached, item):
                say(f"Lock wheel cache hit: {item.filename}")
            else:
                # Never used: a restore that does not match the lock is a miss.
                say(f"::warning::{item.filename} cache copy differs from uv.lock")
                cached.unlink()
        if not cached.exists():
            say(f"Lock wheel cache miss: fetching {item.url}")
            partial = cache / (item.filename + ".partial")
            try:
                download(item, partial, **transport)
                if not verified(partial, item):
                    # Integrity, not availability: no retry, nothing kept.
                    got = sha256(partial)
                    raise WheelError(f"{item.filename} sha256 {got} is not uv.lock's")
                partial.replace(cached)
            finally:
                partial.unlink(missing_ok=True)
        shutil.copyfile(cached, out / item.filename)
        if not verified(out / item.filename, item):
            raise WheelError(f"{item.filename} changed while staging")
        say(f"Verified {item.filename} against uv.lock sha256:{item.sha256}")


def requirements(items, directory):
    lines = []
    for item in items:
        path = directory / item.filename
        if not path.is_file():
            raise WheelError(f"{path} is missing; this job never downloads it")
        if not verified(path, item):
            raise WheelError(f"{path} does not match uv.lock sha256:{item.sha256}")
        uri = path.resolve().as_uri()
        lines.append(f"{item.name} @ {uri} --hash=sha256:{item.sha256}\n")
    return "".join(lines)


def check(items, python):
    probe = "import importlib.metadata as m, sys; print(m.version(sys.argv[1]))"
    for item in items:
        command = [python, "-I", "-c", probe, item.name]
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        found = result.stdout.strip()
        if result.returncode != 0 or found != item.version:
            detail = result.stderr.strip()
            raise WheelError(
                f"{item.name} installed {found or 'nothing'}, uv.lock pins "
                f"{item.version}: {detail}"
            )
        say(f"{item.name} {found} installed from the verified lock wheel")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("key").add_argument("lock", type=pathlib.Path)
    command = commands.add_parser("fetch")
    for name in ("lock", "cache", "out"):
        command.add_argument(name, type=pathlib.Path)
    command = commands.add_parser("requirements")
    for name in ("lock", "directory", "output"):
        command.add_argument(name, type=pathlib.Path)
    command = commands.add_parser("check")
    command.add_argument("lock", type=pathlib.Path)
    command.add_argument("python")
    args = parser.parse_args(argv)
    try:
        items = locked_all(args.lock)
        if args.command == "key":
            say(f"key={key(items)}")
        elif args.command == "fetch":
            fetch(items, args.cache, args.out)
        elif args.command == "requirements":
            args.output.write_text(requirements(items, args.directory))
        else:
            check(items, args.python)
    except (LockError, WheelError, OSError) as exc:
        say(f"::error::{exc}", sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
