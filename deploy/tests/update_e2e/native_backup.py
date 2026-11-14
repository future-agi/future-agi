"""Discovery adapter for native vendor fixtures, NOT product qualification.

Only backup_ci.py selects this non-default discovery pattern. The original
fixture assertions/crash matrices are unchanged; its explicit opt-in is set by
that entrypoint, so a green CI job cannot mean zero tests silently skipped.
"""
import json
import os
from pathlib import Path
import re
import sys
import unittest

ROOT=Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0,str(ROOT))
from deploy.tests.update_e2e.real_automatic import home_path, native_ci

if not native_ci() or os.environ.get('FAGI_UPDATE_BACKUP_E2E') != '1':
    raise RuntimeError('native backup entrypoint and explicit storage-fixture opt-in required')

pins=home_path(Path(os.environ['FAGI_UPDATE_NATIVE_BACKUP_IMAGES']))
images=json.loads(pins.read_bytes())
if set(images) != {'app','postgres','clickhouse','busybox'} or not all(
        isinstance(v,str) and re.fullmatch(r'sha256:[0-9a-f]{64}',v) for v in images.values()):
    raise ValueError('invalid measured native fixture image IDs')

from deploy.tests import test_update_e2e_backup as fixture
fixture.IMAGES={k:v for k,v in images.items() if k != 'busybox'}
fixture.BUSYBOX=images['busybox']

# unittest discovery sees the existing two complete test methods. No assertion
# is copied, overridden, skipped, reduced to a mock or marked expectedFailure.
DedicatedBackupRestoreTests=fixture.DedicatedBackupRestoreTests
if getattr(DedicatedBackupRestoreTests,'__unittest_skip__',False) or len(
        unittest.defaultTestLoader.getTestCaseNames(DedicatedBackupRestoreTests)) < 2:
    raise RuntimeError('native backup discovery must execute both existing storage/crash matrices, not skip')
