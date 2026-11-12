"""Update state on disk: the host lock, the in-progress journal and receipts.

Everything lives in .futureagi-updates/ in the checkout (mode 0700, files
0600, ignored by git). Receipts record version values and image digests,
never env file contents; text that came from a command is scrubbed of the
env file's secret values before it is stored.

Every failure to read, write or lock the state is a StateError that names
the file and, when another user owns it (an earlier `sudo ./bin/update`),
how to give it back: never a traceback.
"""

from __future__ import annotations

import errno
import json
import os
import re
import shlex
import stat
import uuid
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, List, Mapping, Optional

STATE_DIR = ".futureagi-updates"
JOURNAL = "journal.json"
LOCK = "update.lock"
RECEIPT_SCHEMA = 2
IDENTITY_FIELDS = ("mode", "production", "env_file", "project")
# Public schema: new private transaction fields must never become receipts by
# accident. This is an allowlist, not a blacklist of known secret-bearing keys.
_PUBLIC_RECEIPT_FIELDS = frozenset((*IDENTITY_FIELDS, "kind", "status", "stage", "started_at", "finished_at",
    "run_id", "target_version", "from_version", "from_versions", "manifest_hash", "manifest_source", "channel",
    "signature", "components", "env_changes", "found_values", "previous_values", "previous_images", "backup",
    "resumes", "rolls_back", "restarted", "target_image_ids", "error", "message", "note"))
_SECRET_KEY_RE = re.compile(r"(PASSWORD|SECRET|TOKEN|_KEY|KEY_|CREDENTIAL|DSN|PRIVATE|LICENSE)", re.I)
_RECEIPT_NAME_RE = re.compile(r"^receipt-\d{8}T\d{6}Z-\d+\.json$")
# Fields of a journal or receipt whose text came from a command or an exception (and may quote the env
# file). Only these are scrubbed: structural fields (project, env_file, paths, images, digests, versions,
# previous_values, found_values) are compared and written back, so they must stay exactly as they are.
_FREE_TEXT_FIELDS = ("error", "message", "note")
# What flock() answers on a file system without working locks (NFS without a lock daemon, some SMB and FUSE
# mounts).
_NO_LOCKS = frozenset(getattr(errno, name) for name in ("ENOLCK", "EOPNOTSUPP", "ENOTSUP", "ENOSYS")
                      if hasattr(errno, name))
# How long lock() keeps trying when the lock is held only for a moment (lock_holder() peeking at it).
_LOCK_RETRY_SECONDS = 0.25


class StateError(RuntimeError):
    """Update state cannot be read, written or locked."""


class AutomaticRecoveryPending(StateError):
    """Unreadable enrolled authority is distinct from a corrupt manual journal."""
    reason_code = 'automatic_recovery_pending'


class StateBusy(StateError):
    """Typed update.lock contention; manual error text and behavior are unchanged."""


def secret_values(env: Mapping[str, str]) -> List[str]:
    return sorted({v for k, v in env.items() if _SECRET_KEY_RE.search(k) and len(v) >= 6}, key=len, reverse=True)


def scrub(text: str, secrets: List[str]) -> str:
    for value in secrets:
        text = text.replace(value, "***")
    return text


def scrub_tree(value: Any, secrets: List[str]) -> Any:
    if isinstance(value, str):
        return scrub(value, secrets)
    if isinstance(value, Mapping):
        return {k: scrub_tree(v, secrets) for k, v in value.items()}
    if isinstance(value, list):
        return [scrub_tree(v, secrets) for v in value]
    return value


def scrub_record(record: Mapping[str, Any], secrets: List[str]) -> Dict[str, Any]:
    """`record` with its free-text fields scrubbed: error, message, note and signature.error.

    Nothing else is touched: a weak secret such as `futureagi` must not turn
    the project `futureagi` (or an image name, or a path) into `***`.
    """

    body = dict(record)
    for key in _FREE_TEXT_FIELDS:
        if key in body:
            body[key] = scrub_tree(body[key], secrets)
    signature = body.get("signature")
    if isinstance(signature, Mapping) and "error" in signature:
        body["signature"] = dict(signature, error=scrub_tree(signature["error"], secrets))
    return body


def _user_name(uid: int) -> Optional[str]:
    try:
        import pwd
        return pwd.getpwuid(uid).pw_name
    except (ImportError, KeyError):
        return None


def ownership_hint(path: Path, state_dir: Path) -> str:
    """Why `path` may be out of reach, when another user owns it (or the state directory), and the fix."""

    geteuid = getattr(os, "geteuid", None)
    if geteuid is None:
        return ""
    me = geteuid()
    for candidate in (path, state_dir):
        try:
            owner = os.stat(str(candidate)).st_uid
        except OSError:
            continue
        if owner != me:
            name = _user_name(owner) or f"uid {owner}"
            # chown takes a numeric id where no name is known.
            mine = _user_name(me) or str(me)
            return (f". It belongs to {name}, probably from an earlier run as {name} (sudo): run bin/update as "
                    f"{name}, or give the state back with `sudo chown -R {shlex.quote(mine)} "
                    f"{shlex.quote(str(state_dir))}`")
    return ""


def _write_json(path: Path, value: Mapping[str, Any], state_dir: Optional[Path] = None) -> None:
    hint = lambda: ownership_hint(path, state_dir or path.parent)  # noqa: E731
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        fd = os.open(str(temp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    except OSError as exc:
        raise StateError(f"cannot write {path}: {exc}{hint()}") from exc
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, sort_keys=True, indent=2)
            handle.write("\n")
            handle.flush()
            from .envcommit import sync_file
            sync_file(handle.fileno())
        os.replace(temp, path)
        _fsync_dir(path.parent)
        # Persist a newly created state directory too, before an env mutation.
        _fsync_dir(path.parent.parent)
        if sys.platform == "darwin":
            with path.open("rb") as handle:
                sync_file(handle.fileno())
    except BaseException as exc:
        try:
            os.unlink(temp)
        except OSError:
            pass
        if isinstance(exc, OSError):  # a full disk, say: an error bin/update reports, not a traceback
            raise StateError(f"cannot write {path}: {exc}{hint()}") from exc
        raise


def _fsync_dir(path: Path) -> None:
    fd = os.open(str(path), os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _read_json(path: Path, state_dir: Optional[Path] = None) -> Dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeError) as exc:
        raise StateError(f"cannot read {path}: {exc}") from exc
    except OSError as exc:
        raise StateError(f"cannot read {path}: {exc}{ownership_hint(path, state_dir or path.parent)}") from exc
    if not isinstance(value, dict):
        raise StateError(f"{path} is not a JSON object")
    return value


def _receipt_number(path: Path) -> int:
    return int(path.stem.rsplit("-", 1)[1])


class StateDir:
    def __init__(self, root: Path, secrets: Optional[List[str]] = None) -> None:
        self.path = root / STATE_DIR
        self.secrets = secrets or []

    def _hint(self, path: Path) -> str:
        return ownership_hint(path, self.path)

    def ensure(self) -> None:
        try:
            self.path.mkdir(mode=0o700, parents=True, exist_ok=True)
        except OSError as exc:
            raise StateError(f"cannot create {self.path}: {exc}{self._hint(self.path)}") from exc
        try:
            os.chmod(self.path, 0o700)
        except OSError as exc:
            raise StateError(f"cannot use {self.path}: {exc}{self._hint(self.path)}") from exc

    @property
    def lock_path(self) -> Path:
        return self.path / LOCK

    @contextmanager
    def lock(self, *, automation: bool = False) -> Iterator[None]:
        """One bin/update at a time; scheduled callers never adopt shared/link state."""

        if automation:
            with self.private_directory(create=True):
                pass
        else:
            self.ensure()
        try:
            import fcntl
        except ImportError as exc:  # Windows
            raise StateError("bin/update needs a POSIX system (Linux, macOS or WSL)") from exc
        path = self.lock_path
        try:
            fd = os.open(str(path), os.O_RDWR | os.O_CREAT | (os.O_NOFOLLOW | os.O_NONBLOCK if automation else 0), 0o600)
        except OSError as exc:
            raise StateError(f"cannot open {path}: {exc}{self._hint(path)}") from exc
        try:
            if automation:
                info = os.fstat(fd)
                if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.geteuid()
                        or stat.S_IMODE(info.st_mode) != 0o600):
                    raise StateError('untrusted update lock')
            deadline = time.monotonic() + (0 if automation else _LOCK_RETRY_SECONDS)
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError as exc:
                    if time.monotonic() < deadline:
                        time.sleep(0.02)
                        continue
                    holder = self._holder_text(fd)
                    raise (StateBusy if automation else StateError)(
                        f"another bin/update is running in this checkout ({holder or 'lock held'})") from exc
                except OSError as exc:
                    if exc.errno in _NO_LOCKS:
                        raise StateError(
                            f"cannot lock {path}: {exc}. The checkout is on a file system without working file "
                            "locks (NFS without a lock daemon, for example): run bin/update from a checkout on a "
                            "local disk, or enable locking on that mount") from exc
                    raise StateError(f"cannot lock {path}: {exc}{self._hint(path)}") from exc
            try:
                try:
                    os.ftruncate(fd, 0)
                    os.pwrite(fd, f"pid {os.getpid()}\n".encode("ascii"), 0)
                except OSError:
                    pass  # who holds the lock is only informational
                yield
            finally:
                try:
                    fcntl.flock(fd, fcntl.LOCK_UN)
                except OSError:
                    pass  # closing the descriptor releases it as well
        finally:
            os.close(fd)

    @staticmethod
    def _holder_text(fd: int) -> str:
        try:
            return os.pread(fd, 64, 0).decode("ascii", "replace").strip()
        except OSError:
            return ""

    def lock_holder(self, *, automation: bool = False) -> Optional[str]:
        """Noncreating, nonblocking probe; unsafe evidence is never 'unheld'.

        Ordinary callers accept owner-owned, non-shared regular legacy locks.
        Scheduled callers additionally require the private directory and 0600
        lock used by lock(automation=True). No PID text is read before fstat.
        """
        try:
            if automation:
                with self.private_directory() as directory:
                    return self._probe_lock(LOCK, directory=directory, private=True)
            try:
                return self._probe_lock(str(self.lock_path))
            except NotADirectoryError:
                # Ordinary status historically tolerates an owned regular file
                # in place of the state directory: no child lock can exist.
                # ENOTDIR alone is insufficient (a FIFO or symlink does that
                # too). Qualify the actual descriptor without following links
                # or blocking, and never extend this exception to automation.
                fd = os.open(str(self.path), os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW | os.O_CLOEXEC)
                try:
                    info = os.fstat(fd)
                    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_nlink != 1
                            or stat.S_IMODE(info.st_mode) & 0o7022):
                        raise StateError('cannot safely probe update lock')
                    return None
                finally:
                    os.close(fd)
        except FileNotFoundError:
            return None
        except (OSError, ImportError) as exc:
            raise StateError('cannot safely probe update lock') from exc

    def _probe_lock(self, path, *, directory=None, private=False):
        import fcntl
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW | os.O_CLOEXEC,
                     dir_fd=directory)
        try:
            info = os.fstat(fd)
            mode = stat.S_IMODE(info.st_mode)
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_nlink != 1
                    or mode & 0o7022 or private and mode != 0o600):
                raise StateError('untrusted update lock')
            try:
                fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
            except BlockingIOError:
                return self._holder_text(fd) or "lock held"
            # Closing releases the shared probe even if explicit unlock fails.
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            except OSError:
                pass
            return None
        finally:
            os.close(fd)

    # --------------------------------------------------------- journal --
    @property
    def journal_path(self) -> Path:
        return self.path / JOURNAL

    def read_journal(self) -> Optional[Dict[str, Any]]:
        # Read rather than test exists(): exists() hides an unreadable journal on newer Pythons and raises on
        # older ones; either way an interrupted run must not go unnoticed.
        try:
            text = self.journal_path.read_text(encoding="utf-8")
        except (FileNotFoundError, NotADirectoryError):
            return None
        except (OSError, UnicodeError) as exc:
            raise StateError(f"cannot read {self.journal_path}: {exc}{self._hint(self.journal_path)}") from exc
        try:
            value = json.loads(text)
        except json.JSONDecodeError as exc:
            raise StateError(f"cannot read {self.journal_path}: {exc}") from exc
        if not isinstance(value, dict):
            raise StateError(f"{self.journal_path} is not a JSON object")
        return value

    def automatic_pending(self, installation) -> bool:
        """Read the enrolled authority, including the pre-env-journal cold gap.

        Manual commands must not interpret an automatic env journal as an
        image-only recovery. Absence remains the ordinary legacy/manual path.
        Corrupt/symlinked enrolled authority is not equivalent to no attempt.
        """
        journal = self.read_journal()
        if journal and journal.get('automatic_transaction'):
            return True
        import hashlib
        from .runner_state import RunnerStateStore, RunnerState, Registration
        # Selection hashes contain the RESOLVED env target. A fresh discovery
        # after alias drift computes a different hash; absence there says nothing
        # about a stopped installation's previously enrolled authority. Inspect
        # all retained selections in this checkout without resolving their aliases
        # or adopting them as valid execution bindings. One checkout has one env
        # journal/update lock, so any outstanding restore-backed attempt interlocks
        # manual mutation. Inactive, valid enrollments do not block legacy use.
        automation = None
        try:
            with self.private_directory() as directory:
                try:
                    automation = os.open('automation', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW |
                                         os.O_CLOEXEC, dir_fd=directory)
                except FileNotFoundError:
                    return False
                info = os.fstat(automation)
                if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
                    raise StateError('untrusted automatic authority')
                names = os.listdir(automation)
                if len(names) > 1024:
                    raise StateError('automatic authority exceeds inspection bound')
                for name in names:
                    # The global runner lock is the only non-selection leaf.
                    if name == 'runner.lock':
                        continue
                    if re.fullmatch(r'[0-9a-f]{64}', name) is None:
                        raise StateError('unrecognized automatic authority')
                    child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                                    dir_fd=automation)
                    try:
                        info = os.fstat(child)
                        if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
                            raise StateError('untrusted automatic authority')
                        current = RunnerStateStore._read(child, 'state.json', RunnerState)
                        registration = RunnerStateStore._read(child, 'registration.json', Registration)
                        binding = registration.binding
                        if (binding.checkout != installation.root.resolve(strict=True)
                                or binding.host_uid != os.geteuid()
                                or binding.selection_hash != name
                                or hashlib.sha256(json.dumps([str(binding.checkout), str(binding.env_target),
                                    binding.production, binding.topology], ensure_ascii=True,
                                    separators=(',', ':')).encode()).hexdigest() != name
                                or current.installation_fingerprint != binding.installation_fingerprint):
                            raise StateError('conflicting automatic authority')
                        if current.active_attempt_id is not None and current.baseline_head is not None:
                            return True
                    finally:
                        os.close(child)
                return False
        except FileNotFoundError as exc:
            # No state directory is the normal never-enrolled path. Missing
            # records INSIDE an existing authority are incomplete, not absent.
            if automation is None:
                return False
            raise AutomaticRecoveryPending('automatic_recovery_pending') from exc
        except Exception as exc:
            raise AutomaticRecoveryPending('automatic_recovery_pending') from exc
        finally:
            if automation is not None:
                os.close(automation)

    def write_journal(self, record: Mapping[str, Any]) -> None:
        self.ensure()
        _write_json(self.journal_path, scrub_record(record, self.secrets), self.path)

    def clear_journal(self) -> None:
        try:
            self.journal_path.unlink()
            _fsync_dir(self.path)
        except FileNotFoundError:
            pass
        except OSError as exc:
            raise StateError(f"cannot remove {self.journal_path}: {exc}{self._hint(self.journal_path)}") from exc

    # -------------------------------------------------------- receipts --
    def _retain_env_reference(self, record: Mapping[str, Any]) -> List[str]:
        recovery = record.get("env_recovery")
        if record.get("journal_version") != 2 or not isinstance(recovery, Mapping) or not recovery.get("commit"):
            return []
        from . import envcommit
        ref = recovery["commit"]
        envcommit.validate_reference(ref)
        # Metadata-only discovery survives journal removal and a target on
        # another volume. It contains no env bytes or full-file digests.
        index = self.path / ("env-retained-" + ref["forward"] + ".json")
        _write_json(index, {"target": recovery["target"], "commit": ref}, self.path)
        return [ref["forward"], ref["restore"]]

    def sync_finished(self, record: Mapping[str, Any], path: Path) -> None:
        """A visible receipt may have been killed just before its namespace barrier."""
        from .envcommit import sync_file
        self.load_receipt(path)  # Require this state's valid receipt before syncing it.
        self._retain_env_reference(record)
        try:
            with path.open("rb") as handle:
                sync_file(handle.fileno())
                _fsync_dir(self.path)
                if sys.platform == "darwin":
                    sync_file(handle.fileno())
        except OSError as exc:
            raise StateError(f"cannot make completed receipt durable: {path}") from exc

    def write_receipt(self, record: Mapping[str, Any]) -> Path:
        self.ensure()
        stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        # Receipts are ordered by this number. The wall clock alone can step backwards (NTP, a manual
        # fix), which would put this receipt before older ones: it always comes after the newest.
        existing = self.receipts()
        number = max(time.time_ns(), _receipt_number(existing[-1]) + 1 if existing else 0)
        path = self.path / f"receipt-{stamp}-{number}.json"
        if 'stage_binding' in record:
            self.write_private('stage', record['run_id'], record['stage_binding'])
        if 'automation' in record:
            self.write_private('automation', record['run_id'], record['automation'])
        body = {key: value for key, value in record.items() if key in _PUBLIC_RECEIPT_FIELDS}
        body["schema_version"] = RECEIPT_SCHEMA
        ids = self._retain_env_reference(record)
        if ids:
            body["env_commit_ids"] = ids
        _write_json(path, scrub_record(body, self.secrets), self.path)
        return path

    @contextmanager
    def private_directory(self, *, create: bool = False) -> Iterator[int]:
        """Qualified descriptor for private automation evidence, never a public receipt.

        Caller holds update.lock for writes. No operation follows a leaf/parent
        symlink, opens a FIFO, or accepts a shared/multiply linked evidence file.
        """
        fd = None
        try:
            if create:
                # Refuse an existing symlink before ensure's manual chmod path.
                try:
                    info = self.path.lstat()
                    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid():
                        raise StateError('untrusted private update state')
                except FileNotFoundError:
                    self.ensure()
            fd = os.open(str(self.path), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            info = os.fstat(fd)
            if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
                raise StateError('untrusted private update state')
            yield fd
        finally:
            if fd is not None:
                os.close(fd)

    @staticmethod
    def _private_name(kind: str, key: str) -> str:
        if kind not in ('download', 'automation', 'fallback', 'stage') or not re.fullmatch(r'[0-9a-f]{32,64}', key):
            raise StateError('invalid private update identity')
        return kind + '-' + key + '.json'

    def write_private(self, kind: str, key: str, value: Mapping[str, Any]) -> Path:
        """Atomic private evidence, independent of unfinished/ROLLBACKABLE history."""
        name = self._private_name(kind, key)
        raw = json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')
        if len(raw) > 262144:
            raise StateError('private update evidence exceeds limit')
        with self.private_directory(create=True) as directory:
            temp = '.' + name + '.' + uuid.uuid4().hex
            fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
            try:
                with os.fdopen(fd, 'wb') as handle:
                    handle.write(raw)
                    handle.flush()
                    from .envcommit import sync_file
                    sync_file(handle.fileno())
                os.replace(temp, name, src_dir_fd=directory, dst_dir_fd=directory)
                os.fsync(directory)
            finally:
                try:
                    os.unlink(temp, dir_fd=directory)
                except FileNotFoundError:
                    pass
        return self.path / name

    def read_private(self, kind: str, key: str) -> Optional[Dict[str, Any]]:
        name = self._private_name(kind, key)
        try:
            with self.private_directory() as directory:
                fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
                with os.fdopen(fd, 'rb') as handle:
                    before = os.fstat(handle.fileno())
                    if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                            or before.st_uid != os.geteuid() or stat.S_IMODE(before.st_mode) != 0o600
                            or before.st_size > 262144):
                        raise StateError('untrusted private update evidence')
                    raw = handle.read(262145)
                    after = os.fstat(handle.fileno())
                    fields = ('st_dev', 'st_ino', 'st_size', 'st_mtime_ns', 'st_ctime_ns',
                              'st_mode', 'st_uid', 'st_nlink')
                    if any(getattr(before, f) != getattr(after, f) for f in fields) or len(raw) != before.st_size:
                        raise StateError('private update evidence changed')
            from .compatibility import decode_document
            value = decode_document(raw)
            if not isinstance(value, dict):
                raise StateError('invalid private update evidence')
            return value
        except FileNotFoundError:
            return None
        except (OSError, ValueError):
            raise StateError('unreadable private update evidence') from None

    def receipts(self) -> List[Path]:
        try:
            names = os.listdir(str(self.path))
        except (FileNotFoundError, NotADirectoryError):
            return []
        except OSError as exc:
            raise StateError(f"cannot read {self.path}: {exc}{self._hint(self.path)}") from exc
        return sorted((self.path / name for name in names if _RECEIPT_NAME_RE.fullmatch(name)), key=_receipt_number)

    def latest_receipt(self, identity: Optional[Mapping[str, Any]] = None) -> Optional[Dict[str, Any]]:
        """The newest receipt; with `identity`, the newest one written for that install.

        A field `identity` sets to None (the project, when Docker cannot say)
        is not compared, and neither is one the receipt does not record.
        """

        for path in reversed(self.receipts()):
            record = _read_json(path, self.path)
            if identity is None or all(identity.get(field) is None or record.get(field) is None
                                       or record.get(field) == identity.get(field) for field in IDENTITY_FIELDS):
                record["_path"] = str(path)
                return record
        return None

    def history(self, identity: Mapping[str, Any]) -> List[Dict[str, Any]]:
        """This install's receipts, oldest first, each with its `_path`.

        One checkout can serve several installs (a second production env
        file, another project): only receipts whose mode, production flag, env
        file and project all equal `identity` belong to this one. Receipts
        written by another version of bin/update are left out.
        """

        records = []
        for path in self.receipts():
            record = _read_json(path, self.path)
            if record.get("schema_version") != RECEIPT_SCHEMA:
                continue
            if all(record.get(field) == identity.get(field) for field in IDENTITY_FIELDS):
                record["_path"] = str(path)
                records.append(record)
        return records

    def load_receipt(self, path: Path) -> Dict[str, Any]:
        """A receipt from this checkout's state directory, and nowhere else."""

        resolved = path.resolve()
        if resolved.parent != self.path.resolve() or not _RECEIPT_NAME_RE.fullmatch(resolved.name):
            raise StateError(f"{path} is not a receipt in {self.path}; use the path bin/update printed")
        record = _read_json(resolved, self.path)
        if record.get("schema_version") != RECEIPT_SCHEMA:
            raise StateError(f"{path} was written by another version of bin/update")
        record["_path"] = str(resolved)
        return record
