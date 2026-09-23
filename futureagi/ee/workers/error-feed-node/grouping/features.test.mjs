import test from 'node:test';
import assert from 'node:assert/strict';
import {buildFeatures, featureDigest, splitFeatureText, FEATURE_LIMITS} from './features.mjs';
import {adaptGroupingSnapshot} from './snapshot.mjs';
import {makeGroupingSnapshotFixture} from './snapshot-fixture.mjs';

const model = {name:'all-MiniLM-L6-v2',dimension:384,servingRelease:'test-deployment-1'};
const vector = () => Array.from({length:384}, (_, i) => i === 0 ? 1 : 0);
const row = (extra={}) => ({id:'one',source_digest:'source',evidence_revision:'evidence',
  views:{semantics:'faulty action',task:'expected action'},...extra});
const embed = async texts => ({vectors:texts.map(vector), model:null,tokenCounts:null,coverageVerified:false});

test('existing serving response prepares features without inventing revision or token coverage', async () => {
  const result = await buildFeatures([row()], {model,embedBatch:embed});
  assert.equal(result.version,'f6-minilm-features/v2');
  assert.equal(result.model_revision,null);
  assert.equal(result.serving_release,model.servingRelease);
  const feature=result.rows.one.views.semantics;
  assert.equal(feature.text_digest,featureDigest('faulty action'));
  assert.equal(feature.coverage.input_complete,true);
  assert.equal(feature.coverage.model_token_coverage,'unknown');
  assert.equal(feature.coverage.truncated,null);
  assert.equal(feature.chunks[0].token_count,null);
});

test('snapshot adapter feeds feature builder without changing source identities', async () => {
  const rows=adaptGroupingSnapshot(makeGroupingSnapshotFixture());
  const result=await buildFeatures(rows,{model,embedBatch:embed});
  assert.equal(result.rows[rows[0].id].source_digest,rows[0].source_digest);
  assert.equal(result.rows[rows[0].id].evidence_revision,rows[0].evidence_revision);
});

test('Unicode chunks preserve every code point and obey byte limits', () => {
  const text='a'.repeat(127)+'🙂界'.repeat(100);
  const chunks=splitFeatureText(text);
  assert.equal(chunks.map(c=>c.text).join(''),text);
  for(const chunk of chunks){
    assert.ok(Buffer.byteLength(chunk.text)<=FEATURE_LIMITS.chunkBytes);
    assert.ok(chunk.text.isWellFormed());
    assert.equal(text.slice(chunk.start,chunk.end),chunk.text);
  }
  assert.throws(()=>splitFeatureText('\ud800'),/Invalid feature text/);
});

test('cache is bound to source, view, text and deployment release and detects corruption', async () => {
  const cache=new Map(); let calls=0;
  const embedBatch=async texts=>{calls++;return embed(texts);};
  const first=await buildFeatures([row()],{model,embedBatch,cache});
  assert.equal(calls,1);
  assert.deepEqual(await buildFeatures([row()],{model,embedBatch,cache}),first);
  assert.equal(calls,1);
  await buildFeatures([row({source_digest:'changed'})],{model,embedBatch,cache});
  assert.equal(calls,2);
  await buildFeatures([row()],{model:{...model,servingRelease:'next'},embedBatch,cache});
  assert.equal(calls,3);
  cache.values().next().value.feature.vector[0]=2;
  await assert.rejects(()=>buildFeatures([row()],{model,embedBatch,cache}),/cache binding/);
});

test('size and duplicate limits reject before external calls', async () => {
  const never=async()=>{throw new Error('unexpected network');};
  await assert.rejects(()=>buildFeatures([row(),row()],{model,embedBatch:never}),/duplicate/);
  await assert.rejects(()=>buildFeatures([row({views:{semantics:'x'.repeat(256*1024+1)}})],
    {model,embedBatch:never}),/byte limit/);
  await assert.rejects(()=>buildFeatures([row({views:{semantics:'x'.repeat(128*513)}})],
    {model,embedBatch:never}),/chunk limit/);
});

test('bad count, zero and non-finite vectors fail and do not cache', async () => {
  for(const vectors of [[],[Array(384).fill(0),vector()],[Array(384).fill(NaN),vector()]]){
    const cache=new Map();
    await assert.rejects(()=>buildFeatures([row()],{model,cache,embedBatch:async()=>({vectors})}),/Embedding|embedding/);
    assert.equal(cache.size,0);
  }
});

test('whitespace-only chunks retain source coverage without sending empty requests', async () => {
  const text='x'+' '.repeat(400)+'y';
  const result=await buildFeatures([row({views:{semantics:text}})],{
    model,embedBatch:async texts=>{assert.ok(texts.every(t=>t.trim()));return embed(texts);},
  });
  const feature=result.rows.one.views.semantics;
  assert.equal(feature.coverage.end,text.length);
  assert.ok(feature.chunks.some(c=>!c.embedded));
});
