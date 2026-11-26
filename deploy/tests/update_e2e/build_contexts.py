"""Minimal FROM scratch context probes, not production image builds/publication.

Exercise the repository's effective ignore policies with Docker itself and
inspect every saved image layer. A unique test tag is loaded, saved and removed;
no daemon settings or builder defaults change. Synthetic retained env files are created on the test host
(APFS/ext4, never an overlay/virtiofs commit target).
"""
from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import uuid


def export_probe_image(buildx, builder, dockerfile, context, archive, log):
    """Portable across classic Docker and containerd stores, without a new builder.

    The docker driver's tar exporter is not universal. Loading a uniquely owned
    tag and saving that exact image works with both stores. Never remove an ID
    (which might have other tags), use force, or prune the daemon/builder cache.
    """
    tag = 'fagi-upd-e2e-context-' + uuid.uuid4().hex + ':probe'
    command = [*buildx, 'build', *builder, '--network=none', '--no-cache', '--provenance=false',
               '--progress=plain', '--file', str(dockerfile), '--load', '--tag', tag,
               '--label', 'com.futureagi.update-e2e.context=' + tag, str(context)]
    save = ['docker', 'save', '--output', str(archive), tag]
    cleanup = ['docker', 'image', 'rm', tag]
    log.write_text(json.dumps({'build': command, 'save': save, 'cleanup': cleanup}) + '\n')
    built = None
    def run(words, timeout):
        result = subprocess.run(words, capture_output=True, text=True, timeout=timeout)
        with log.open('a') as handle:
            handle.write(f'\n{words!r}\nexit {result.returncode}\n{result.stdout}{result.stderr}\n')
        return result
    try:
        built = run(command, 120)
        if built.returncode:
            raise AssertionError(built.stdout + built.stderr)
        saved = run(save, 120)
        if saved.returncode:
            raise AssertionError(saved.stdout + saved.stderr)
        if not archive.is_file() or not archive.stat().st_size:
            raise AssertionError('docker save produced no image archive')
        return {'command': command, 'save_command': save, 'cleanup_command': cleanup,
                'image_tag': tag, 'exit': built.returncode}
    finally:
        removed = run(cleanup, 30)
        # An unsuccessful build might never create its tag. After a successful
        # load, cleanup failure is itself a failed test, never a leaked success.
        if built is not None and built.returncode == 0 and removed.returncode:
            raise AssertionError('test image cleanup failed: ' + removed.stdout + removed.stderr)


def assert_private_contexts_excluded(case, source: Path, scratch: Path):
    sys.path.insert(0, str(source))
    from deploy.update import envfile
    scratch.mkdir(parents=True, exist_ok=True)
    rows = []
    # Prefer the plugin; some developer hosts install the standalone binary.
    plugin = subprocess.run(['docker', 'buildx', 'version'], capture_output=True, timeout=30)
    buildx = ['docker', 'buildx'] if plugin.returncode == 0 else [shutil.which('docker-buildx') or 'docker-buildx']
    builder = ['--builder', os.environ['DOCKER_CONTEXT']] if os.environ.get('DOCKER_CONTEXT') else []
    with tempfile.TemporaryDirectory(prefix='private-context-', dir=str(scratch)) as temporary:
        base = Path(temporary)
        for symlink in (False, True):
            root = base / ('symlink' if symlink else 'custom')
            backend = root / 'futureagi'
            backend.mkdir(parents=True)
            target = backend / '.env'  # custom env below checkout, shared by root/backend build contexts
            original = b'FUTURE_AGI_VERSION=v1.0.0\nSECRET=synthetic-retained-env-6a490b\n'
            target.write_bytes(original)
            target.chmod(0o600)
            alias = root / '.env' if symlink else target
            if symlink:
                alias.symlink_to('futureagi/.env')
            recovery = envfile.recovery_record(alias, {'FUTURE_AGI_VERSION': 'v1.1.0'})
            envfile.write_changes(alias, {'FUTURE_AGI_VERSION': 'v1.1.0'}, recovery=recovery)
            updated = target.read_bytes()
            envfile.restore_changes(alias, recovery)
            case.assertEqual(target.read_bytes(), original)
            if symlink:
                case.assertTrue(alias.is_symlink())
            retained = [p.read_bytes() for p in backend.glob('.futureagi-updates/env-commits/*/*/*')
                        if p.name in ('candidate', 'slot')]
            case.assertIn(original, retained)
            case.assertIn(updated, retained)
            # A real existing backend negation re-includes agentic_eval/core/**.
            # Also cover state at greater depth and at the context root.
            for parent in (root, backend / 'agentic_eval/core/deep'):
                private = parent / '.futureagi-updates/env-commits/test-operation'
                private.mkdir(parents=True, mode=0o700)
                (private / 'candidate').write_bytes(updated)
                (private / 'slot').write_bytes(original)
            public = backend / 'agentic_eval/core/public_source.py'
            public.parent.mkdir(parents=True, exist_ok=True)
            public.write_bytes(b'# public-source-control-9fed71\n')
            for relative in ('.dockerignore', 'futureagi/.dockerignore'):
                shutil.copy2(source / relative, root / relative)
            for name, context, relative, copy in (
                    ('backend', backend, 'futureagi/Dockerfile.oss', 'COPY . /app/'),
                    ('root', root, 'Dockerfile', 'COPY futureagi/ /app/')):
                dockerfile = root / relative
                dockerfile.write_text('FROM scratch\nLABEL test.purpose="updater-secret-exclusion"\n' + copy + '\n')
                # Dockerfile-specific rules take precedence, including a newly
                # added policy; don't silently keep testing the context fallback.
                specific = source / (relative + '.dockerignore')
                if specific.exists():
                    shutil.copy2(specific, root / (relative + '.dockerignore'))
                label = ('symlink-' if symlink else 'custom-') + name
                archive = base / (label + '.tar')
                exported = export_probe_image(buildx, builder, dockerfile, context, archive,
                                              scratch / (label + '.build.log'))
                hits, private_names, controls, layer_files = [], [], [], []
                with tarfile.open(archive) as image:
                    manifest = json.load(image.extractfile('manifest.json'))
                    for layer in manifest[0]['Layers']:
                        with tarfile.open(fileobj=io.BytesIO(image.extractfile(layer).read())) as contents:
                            for member in contents:
                                layer_files.append(member.name)
                                if '.futureagi-updates' in Path(member.name).parts:
                                    private_names.append(member.name)
                                if member.isfile():
                                    data = contents.extractfile(member).read()
                                    if b'synthetic-retained-env-6a490b' in data:
                                        hits.append(member.name)
                                    if b'public-source-control-9fed71' in data:
                                        controls.append(member.name)
                row = {'layout': label, **exported,
                       'archive_sha256': hashlib.sha256(archive.read_bytes()).hexdigest(),
                       'retained_before_and_after': True, 'secret_hits': hits, 'private_paths': private_names,
                       'public_controls': controls, 'layer_paths': layer_files}
                rows.append(row)
                # Record each build even on a failing pre-fix run. Continue all
                # four subtests so both actual build contexts are demonstrated.
                (scratch / 'context-results.json').write_text(json.dumps(rows, indent=2) + '\n')
                with case.subTest(layout=label):
                    case.assertTrue(controls, 'COPY must still include public application source')
                    case.assertFalse(hits, 'synthetic retained env secret reached an image layer')
                    case.assertFalse(private_names, 'private updater state reached an image layer')
    # Each loaded tag was removed after save, even on failure. Temporary contexts
    # and archives are removed here; logs contain only synthetic test evidence.
