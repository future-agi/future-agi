"""Stdlib orchestration/source-contract tests, NOT Django/PostgreSQL qualification.

Container programs are compiled/inspected, never executed on the host. The fake
models committed chunk boundaries only; it cannot establish SQL correctness.
"""
import ast
import copy
import json
from types import SimpleNamespace
import unittest
from unittest import mock

from scripts import rehearse_update_compatibility as p
from scripts import update_rehearsal_shipping as s
from scripts import update_rehearsal_qualification as q


TABLES = ('tracer_trace_error_group', 'tracer_error_cluster_traces', 'tracer_eval_logger')
ENTRIES = tuple(row for row in p.load_high_volume_registry() if row['database']=='postgres' and row['table'] in TABLES)


class FakeApp:
    def __init__(self, scale=1):
        self.scale = scale
        self.plan = s.error_feed_reference_plan(scale)
        self.baseline = dict(zip(TABLES, (7, 11, 13)))
        self.committed = set()
        self.calls = []
        self.skipped = []
        self.fail_at = None
        self.alter_verified = lambda value: value
        self.alter_prepared = lambda value: value
        self.context = dict(project_id='fixture-project', configs={'span':'bool', 'trace':'float', 'session':'session'},
                            tasks={}, config_hashes={}, historical_at='2026-01-01T00:00:00+00:00')

    def __call__(self, program, args):
        self.calls.append((program, copy.deepcopy(args)))
        if program == s.ERROR_FEED_REFERENCE_PREPARE:
            # Abstract physical counts for resume tests, not simulated ORM rows.
            seeded = {table: len(self.committed) * 5000 for table in TABLES}
            return self.alter_prepared(dict(context=copy.deepcopy(self.context), seeded_counts=seeded,
                counts={table: self.baseline[table]+seeded[table] for table in TABLES}))
        if program == s.ERROR_FEED_REFERENCE_CHUNK:
            self.assert_slice(args)
            if args['start'] == self.fail_at:
                self.fail_at = None
                raise RuntimeError('synthetic chunk failure')
            skipped = args['start'] in self.committed
            if skipped:
                self.skipped.append(args['start'])
            self.committed.add(args['start'])
            return {'skipped':skipped}
        if program == s.ERROR_FEED_REFERENCE_VERIFY:
            assert self.committed == set(range(0, 100000*self.scale, 5000)), 'premature verification'
            return self.alter_verified(dict(counts={table:self.baseline[table]+100000*self.scale for table in TABLES},
                seeded_counts=copy.deepcopy(self.plan['counts']), cohorts=copy.deepcopy(self.plan['cohorts'])))
        raise AssertionError('unexpected container operation')

    def assert_slice(self, args):
        assert args['scale'] == self.scale
        assert args['stop'] == args['start']+5000
        assert args['context'] == self.context


def driver_for(fake=None, cls=s.ShippingRehearsal, corpus=True):
    driver = object.__new__(cls)
    driver.witnesses = SimpleNamespace(namespaces={'source':dict(namespace='reference-source', headers={
        'X-Organization-Id':'fixture-org', 'X-Workspace-Id':'fixture-workspace',
        'Authorization':'Bearer DO-NOT-RETAIN', 'X-Api-Key':'SECRET-KEY'})})
    driver.app_python = fake if fake is not None else FakeApp()
    if corpus:
        driver.corpus = {}
    return driver


class ErrorFeedReferenceTests(unittest.TestCase):
    def test_dispatch_accepts_exact_registry_keys_and_qualification_inherits(self):
        self.assertEqual(len(ENTRIES), 3)
        self.assertEqual(s.ERROR_FEED_REFERENCE_MODELS, {
            ('tracer','EvalLogger'):'tracer_eval_logger',
            ('tracer','ErrorClusterTraces'):'tracer_error_cluster_traces',
            ('tracer','TraceErrorGroup'):'tracer_trace_error_group'})
        for cls in (s.ShippingRehearsal, q.QualificationRehearsal):
            for entry in ENTRIES:
                with self.subTest(cls=cls.__name__, table=entry['table']):
                    driver = driver_for(cls=cls)
                    driver.seed_error_feed_reference_family = mock.Mock()
                    driver.app_python = mock.Mock()
                    driver.seed_reference_table(entry, 100000)
                    driver.seed_error_feed_reference_family.assert_called_once_with(100000)
                    driver.app_python.assert_not_called()

    def test_dispatch_refuses_engine_app_model_and_table_mismatches(self):
        driver = driver_for()
        driver.app_python = mock.Mock()
        driver.seed_error_feed_reference_family = mock.Mock()
        for entry in ENTRIES:
            bad = [dict(entry, **{key:value}) for key,value in (
                ('database','clickhouse'), ('database','sqlite'), ('app_label','unknown'),
                ('model','Unknown'), ('table','public.'+entry['table']), ('table','unreviewed'))]
            bad += [dict(entry, table=other['table']) for other in ENTRIES if other != entry]
            bad += [dict(entry, app_label='prism', model='AgentccShadowResult')]
            for mismatch in bad:
                with self.subTest(entry=mismatch), self.assertRaisesRegex(p.ManualRelease, 'high_volume_reference_unavailable'):
                    driver.seed_reference_table(mismatch, 100000)
        driver.app_python.assert_not_called()
        driver.seed_error_feed_reference_family.assert_not_called()

    def test_append_only_dispatch_remains_narrow_after_relational_usage_dispatch(self):
        driver = driver_for()
        driver.app_python = mock.Mock()
        entry = dict(database='postgres',app_label='prism',model='AgentccShadowResult',table='agentcc_shadow_result')
        driver.seed_reference_table(entry, 100000)
        driver.app_python.assert_called_once_with(s.REFERENCE_SEED, dict(entry,rows=100000))
        self.assertEqual(s.APPEND_ONLY_MODELS, frozenset({('prism','AgentccRequestLog'),
            ('prism','AgentccShadowResult'),('prism','AgentccWebhookEvent')}))
        with self.assertRaises(p.ManualRelease):
            driver.seed_reference_table(dict(database='postgres',app_label='usage',
                model='APICallLog',table='usage_api_call_log'),100000)

    def test_cohort_arithmetic_and_scaling(self):
        self.assertEqual(tuple(sum(row[i] for row in s.ERROR_FEED_REFERENCE_COHORTS.values()) for i in range(5)),
                         (100000,100000,100000,41000,43000))
        self.assertEqual(s.ERROR_FEED_REFERENCE_COHORTS['D'], (3000,4000,4000,0,1000))
        self.assertEqual(s.ERROR_FEED_REFERENCE_COHORTS['C'], (100,5000,5000,1000,1500))
        for scale in (1,2,17,1000):
            plan = s.error_feed_reference_plan(scale)
            self.assertEqual(plan['counts'], {table:100000*scale for table in TABLES})
            self.assertEqual(plan['cohorts']['stale_memberships'], 41000*scale)
            self.assertEqual(plan['cohorts']['deleted_eval_logs'], 43000*scale)
            self.assertEqual(plan['cohorts']['live_eval_groups'], 98500*scale)
            self.assertEqual(plan['cohorts']['owned_empty_groups'], 2400*scale)
            self.assertEqual(plan['cohorts']['unowned_empty_groups'], 21000*scale)

    def test_programs_compile_and_share_the_checked_cohort_plan_without_execution(self):
        for name in ('ERROR_FEED_REFERENCE_PREPARE','ERROR_FEED_REFERENCE_CHUNK','ERROR_FEED_REFERENCE_VERIFY'):
            with self.subTest(program=name):
                program = getattr(s, name)
                self.assertTrue(program.startswith(s.DJANGO_PREFIX))
                compile(program, '<container-only>', 'exec')
                tree = ast.parse(program)
                # No conflict suppression or destructive ORM calls in fixtures.
                self.assertFalse(any(isinstance(node, ast.keyword) and node.arg=='ignore_conflicts' for node in ast.walk(tree)))
                self.assertFalse(any(isinstance(node, ast.Attribute) and node.attr=='delete' for node in ast.walk(tree)))
        tree = ast.parse(s.ERROR_FEED_REFERENCE_CHUNK)
        cohorts = next(node.value for node in tree.body if isinstance(node, ast.Assign)
                       and any(isinstance(t,ast.Name) and t.id=='COHORTS' for t in node.targets))
        self.assertEqual(ast.literal_eval(cohorts), s.ERROR_FEED_REFERENCE_COHORTS)
        self.assertIn("assert existing in (0,len(group_ids))", s.ERROR_FEED_REFERENCE_CHUNK)
        self.assertIn('batch_size=1000', s.ERROR_FEED_REFERENCE_CHUNK)
        self.assertIn('db_constraint=False and 0119 never joins', s.ERROR_FEED_REFERENCE_CHUNK)

    def test_first_callback_completes_family_before_each_independent_measurement(self):
        fake = FakeApp()
        driver = driver_for(fake, cls=q.QualificationRehearsal)
        measurements = []
        def measure(entry):
            self.assertEqual(len(fake.committed), 20)
            self.assertIsNotNone(getattr(driver,'_error_feed_reference',None))
            self.assertEqual(fake.calls[-1][0], s.ERROR_FEED_REFERENCE_VERIFY)
            measurements.append((entry['table'],len(fake.calls)))
            return dict(engine='postgres',table='public.'+entry['table'],rows=100000+fake.baseline[entry['table']],bytes=123456)
        result = p.seed_reference_tables(ENTRIES, driver.seed_reference_table, measure)
        self.assertEqual(len(result), 3)
        self.assertEqual([length for table,length in measurements], [22,23,24])
        self.assertEqual([program for program,args in fake.calls],
            [s.ERROR_FEED_REFERENCE_PREPARE]+[s.ERROR_FEED_REFERENCE_CHUNK]*20+[s.ERROR_FEED_REFERENCE_VERIFY]*3)
        chunks = [args for program,args in fake.calls if program==s.ERROR_FEED_REFERENCE_CHUNK]
        self.assertEqual([args['start'] for args in chunks], list(range(0,100000,5000)))
        self.assertEqual([args['stop'] for args in chunks], list(range(5000,100001,5000)))
        self.assertEqual(fake.calls[0][1], dict(namespace='reference-source',organization_id='fixture-org',workspace_id='fixture-workspace'))
        evidence = driver.corpus['reference_seed_operations']
        self.assertEqual(len(evidence), 1)
        self.assertEqual(set(evidence[0]), {'table','operation','version','requested_rows','scale','chunks','chunk_seconds','counts','cohorts'})
        self.assertEqual((evidence[0]['table'],evidence[0]['operation'],evidence[0]['version']),
                         ('tracer_error_feed_family','error_feed_reference_fixture',1))
        self.assertEqual((evidence[0]['chunks'],len(evidence[0]['chunk_seconds'])), (20,20))
        self.assertTrue(all(value>=0 for value in evidence[0]['chunk_seconds']))
        self.assertEqual(evidence[0]['cohorts'], s.error_feed_reference_plan(1)['cohorts'])
        for secret in ('DO-NOT-RETAIN','SECRET-KEY','Authorization','X-Api-Key'):
            self.assertNotIn(secret,json.dumps(evidence))
            self.assertNotIn(secret,json.dumps([args for program,args in fake.calls]))

    def test_chunk_failure_does_not_mark_complete_and_retry_resumes_without_prepare(self):
        fake = FakeApp()
        fake.fail_at = 10000
        driver = driver_for(fake)
        with self.assertRaisesRegex(RuntimeError, 'synthetic chunk failure'):
            driver.seed_reference_table(ENTRIES[0],100000)
        self.assertFalse(hasattr(driver,'_error_feed_reference'))
        self.assertNotIn('reference_seed_operations',driver.corpus)
        self.assertEqual(fake.committed, {0,5000})
        driver.seed_reference_table(ENTRIES[1],100000)
        self.assertEqual(fake.skipped, [0,5000])
        self.assertEqual(sum(program==s.ERROR_FEED_REFERENCE_PREPARE for program,args in fake.calls), 1)
        self.assertTrue(hasattr(driver,'_error_feed_reference'))
        self.assertFalse(hasattr(driver,'_error_feed_reference_pending'))
        self.assertEqual(driver.corpus['reference_seed_operations'][0]['chunks'],20)

    def test_new_instance_resumes_committed_chunks_using_physical_minus_seeded_baseline(self):
        fake = FakeApp()
        fake.fail_at = 15000
        with self.assertRaises(RuntimeError):
            driver_for(fake).seed_reference_table(ENTRIES[0],100000)
        driver = driver_for(fake)
        driver.seed_reference_table(ENTRIES[0],100000)
        self.assertEqual(fake.skipped,[0,5000,10000])
        self.assertEqual(driver._error_feed_reference['baseline'],fake.baseline)
        self.assertEqual(sum(program==s.ERROR_FEED_REFERENCE_PREPARE for program,args in fake.calls),2)

    def test_changed_rows_refuse_even_same_scale_or_after_partial_failure(self):
        for partial in (False,True):
            fake = FakeApp()
            driver = driver_for(fake)
            if partial:
                fake.fail_at=5000
                with self.assertRaises(RuntimeError): driver.seed_error_feed_reference_family(100000)
            else:
                driver.seed_error_feed_reference_family(100000)
            called=len(fake.calls)
            for rows in (100001,200000):
                with self.subTest(partial=partial,rows=rows), self.assertRaises(p.ManualRelease):
                    driver.seed_error_feed_reference_family(rows)
            self.assertEqual(len(fake.calls),called)

    def test_rounds_up_scale_and_supports_no_corpus(self):
        for rows,scale in ((100000,1),(100001,2),(200000,2)):
            with self.subTest(rows=rows):
                fake = FakeApp(scale)
                driver = driver_for(fake,corpus=False)
                driver.seed_error_feed_reference_family(rows)
                self.assertEqual(len(fake.committed),20*scale)
                self.assertEqual(driver._error_feed_reference['rows'],rows)
                self.assertFalse(hasattr(driver,'corpus'))

    def test_missing_witness_context_and_invalid_rows_refuse_without_app_calls(self):
        for source in (None,{}, {'namespace':'test'}, {'namespace':'test','headers':{}},
                       {'namespace':'test','headers':{'X-Organization-Id':'org'}}):
            driver = driver_for()
            driver.app_python = mock.Mock()
            driver.witnesses.namespaces = {} if source is None else {'source':source}
            with self.subTest(source=source), self.assertRaises(p.ManualRelease):
                driver.seed_error_feed_reference_family(100000)
            driver.app_python.assert_not_called()
        driver = driver_for()
        del driver.witnesses
        with self.assertRaises(p.ManualRelease): driver.seed_error_feed_reference_family(100000)
        for rows in (True,99999,100000001,100000.0,'100000'):
            driver=driver_for()
            driver.app_python=mock.Mock()
            with self.subTest(rows=rows), self.assertRaises(p.ManualRelease): driver.seed_error_feed_reference_family(rows)
            driver.app_python.assert_not_called()

    def test_bad_verified_counts_and_cohorts_never_mark_success(self):
        mutations = [lambda value:None,
            lambda value:dict(value, seeded_counts={}),
            lambda value:dict(value, counts={}),
            lambda value:dict(value, counts={table:99999 for table in TABLES}),
            # Fewer physical rows than the baseline plus the whole fixture.
            lambda value:dict(value, counts={table:99999+offset for table,offset in FakeApp().baseline.items()}),
            lambda value:dict(value, cohorts=dict(value['cohorts'],stale_memberships=40999)),
            lambda value:dict(value, cohorts=dict(value['cohorts'],owned_empty_groups=0)),
            lambda value:dict(value, seeded_counts={table:100000.0 for table in TABLES})]
        for alter in mutations:
            fake=FakeApp()
            fake.alter_verified=alter
            driver=driver_for(fake)
            with self.subTest(alter=alter), self.assertRaises(p.ManualRelease): driver.seed_error_feed_reference_family(100000)
            self.assertFalse(hasattr(driver,'_error_feed_reference'))
            self.assertNotIn('reference_seed_operations',driver.corpus)

    def test_reverification_tolerates_product_growth_but_detects_fixture_change(self):
        # The running product may add rows (onboarding demo results); each table's
        # reference is its own independent measurement. The fixture must not change.
        fake=FakeApp()
        driver=driver_for(fake)
        driver.seed_reference_table(ENTRIES[0],100000)
        fake.alter_verified=lambda value:dict(value,counts=dict(value['counts'],tracer_eval_logger=200000))
        before=len(fake.calls)
        driver.seed_reference_table(ENTRIES[1],100000)
        self.assertEqual([program for program,args in fake.calls[before:]], [s.ERROR_FEED_REFERENCE_VERIFY])
        fake.alter_verified=lambda value:dict(value,cohorts=dict(value['cohorts'],stale_memberships=40999))
        before=len(fake.calls)
        with self.assertRaises(p.ManualRelease): driver.seed_reference_table(ENTRIES[2],100000)
        self.assertEqual([program for program,args in fake.calls[before:]], [s.ERROR_FEED_REFERENCE_VERIFY])
        self.assertEqual(len(driver.corpus['reference_seed_operations']),1)

    def test_verification_failure_retries_all_chunks_as_skips_without_more_rows(self):
        fake=FakeApp()
        driver=driver_for(fake)
        fake.alter_verified=lambda value:dict(value,cohorts={})
        with self.assertRaises(p.ManualRelease): driver.seed_error_feed_reference_family(100000)
        self.assertFalse(hasattr(driver,'_error_feed_reference'))
        self.assertEqual(len(fake.committed),20)
        fake.alter_verified=lambda value:value
        driver.seed_error_feed_reference_family(100000)
        self.assertEqual(fake.skipped,list(range(0,100000,5000)))
        self.assertEqual(len(fake.committed),20)
        self.assertEqual(sum(program==s.ERROR_FEED_REFERENCE_PREPARE for program,args in fake.calls),1)
        self.assertEqual(len(driver.corpus['reference_seed_operations']),1)

    def test_changed_witness_refuses_before_reverification(self):
        fake=FakeApp()
        driver=driver_for(fake)
        driver.seed_error_feed_reference_family(100000)
        calls=len(fake.calls)
        driver.witnesses.namespaces['source']['headers']['X-Workspace-Id']='another-workspace'
        with self.assertRaises(p.ManualRelease): driver.seed_error_feed_reference_family(100000)
        self.assertEqual(len(fake.calls),calls)
        driver.witnesses.namespaces=None
        with self.assertRaises(p.ManualRelease): driver.seed_error_feed_reference_family(100000)
        self.assertEqual(len(fake.calls),calls)

    def test_invalid_prepare_inventory_refuses_before_chunks(self):
        for mutate in (lambda value:None,lambda value:dict(value,counts={}),
                lambda value:dict(value,seeded_counts={table:100001 for table in TABLES}),
                lambda value:dict(value,seeded_counts={table:-1 for table in TABLES}),
                lambda value:dict(value,seeded_counts={table:False for table in TABLES})):
            fake=FakeApp()
            fake.alter_prepared=mutate
            driver=driver_for(fake)
            with self.subTest(mutate=mutate), self.assertRaises(p.ManualRelease):
                driver.seed_error_feed_reference_family(100000)
            self.assertEqual([program for program,args in fake.calls],[s.ERROR_FEED_REFERENCE_PREPARE])
            self.assertFalse(hasattr(driver,'_error_feed_reference_pending'))
            self.assertFalse(hasattr(driver,'_error_feed_reference'))

    def test_verify_stale_predicate_is_exactly_0119_with_separate_namespace_scope(self):
        tree=ast.parse(s.ERROR_FEED_REFERENCE_VERIFY)
        stale=next(node.value for node in tree.body if isinstance(node,ast.Assign)
            and any(isinstance(target,ast.Name) and target.id=='stale' for target in node.targets))
        self.assertEqual({kw.arg:ast.literal_eval(kw.value) for kw in stale.keywords},dict(
            deleted=False,trace_id__isnull=False,eval_logger__deleted=True,cluster__source='eval',cluster__deleted=False))
        self.assertEqual(stale.func.value.id,'members')
        self.assertIn("cluster_id__startswith='QR'",s.ERROR_FEED_REFERENCE_VERIFY)
        self.assertIn("custom_eval_config__project_id=context['project_id']",s.ERROR_FEED_REFERENCE_VERIFY)
        self.assertIn("SELECT count(*) FROM ",s.ERROR_FEED_REFERENCE_VERIFY)

    def test_prepare_failure_leaves_no_pending_or_completed_mark(self):
        driver=driver_for()
        driver.app_python=mock.Mock(side_effect=RuntimeError('prepare failed'))
        with self.assertRaises(RuntimeError): driver.seed_error_feed_reference_family(100000)
        self.assertFalse(hasattr(driver,'_error_feed_reference_pending'))
        self.assertFalse(hasattr(driver,'_error_feed_reference'))


if __name__ == '__main__':
    unittest.main()
