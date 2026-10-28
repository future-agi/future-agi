#!/usr/bin/env python3
"""Native runner preparation for existing small vendor-storage restore tests.

Not product qualification. The existing tests run serially through unittest
DISCOVER, with their actual opt-in variable set (not FUTURE_AGI_UPDATE_E2E).
No arm64 developer image IDs are assumed on an empty amd64 Actions runner.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0,str(ROOT))
from deploy.tests.update_e2e.real_automatic import CONTEXT, home_path, native_ci

VENDOR_IMAGES={'postgres':'postgres:16','clickhouse':'clickhouse/clickhouse-server:25.3-alpine',
               'app':'python:3.11-bookworm','busybox':'busybox:1.37'}


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',type=Path)
    parser.add_argument('--plan',action='store_true')
    args=parser.parse_args(argv)
    if args.plan:
        print(json.dumps(dict(context=CONTEXT,platform='linux/amd64',product_qualification=False,
            images=VENDOR_IMAGES,opt_in='FAGI_UPDATE_BACKUP_E2E=1',
            command=['python','-m','unittest','discover','-s','deploy/tests/update_e2e','-p','native_backup.py','-v']),indent=2))
        return 0
    if not native_ci() or args.out is None:
        parser.error('native Linux Actions only; dedicated Mac fixture entrypoint is unchanged')
    out=home_path(args.out); out.mkdir(mode=0o700)
    images={}
    for service,ref in VENDOR_IMAGES.items():
        subprocess.run(['docker','--context',CONTEXT,'pull','--platform','linux/amd64',ref],check=True)
        raw=subprocess.check_output(['docker','--context',CONTEXT,'image','inspect',ref])
        image=json.loads(raw)[0]
        if image['Architecture'] != 'amd64' or image['Os'] != 'linux' or not image.get('RepoDigests'):
            raise ValueError('native vendor fixture image required')
        images[service]=image['Id']
    pins=out/'fixture-images.json'; pins.write_text(json.dumps(images)); pins.chmod(0o600)
    env=dict(os.environ,FAGI_UPDATE_BACKUP_E2E='1',FAGI_UPDATE_BACKUP_EVIDENCE=str(out),
             FAGI_UPDATE_NATIVE_BACKUP_IMAGES=str(pins))
    return subprocess.call([sys.executable,'-m','unittest','discover','-s','deploy/tests/update_e2e',
                            '-p','native_backup.py','-v'],cwd=ROOT,env=env)


if __name__ == '__main__':
    raise SystemExit(main())
