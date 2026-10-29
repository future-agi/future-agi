#!/usr/bin/env python3
"""Nonpublishing native acceptance entrypoint. --plan is daemon/network free.

Historical products are frozen from real registry descriptors and tag deployment
DATA; missing update assets are generated locally, never assumed to exist. Chart
DATA is packaged as a real local OCI artifact, not a fictitious GHCR release.
PR components are built as OCI directories, served read-only over loopback,
never pushed. Qualification/packaging belong to the shipping producer CLI.
Current producer incompleteness is a HARD failure, never a passing acceptance.
Private keys, API credentials, env files and DB evidence must not be uploaded.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
from datetime import datetime, timezone
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
from urllib.request import ProxyHandler, Request, build_opener

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0,str(ROOT))

from deploy.tests.update_e2e.real_automatic import CONTEXT, home_path, native_ci, redacted_tail
from deploy.tests.update_e2e.oci_registry import OCIRegistry
from deploy.update.manifest import (COMPONENT_REPOSITORIES, COMPONENT_TAG_SUFFIX, PRODUCT,
                                    canonical_json, deployment_hashes, hash_path, parse_version,
                                    read_deployment_file_list, read_upgrade_floor)
from scripts.generate_update_manifest import apply_release_policy

BUILDS = (
    ('backend','futureagi','futureagi/Dockerfile.oss', ('IMAGE_VARIANT=slim',)),
    ('frontend','frontend','frontend/Dockerfile', ()),
    ('fi_collector','fi-collector','fi-collector/Dockerfile', ()),
    ('agentcc_gateway','agentcc-gateway','agentcc-gateway/Dockerfile', ()),
    ('standalone','deploy/standalone','deploy/standalone/Dockerfile', ()),
)


def get_json(url):
    with build_opener(ProxyHandler({})).open(Request(url,headers={'Accept':'application/json'}),timeout=60) as response:
        raw = response.read(8*1024*1024+1)
    if len(raw) > 8*1024*1024:
        raise ValueError('release response exceeds bound')
    return json.loads(raw)


def plan(mode):
    return dict(schema_version=1, mode=mode, platform='linux/amd64', context=CONTEXT,
        versions=['v1.53.0','v1.54.0','v1.55.0'] if mode == 'a-b-c' else ['latest-stable','PR-build'],
        historical_inputs='real image descriptors and tag deployment data; local test manifests/proofs, no historical update assets',
        chart='historical source chart packaged into private GET-only OCI transport; no GHCR publication',
        scheduler='setup-installed current-user cron; never test-driven ticks',
        build_output='OCI directory; read-only loopback GET/HEAD, no registry writes',
        builds=[] if mode == 'a-b-c' else [dict(component=n,context=c,dockerfile=d,args=list(a)) for n,c,d,a in BUILDS],
        qualification=['python -I scripts/rehearse_update_compatibility.py qualify',
                       '--reference-scale registered-100k','--boot-timeout 4200',
                       'python -I scripts/rehearse_update_compatibility.py package'],
        acceptance=['one-opt-in forward sequence','fresh A -> failing B -> automatically restored A'],
        fail_closed=True, product_qualification=False,
        known_blockers=['shipping producer transaction/workloads/suites incomplete',
                        'retained Docker Hub Compose does not support PR loopback registry images'])


class NativeAcceptance:
    def __init__(self,out):
        self.out = home_path(out)
        self.commands=[]
        self.diagnostics=[]
        self._secret_values = {value for key, value in os.environ.items()
                               if re.search(r'PASSWORD|SECRET|TOKEN|KEY', key, re.I)}
        # Shared by qualification, bootstrap, forward/restore cases and teardown.
        self.deadline = time.monotonic()+21000

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
                result=subprocess.run([str(a) for a in args],cwd=ROOT,env=os.environ.copy(),
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
        # Registry reads only. Resolve a real OCI descriptor; never create a
        # stand-in digest, load/retag images, or depend on historical update assets.
        raw = self.command(['docker','--context',CONTEXT,'buildx','imagetools','inspect',
                            reference,'--format','{{json .Manifest}}'], timeout=180)
        try:
            descriptor = json.loads(raw)
            digest = descriptor.get('digest') if isinstance(descriptor,dict) else None
        except ValueError:
            digest = None
        if not isinstance(digest,str) or not re.fullmatch(r'sha256:[0-9a-f]{64}',digest):
            raise ValueError('registry did not return an immutable descriptor')
        return digest

    def historical_chart(self, version, sha, registry):
        """Real tag chart data, packaged locally; never claim a missing GHCR release.

        Standalone does not run Helm. Its required manifest field still names a
        real, hash-verified OCI artifact on our GET-only test transport, rather
        than a fabricated production digest. Historical chart code is never run.
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
        metadata = get_json('https://api.github.com/repos/future-agi/future-agi/releases/tags/'+version)
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

    def build_pr(self,previous,registry):
        version='v%d.%d.%d'%(parse_version(previous['version'])[:2]+(parse_version(previous['version'])[2]+1,))
        # parse_version returns (major,minor,patch,stage,number); do not use a
        # prerelease tag as the source of a stable automatic test sequence.
        sha=self.command(['git','rev-parse','HEAD']).decode().strip()
        created=datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
        layouts={}
        pins={}
        for component,context,dockerfile,extra in BUILDS:
            layout=self.out/('oci-'+component)
            args=['docker','--context',CONTEXT,'buildx','build','--platform','linux/amd64',
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
            repository=previous['components'][component]['image'].split(':')[0]
            repository='/'.join(repository.split('/')[-2:])
            digest=registry.add(repository,version,layout)
            pins[component]=dict(image=registry.host+'/'+repository+':'+version,digest=digest,platforms=['linux/amd64'])
        (self.out/'pr-built-images.json').write_bytes(canonical_json(dict(source_sha=sha,version=version,
            components=pins,product_qualified=False,transport='read-only-loopback-oci')))
        # Do not invent the remaining component/chart digests or silently edit
        # retained Compose to a new registry. The current updater requires the
        # retained Docker Hub registry identity; OCI exports have no Docker Hub
        # RepoDigest, and a locally retagged load does not establish one. A
        # reviewed nonpublishing image transport/package interface is needed.
        # Native CI must remain RED until that real path exists.
        raise RuntimeError('PR acceptance blocked: unpublished OCI images cannot use the retained Docker Hub '
                           'layout; a complete real component/chart candidate package and reviewed test transport are required')

    def run_tests(self,mode,registry):
        versions=['v1.53.0','v1.54.0','v1.55.0']
        if mode == 'upgrade':
            latest=get_json('https://api.github.com/repos/future-agi/future-agi/releases/latest')
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
        env=dict(os.environ,FUTURE_AGI_UPDATE_E2E='1',FAGI_UPDATE_ACCEPTANCE_INPUTS=str(config))
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
        with OCIRegistry() as registry:
            status=run.run_tests(args.mode,registry)
    except Exception as error:
        # Do not leak private command output, credentials or DB data to CI logs.
        (out/'failure.json').write_bytes(canonical_json(dict(status='failed',reason=str(error))))
        print('real automatic acceptance failed; bounded diagnostics retained',file=sys.stderr)
    finally:
        (out/'summary.json').write_bytes(canonical_json(dict(schema_version=1,mode=args.mode,
            status='passed' if status == 0 else 'failed',product_acceptance_passed=status == 0,
            phase_1_complete=False)))
    return status


if __name__ == '__main__':
    raise SystemExit(main())
