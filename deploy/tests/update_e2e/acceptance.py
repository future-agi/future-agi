#!/usr/bin/env python3
"""Nonpublishing native acceptance entrypoint. --plan is daemon/network free.

Published releases are frozen from their real release assets and git commits.
PR components are built as OCI directories, served read-only over loopback,
never pushed. Qualification/packaging belong to the shipping producer CLI.
Current producer incompleteness is a HARD failure, never a passing acceptance.
Private keys, API credentials, env files and DB evidence must not be uploaded.
"""
from __future__ import annotations

import argparse
import copy
from datetime import datetime, timezone
import io
import json
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

from deploy.tests.update_e2e.real_automatic import CONTEXT, home_path, native_ci
from deploy.tests.update_e2e.oci_registry import OCIRegistry
from deploy.update.manifest import (MANIFEST_ASSET, canonical_json, hash_path, parse_version,
                                    validate_manifest)
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

    def command(self,args,*,timeout=1800):
        index=len(self.commands)+1
        self.commands.append([str(a) for a in args])
        (self.out/'commands.json').write_bytes(canonical_json(self.commands))
        with (self.out/('command-%04d.stderr'%index)).open('wb') as err:
            result=subprocess.run([str(a) for a in args],cwd=ROOT,env=os.environ.copy(),
                                  stdout=subprocess.PIPE,stderr=err,timeout=timeout)
        (self.out/('command-%04d.stdout'%index)).write_bytes(result.stdout)
        if result.returncode:
            raise RuntimeError('native acceptance prerequisite/command %d failed; private diagnostics retained'%index)
        return result.stdout

    def retained(self,manifest):
        """Extract only regular deployment DATA from the release commit, never code."""
        sha=manifest['source_sha']
        if not re.fullmatch('[0-9a-f]{40}',sha):
            raise ValueError('invalid release source commit')
        self.command(['git','cat-file','-e',sha+'^{commit}'])
        directory=self.out/('retained-'+manifest['version']); directory.mkdir(mode=0o700)
        names=list(manifest['deployment']['files'])
        raw=self.command(['git','archive','--format=tar',sha,'--',*names])
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
        for name,digest in manifest['deployment']['files'].items():
            if hash_path(directory,name) != digest:
                raise ValueError('retained deployment hash mismatch: '+name)
        return directory

    def published(self,version):
        parse_version(version)
        manifest=validate_manifest(get_json('https://github.com/future-agi/future-agi/releases/download/'+version+'/'+MANIFEST_ASSET))
        if manifest['version'] != version:
            raise ValueError('published release version differs')
        # A test-key signature below authenticates these frozen inputs locally.
        # It grants NO compatibility; each successor still needs real qualify.
        manifest=apply_release_policy(copy.deepcopy(manifest),SimpleNamespace(schema_version=2))
        return manifest,self.retained(manifest)

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
            candidates[version],directory=self.published(version)
            deployments[version]=str(directory)
        if mode == 'upgrade':
            target=self.build_pr(candidates[versions[0]],registry)
            versions.append(target['version']); candidates[target['version']]=target
            deployments[target['version']]=str(ROOT)
        config=self.out/'inputs.json'
        config.write_bytes(canonical_json(dict(versions=versions,candidates=candidates,deployment_dirs=deployments,
            base=str(self.out/'acceptance'),run_id=int(os.environ['GITHUB_RUN_ID']))))
        config.chmod(0o600)
        env=dict(os.environ,FUTURE_AGI_UPDATE_E2E='1',FAGI_UPDATE_ACCEPTANCE_INPUTS=str(config))
        with (self.out/'test-output.log').open('wb') as output:
            result=subprocess.run([sys.executable,'-I','-m','unittest','discover','-s','deploy/tests',
                '-p','test_update_e2e_automatic.py','-v'],cwd=ROOT,env=env,stdout=output,stderr=subprocess.STDOUT,timeout=21000)
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
