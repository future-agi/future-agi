"""Verifier-equipped HTTPS transport lane; NOT product image qualification.

Run explicitly with host cosign and openssl available. The reusable proof
workflow provisions the pinned verifier and runs this module on PRs. Update CI
also provisions that verifier for its signed component integration fixtures.
No feature skips or fake verifier fallback are used here.
"""
import json
import os
from pathlib import Path
import shutil
import ssl
import tempfile
import unittest
import urllib.request

from deploy.tests.test_update_automatic_release import default_v2, fixture, producer, versioned
from deploy.tests.update_e2e.automatic_publisher import AutomaticPublisher
from deploy.update.compatibility import PROOF_ASSET


class HTTPSAutomaticPublisherTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name).resolve()
        cosign = os.environ.get('FUTURE_AGI_TEST_COSIGN') or shutil.which('cosign')
        if not cosign:
            raise RuntimeError('real host cosign required; no synthetic verifier fallback')
        a = versioned(default_v2(),'v1.51.0')
        _,_,_,b,bproof = fixture(cls.root/'b',previous=a)
        _,_,_,c,cproof = fixture(cls.root/'c',previous=b,version='v1.53.0',execution=a['deployment'])
        cls.a,cls.b,cls.c = a,b,c
        cls.fixtures = {a['version']:{'manifest':a,'proof':None,'purpose':'SYNTHETIC-TRANSPORT-ONLY'},
                        b['version']:{'manifest':b,'proof':bproof,'purpose':'SYNTHETIC-TRANSPORT-ONLY'},
                        c['version']:{'manifest':c,'proof':cproof,'purpose':'SYNTHETIC-TRANSPORT-ONLY'}}
        cls.publisher = AutomaticPublisher(cls.root/'publisher',lane='synthetic',versions=tuple(cls.fixtures),
            retained_layout=a['version'],synthetic_fixtures=cls.fixtures,cosign=cosign)
        cls.publisher.__enter__()
        for version in cls.fixtures:
            cls.publisher.publish(version)

    @classmethod
    def tearDownClass(cls):
        cls.publisher.__exit__(None,None,None)
        cls.temp.cleanup()

    def discovery(self):
        return self.publisher.discovery(self.root/('cache-'+os.urandom(8).hex()))

    def test_actual_TLS_CA_and_host_signature_verification_A_B_C(self):
        source,key = self.publisher.source,self.publisher.public_key.read_bytes()
        discovery = self.discovery()
        for manifest in (self.a,self.b,self.c):
            first = discovery.refresh(manifest['version'])
            self.assertTrue(first.complete,first.reason_codes)
            self.assertTrue(first.candidate.verification.verified)
            second = discovery.refresh(manifest['version'])
            self.assertTrue(second.complete,second.reason_codes)
            self.assertEqual(first.candidate.raw_manifest,second.candidate.raw_manifest)
            if manifest is self.a:
                self.assertFalse(second.candidate.release_proof)
            else:
                self.assertEqual(second.candidate.proof_reason_codes,())
        self.assertEqual(self.publisher.source,source)
        self.assertEqual(self.publisher.public_key.read_bytes(),key)

    def test_system_CA_does_not_trust_private_publisher(self):
        url = self.publisher.source+'/repos/'+producer.REPOSITORY+'/releases?per_page=100&page=1'
        with self.assertRaises(Exception):
            urllib.request.urlopen(url,context=ssl.create_default_context(),timeout=5)
        context = ssl.create_default_context(cafile=str(self.publisher.ca_file))
        with urllib.request.urlopen(url,context=context,timeout=5) as response:
            self.assertEqual(len(json.load(response)),3)

    def test_automatic_predecessor_selection_keeps_B_on_A_for_C(self):
        rows = producer.select_predecessors(self.c,self.discovery())
        selected = [row for row in rows if row.release.version==self.b['version'] and
                    row.execution_deployment==self.a['deployment']]
        self.assertEqual(len(selected),1)
        self.assertEqual(selected[0].layout_source_sha,self.a['source_sha'])
        self.assertEqual(selected[0].release.manifest['deployment'],self.b['deployment'])

    def test_published_bytes_are_immutable_and_synthetic_cannot_seed_product(self):
        before = dict(self.publisher._assets)
        self.publisher.publish(self.b['version'])
        self.assertEqual(self.publisher._assets,before)
        inputs = self.publisher.fixtures[self.b['version']]
        old = inputs['purpose']; inputs['purpose']='altered'
        try:
            with self.assertRaisesRegex(producer.RehearsalError,'immutable'):
                self.publisher.publish(self.b['version'])
        finally:
            inputs['purpose']=old
        with self.assertRaises(producer.RehearsalError):
            self.publisher.seed_and_check(None)

    def test_modified_server_proof_is_not_authorized(self):
        prefix = '/'+producer.REPOSITORY+'/releases/download/'+self.c['version']+'/'
        path = prefix+PROOF_ASSET
        original = self.publisher._assets[path]
        try:
            self.publisher._assets[path] = original+b' '
            result = self.discovery().refresh(self.c['version'])
            self.assertTrue(result.candidate.verification.verified)
            self.assertFalse(result.candidate.release_proof)
            self.assertTrue(result.candidate.proof_reason_codes)
        finally:
            self.publisher._assets[path] = original

    def test_modified_signed_predecessor_is_not_selected(self):
        path = '/'+producer.REPOSITORY+'/releases/download/'+self.a['version']+'/future-agi-update-manifest.json'
        original = self.publisher._assets[path]
        try:
            self.publisher._assets[path] = original+b' '
            with self.assertRaises(producer.RehearsalError):
                producer.select_predecessors(self.c,self.discovery())
        finally:
            self.publisher._assets[path] = original

    def test_beta_assets_are_also_immutable_with_one_CA_and_key(self):
        a = versioned(default_v2(),'v1.52.0-beta.1')
        _,_,_,b,proof = fixture(self.root/'beta-evidence',previous=a,version='v1.52.0-beta.2')
        fixtures = {a['version']:{'manifest':a,'proof':None,'purpose':'SYNTHETIC-TRANSPORT-ONLY'},
                    b['version']:{'manifest':b,'proof':proof,'purpose':'SYNTHETIC-TRANSPORT-ONLY'}}
        with AutomaticPublisher(self.root/'beta-publisher',lane='synthetic',versions=tuple(fixtures),
                retained_layout=a['version'],synthetic_fixtures=fixtures,cosign=self.publisher.cosign) as publisher:
            source,key = publisher.source,publisher.public_key.read_bytes()
            publisher.publish(a['version']); publisher.publish(b['version'])
            result = publisher.discovery(self.root/'beta-cache').discover(current=a['version'],channel='beta')
            self.assertTrue(result.complete,result.reason_codes)
            self.assertEqual(result.candidate.version,b['version'])
            before = dict(publisher._assets)
            publisher.publish(b['version'])
            self.assertEqual(publisher._assets,before)
            publisher.fixtures[b['version']]['manifest']['published_at']='2026-10-21T00:00:00Z'
            with self.assertRaisesRegex(producer.RehearsalError,'immutable'):
                publisher.publish(b['version'])
            self.assertEqual((publisher.source,publisher.public_key.read_bytes()),(source,key))

    def test_prepare_is_invisible_and_publish_only_changes_visibility(self):
        a = versioned(default_v2(),'v1.51.0')
        fixtures = {a['version']:{'manifest':a,'proof':None,'purpose':'SYNTHETIC-TRANSPORT-ONLY'}}
        directory=self.root/('prepare-only-'+os.urandom(8).hex())
        with AutomaticPublisher(directory,lane='synthetic',versions=tuple(fixtures),
                retained_layout=a['version'],synthetic_fixtures=fixtures,cosign=self.publisher.cosign) as publisher:
            publisher.prepare(a['version'])
            self.assertEqual(publisher._assets,{})
            self.assertEqual(json.loads(publisher.response('/repos/'+producer.REPOSITORY+'/releases?per_page=100&page=1')),[])
            files={str(p.relative_to(directory)):p.read_bytes() for p in directory.rglob('*') if p.is_file()}
            from unittest.mock import patch
            with patch.object(publisher,'_command',side_effect=AssertionError('publish must not sign/qualify')):
                publisher.publish(a['version'])
            self.assertEqual(files,{str(p.relative_to(directory)):p.read_bytes() for p in directory.rglob('*') if p.is_file()})
            result=publisher.discovery(self.root/('prepare-cache-'+os.urandom(8).hex())).refresh(a['version'])
            self.assertTrue(result.candidate.verification.verified)

    def test_independent_scenarios_reuse_immutable_packages_without_requalification(self):
        from unittest.mock import patch
        a = self.a
        fixtures = {a['version']: self.fixtures[a['version']]}
        directory = self.root/('scenario-reset-'+os.urandom(8).hex())
        with AutomaticPublisher(directory, lane='synthetic', versions=tuple(fixtures),
                retained_layout=a['version'], synthetic_fixtures=fixtures, cosign=self.publisher.cosign) as publisher:
            publisher.prepare(a['version'])
            publisher.publish(a['version'])
            prepared = dict(publisher._prepared)
            files = {str(p.relative_to(directory)): p.read_bytes() for p in directory.rglob('*') if p.is_file()}
            with patch.object(publisher, '_command', side_effect=AssertionError('visibility must not sign or qualify')):
                publisher.hide_releases()
                self.assertEqual(publisher._records, {})
                self.assertEqual(publisher._assets, {})
                self.assertEqual(publisher._published, {})
                publisher.publish(a['version'])
            self.assertEqual(publisher._prepared, prepared)
            self.assertEqual(files, {str(p.relative_to(directory)): p.read_bytes()
                                   for p in directory.rglob('*') if p.is_file()})
            self.assertEqual(set(publisher._records), {a['version']})

    def test_product_visibility_cannot_qualify_or_start_a_fixture(self):
        directory=self.root/('product-unprepared-'+os.urandom(8).hex())
        with AutomaticPublisher(directory,lane='product',versions=(self.a['version'],),
                retained_layout=self.a['version'],candidates={self.a['version']:self.a},
                cosign=self.publisher.cosign) as publisher:
            from unittest.mock import patch
            with patch.object(publisher,'_command',side_effect=AssertionError('publish must not run commands')):
                with self.assertRaisesRegex(producer.RehearsalError,'qualify before'):
                    publisher.publish(self.a['version'])
            self.assertEqual(publisher._assets,{})
            with self.assertRaisesRegex(producer.RehearsalError,'ProductHarness'):
                publisher.new_checkout('not-a-fake')

    def test_product_qualify_uses_fixed_entrypoint_and_refusal_never_publishes(self):
        from unittest.mock import patch
        directory=self.root/('product-negative-'+os.urandom(8).hex()); directory.mkdir()
        publisher=AutomaticPublisher(directory,lane='product',versions=(self.a['version'],self.b['version']),
            retained_layout=self.a['version'],candidates={self.a['version']:self.a,self.b['version']:self.b},
            deployment_dirs={self.a['version']:self.root},cosign=self.publisher.cosign)
        publisher._server=object()  # no network operation is reachable in this negative test
        prefix='/'+producer.REPOSITORY+'/releases/download/'+self.a['version']+'/'
        publisher._prepared[self.a['version']]=dict(inputs=b'negative-control',manifest=self.a,
            assets={name:self.publisher._assets[prefix+name] for name in ('future-agi-update-manifest.json',
                                                                        'future-agi-update-manifest.sigstore.json')})
        with patch('scripts.update_rehearsal_shipping.copy_retained_files') as copy_files, \
             patch.object(publisher,'_command',side_effect=RuntimeError('producer exit 2')) as command:
            with self.assertRaisesRegex(RuntimeError,'producer exit 2'):
                publisher.prepare(self.b['version'])
        copy_files.assert_called_once()
        argv=[str(a) for a in command.call_args.args[0]]
        self.assertEqual(argv[:4],[__import__('sys').executable,'-I',str(producer.ROOT/'scripts/rehearse_update_compatibility.py'),'qualify'])
        self.assertEqual(argv[argv.index('--reference-scale')+1],'registered-100k')
        self.assertEqual(argv[argv.index('--boot-timeout')+1],'4200')
        self.assertIn('--public-key',argv)
        self.assertIn('--deployment-dir',argv)
        self.assertEqual(command.call_count,1,'package/sign cannot follow qualification refusal')
        self.assertNotIn(self.b['version'],publisher._prepared)
        self.assertEqual(publisher._assets,{})
        self.assertEqual(publisher._records,{})

    def test_product_lane_refuses_arbitrary_success_fixture(self):
        with self.assertRaises(producer.RehearsalError):
            AutomaticPublisher(self.root/'bad',lane='product',versions=tuple(self.fixtures),
                               retained_layout=self.a['version'],synthetic_fixtures=self.fixtures)


if __name__ == '__main__':
    unittest.main()
