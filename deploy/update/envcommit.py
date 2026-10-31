"""Preserving env commits, not compare-and-swap.

Only APFS on macOS and ext4 on Linux are enabled. The native exchange moves
whatever entry actually occupies the target into a private retained slot.
Neither that slot nor its candidate identity anchor is ever unlinked here,
including on success: an editor can still hold a writable descriptor to it.
An armed operation is reconciled by names/identities, never blindly replayed.
Parent topology and the private namespace must not be concurrently changed.
"""
from __future__ import annotations

import ctypes
import errno
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import uuid
from contextlib import ExitStack, contextmanager


class CommitError(ValueError):
    """An unresolved env commit. Do not automatically restore or clear its journal."""


_ID = re.compile(r"[0-9a-f]{32}")
_MAX_OPERATIONS = 1024  # Refuse, never prune: slots can hold the only operator edit.


def reference() -> dict:
    # Both directions are allocated BEFORE the containing journal is durable.
    return {"format": 1, "forward": uuid.uuid4().hex, "restore": uuid.uuid4().hex}


def validate_reference(ref) -> None:
    if (not isinstance(ref, dict) or set(ref) != {"format", "forward", "restore"}
            or type(ref["format"]) is not int or ref["format"] != 1
            or any(not isinstance(ref[k], str) or not _ID.fullmatch(ref[k]) for k in ("forward", "restore"))
            or ref["forward"] == ref["restore"]):
        raise CommitError("invalid private env commit reference; keep the journal for manual recovery")


def directory(target: Path, op_id: str) -> Path:
    if not isinstance(op_id, str) or not _ID.fullmatch(op_id):
        raise CommitError("invalid env commit operation id")
    key = hashlib.sha256(os.fsencode(target.name)).hexdigest()[:32]
    return target.parent / '.futureagi-updates' / 'env-commits' / key / op_id


def _libc():
    if sys.platform not in ('linux', 'darwin'):
        raise CommitError("safe env commits require Linux/ext4 or macOS/APFS; no replacement fallback")
    return ctypes.CDLL(None, use_errno=True)


def exchange(src_fd: int, src: str, dst_fd: int, dst: str) -> None:
    """One native exchange, with no fallback and no implicit path resolution."""
    libc = _libc()
    try:
        function = getattr(libc, 'renameat2' if sys.platform == 'linux' else 'renameatx_np')
    except AttributeError as exc:
        raise CommitError("native atomic env exchange is unavailable; no replacement fallback") from exc
    function.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    function.restype = ctypes.c_int
    # Linux RENAME_EXCHANGE and Darwin RENAME_SWAP are both 2 (NOT NOREPLACE).
    if function(src_fd, os.fsencode(src), dst_fd, os.fsencode(dst), 2):
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code))


def _filesystem(fd: int) -> str:
    libc = _libc()
    # fstatfs's output is platform-specific. Oversized/aligned storage avoids
    # exposing unrelated struct fields; offsets below follow the native headers.
    storage = (ctypes.c_uint64 * 512)()
    function = libc.fstatfs
    function.argtypes = [ctypes.c_int, ctypes.c_void_p]
    function.restype = ctypes.c_int
    if function(fd, ctypes.byref(storage)):
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code))
    if sys.platform == 'linux':
        kind = ctypes.cast(storage, ctypes.POINTER(ctypes.c_long))[0]
        name = 'unsupported'
        # ext2/3 share ext4's magic; require the actual mount type as well.
        selected = os.path.realpath('/proc/self/fd/' + str(fd))
        mounts = []
        for line in Path('/proc/self/mountinfo').read_text(encoding='utf-8').splitlines():
            fields, tail = line.split(' - ', 1)
            mount = re.sub(r'\\([0-7]{3})', lambda m: chr(int(m.group(1), 8)), fields.split()[4])
            if selected == mount or selected.startswith(mount.rstrip('/') + '/'):
                mounts.append((len(mount), tail.split()[0]))
        if kind == 0xEF53 and mounts and max(mounts)[1] == 'ext4':
            name = 'ext4'
    else:
        # Darwin's 64-bit statfs: two uint32, five uint64, fsid_t,
        # owner/type/flags/subtype uint32, then f_fstypename[16].
        name = bytes(storage)[72:88].split(b'\0', 1)[0].decode('ascii', 'replace')
    if name not in ('ext4', 'apfs'):
        raise CommitError("safe env commits support only local ext4/APFS, not this filesystem; "
                          "no replacement fallback")
    return name


def sync_file(fd: int) -> None:
    os.fsync(fd)
    if sys.platform == 'darwin':
        import fcntl
        fcntl.fcntl(fd, 51)  # F_FULLFSYNC, in addition to namespace fsync below


def sync_dir(fd: int) -> None:
    os.fsync(fd)


def _plain_metadata(fd: int) -> None:
    # Do not silently discard ACLs/security labels/xattrs on the replacement.
    if sys.platform == 'darwin':
        libc = _libc()
        listing = libc.flistxattr
        listing.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_int]
        listing.restype = ctypes.c_ssize_t
        names = ctypes.create_string_buffer(65536)
        count = listing(fd, names, len(names), 0)
        # macOS adds this system-managed provenance to newly created files too;
        # it is not copied. All caller-managed metadata remains unsupported.
        if count < 0 or set(names.raw[:count].split(b'\0')) - {b'', b'com.apple.provenance'}:
            raise CommitError("env commit requires verifiably xattr-free files/directories")
    elif os.listxattr(fd):
        raise CommitError("env commit requires files/directories without extended attributes or ACLs")
    if sys.platform == 'darwin':
        libc = _libc()
        get = libc.acl_get_fd_np
        get.argtypes, get.restype = [ctypes.c_int, ctypes.c_int], ctypes.c_void_p
        acl = get(fd, 0x100)  # ACL_TYPE_EXTENDED
        if acl:
            free = libc.acl_free
            free.argtypes, free.restype = [ctypes.c_void_p], ctypes.c_int
            free(acl)
            raise CommitError("env commit requires files/directories without extended ACLs")
        if ctypes.get_errno() != errno.ENOENT:
            raise CommitError("cannot establish private ACL-free env retention")


@contextmanager
def _dir(path: Path, private: bool = False):
    fd = os.open(str(path), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        if private:
            if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
                raise CommitError("env retention directory must be owned by this user and mode 0700")
            _plain_metadata(fd)
        yield fd
    finally:
        os.close(fd)


def _identity(info) -> list:
    return [info.st_dev, info.st_ino]


def _snapshot(fd: int, name: str, *, sync: bool = False) -> dict:
    handle = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
    try:
        info = os.fstat(handle)
        if not stat.S_ISREG(info.st_mode):
            raise CommitError("env commit encountered a non-regular entry; it was not followed")
        _plain_metadata(handle)
        digest = hashlib.sha256()
        while True:
            data = os.read(handle, 131072)
            if not data:
                break
            digest.update(data)
        after = os.fstat(handle)
        if (info.st_size, info.st_mtime_ns, info.st_ctime_ns) != (
                after.st_size, after.st_mtime_ns, after.st_ctime_ns):
            raise CommitError("env entry changed while being inspected")
        if sync:
            sync_file(handle)
        return {"identity": _identity(info), "mode": stat.S_IMODE(info.st_mode),
                "uid": info.st_uid, "gid": info.st_gid, "links": info.st_nlink,
                "sha256": digest.hexdigest()}
    finally:
        os.close(handle)


@contextmanager
def _stream(fd: int, mode: str):
    try:
        stream = os.fdopen(fd, mode)
    except BaseException:
        os.close(fd)
        raise
    with stream:
        yield stream


def _json(fd: int, name: str, body: dict) -> None:
    # Only updater-owned metadata is replaced. Never use this for env entries.
    temp = '.' + uuid.uuid4().hex + '.json'
    handle = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
    try:
        with _stream(handle, 'wb') as stream:
            stream.write((json.dumps(body, sort_keys=True) + '\n').encode('ascii'))
            stream.flush()
            sync_file(stream.fileno())
        os.replace(temp, name, src_dir_fd=fd, dst_dir_fd=fd)
        sync_dir(fd)
        if sys.platform == 'darwin':
            published = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=fd)
            try:
                sync_file(published)
            finally:
                os.close(published)
    finally:
        try:
            os.unlink(temp, dir_fd=fd)
        except FileNotFoundError:
            pass


def _load(fd: int) -> dict:
    handle = os.open('manifest.json', os.O_RDONLY | os.O_NOFOLLOW, dir_fd=fd)
    with _stream(handle, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o600:
            raise CommitError("invalid private env manifest metadata")
        try:
            body = json.loads(stream.read(65537))
        except (ValueError, UnicodeError) as exc:
            raise CommitError("incomplete private env manifest; keep all retained files") from exc
    if not isinstance(body, dict):
        raise CommitError("invalid private env manifest")
    return body


def _error(op: Path, reason: str) -> CommitError:
    return CommitError(f"env commit conflict or indeterminate result ({reason}). The live env may have changed. "
                       f"Keep the journal and private retained entries in {op}; no automatic restoration or "
                       "cleanup was attempted. Quiesce editors and reconcile these files manually (update README)")


def _topology(path: Path, target: Path, parent: int, body: dict) -> bool:
    return (os.path.realpath(path) == str(target) and _identity(os.fstat(parent)) == body['parent']
            and _identity(target.parent.stat()) == body['parent'])


def _validate(body, target, op_id):
    if (body.get('format') != 1 or type(body.get('format')) is not int
            or body.get('target') != str(target) or body.get('id') != op_id
            or body.get('phase') not in ('preparing', 'armed', 'accepted', 'conflict', 'not_applied')
            or not isinstance(body.get('parent'), list) or len(body['parent']) != 2
            or not isinstance(body.get('expected'), dict)):
        raise CommitError("invalid or unknown private env operation record")
    for key in ('expected', 'candidate'):
        if key == 'candidate' and body['phase'] in ('preparing', 'not_applied'):
            continue
        item = body.get(key)
        if (not isinstance(item, dict) or set(item) != {'identity', 'mode', 'uid', 'gid', 'links', 'sha256'}
                or not isinstance(item['identity'], list) or len(item['identity']) != 2
                or any(type(n) is not int or n < 0 for n in item['identity'])
                or any(type(item[n]) is not int or item[n] < 0 for n in ('mode', 'uid', 'gid', 'links'))
                or not isinstance(item['sha256'], str) or not re.fullmatch('[0-9a-f]{64}', item['sha256'])):
            raise CommitError("incomplete private env operation record")


def _inspect(path, target, parent, opfd, body, *, durable, snapshot=None):
    # Qualification supplies a bounded reader; commit/recovery retain their
    # original snapshot and durability semantics.
    snapshot = _snapshot if snapshot is None else snapshot
    if durable:
        # Exchange may already have happened, even when an unexpected symlink
        # or special object prevents inspection. Preserve both namespace entries
        # before any classification can fail; never rely on syncing only the slot.
        sync_dir(opfd)
        sync_dir(parent)
    if body['phase'] == 'conflict':
        raise CommitError("previously recorded conflict requires manual reconciliation")
    if not _topology(path, target, parent, body):
        raise CommitError("env path or parent changed")
    live = snapshot(parent, target.name, sync=durable)
    if body['phase'] in ('preparing', 'not_applied'):
        # Exchange is prohibited until the complete armed manifest is durable.
        if live != body['expected']:
            raise CommitError("env changed during preparation")
        return 'untouched'
    candidate = snapshot(opfd, 'candidate', sync=durable)
    slot = snapshot(opfd, 'slot', sync=durable)
    if durable:
        # Order the captured regular file's data before BOTH directory barriers,
        # including on mismatch, not only on the accepted path.
        sync_dir(opfd)
        sync_dir(parent)
    if candidate != body['candidate']:
        raise CommitError("retained candidate changed")
    if slot == candidate and live == body['expected'] and body['phase'] == 'armed':
        return 'untouched'
    if slot != body['expected'] or live != candidate:
        raise CommitError("the entry displaced at commit or the live env changed")
    if durable:
        # The file full-sync after both namespace barriers orders macOS media flush.
        _snapshot(opfd, 'candidate', sync=True)
        body['phase'] = 'accepted'
        _json(opfd, 'manifest.json', body)
    return 'accepted'


def inspect(path: Path, target: Path, op_id: str, *, durable: bool = False, cancel: bool = False) -> str:
    """Read/reconcile one operation; absent is not evidence of an exchange."""
    op = directory(target, op_id)
    try:
        with _dir(target.parent) as parent:
            # Check every retention ancestor; no symlinked/private-path escape.
            for ancestor in (op.parent.parent.parent, op.parent.parent, op.parent):
                if not os.path.lexists(ancestor):
                    return 'absent'
                with _dir(ancestor, True):
                    pass
            if not os.path.lexists(op):
                return 'absent'
            with _dir(op, True) as opfd:
                body = _load(opfd)
                _validate(body, target, op_id)
                try:
                    result = _inspect(path, target, parent, opfd, body, durable=durable)
                    if result == 'untouched' and cancel and durable:
                        body['phase'] = 'not_applied'
                        _json(opfd, 'manifest.json', body)
                    return result
                except CommitError:
                    if durable:
                        body['phase'] = 'conflict'
                        _json(opfd, 'manifest.json', body)
                    raise
    except (OSError, ValueError, KeyError, TypeError) as exc:
        # Never interpolate external contents, digests or arbitrary exception text.
        reason = str(exc) if isinstance(exc, CommitError) else 'inspection failed, errno ' + str(getattr(exc, 'errno', None))
        raise _error(op, reason) from exc


def _retention(op: Path, parent: int, size: int) -> None:
    previous = parent
    opened = []
    try:
        for path in (op.parent.parent.parent, op.parent.parent, op.parent, op):
            try:
                path.mkdir(mode=0o700)
            except FileExistsError:
                if path == op:
                    raise CommitError("operation already exists; reconcile instead of replaying")
            fd = os.open(str(path), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            opened.append(fd)
            info = os.fstat(fd)
            if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
                raise CommitError("env retention is not private and owned by this user")
            _plain_metadata(fd)
            if path == op.parent.parent:  # Hide retention even in a nested/custom checkout path.
                try:
                    ignored = os.open('.gitignore', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                      0o600, dir_fd=fd)
                except FileExistsError:
                    ignored = os.open('.gitignore', os.O_RDONLY | os.O_NOFOLLOW, dir_fd=fd)
                    try:
                        if os.read(ignored, 3) != b'*\n':
                            raise CommitError('retention ignore policy changed; not overwriting it')
                    finally:
                        os.close(ignored)
                else:
                    try:
                        if os.write(ignored, b'*\n') != 2:
                            raise CommitError('incomplete retention ignore policy')
                        sync_file(ignored)
                    finally:
                        os.close(ignored)
            sync_dir(fd)
            sync_dir(previous)
            previous = fd
        if len(os.listdir(op.parent)) > _MAX_OPERATIONS:
            raise CommitError("env retention budget exhausted; archive retained operations manually, never prune blindly")
        space = os.fstatvfs(previous)
        if space.f_bavail * space.f_frsize < 2 * size + 1048576:
            raise CommitError("insufficient space for private env retention")
    finally:
        for fd in reversed(opened):
            os.close(fd)


def _known_links(target: Path, info) -> bool:
    if info.st_nlink == 1:
        return True
    if info.st_nlink != 2:
        return False
    base = directory(target, '0' * 32).parent
    if not base.exists():
        return False
    for entry in base.iterdir():
        if not _ID.fullmatch(entry.name):
            continue
        candidate = entry / 'candidate'
        try:
            if _identity(candidate.lstat()) == _identity(info):
                return True
        except FileNotFoundError:
            pass
    return False


def _qualification_stamp(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid,
            info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


@contextmanager
def _qualification_directory(parent, name, *, private=False):
    """Pin every ancestry edge, not just the final no-follow pathname."""
    fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
    try:
        info = os.fstat(fd)
        if private:
            if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
                raise CommitError('unsafe env retention directory')
            _plain_metadata(fd)
        elif (info.st_uid not in (0, os.geteuid())
              or info.st_mode & 0o022 and not (info.st_uid == 0 and info.st_mode & stat.S_ISVTX)):
            raise CommitError('unsafe env ancestry')
        yield fd
        # Ignore directory timestamps: unrelated entries may be created. Never
        # ignore replacement, ownership or permissions of the pinned directory.
        fields = ('st_dev', 'st_ino', 'st_mode', 'st_uid', 'st_gid')
        for after in (os.fstat(fd), os.stat(name, dir_fd=parent, follow_symlinks=False)):
            if any(getattr(info, key) != getattr(after, key) for key in fields):
                raise CommitError('env retention ancestry changed')
        if private:
            _plain_metadata(fd)
    finally:
        os.close(fd)


def _qualification_read(parent, name, maximum, *, manifest=False):
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                or info.st_mode & 0o7022 or not 0 <= info.st_size <= maximum
                or info.st_nlink not in (1, 2)
                or manifest and (stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1)):
            raise CommitError('unsafe retained env metadata')
        _plain_metadata(fd)
        chunks, count = [], 0
        while count <= maximum:
            chunk = os.read(fd, min(65536, maximum + 1 - count))
            if not chunk:
                break
            chunks.append(chunk)
            count += len(chunk)
        if (count != info.st_size or _qualification_stamp(os.fstat(fd)) != _qualification_stamp(info)
                or _qualification_stamp(os.stat(name, dir_fd=parent, follow_symlinks=False)) != _qualification_stamp(info)):
            raise CommitError('retained env changed during read')
        return b''.join(chunks), info
    finally:
        os.close(fd)


def _qualification_object(pairs):
    body = {}
    for key, value in pairs:
        if key in body:
            raise CommitError('duplicate env manifest field')
        body[key] = value
    return body


def qualify_retained_env(target: Path, handle: int, before, raw: bytes, *, maximum: int) -> None:
    """Read-only qualification of the selected .env's second link for backup.

    _known_links is only a commit preflight hint, NOT sufficient authorization
    for a backup. Require the private accepted operation, exact live candidate
    and displaced preimage through the same _validate/_inspect checks. Armed
    operations must be reconciled by the existing engine, never by this reader.
    Both forward and inverse commits use this same candidate form. Nothing is
    synced, rewritten, unlinked or repaired here. The owning user/private tree
    is the trust boundary, not an operation's filename or a two-link count.
    """
    try:
        if (not target.is_absolute() or '..' in target.parts or target.name != '.env'
                or before.st_nlink != 2 or len(raw) != before.st_size
                or _qualification_stamp(os.fstat(handle)) != _qualification_stamp(before)):
            raise CommitError('unqualified live env')
        with ExitStack() as stack:
            parent = os.open('/', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            stack.callback(os.close, parent)
            for part in target.parent.parts[1:]:
                parent = stack.enter_context(_qualification_directory(parent, part))
            _filesystem(parent)
            base = parent
            for part in ('.futureagi-updates', 'env-commits', directory(target, '0' * 32).parent.name):
                base = stack.enter_context(_qualification_directory(base, part, private=True))
            names = []
            with os.scandir(base) as entries:
                for entry in entries:
                    names.append(entry.name)
                    if len(names) > _MAX_OPERATIONS:
                        raise CommitError('env retention budget exhausted')
            for name in sorted(names):
                if not _ID.fullmatch(name):
                    continue
                with _qualification_directory(base, name, private=True) as opfd:
                    try:
                        anchor = os.stat('candidate', dir_fd=opfd, follow_symlinks=False)
                    except FileNotFoundError:
                        continue
                    if _identity(anchor) != _identity(before):
                        continue
                    metadata, manifest_info = _qualification_read(opfd, 'manifest.json', 65536, manifest=True)
                    body = json.loads(metadata, object_pairs_hook=_qualification_object)
                    _validate(body, target, name)
                    if (set(body) != {'format', 'id', 'target', 'parent', 'expected', 'candidate', 'phase'}
                            or any(type(value) is not int or value < 0 for value in body['parent'])
                            or body['phase'] != 'accepted' or body['candidate']['links'] != 2
                            or body['expected']['links'] not in (1, 2)
                            or body['expected']['identity'] == body['candidate']['identity']
                            or any(body['expected'][key] != body['candidate'][key] for key in ('uid', 'gid', 'mode'))):
                        raise CommitError('unaccepted env operation')
                    reads = [(opfd, 'manifest.json', manifest_info)]

                    def snapshot(fd, leaf, *, sync=False):
                        data, info = _qualification_read(fd, leaf, maximum)
                        if fd == parent and (data != raw or _qualification_stamp(info) != _qualification_stamp(before)):
                            raise CommitError('live env no longer matches the read descriptor')
                        reads.append((fd, leaf, info))
                        return dict(identity=_identity(info), mode=stat.S_IMODE(info.st_mode),
                                    uid=info.st_uid, gid=info.st_gid, links=info.st_nlink,
                                    sha256=hashlib.sha256(data).hexdigest())

                    if _inspect(target, target, parent, opfd, body, durable=False, snapshot=snapshot) != 'accepted':
                        raise CommitError('env postimage is not accepted')
                    for fd, leaf, info in reads:
                        if _qualification_stamp(os.stat(leaf, dir_fd=fd, follow_symlinks=False)) != _qualification_stamp(info):
                            raise CommitError('retained env evidence changed')
                    if _qualification_stamp(os.fstat(handle)) != _qualification_stamp(before):
                        raise CommitError('live env descriptor changed')
                    return
            raise CommitError('no qualified env candidate anchor')
    except (OSError, ValueError, KeyError, TypeError, AttributeError, RecursionError):
        # Do not leak private paths, manifest contents, hashes or env bytes.
        raise CommitError('unqualified retained env link') from None


def _check_prepared(body: dict, original_sha256: str, candidate_sha256: str) -> None:
    # Shared by the real replay and its read-only preview. 'untouched' alone
    # includes partial preparation, which is NOT permission to replay an inverse.
    if (body['phase'] != 'armed' or body['expected']['sha256'] != original_sha256
            or body['candidate']['sha256'] != candidate_sha256):
        raise CommitError("incomplete env preparation needs manual recovery")


def check_resume(path: Path, target: Path, op_id: str, original_sha256: str, candidate_sha256: str) -> None:
    """Validate an existing inverse without exchange, sync, or metadata writes."""
    op = directory(target, op_id)
    try:
        if inspect(path, target, op_id) == 'accepted':
            return
        with _dir(target.parent) as parent, _dir(op, True) as opfd:
            _filesystem(parent)
            body = _load(opfd)
            _validate(body, target, op_id)
            _check_prepared(body, original_sha256, candidate_sha256)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        reason = str(exc) if isinstance(exc, CommitError) else 'inspection failed, errno ' + str(getattr(exc, 'errno', None))
        raise _error(op, reason) from exc


def commit(path: Path, target: Path, original: bytes, data: bytes, mode, op_id: str) -> None:
    op = directory(target, op_id)
    try:
        if os.path.lexists(op):
            outcome = inspect(path, target, op_id, durable=True)
            if outcome == 'accepted':
                return
            with _dir(target.parent) as parent, _dir(op, True) as opfd:
                _filesystem(parent)
                body = _load(opfd)
                _validate(body, target, op_id)
                _check_prepared(body, hashlib.sha256(original).hexdigest(), hashlib.sha256(data).hexdigest())
                # inspect proved slot == candidate and live == expected. Only
                # that unexchanged state may finish an interrupted inverse.
                exchange(opfd, 'slot', parent, target.name)
                try:
                    _inspect(path, target, parent, opfd, body, durable=True)
                except CommitError:
                    body['phase'] = 'conflict'
                    _json(opfd, 'manifest.json', body)
                    raise
                return
        with _dir(target.parent) as parent:
            _filesystem(parent)
            expected = _snapshot(parent, target.name)
            info = os.stat(target.name, dir_fd=parent, follow_symlinks=False)
            if (expected['sha256'] != hashlib.sha256(original).hexdigest()
                    or mode is not None and expected['mode'] != mode
                    or expected['uid'] != os.geteuid() or expected['mode'] & 0o7000
                    or not _known_links(target, info)):
                raise CommitError("env bytes, mode, ownership or hard links changed/unsupported")
            body = {'format': 1, 'id': op_id, 'target': str(target), 'parent': _identity(os.fstat(parent)),
                    'expected': expected, 'phase': 'preparing'}
            if not _topology(path, target, parent, body):
                raise CommitError("env target changed")
            _retention(op, parent, len(data))
            with _dir(op, True) as opfd:
                _json(opfd, 'manifest.json', body)
                handle = os.open('candidate', os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                 0o600, dir_fd=opfd)
                with _stream(handle, 'w+b') as stream:
                    stream.write(data)
                    stream.flush()
                    os.fchown(stream.fileno(), expected['uid'], expected['gid'])
                    os.fchmod(stream.fileno(), expected['mode'])
                    _plain_metadata(stream.fileno())
                    sync_file(stream.fileno())
                os.link('candidate', 'slot', src_dir_fd=opfd, dst_dir_fd=opfd, follow_symlinks=False)
                sync_dir(opfd)
                # Test native support with distinct, non-secret entries first.
                for name in ('probe-a', 'probe-b'):
                    fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=opfd)
                    os.close(fd)
                exchange(opfd, 'probe-a', opfd, 'probe-b')
                os.unlink('probe-a', dir_fd=opfd)
                os.unlink('probe-b', dir_fd=opfd)
                body['candidate'] = _snapshot(opfd, 'candidate')
                body['phase'] = 'armed'
                _json(opfd, 'manifest.json', body)
                # No reads of the target are a writer fence. The entry ACTUALLY
                # displaced by the syscall below is what _inspect must compare.
                exchange(opfd, 'slot', parent, target.name)
                try:
                    _inspect(path, target, parent, opfd, body, durable=True)
                except CommitError:
                    body['phase'] = 'conflict'
                    _json(opfd, 'manifest.json', body)
                    raise
    except (OSError, ValueError, KeyError, TypeError) as exc:
        reason = str(exc) if isinstance(exc, CommitError) else 'commit failed, errno ' + str(getattr(exc, 'errno', None))
        raise _error(op, reason) from exc
