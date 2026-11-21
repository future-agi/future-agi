"""Read build inputs as data at one local Git commit. Never source a tagged script."""

import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def git(*args):
    return subprocess.check_output(['git', '-C', ROOT, *args], text=True).rstrip('\n')


def one(pattern, text):
    matches = re.findall(pattern, text, re.MULTILINE)
    if len(matches) != 1:
        raise ValueError(f'expected exactly one match for {pattern}')
    return matches[0]


def release_variant(workflow, suffix, default):
    import yaml  # Only automatic release lookup requires PyYAML on the host.

    class UniqueLoader(yaml.SafeLoader):
        def construct_mapping(self, node, deep=False):
            mapping = super().construct_mapping(node, deep)
            if len(mapping) != len(node.value):
                raise ValueError('duplicate workflow key')
            return mapping

    try:
        jobs = yaml.load(workflow, Loader=UniqueLoader)['jobs']
    except yaml.YAMLError as exc:
        raise ValueError('unrecognized release workflow') from exc
    job = jobs['backend' + suffix]
    inputs = job['with']
    if (job['uses'] != './.github/workflows/build-image-multiarch.yml'
            or inputs['image'] != 'futureagi/future-agi'
            or inputs['context'] != './futureagi'
            or inputs['dockerfile'] != './futureagi/Dockerfile.oss'
            or inputs['ref'] != '${{ github.ref }}'
            or inputs['version'] != '${{ needs.guard.outputs.version }}'
            or inputs.get('tag-suffix', '') != suffix):
        raise ValueError('unrecognized backend build')
    args = inputs.get('build-args', '').strip()
    if not args:
        return default
    match = re.fullmatch(r'IMAGE_VARIANT=(standard|slim)', args)
    if not match:
        raise ValueError('unrecognized release build arguments')
    return match[1]


def contract(version, image, variant, flavor):
    explicit = bool(variant or flavor)
    if explicit:
        if variant not in ('standard', 'slim') or flavor not in ('minimal', 'debian'):
            raise ValueError('supply both OSS_VARIANT and EXPECTED_FFMPEG_FLAVOR')
        ref = os.getenv('OSS_BUILD_REVISION', 'HEAD')
        if ref != 'HEAD' and not re.fullmatch('[0-9a-f]{40}', ref):
            raise ValueError('OSS_BUILD_REVISION must be a full build commit')
    else:
        if not re.fullmatch(r'v\d+\.\d+\.\d+(?:-[a-zA-Z0-9.-]+)?', version):
            raise ValueError('expected a local release tag')
        base = 'futureagi/future-agi:' + version
        if image not in (base, base + '-slim') or 'OSS_BUILD_REVISION' in os.environ:
            raise ValueError('supply explicit expectations for custom images')
        ref = 'refs/tags/' + version
    # A shallow checkout may lack this tag. Fetch only that tag into a disposable
    # object store, never into the checkout's refs, and never consult latest.
    if not explicit and subprocess.run(
        ['git', '-C', ROOT, 'show-ref', '--verify', '--quiet', ref], check=False,
    ).returncode == 1:
        with tempfile.TemporaryDirectory() as tmp:
            url = git('remote', 'get-url', 'origin')
            subprocess.run(['git', 'init', '--bare', '--quiet', tmp], check=True)
            fetch = subprocess.run(
                ['git', '-C', tmp, 'fetch', '--quiet', '--no-tags', '--depth=1', '--', url, ref],
                stderr=subprocess.PIPE, check=False,
            )
            if fetch.returncode:
                raise ValueError(f'cannot fetch {ref} from origin')
            return from_source(tmp, 'FETCH_HEAD', explicit, image[len(base):], variant, flavor)
    return from_source(ROOT, ref, explicit, '' if explicit else image[len(base):], variant, flavor)


def from_source(repo, ref, explicit, suffix, variant, flavor):
    revision = git('-C', repo, 'rev-parse', '--verify', ref + '^{commit}')

    def source(path):
        return git('-C', repo, 'cat-file', 'blob', revision + ':' + path)

    docker = source('futureagi/Dockerfile.oss')
    if not explicit:
        default = one(r'^ARG IMAGE_VARIANT=(standard|slim)$', docker)
        variant = release_variant(source('.github/workflows/release-images.yml'), suffix, default)
        if ('ARG FFMPEG_FLAVOR=""' not in docker.splitlines()
                or 'FROM ffmpeg-${FFMPEG_FLAVOR:-${IMAGE_VARIANT}} AS ffmpeg' not in docker.splitlines()):
            raise ValueError('unrecognized ffmpeg stage selection')
        stage = one(r'^FROM (\S+) AS ffmpeg-' + variant + '$', docker)
        if stage not in ('ffmpeg-minimal', 'ffmpeg-debian'):
            raise ValueError('unrecognized ffmpeg stage')
        flavor = stage.removeprefix('ffmpeg-')
        resolver = source('futureagi/docker/image-variant.sh')
        arm = one(r'^    ' + variant + r'\)\n([\s\S]*?)^        ;;$', resolver)
        resolved = one(r'^        : "\$\{FFMPEG_FLAVOR:=(\w+)\}"$', arm)
        if resolved != flavor:
            raise ValueError('runtime package selection disagrees with build stage')
    if flavor == 'debian':
        return variant, flavor, '-', '-', '-'
    config = source('futureagi/docker/ffmpeg/configure-minimal.sh')

    def codecs(name):
        # The sole dynamic addition in the supported recipes is the native PCM
        # decoder family. Never expand shell syntax to discover these names.
        assignments = re.findall('^' + name + '=.*$', config, re.MULTILINE)
        extra = '''DECODERS="$DECODERS $(list decoder | grep '^pcm_' | grep -v '_at$' | tr '\\n' ' ')"'''
        if assignments[1:] != ([extra] if name == 'DECODERS' else []):
            raise ValueError('unrecognized ' + name + ' assignments')
        return ','.join(one('^' + name + r'="([a-z0-9_\s]+)"$', config).split())

    return variant, flavor, one(r'^ARG FFMPEG_VERSION=([\d.]+)$', docker), codecs('ENCODERS'), codecs('DECODERS')


if __name__ == '__main__':
    try:
        print(*contract(*sys.argv[1:]))
    except (ValueError, KeyError, TypeError, AttributeError, ImportError, subprocess.CalledProcessError) as exc:
        sys.exit(f'Unknown build expectation: {exc}')
