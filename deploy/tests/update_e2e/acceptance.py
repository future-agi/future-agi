#!/usr/bin/env python3
"""Nonpublishing native acceptance entrypoint. --plan is daemon/network free.

Historical products are frozen from real registry descriptors and tag deployment
DATA; missing update assets are generated locally, never assumed to exist. Chart
DATA is packaged as a real local OCI artifact, not a fictitious GHCR release.
PR components are built as OCI directories and pushed only to a runner-local
Docker Hub mirror, then pulled by their normal names to establish RepoDigests.
Qualification/packaging belong to the shipping producer CLI.
Current producer incompleteness is a HARD failure, never a passing acceptance.
Private keys, API credentials, env files and DB evidence must not be uploaded.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
from datetime import datetime, timezone
from http.client import HTTPException
import io
import json
import time
import os
from pathlib import Path
import re
import subprocess
import sys
import tarfile
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0,str(ROOT))

from deploy.tests.update_e2e.real_automatic import CONTEXT, home_path, native_ci, redacted_tail
from deploy.tests.update_e2e.oci_registry import DockerHubMirror, MIRROR_HOST, OCIRegistry, REGISTRY_IMAGE
from deploy.update.manifest import (COMPONENT_REPOSITORIES, COMPONENT_TAG_SUFFIX, PRODUCT,
                                    canonical_json, deployment_hashes, hash_path, parse_version,
                                    read_deployment_file_list, read_upgrade_floor, parse_image_ref, is_version, validate_manifest)
from scripts.generate_update_manifest import apply_release_policy

# Counted from the acceptance step, not the job start: finish before the
# 355-minute job ceiling while leaving the setup steps (checkout, mirror,
# Buildx: a few minutes), deactivation/compose teardown and artifact upload.
JOB_SECONDS = 20400

BUILDS = (
    ('backend','futureagi','futureagi/Dockerfile.oss', ('IMAGE_VARIANT=slim',)),
    ('frontend','frontend','frontend/Dockerfile', ()),
    ('fi_collector','fi-collector','fi-collector/Dockerfile', ()),
    ('agentcc_gateway','agentcc-gateway','agentcc-gateway/Dockerfile', ()),
    ('standalone','deploy/standalone','deploy/standalone/Dockerfile', ()),
)


RELEASE_API = 'https://api.github.com/repos/future-agi/future-agi/releases/'
RELEASE_TOKEN_ENV = 'FAGI_UPDATE_RELEASE_TOKEN'


class _NoReleaseRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Refuse even same-origin redirects: metadata is never an asset download.
        return None


def get_json(url):
    # Validate the raw spelling, before urllib can normalize controls, userinfo,
    # ports or path escapes. Only our two stable release-metadata routes qualify.
    if not isinstance(url, str) or not url.startswith(RELEASE_API):
        raise ValueError('unsupported release metadata URL')
    endpoint = url[len(RELEASE_API):]
    if endpoint != 'latest' and not (endpoint.startswith('tags/') and
            is_version(endpoint[5:]) and parse_version(endpoint[5:])[3] == 2):
        raise ValueError('unsupported release metadata URL')
    # This step-scoped read-only Actions credential is not an app/updater token.
    # No fallback to an operator's GH_TOKEN/GITHUB_TOKEN or credential helper.
    token = os.environ.get(RELEASE_TOKEN_ENV, '')
    if token and (len(token) > 4096 or not re.fullmatch(r'[A-Za-z0-9._~+/-]+=*', token)):
        raise ValueError('invalid release metadata credential')
    request = Request(url, headers={'Accept': 'application/json'})
    if token:
        request.add_unredirected_header('Authorization', 'Bearer '+token)
    try:
        # The default HTTPS handler retains certificate and hostname validation.
        with build_opener(ProxyHandler({}), _NoReleaseRedirects()).open(request, timeout=60) as response:
            raw = response.read(8*1024*1024+1)
    except HTTPError as error:
        error.close()
        # Never retain server reason/body/Location or exception chains: they may
        # reflect credentials, and main writes the exception to failure.json.
        raise RuntimeError('release metadata HTTP request failed (status %d)' % error.code) from None
    except (OSError, HTTPException, ValueError):
        raise RuntimeError('release metadata request failed') from None
    if len(raw) > 8*1024*1024:
        raise ValueError('release response exceeds bound')
    try:
        return json.loads(raw)
    except (ValueError, RecursionError):
        raise ValueError('invalid release metadata JSON') from None


def command_environment():
    # Both native command execution and the unittest/qualify/package subtree
    # otherwise inherit os.environ; stripping only at the app layer is too late.
    return {key: value for key, value in os.environ.items() if key != RELEASE_TOKEN_ENV}


def plan(mode):
    return dict(schema_version=1, mode=mode, platform='linux/amd64', context=CONTEXT,
        versions=['v1.53.0','v1.54.0','v1.55.0'] if mode == 'a-b-c' else ['latest-stable','PR-build'],
        historical_inputs='real image descriptors and tag deployment data; local test manifests/proofs, no historical update assets',
        chart='historical source chart packaged into private GET-only OCI transport; no GHCR publication',
        scheduler='setup-installed current-user cron; never test-driven ticks',
        build_output='OCI directory; push only to runner-local registry, pull normal Docker Hub names' if mode == 'upgrade' else 'no PR builds',
        image_transport=dict(registry_image=REGISTRY_IMAGE,mirror='http://'+MIRROR_HOST,
            scope='ephemeral native Actions runner only',proxy_mode=False,
            writes='loopback only; unmodified OCI bytes, PR-specific and retained Compose version tags',
            identity='normal docker.io names and genuine RepoDigests, checked against OCI export',
            unchanged='Docker Hub digests retained; mirror misses fall back to Hub') if mode == 'upgrade' else None,
        builds=[] if mode == 'a-b-c' else [dict(component=n,context=c,dockerfile=d,args=list(a)) for n,c,d,a in BUILDS],
        builder=None if mode == 'a-b-c' else 'explicit --builder FAGI_UPDATE_E2E_BUILDER; docker-container driver verified first',
        qualification=['python -I scripts/rehearse_update_compatibility.py qualify',
                       '--reference-scale registered-100k','--boot-timeout 4200',
                       'python -I scripts/rehearse_update_compatibility.py package'],
        acceptance=['one-opt-in forward sequence','fresh A -> failing B -> automatically restored A'],
        fail_closed=True, product_qualification=False,
        qualification_contract='full qualify/package must execute every shipping suite; no milestone proof',
        fault_seam='test-only post-migration-stop-v1; no product image or updater fault switch',
        wall_clock_seconds=JOB_SECONDS, known_blockers=[])


class NativeAcceptance:
    def __init__(self,out):
        self.out = home_path(out)
        self.commands=[]
        self.diagnostics=[]
        self._secret_values = {value for key, value in os.environ.items()
                               if re.search(r'PASSWORD|SECRET|TOKEN|KEY', key, re.I)}
        # Shared by qualification, bootstrap, forward/restore cases and teardown.
        self.deadline = time.monotonic()+JOB_SECONDS

    def command(self,args,*,timeout=1800):
        index=len(self.commands)+1
        self.commands.append([str(a) for a in args])
        (self.out/'commands.json').write_bytes(canonical_json(self.commands))
        started = time.monotonic()
        entry = dict(index=index, executable=Path(str(args[0])).name, outcome='interrupted')
        # Only fixed caller-owned command categories, never credential/argv dumps.
        for category in ('build', 'inspect', 'archive', 'rev-parse', 'cat-file', 'ls-tree', 'is-active'):
            if category in args:
                entry['operation'] = category
        stderr = self.out/('command-%04d.stderr'%index)
        try:
            with stderr.open('wb') as err:
                result=subprocess.run([str(a) for a in args],cwd=ROOT,env=command_environment(),
                                      stdout=subprocess.PIPE,stderr=err,
                                      timeout=max(1,min(timeout,self.deadline-time.monotonic())))
            (self.out/('command-%04d.stdout'%index)).write_bytes(result.stdout)
            entry.update(outcome='passed' if result.returncode == 0 else 'failed', exit_code=result.returncode)
            if result.returncode:
                raise RuntimeError('native acceptance prerequisite/command %d failed; see command-diagnostics.json'%index)
            return result.stdout
        except subprocess.TimeoutExpired:
            entry['outcome'] = 'timed_out'
            raise
        finally:
            entry['seconds'] = round(time.monotonic()-started, 3)
            if entry['outcome'] != 'passed' and stderr.is_file():
                with stderr.open('rb') as source:
                    source.seek(max(0, stderr.stat().st_size-32768))
                    entry['stderr_tail'] = redacted_tail(source.read(32768), self._secret_values)
            self.diagnostics.append(entry)
            (self.out/'command-diagnostics.json').write_bytes(canonical_json(dict(
                schema_version=1, qualification_evidence=False, commands=self.diagnostics)))

    def extract_deployment(self, version, sha, names):
        """Extract regular deployment DATA only; never execute a historical tree."""
        if not re.fullmatch('[0-9a-f]{40}',sha):
            raise ValueError('invalid release source commit')
        self.command(['git','cat-file','-e',sha+'^{commit}'])
        if not names or any(Path(name).is_absolute() or '..' in Path(name).parts for name in names):
            raise ValueError('invalid fixed deployment inventory')
        directory=self.out/('retained-'+version); directory.mkdir(mode=0o700)
        # Older tags predate the host-status overlays and updater inventory.
        # Ask git for existing paths before archive; missing pathspecs are errors.
        present = self.command(['git','ls-tree','--name-only',sha,'--',*names]).decode().splitlines()
        if not present:
            raise ValueError('release deployment is absent')
        raw=self.command(['git','archive','--format=tar',sha,'--',*present])
        with tarfile.open(fileobj=io.BytesIO(raw),mode='r:') as archive:
            total=0
            for member in archive:
                parts=Path(member.name).parts
                if member.name.startswith('/') or '..' in parts or not (member.isfile() or member.isdir()):
                    raise ValueError('unsafe retained release member')
                total+=member.size
                if total > 128*1024*1024:
                    raise ValueError('retained release deployment exceeds bound')
                path=directory/member.name
                if member.isdir():
                    path.mkdir(mode=0o700,parents=True,exist_ok=True)
                else:
                    path.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
                    with path.open('xb') as out, archive.extractfile(member) as source:
                        out.write(source.read())
                    path.chmod(0o600)
        return directory

    def retained(self,manifest):
        directory = self.extract_deployment(manifest['version'], manifest['source_sha'], list(manifest['deployment']['files']))
        for name,digest in manifest['deployment']['files'].items():
            if hash_path(directory,name) != digest:
                raise ValueError('retained deployment hash mismatch: '+name)
        return directory

    def registry_digest(self, reference):
        """Select the native leaf from registry DATA, not the multiarch index.

        An index digest in a manifest but a platform-leaf RepoDigest on the
        running app fails real enrollment/observation. Never pick the first
        descriptor (v1.54.0's first leaf is arm64), or guess v1.55.0's digest.
        """
        raw = self.command(['docker','--context',CONTEXT,'manifest','inspect',reference], timeout=180)
        try:
            document = json.loads(raw)
        except ValueError:
            raise ValueError('registry did not return an immutable descriptor') from None
        if not isinstance(document, dict):
            raise ValueError('registry did not return an immutable descriptor')
        if 'manifests' in document:
            descriptors = document['manifests']
        else:
            verbose = json.loads(self.command(['docker','--context',CONTEXT,'manifest','inspect',
                                               '--verbose',reference], timeout=180))
            descriptors = [verbose.get('Descriptor', {})] if isinstance(verbose,dict) else []
        if not isinstance(descriptors,list):
            raise ValueError('registry manifest index is invalid')
        selected = [row for row in descriptors if isinstance(row,dict) and
            row.get('platform',{}).get('os') == 'linux' and
            row.get('platform',{}).get('architecture') == 'amd64' and
            row.get('platform',{}).get('variant','') in ('','v1')]
        if len(selected) != 1:
            raise ValueError('registry requires exactly one native linux/amd64 leaf')
        descriptor = selected[0]
        digest = descriptor.get('digest')
        if (not isinstance(digest,str) or not re.fullmatch(r'sha256:[0-9a-f]{64}',digest) or
                descriptor.get('mediaType') not in ('application/vnd.oci.image.manifest.v1+json',
                    'application/vnd.docker.distribution.manifest.v2+json')):
            raise ValueError('registry did not return an immutable native image descriptor')
        path = self.out/'platform-images.json'
        images = json.loads(path.read_bytes()) if path.exists() else {}
        images[reference] = dict(platform='linux/amd64', digest=digest, descriptor=descriptor)
        path.write_bytes(canonical_json(images))
        return digest

    def historical_chart(self, version, sha, registry, *, candidate=False):
        """Real source chart data, packaged locally; no claimed GHCR release.

        Standalone does not run Helm. Its required manifest field still names a
        real, hash-verified OCI artifact on our GET-only test transport, rather
        than a fabricated production digest. Historical chart code is never run.
        PR packages override version/appVersion only, like helm package does.
        """
        if registry is None:
            raise ValueError('private read-only OCI transport required')
        raw = self.command(['git','archive','--format=tar',sha,'--','deploy/helm/futureagi'])
        payload = io.BytesIO()
        metadata = None
        total = 0
        with tarfile.open(fileobj=io.BytesIO(raw),mode='r:') as source, \
             gzip.GzipFile(fileobj=payload,mode='wb',mtime=0) as compressed, \
             tarfile.open(fileobj=compressed,mode='w|',format=tarfile.PAX_FORMAT) as package:
            for member in source:
                path = Path(member.name)
                if path.is_absolute() or '..' in path.parts or not (member.isfile() or member.isdir()):
                    raise ValueError('unsafe historical chart member')
                if member.isdir():
                    continue  # git archive includes the parent directory entries
                relative = path.relative_to('deploy/helm/futureagi')
                if not relative.parts or relative.parts[0] in ('hack','ci') or any(p.startswith('.') for p in relative.parts):
                    continue
                if not member.isfile():
                    continue
                total += member.size
                if total > 16*1024*1024:
                    raise ValueError('historical chart exceeds packaging bound')
                data = source.extractfile(member).read()
                if relative.as_posix() == 'Chart.yaml':
                    text = data.decode()
                    if candidate:
                        # Like helm package --version/--app-version: only the
                        # private package metadata changes, never the PR tree.
                        text, versions = re.subn(r'^version: [^\n]+$', 'version: '+version[1:], text, flags=re.M)
                        text, apps = re.subn(r'^appVersion: [^\n]+$', 'appVersion: "'+version+'"', text, flags=re.M)
                        if versions != 1 or apps != 1:
                            raise ValueError('PR chart version metadata absent or ambiguous')
                        data = text.encode()
                    if not re.search(r'^name: futureagi\s*$',text,re.M) or not re.search(
                            r'^version: '+re.escape(version[1:])+r'(?:\s|$)',text,re.M):
                        raise ValueError('historical chart version differs')
                    metadata = dict(name='futureagi',version=version[1:],appVersion=version)
                entry = tarfile.TarInfo('futureagi/'+relative.as_posix())
                entry.size, entry.mode, entry.mtime = len(data), 0o644, 0
                package.addfile(entry,io.BytesIO(data))
        if metadata is None:
            raise ValueError('historical chart metadata absent')
        layout = self.out/('chart-oci-'+version)
        blobs = layout/'blobs/sha256'; blobs.mkdir(mode=0o700,parents=True)
        def blob(data, media):
            digest = hashlib.sha256(data).hexdigest()
            (blobs/digest).write_bytes(data)
            return dict(mediaType=media,digest='sha256:'+digest,size=len(data))
        config = blob(canonical_json(metadata),'application/vnd.cncf.helm.config.v1+json')
        layer = blob(payload.getvalue(),'application/vnd.cncf.helm.chart.content.v1.tar+gzip')
        descriptor = blob(canonical_json(dict(schemaVersion=2,config=config,layers=[layer],
            mediaType='application/vnd.oci.image.manifest.v1+json')),'application/vnd.oci.image.manifest.v1+json')
        (layout/'index.json').write_bytes(canonical_json(dict(schemaVersion=2,manifests=[descriptor])))
        (layout/'oci-layout').write_bytes(b'{"imageLayoutVersion":"1.0.0"}')
        repository = 'futureagi/acceptance-chart-'+version.replace('.','-')
        digest = registry.add(repository,version[1:],layout)
        return dict(chart='oci://'+registry.host+'/'+repository,version=version[1:],digest=digest)

    def published(self,version,registry=None):
        """Freeze historical images/tag deployment into an UNCERTIFIED test candidate.

        v1.53.0..v1.55.0 predate update manifests. The current trusted generator
        policy supplies notify-only metadata; AutomaticPublisher later signs A
        and runs the real qualify/package entrypoints for B and C. No published
        update manifest, proof, or production signing authority is assumed.
        """
        parse_version(version)
        metadata = get_json(RELEASE_API+'tags/'+version)
        if metadata.get('tag_name') != version or metadata.get('draft') or metadata.get('prerelease'):
            raise ValueError('historical source is not the requested stable release')
        sha = self.command(['git','rev-parse','refs/tags/'+version+'^{commit}']).decode().strip()
        names = read_deployment_file_list(ROOT)
        directory = self.extract_deployment(version, sha, names)
        # Only inputs that actually shipped at this tag are retained. New host
        # tooling is copied separately, not retroactively called release data.
        files = [name for name in names if (directory/name).exists()]
        if 'docker-compose.yml' not in files:
            raise ValueError('historical Standalone deployment is absent')
        components = {}
        for name,repository in COMPONENT_REPOSITORIES.items():
            image = 'docker.io/'+repository+':'+version+COMPONENT_TAG_SUFFIX.get(name,'')
            components[name] = dict(image=image,digest=self.registry_digest(image),
                                   platforms=['linux/amd64'])
        chart = self.historical_chart(version, sha, registry)
        manifest = dict(schema_version=2,product=PRODUCT,channel='stable',version=version,source_sha=sha,
            published_at=metadata['published_at'],minimum_version=read_upgrade_floor(ROOT),
            release_notes='https://github.com/future-agi/future-agi/releases/tag/'+version,
            signature={'bundle':'future-agi-update-manifest.sigstore.json'},components=components,
            helm=chart,
            deployment={'files':deployment_hashes(directory,files)},
            migration={'requires_bootstrap':True,'rollback':'manual'})
        manifest = apply_release_policy(manifest,SimpleNamespace(schema_version=2))
        (self.out/('test-candidate-'+version+'.json')).write_bytes(canonical_json(manifest))
        return manifest,directory

    def pull_pr_image(self, image, digest, pr_tag):
        """Prove mirror resolution, tag agreement and Docker Hub RepoDigests."""
        repository = parse_image_ref(image).repository
        refs = ['docker.io/'+repository+':'+pr_tag, image, image+'@'+digest]
        for ref in refs:
            self.command(['docker','--context',CONTEXT,'pull','--platform','linux/amd64',ref], timeout=1800)
        rows = json.loads(self.command(['docker','--context',CONTEXT,'image','inspect',*refs]))
        if not isinstance(rows, list) or len(rows) != len(refs):
            raise ValueError('PR mirror inspection incomplete')
        for row in rows:
            pins = [parse_image_ref(pin) for pin in row.get('RepoDigests',[])]
            if (row.get('Os') != 'linux' or row.get('Architecture') != 'amd64'
                    or not any(pin.registry == 'docker.io' and pin.repository == repository
                               and pin.digest == digest for pin in pins)):
                raise ValueError('PR mirror did not establish the expected native Docker Hub RepoDigest')
        if not rows[0].get('Id') or len({row.get('Id') for row in rows}) != 1:
            raise ValueError('PR mirror tag and immutable image identity differ')
        return dict(image=image,digest=digest,pr_tag=pr_tag,image_id=rows[0]['Id'],
                    repo_digests=rows[0]['RepoDigests'],platform='linux/amd64')

    def cron_environment_probe(self):
        """Start what the installed cron entry starts, before any qualification.

        The scheduler launches `env -i HOME PATH PYTHON DOCKER_CONTEXT /bin/bash
        bin/update run` with the PATH registered at setup. If the interpreter,
        Docker context, Compose launcher or cosign cannot start in that cleared
        environment, every cron wake fails silently (output goes to /dev/null)
        and the acceptance would only time out hours later. Fail here instead.
        Read-only: --help/version/info only, no installation or daemon change.
        """
        from deploy.tests.update_e2e.real_automatic import native_launcher_path
        path = native_launcher_path(os.environ)
        base = ['/usr/bin/env','-i','HOME='+str(Path.home()),'PATH='+path,'PYTHON='+sys.executable,
                'DOCKER_CONTEXT='+CONTEXT]
        self.command([*base,'/bin/bash','--',ROOT/'bin/update','--help'], timeout=60)
        if self.command([*base,'docker','info','--format','{{.Architecture}}'], timeout=60).decode().strip() \
                not in ('x86_64','amd64'):
            raise ValueError('cron-environment Docker context is not the native amd64 daemon')
        self.command([*base,'docker-compose','version'], timeout=60)
        self.command([*base,'cosign','version'], timeout=60)
        (self.out/'cron-environment.json').write_bytes(canonical_json(dict(schema_version=1,
            status='passed',path=path,python=sys.executable,context=CONTEXT,
            probes=['bin/update --help','docker info','docker-compose version','cosign version'])))
        return path

    def pr_builder(self):
        """The workflow's named docker-container builder, selected explicitly.

        `buildx use` from setup-buildx-action is scoped to the default context,
        so `docker --context colima-fagi-update buildx build` silently fell back
        to that context's docker driver, which cannot export OCI layouts (CI run
        40965747765). Never rely on the ambient builder; refuse any other driver
        before building or uploading anything.
        """
        name = os.environ.get('FAGI_UPDATE_E2E_BUILDER', '')
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}', name):
            raise ValueError('explicit docker-container Buildx builder required')
        raw = self.command(['docker','--context',CONTEXT,'buildx','inspect',name], timeout=120).decode('utf-8','replace')
        drivers = re.findall(r'(?m)^Driver:[ \t]+(\S+)[ \t]*$', raw)
        if drivers != ['docker-container']:
            raise ValueError('PR OCI export requires the docker-container Buildx driver')
        return name

    def build_pr(self,previous,registry):
        previous = validate_manifest(previous)
        parsed = parse_version(previous['version'])
        if parsed[3] != 2:
            raise ValueError('PR automatic acceptance requires a stable predecessor')
        version='v%d.%d.%d'%(parsed[:2]+(parsed[2]+1,))
        sha=self.command(['git','rev-parse','HEAD']).decode().strip()
        if not re.fullmatch('[0-9a-f]{40}', sha):
            raise ValueError('invalid PR source commit')
        run, attempt = os.environ.get('GITHUB_RUN_ID',''), os.environ.get('GITHUB_RUN_ATTEMPT','')
        if not all(re.fullmatch('[0-9]+', value) for value in (run,attempt)):
            raise ValueError('PR transport requires Actions run and attempt identity')
        pr_tag='pr-'+sha+'-'+run+'-'+attempt
        mirrors = json.loads(self.command(['docker','--context',CONTEXT,'info','--format','{{json .RegistryConfig.Mirrors}}']))
        if not isinstance(mirrors,list) or not any(value in mirrors for value in
                ('http://'+MIRROR_HOST+'/', 'http://'+MIRROR_HOST)):
            raise ValueError('runner-local Docker Hub mirror is not configured')
        builder = self.pr_builder()
        mirror = DockerHubMirror()
        created=datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
        layouts={}
        # The schema requires version-shaped image tags. Inactive, unbuilt
        # profile components retain the exact upstream digest; their immutable
        # image@digest pulls ignore this descriptive tag and fall back to Hub.
        # Standalone's actual runtime dependencies keep retained Compose refs.
        pins={name:dict(image='docker.io/'+repo+':'+version+COMPONENT_TAG_SUFFIX.get(name,''),
                        digest=previous['components'][name]['digest'],platforms=['linux/amd64'])
              for name,repo in COMPONENT_REPOSITORIES.items()}
        observations={}
        for component,context,dockerfile,extra in BUILDS:
            layout=self.out/('oci-'+component)
            args=['docker','--context',CONTEXT,'buildx','build','--builder',builder,'--platform','linux/amd64',
                  '--provenance=false','--sbom=false','--file',ROOT/dockerfile,
                  '--output','type=oci,dest='+str(layout)+',tar=false']
            for value in ('VERSION='+version,'REVISION='+sha,'CREATED='+created,*extra):
                args.extend(['--build-arg',value])
            if component == 'standalone':
                for arg,name in (('BACKEND_IMAGE','backend'),('FRONTEND_IMAGE','frontend'),
                                 ('FI_COLLECTOR_IMAGE','fi_collector'),('AGENTCC_GATEWAY_IMAGE','agentcc_gateway')):
                    alias='ci-'+name.replace('_','-')
                    args.extend(['--build-context',alias+'=oci-layout://'+str(layouts[name]),'--build-arg',arg+'='+alias])
            self.command([*args,ROOT/context],timeout=7200)
            layouts[component]=layout
            repository=COMPONENT_REPOSITORIES[component]
            digest=mirror.publish(repository,version,pr_tag,layout)
            pins[component]['digest']=digest
            observations[component]=self.pull_pr_image(pins[component]['image'],digest,pr_tag)
            (self.out/'pr-image-transport.json').write_bytes(canonical_json(dict(schema_version=1,
                source_sha=sha,version=version,mirror='http://'+MIRROR_HOST,images=observations,
                qualification_evidence=False)))
        chart=self.historical_chart(version,sha,registry,candidate=True)
        manifest=dict(schema_version=2,product=PRODUCT,channel='stable',version=version,source_sha=sha,
            published_at=created,minimum_version=read_upgrade_floor(ROOT),
            release_notes='https://github.com/future-agi/future-agi/commit/'+sha,
            signature={'bundle':'future-agi-update-manifest.sigstore.json'},components=pins,helm=chart,
            deployment={'files':deployment_hashes(ROOT,read_deployment_file_list(ROOT))},
            migration={'requires_bootstrap':True,'rollback':'manual'})
        manifest=apply_release_policy(manifest,SimpleNamespace(schema_version=2))
        (self.out/'pr-built-images.json').write_bytes(canonical_json(dict(source_sha=sha,version=version,
            components={name:pins[name] for name,*_ in BUILDS},pr_tag=pr_tag,product_qualified=False,
            unchanged_components={name:previous['components'][name] for name in pins if name not in observations},
            transport='runner-local-docker-hub-mirror')))
        (self.out/('test-candidate-'+version+'.json')).write_bytes(canonical_json(manifest))
        return manifest

    def run_tests(self,mode,registry):
        versions=['v1.53.0','v1.54.0','v1.55.0']
        if mode == 'upgrade':
            latest=get_json(RELEASE_API+'latest')
            if latest.get('draft') or latest.get('prerelease'):
                raise ValueError('latest source is not stable')
            versions=[latest['tag_name']]
        candidates,deployments={},{}
        for version in versions:
            candidates[version],directory=self.published(version,registry)
            deployments[version]=str(directory)
        if mode == 'upgrade':
            target=self.build_pr(candidates[versions[0]],registry)
            versions.append(target['version']); candidates[target['version']]=target
            deployments[target['version']]=str(ROOT)
        config=self.out/'inputs.json'
        config.write_bytes(canonical_json(dict(mode=mode,versions=versions,candidates=candidates,deployment_dirs=deployments,
            base=str(self.out/'acceptance'),run_id=int(os.environ['GITHUB_RUN_ID']),
            deadline_monotonic=self.deadline)))
        config.chmod(0o600)
        env=dict(command_environment(),FUTURE_AGI_UPDATE_E2E='1',FAGI_UPDATE_ACCEPTANCE_INPUTS=str(config))
        with (self.out/'test-output.log').open('wb') as output:
            result=subprocess.run([sys.executable,'-m','unittest','discover','-s','deploy/tests',
                '-p','test_update_e2e_automatic.py','-v'],cwd=ROOT,env=env,stdout=output,stderr=subprocess.STDOUT,timeout=max(1,self.deadline-time.monotonic()))
        if result.returncode == 0:
            if (self.out/'test-output.log').stat().st_size > 8*1024*1024:
                raise RuntimeError('acceptance result output exceeds verification bound')
            log=(self.out/'test-output.log').read_text(errors='replace')
            count=re.search(r'Ran ([0-9]+) tests? in ',log)
            if count is None or int(count[1]) < 1 or re.search(r'skipped=[1-9]',log):
                raise RuntimeError('automatic acceptance did not execute its opted-in tests without skips')
        else:
            # Surface the producer's fixed refusal, not arbitrary stderr or
            # secret command output. A nonzero test/qualification stays RED.
            for path in self.out.glob('acceptance/fagi-upd-e2e-real-publisher-*/v*/qualification/diagnostics.json'):
                if path.is_symlink() or path.stat().st_size > 1024*1024:
                    continue
                try:
                    diagnostic=json.loads(path.read_bytes())
                except (OSError,ValueError):
                    continue
                failure=diagnostic.get('failure') if isinstance(diagnostic,dict) else None
                code=failure.get('refusal_code') if isinstance(failure,dict) else None
                if isinstance(code,str) and re.fullmatch(r'[a-z][a-z0-9_]{0,63}',code):
                    # Closed producer fields only: code, stage and per-suite state.
                    summary=dict(status='failed',reason=code,qualification_evidence=False)
                    if re.fullmatch(r'v[0-9]{1,4}\.[0-9]{1,4}\.[0-9]{1,6}',path.parent.parent.name):
                        summary['target_version']=path.parent.parent.name
                    suites=failure.get('suites')
                    if isinstance(suites,dict) and all(isinstance(k,str) and re.fullmatch(r'[a-z_]{1,40}',k)
                            and v in ('passed','failed','incomplete','unrun') for k,v in suites.items()):
                        summary['suites']=suites
                    for key in ('phase','active_suite'):
                        if isinstance(failure.get(key),str) and re.fullmatch(r'[a-z_]{1,64}',failure[key]):
                            summary[key]=failure[key]
                    (self.out/'failure.json').write_bytes(canonical_json(summary))
                    print('real automatic acceptance refused by qualification: '+code,file=sys.stderr)
                    break
        return result.returncode


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode',choices=('a-b-c','upgrade'),required=True)
    parser.add_argument('--out',type=Path)
    parser.add_argument('--plan',action='store_true')
    args=parser.parse_args(argv)
    if args.plan:
        print(json.dumps(plan(args.mode),indent=2)); return 0
    if not native_ci() or args.out is None:
        parser.error('real product acceptance runs only on native Linux Actions with a new HOME --out directory')
    out=home_path(args.out)
    out.mkdir(mode=0o700)
    os.umask(0o077)
    status=1
    try:
        run=NativeAcceptance(out)
        if run.command(['docker','--context',CONTEXT,'info','--format','{{.Architecture}}']).decode().strip() not in ('x86_64','amd64'):
            raise ValueError('native amd64 Docker required')
        run.command(['systemctl','is-active','--quiet','cron.service'])
        run.cron_environment_probe()
        with OCIRegistry() as registry:
            status=run.run_tests(args.mode,registry)
    except Exception as error:
        # Do not leak private command output, credentials or DB data to CI logs.
        reason = str(error)
        token = os.environ.get(RELEASE_TOKEN_ENV)
        if token:
            reason = reason.replace(token, '[REDACTED]')
        (out/'failure.json').write_bytes(canonical_json(dict(status='failed',reason=redacted_tail(reason))))
        print('real automatic acceptance failed; bounded diagnostics retained',file=sys.stderr)
    finally:
        (out/'summary.json').write_bytes(canonical_json(dict(schema_version=1,mode=args.mode,
            status='passed' if status == 0 else 'failed',product_acceptance_passed=status == 0,
            phase_1_complete=False)))
    return status


if __name__ == '__main__':
    raise SystemExit(main())
