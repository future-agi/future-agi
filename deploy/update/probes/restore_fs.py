"""Fixed, isolated Linux filesystem probe/clear helper. Never imports volume code.

Executed with -I -B -S and entrypoint bypass. All mutations are confined to a
single already-qualified mount. Archive extraction is GNU tar, not this helper.
Metadata normalization is also imported by the host's archive verifier.
"""
import base64
import ctypes
import hashlib
import json
import os
import signal
import stat
import struct
import sys


class FilesystemError(RuntimeError):
    pass


def need(value):
    if not value:
        raise FilesystemError('restore_verification_failed')


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False,
                      allow_nan=False).encode('utf-8')


def acl_text(value):
    result = []
    for entry in value.strip().replace('\n', ',').split(','):
        fields = entry.strip().split(':')
        need(len(fields) == 3 and fields[0] in ('user', 'group', 'mask', 'other'))
        kind, identity, permissions = fields
        need(not identity or identity.isdigit())
        need(len(permissions) == 3 and all(c in (p, '-') for c, p in zip(permissions, 'rwx')))
        result.append([kind, int(identity) if identity else None,
                       sum(bit for c, bit in zip(permissions, (4, 2, 1)) if c != '-')])
    return sorted(result, key=lambda r: (r[0], -1 if r[1] is None else r[1]))


def acl_binary(raw):
    need(len(raw) >= 4 and (len(raw) - 4) % 8 == 0 and struct.unpack('<I', raw[:4])[0] == 2)
    tags = {1: 'user', 2: 'user', 4: 'group', 8: 'group', 16: 'mask', 32: 'other'}
    result = []
    for offset in range(4, len(raw), 8):
        tag, permission, identity = struct.unpack('<HHI', raw[offset:offset + 8])
        need(tag in tags and permission <= 7)
        result.append([tags[tag], identity if tag in (2, 8) else None, permission])
    return sorted(result, key=lambda r: (r[0], -1 if r[1] is None else r[1]))


def metadata(path=None, fd=None):
    target = fd if fd is not None else path
    options = {} if fd is not None else {'follow_symlinks': False}
    attrs, acls = {}, {}
    for key in os.listxattr(target, **options):
        raw = os.getxattr(target, key, **options)
        if key.startswith('user.'):
            attrs[key] = base64.b64encode(raw).decode('ascii')
        elif key in ('system.posix_acl_access', 'system.posix_acl_default'):
            acls['access' if key.endswith('access') else 'default'] = acl_binary(raw)
        else:
            raise FilesystemError('unsupported_restore_metadata')
    return attrs, acls


def identity(info):
    return '%d:%d' % (info.st_dev, info.st_ino)


def stable(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mode, info.st_uid,
            info.st_gid, info.st_mtime_ns, info.st_ctime_ns, info.st_nlink)


def scan(root, *, max_members=1000000, max_bytes=274877906944):
    root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    rows, groups, hashed, total = {}, {}, {}, 0
    device = os.fstat(root_fd).st_dev

    def visit(parent, leaf, name):
        nonlocal total
        before = os.fstat(parent) if leaf is None else os.stat(leaf, dir_fd=parent, follow_symlinks=False)
        need(before.st_dev == device and len(rows) < max_members)
        mode = before.st_mode
        need(stat.S_ISDIR(mode) or stat.S_ISREG(mode) or stat.S_ISLNK(mode))
        typ = 'directory' if stat.S_ISDIR(mode) else 'symlink' if stat.S_ISLNK(mode) else 'file'
        row = dict(name=name, type=typ, size=before.st_size if typ == 'file' else 0,
                   uid=before.st_uid, gid=before.st_gid, mode=stat.S_IMODE(mode),
                   mtime_ns=before.st_mtime_ns, link=None, sha256=None, hardlinks=[], xattrs={}, acls={})
        fd = None
        try:
            if typ == 'symlink':
                row['link'] = os.readlink(leaf, dir_fd=parent)
                row['xattrs'], row['acls'] = metadata('/proc/self/fd/%d/%s' % (parent, leaf))
            else:
                fd = os.dup(parent) if leaf is None else os.open(leaf,
                    os.O_RDONLY | os.O_NOFOLLOW | (os.O_DIRECTORY if typ == 'directory' else 0), dir_fd=parent)
                need(stable(os.fstat(fd)) == stable(before))
                row['xattrs'], row['acls'] = metadata(fd=fd)
                if typ == 'file':
                    inode = (before.st_dev, before.st_ino)
                    if inode in hashed:
                        # Another link to an unchanged inode: one payload read per
                        # hardlink group, as the signed logical-bytes basis counts it.
                        identity, row['sha256'] = hashed[inode]
                        need(identity == stable(before))
                    else:
                        total += before.st_size
                        need(total <= max_bytes)
                        digest = hashlib.sha256()
                        size = 0
                        while True:
                            chunk = os.read(fd, 65536)
                            if not chunk:
                                break
                            digest.update(chunk)
                            size += len(chunk)
                        need(size == before.st_size)
                        row['sha256'] = digest.hexdigest()
                        hashed[inode] = (stable(before), row['sha256'])
                    groups.setdefault(inode, []).append(name)
                else:
                    for child in sorted(os.listdir(fd)):
                        need(child not in ('.', '..') and '/' not in child)
                        visit(fd, child, child if name == '.' else name + '/' + child)
                need(stable(os.fstat(fd)) == stable(before))
            after = os.fstat(parent) if leaf is None else os.stat(leaf, dir_fd=parent, follow_symlinks=False)
            need(stable(after) == stable(before))
            rows[name] = row
        finally:
            if fd is not None:
                os.close(fd)

    try:
        visit(root_fd, None, '.')
        for names in groups.values():
            if len(names) > 1:
                for name in names:
                    rows[name]['hardlinks'] = sorted(names)
        return [rows[name] for name in sorted(rows)]
    finally:
        os.close(root_fd)


def synchronize(root):
    for parent, directories, files, fd in os.fwalk(root, follow_symlinks=False):
        for name in files:
            if stat.S_ISREG(os.stat(name, dir_fd=fd, follow_symlinks=False).st_mode):
                child = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=fd)
                try:
                    os.fsync(child)
                finally:
                    os.close(child)
        os.fsync(fd)
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        need(libc.syncfs(fd) == 0)
    finally:
        os.close(fd)


def clear(root, expected):
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    initial = os.fstat(fd)
    need(identity(initial) == expected)

    def remove(parent):
        for name in os.listdir(parent):
            info = os.stat(name, dir_fd=parent, follow_symlinks=False)
            need(info.st_dev == initial.st_dev)
            if stat.S_ISDIR(info.st_mode):
                child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
                try:
                    need(identity(os.fstat(child)) == identity(info))
                    remove(child)
                finally:
                    os.close(child)
                os.rmdir(name, dir_fd=parent)
            else:
                # Unlink symlinks, never follow them, including absolute CH links.
                os.unlink(name, dir_fd=parent)
        os.fsync(parent)
    try:
        remove(fd)
        need(not os.listdir(fd) and identity(os.fstat(fd)) == expected)
    finally:
        os.close(fd)
    synchronize(root)


def main():
    seconds = int(sys.argv[-1])
    need(1 <= seconds <= 86400)
    signal.alarm(seconds)  # daemon-side bound survives loss of the Docker client
    operation, root = sys.argv[1:3]
    need(root in ('/data', '/var/lib/postgresql/data', '/var/lib/clickhouse'))
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        info, capacity = os.fstat(fd), os.fstatvfs(fd)
    finally:
        os.close(fd)
    if operation == 'inspect':
        print(canonical(dict(root_identity=identity(info), filesystem=str(info.st_dev),
            free_bytes=capacity.f_bavail * capacity.f_frsize, free_inodes=capacity.f_favail)).decode())
    elif operation == 'empty':
        need(identity(info) == sys.argv[3] and not os.listdir(root))
        print('empty')
    elif operation == 'clear':
        clear(root, sys.argv[3])
        print('empty')
    elif operation == 'sync':
        need(identity(info) == sys.argv[3])
        synchronize(root)
        print('synchronized')
    elif operation == 'inventory':
        need(identity(info) == sys.argv[3])
        for row in scan(root, max_members=int(sys.argv[4]), max_bytes=int(sys.argv[5])):
            sys.stdout.buffer.write(canonical(row) + b'\n')
    else:
        raise FilesystemError('unsupported_restore_operation')


if __name__ == '__main__':
    try:
        main()
    except Exception:
        print('restore_verification_failed', file=sys.stderr)
        raise SystemExit(1)
