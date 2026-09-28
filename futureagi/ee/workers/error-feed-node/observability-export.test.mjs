import test from 'node:test';
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {startObservability, stopObservability, observe, modelAttributes, claimAttributes, executionResult} from './observability.mjs';

test('pinned traceAI exports nested spans to an OTLP receiver and flushes on shutdown', async () => {
  const requests = [];
  const server = createServer(async (req,res) => {
    const chunks = [];
    for await (const chunk of req) chunks.push(chunk);
    requests.push({headers:req.headers,path:req.url,body:Buffer.concat(chunks)});
    res.writeHead(200, {'content-type':'application/json'});res.end('{}');
  });
  await new Promise(resolve => server.listen(0,'127.0.0.1',resolve));
  try {
    await startObservability({OMEGA_OBSERVABILITY_ENABLED:'true',FI_PROJECT_NAME:'error-feed-test',
      OMEGA_OBSERVABILITY_CAPTURE_CONTENT:'true',
      FI_API_KEY:'test-key',FI_SECRET_KEY:'test-secret',FI_BASE_URL:`http://127.0.0.1:${server.address().port}`});
    await observe('error_feed.investigation','AGENT',claimAttributes({attempt_id:'test-attempt',organization_id:'test-org',organization_name:'Test Org',project_id:'source-project',project_name:'Source Project'}),async () => {
      await observe('error_feed.model','LLM',{},async () => modelAttributes({requested_model:'fixture-model',
        usage:{prompt_tokens:10,completion_tokens:2},cost_microusd:0,cost_status:'reported'}));
      return {outcome:'success'};
    }, {root:true,input:{task:'test investigation'},output:true});
    await observe('failed-investigation', 'AGENT', claimAttributes({organization_id:'test-org',
      organization_name:'Test Org',attempt_id:'test-attempt'}), async () =>
      executionResult({execution_status:'failed',outcome:'unknown'}), {root:true});
    await stopObservability();
    assert.ok(requests.length > 0, 'SDK must actually export spans');
    assert.equal(requests[0].headers['x-api-key'],'test-key');
    assert.equal(requests[0].headers['x-secret-key'],'test-secret');
    const spans = requests.flatMap(request => JSON.parse(request.body.toString()).resourceSpans
      .flatMap(resource => resource.scopeSpans.flatMap(scope => scope.spans)));
    assert.equal(spans.length, 3);
    const root = spans.find(span => span.name === 'error_feed.investigation');
    const model = spans.find(span => span.name === 'error_feed.model');
    assert.ok(root && model);
    assert.equal(root.status.code, 1);
    assert.equal(model.status.code, 1);
    assert.equal(spans.find(span => span.name === 'failed-investigation').status.code, 2);
    assert.equal(model.traceId,root.traceId);
    assert.equal(model.parentSpanId,root.spanId);
    for (const span of spans) {
      assert.equal(span.attributes.find(attribute => attribute.key === 'user.id')?.value.stringValue, 'test-org');
      assert.equal(span.attributes.find(attribute => attribute.key === 'user.name')?.value.stringValue, 'Test Org');
      assert.equal(span.attributes.find(attribute => attribute.key === 'session.id')?.value.stringValue, 'error-feed:test-attempt');
    }
    assert.equal(root.attributes.find(attribute => attribute.key === 'input.value')?.value.stringValue, '{"task":"test investigation"}');
    assert.equal(root.attributes.find(attribute => attribute.key === 'output.value')?.value.stringValue, '{"outcome":"success"}');
    const cost = model.attributes.find(attribute => attribute.key === 'gen_ai.cost.total')?.value;
    assert.equal(Number(cost?.intValue ?? cost?.doubleValue), 0);
    assert.ok(!requests[0].body.includes('test-secret'));
  } finally {await stopObservability();await new Promise(resolve => server.close(resolve));}
});
