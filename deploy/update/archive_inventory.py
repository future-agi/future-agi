"""Bounded tar-as-data verification and complete restorable-tree inventory v2.

No extractall, no release code, no archive paths used as host paths. Payloads
stream; metadata is bounded separately. Only allocation, ctime and atime are
ignored. The normalized inventory is the same fixed helper's extracted tree.
"""
from __future__ import annotations

import base64
from decimal import Decimal, InvalidOperation
import hashlib
import os
import posixpath
import tarfile
import time

from .manifest import canonical_json
# Complete v2 inventories are compared as bytes with the independent helper.
# Share that UTF-8 serializer; retain the legacy/manual inventory wire encoding.
from .probes.restore_fs import acl_text, acl_binary, canonical as canonical_tree


def _need(value, code='unsafe_archive_member'):
    from .standalone_backup import BackupError
    if not value:
        raise BackupError(code)


def normalized_path(value):
    _need(isinstance(value, str) and value and '\0' not in value and len(value) <= 4096
          and not value.startswith('/') and '..' not in value.split('/'))
    name = posixpath.normpath(value)
    _need(name != '..' and not name.startswith('../'))
    return name


def _mtime(member):
    try:
        ns = Decimal(str(member.pax_headers.get('mtime', member.mtime))) * 1000000000
        _need(ns.is_finite() and ns == ns.to_integral_value() and abs(ns) < 2 ** 63,
              'unsupported_restore_metadata')
        return int(ns)
    except (ValueError, InvalidOperation):
        _need(False, 'unsupported_restore_metadata')


def _metadata(member):
    attrs, acls = {}, {}
    for key, value in member.pax_headers.items():
        if key in ('path', 'linkpath', 'size', 'uid', 'gid', 'uname', 'gname', 'mtime', 'atime', 'ctime'):
            continue
        if key == 'hdrcharset':
            _need(value == 'BINARY', 'unsupported_restore_metadata')
        elif key.startswith('GNU.sparse.'):
            _need(key in ('GNU.sparse.major', 'GNU.sparse.minor', 'GNU.sparse.name',
                          'GNU.sparse.realsize', 'GNU.sparse.size', 'GNU.sparse.numblocks',
                          'GNU.sparse.offset', 'GNU.sparse.numbytes', 'GNU.sparse.map'), 'unsupported_archive_format')
        elif key.startswith('SCHILY.xattr.user.'):
            attrs[key[len('SCHILY.xattr.'):]] = base64.b64encode(value.encode('utf-8', 'surrogateescape')).decode('ascii')
        elif key in ('SCHILY.acl.access', 'SCHILY.acl.default',
                     'SCHILY.xattr.system.posix_acl_access', 'SCHILY.xattr.system.posix_acl_default'):
            try:
                kind = 'access' if key.endswith('access') else 'default'
                decoded = (acl_text(value) if key.startswith('SCHILY.acl.') else
                           acl_binary(value.encode('utf-8', 'surrogateescape')))
                _need(kind not in acls or acls[kind] == decoded, 'unsupported_restore_metadata')
                acls[kind] = decoded
            except (RuntimeError, ValueError):
                _need(False, 'unsupported_restore_metadata')
        else:
            _need(False, 'unsupported_restore_metadata')
    return attrs, acls


def verify_archive(fd, inventory_fd, policy, *, deadline, volume_root=None, complete_tree=False,
                   allow_legacy_absolute_links=False):
    from .standalone_backup import (_BoundedTarInfo, _private, _file_identity, _write_all, sync_file)
    _private(fd)
    _private(inventory_fd)
    _need(os.fstat(inventory_fd).st_size == 0, 'archive_inventory_not_empty')
    before = os.fstat(fd)
    serialize = canonical_tree if complete_tree else canonical_json
    _need(1024 <= before.st_size <= policy.max_archive_bytes and before.st_size % 512 == 0, 'archive_incomplete')
    os.lseek(fd, 0, os.SEEK_SET)
    rows, hardlinks, metadata_bytes, logical = {}, {}, 0, 0
    with os.fdopen(os.dup(fd), 'rb') as stream, tarfile.open(fileobj=stream, mode='r|', tarinfo=_BoundedTarInfo) as archive:
        while True:
            _need(time.monotonic() < deadline, 'backup_timeout')
            archive._backup_metadata_bytes = 0
            member = archive.next()
            if member is None:
                end = archive.offset
                break
            archive.members.clear()
            name = normalized_path(member.name)
            _need(name not in rows, 'archive_duplicate_member')
            _need(len(rows) < policy.max_members, 'archive_inventory_unbounded')
            _need(member.isfile() or member.isdir() or member.issym() or member.islnk())
            _need(type(member.uid) is int and type(member.gid) is int and 0 <= member.uid < 2 ** 32
                  and 0 <= member.gid < 2 ** 32 and 0 <= member.mode <= 0o7777, 'unsupported_restore_metadata')
            attrs, acls = _metadata(member)
            typ = 'directory' if member.isdir() else 'symlink' if member.issym() else 'file'
            row = dict(name=name, type=typ, size=member.size if member.isfile() else 0,
                       uid=member.uid, gid=member.gid, mode=member.mode, mtime_ns=_mtime(member),
                       link=member.linkname if member.issym() else None, sha256=None, hardlinks=[],
                       xattrs=attrs, acls=acls)
            if member.islnk():
                hardlinks[name] = normalized_path(member.linkname)
            if member.issym():
                _need(member.linkname and '\0' not in member.linkname and len(member.linkname) <= 4096)
                target = posixpath.normpath(member.linkname if member.linkname.startswith('/') else
                                            posixpath.join(posixpath.dirname(name), member.linkname))
                if member.linkname.startswith('/'):
                    allowed_root = volume_root == '/var/lib/clickhouse' or (allow_legacy_absolute_links
                        and volume_root in ('/data', '/var/lib/postgresql/data'))
                    _need(allowed_root and (target == volume_root or target.startswith(volume_root + '/')))
                else:
                    _need(target != '..' and not target.startswith('../'))
            if member.isfile():
                _need(member.size >= 0, 'archive_incomplete')
                logical += member.size
                _need(logical <= policy.max_archive_bytes, 'archive_logical_size_unbounded')
                if member.sparse is not None:
                    last = 0
                    _need(len(member.sparse) <= 65536, 'archive_metadata_unbounded')
                    for offset, size in member.sparse:
                        _need(offset >= last and size >= 0 and offset + size <= member.size, 'unsupported_archive_format')
                        last = offset + size
                digest, size = hashlib.sha256(), 0
                payload = archive.extractfile(member)
                while True:
                    _need(time.monotonic() < deadline, 'backup_timeout')
                    chunk = payload.read(65536)
                    if not chunk:
                        break
                    size += len(chunk)
                    digest.update(chunk)
                _need(size == member.size, 'archive_incomplete')
                row['sha256'] = digest.hexdigest()
            metadata_bytes += len(serialize(row)) + len(name) + 1
            _need(metadata_bytes <= policy.max_inventory_bytes, 'archive_inventory_unbounded')
            rows[name] = row
    _need(rows and before.st_size - end >= 1024, 'archive_incomplete')
    if complete_tree:
        _need('.' in rows and rows['.']['type'] == 'directory', 'archive_inventory_incomplete')
    # Validate AFTER collecting: a later symlink cannot be an earlier path's
    # ancestor; hardlink forward references are refused (GNU tar cannot restore
    # them reliably). No link traversal is ever delegated to tar.
    order = {name: index for index, name in enumerate(rows)}
    groups = {}
    for name, row in rows.items():
        parent = posixpath.dirname(name)
        while parent:
            _need(parent not in rows or rows[parent]['type'] == 'directory')
            if complete_tree:
                _need(parent in rows, 'archive_inventory_incomplete')
            parent = posixpath.dirname(parent)
        if name in hardlinks:
            target = hardlinks[name]
            _need(target in rows and rows[target]['type'] == 'file' and order[target] < order[name])
            visited = {name}
            while target in hardlinks:
                _need(target not in visited)
                visited.add(target)
                target = hardlinks[target]
            _need(target in rows and rows[target]['sha256'] is not None)
            original = rows[target]
            _need(all(row[k] == original[k] for k in ('uid', 'gid', 'mode', 'mtime_ns')),
                  'unsupported_restore_metadata')
            # GNU tar emits inode xattrs/ACLs only on the payload-bearing member;
            # an absent hardlink header does not remove the inode's metadata.
            for metadata_key in ('xattrs', 'acls'):
                _need(not row[metadata_key] or row[metadata_key] == original[metadata_key], 'unsupported_restore_metadata')
                row[metadata_key] = original[metadata_key]
            row['size'], row['sha256'] = original['size'], original['sha256']
            groups.setdefault(target, {target}).add(name)
    for names in groups.values():
        for name in names:
            rows[name]['hardlinks'] = sorted(names)
    size, digest = 0, hashlib.sha256()
    for name in sorted(rows):
        row = serialize(rows[name]) + b'\n'
        size += len(row)
        _need(size <= policy.max_inventory_bytes, 'archive_inventory_unbounded')
        space = os.fstatvfs(inventory_fd)
        _need(space.f_bavail * space.f_frsize >= policy.reserve_bytes + len(row), 'backup_destination_full')
        _write_all(inventory_fd, row)
        digest.update(row)
    os.lseek(fd, end, os.SEEK_SET)
    while True:
        chunk = os.read(fd, 65536)
        if not chunk:
            break
        _need(not any(chunk) and time.monotonic() < deadline, 'archive_incomplete')
    os.lseek(fd, 0, os.SEEK_SET)
    archive_digest = hashlib.sha256()
    while True:
        _need(time.monotonic() < deadline, 'backup_timeout')
        chunk = os.read(fd, 65536)
        if not chunk:
            break
        archive_digest.update(chunk)
    _need(_file_identity(os.fstat(fd)) == _file_identity(before), 'archive_changed')
    sync_file(inventory_fd)
    return dict(size=before.st_size, sha256=archive_digest.hexdigest(), members=len(rows),
                logical_bytes=logical, members_sha256=digest.hexdigest(), members_bytes=size,
                inventory_version=2)
