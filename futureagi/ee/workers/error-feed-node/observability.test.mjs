import test from 'node:test';
import assert from 'node:assert/strict';
import {configureObservability, observe, claimAttributes, spanAttributes, executionResult,
  startObservability, stopObservability, modelAttributes, spanContent, claimInput} from './observability.mjs';

function recorder(captureContent = false) {
  const spans = [];
  configureObservability({captureContent, rootContext: null, withParent: (_, span) => span,
    provider: {shutdown: async () => {}}, tracer: {startSpan(name, options, parent) {
      const span = {name, parent, attributes: {...options.attributes}, ended: false,
        setAttributes(value) {Object.assign(this.attributes, value);},
        setStatus(value) {this.status = value;}, end() {this.ended = true;}};
      spans.push(span); return span;
    }}});
  return spans;
}

test('concurrent attempts keep separate roots and nested model parents', async () => {
  const spans = recorder();
  try {
    await Promise.all(['a','b'].map(id => observe(id, 'AGENT', claimAttributes({organization_id:'org-' + id, project_id:'project-' + id}), async () => {
      await new Promise(resolve => setImmediate(resolve));
      await observe(id + '.model', 'LLM', {}, async () => spanAttributes({attempt: id}));
    }, {root: true})));
    for (const id of ['a','b']) {
      const root = spans.find(span => span.name === id);
      const child = spans.find(span => span.name === id + '.model');
      assert.equal(root.parent, null);
      assert.equal(child.parent, root);
      assert.equal(child.attributes.attempt, id);
      assert.equal(root.attributes['user.id'], 'org-' + id);
      assert.equal(child.attributes['user.id'], 'org-' + id);
    }
    assert.ok(spans.every(span => span.ended));
    assert.ok(spans.every(span => span.status.code === 1));
  } finally {await stopObservability();}
});

test('content capture is opt-in and preserves work results', async () => {
  for (const enabled of [false, true]) {
    const spans = recorder(enabled);
    let calls = 0;
    const result = {answer: 'actual result'};
    try {
      assert.equal(await observe('work', 'AGENT', {}, async () => {
        calls++;
        return result;
      }, {input: {prompt: 'actual input'}, output: true}), result);
      assert.equal(calls, 1);
      assert.equal(spans[0].attributes['input.value'], enabled ? '{"prompt":"actual input"}' : undefined);
      assert.equal(spans[0].attributes['output.value'], enabled ? '{"answer":"actual result"}' : undefined);
    } finally {await stopObservability();}
  }
});

test('captured inputs exclude control credentials and bound escaped unicode content', async () => {
  const spans = recorder(true);
  try {
    const claim = {organization_id: 'org', memory: {entries: ['actual memory']}, lease_token: 'private-lease'};
    assert.equal(claimInput(claim).lease_token, undefined);
    await observe('work', 'AGENT', {}, async () => {
      spanContent('input', {lease_token: 'private-lease', nested: {api_key: 'private-key'}, text: 'actual input'});
      spanContent('output', {text: '\\"😀'.repeat(40000)});
      const cyclic = {}; cyclic.self = cyclic;
      spanContent('output', cyclic);
    });
    const attrs = spans[0].attributes;
    assert.ok(!attrs['input.value'].includes('private-'));
    assert.equal(JSON.parse(attrs['input.value']).text, 'actual input');
    assert.ok(Buffer.byteLength(attrs['output.value']) <= 32768);
    assert.equal(JSON.parse(attrs['output.value']).truncated, true);
    assert.equal(attrs['error_feed.output.truncated'], true);
  } finally {await stopObservability();}
});

test('names and sessions propagate without replacing stable user identity', async () => {
  const spans = recorder();
  try {
    await observe('root', 'AGENT', claimAttributes({organization_id: 'org', organization_name: 'Acme',
      project_id: 'project', project_name: 'Support', job_id: 'job'}), async () => {
      await observe('child', 'LLM', {}, async () => {});
    }, {root: true});
    for (const span of spans) {
      assert.equal(span.attributes['user.id'], 'org');
      assert.equal(span.attributes['user.name'], 'Acme');
      assert.equal(span.attributes['error_feed.project_name'], 'Support');
      assert.equal(span.attributes['session.id'], 'error-feed:job');
    }
  } finally {await stopObservability();}
});

test('failures preserve original error and never capture its message or stack', async () => {
  const spans = recorder(), error = new Error('sensitive evidence and credentials');
  try {
    await assert.rejects(observe('attempt','AGENT',{},async () => {throw error;}), value => value === error);
    assert.deepEqual(spans[0].status, {code: 2, message: 'operation_failed'});
    assert.ok(!JSON.stringify(spans).includes(error.message));
    await observe('failed-report','AGENT',{},async () => executionResult({execution_status:'failed',outcome:'unknown'}));
    assert.equal(spans[1].status.code, 2);
  } finally {await stopObservability();}
});

test('customer failures are successful investigations; returned execution failures stay errors', async () => {
  const spans = recorder();
  try {
    await observe('completed-investigation', 'AGENT', {}, async () => executionResult({
      execution_status: 'completed', outcome: 'failure', findings: [{}],
    }));
    await observe('failed-investigation', 'AGENT', {}, async () => {
      await observe('successful-child', 'TOOL', {}, async () => 'evidence');
      return executionResult({execution_status: 'failed', outcome: 'unknown'});
    });
    assert.equal(spans.find(span => span.name === 'completed-investigation').status.code, 1);
    assert.equal(spans.find(span => span.name === 'failed-investigation').status.code, 2);
    assert.equal(spans.find(span => span.name === 'successful-child').status.code, 1);
  } finally {await stopObservability();}
});

test('disabled and broken telemetry execute work exactly once', async t => {
  const now = Date.now() + 60_000;
  t.mock.method(Date, 'now', () => now);
  const diagnostic = t.mock.method(process.stderr, 'write', () => true);
  for (const value of [undefined, {tracer:{startSpan(){throw new Error('broken');}}}]) {
    configureObservability(value);
    let count = 0;
    assert.equal(await observe('work','CHAIN',{},async () => ++count), 1);
    assert.equal(count, 1);
  }
  assert.equal(diagnostic.mock.callCount(), 1);
  assert.equal(diagnostic.mock.calls[0].arguments[0], '{"event":"omega_observability_unavailable"}\n');
  configureObservability(undefined);
  let loaded = false;
  await startObservability({}, async () => {loaded = true;});
  assert.equal(loaded, false);
});

test('claim and model attributes exclude leases, prompts and unknown costs', async () => {
  const spans = recorder();
  try {
    const attrs = claimAttributes({attempt_id:'attempt',lease_token:'secret',snapshot:{text:'customer'}});
    assert.deepEqual(attrs, {'error_feed.attempt_id':'attempt'});
    await observe('model','LLM',attrs,async () => modelAttributes({requested_model:'model',
      cost_microusd:null,cost_status:'unknown',usage:{prompt_tokens:12,completion_tokens:3},
      raw:'sensitive'}));
    assert.equal(spans[0].attributes['gen_ai.usage.input_tokens'],12);
    assert.equal(spans[0].attributes['error_feed.gateway.cost_usd'],undefined);
    assert.equal(spans[0].attributes['gen_ai.cost.total'],undefined);
    assert.ok(!JSON.stringify(spans).includes('sensitive'));
  } finally {await stopObservability();}
});

test('Observe uses the routed model and exact gateway charge, including zero', async () => {
  const spans = recorder();
  try {
    for (const cost of [3526, 0]) {
      await observe('model', 'LLM', {}, async () => modelAttributes({
        requested_model: 'routing-alias', routed_model: 'resolved-model', provider: 'provider',
        usage: {prompt_tokens: 4361, completion_tokens: 68, total_tokens: 4429},
        cost_microusd: cost, cost_status: 'reported',
      }));
    }
    for (const [index, cost] of [0.003526, 0].entries()) {
      assert.equal(spans[index].attributes['gen_ai.cost.total'], cost);
      assert.equal(spans[index].attributes['llm.model_name'], 'resolved-model');
      assert.equal(spans[index].attributes['gen_ai.request.model'], 'routing-alias');
      assert.equal(spans[index].attributes['gen_ai.usage.total_tokens'], 4429);
    }
  } finally {await stopObservability();}
});

test('startup registers Observe with explicit headers and drains provider', async () => {
  let options, stopped = false;
  await startObservability({OMEGA_OBSERVABILITY_ENABLED:'true', FI_PROJECT_NAME:'internal',
    FI_API_KEY:'key',FI_SECRET_KEY:'secret',FI_BASE_URL:'http://localhost:4318'},async () => ({
      ProjectType:{OBSERVE:'observe'},ROOT_CONTEXT:null,trace:{setSpan:()=>null},
      register(value) {options=value;return {getTracer:()=>({}),shutdown:async()=>{stopped=true;}};},
    }));
  assert.equal(options.projectType,'observe');
  assert.equal(options.setGlobalTracerProvider,false);
  assert.deepEqual(options.headers,{'x-api-key':'key','x-secret-key':'secret'});
  await stopObservability();
  assert.equal(stopped,true);
});


test('organization user identity reaches grandchildren and resets for independent roots', async () => {
  const spans = recorder();
  try {
    await observe('investigation', 'AGENT', claimAttributes({organization_id:'org-a'}), async () => {
      await observe('agent', 'AGENT', {}, async () => {
        await observe('tool', 'TOOL', {}, async () => {
          await observe('model', 'LLM', {}, async () => {});
        });
      });
      await observe('other-root', 'CHAIN', claimAttributes({organization_id:'org-b'}), async () => {
        await observe('other-model', 'LLM', {}, async () => {});
      }, {root:true});
      await observe('unscoped-root', 'CHAIN', claimAttributes({}), async () => {
        await observe('unscoped-model', 'LLM', {}, async () => {});
      }, {root:true});
    }, {root:true});
    for (const span of spans) {
      const expected = span.name.startsWith('other-') ? 'org-b'
        : span.name.startsWith('unscoped-') ? undefined : 'org-a';
      assert.equal(span.attributes['user.id'], expected, span.name);
    }
    for (const organization_id of [null, undefined, '', '   ']) {
      assert.equal(claimAttributes({organization_id})['user.id'], undefined);
    }
  } finally {await stopObservability();}
});
