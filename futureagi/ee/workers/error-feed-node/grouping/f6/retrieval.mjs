import assert from 'node:assert/strict';
import {VIEWS, cosine, digest, validateVector, sameScope, pairSafety, unique, scopeKey} from './common.mjs';

// Seeded random-hyperplane LSH: derived acceleration only, never identity authority.
function plane(table, bit, dimension) {
  let seed = parseInt(digest([table, bit]).slice(0, 8), 16) || 1;
  return Array.from({length: dimension}, () => { seed ^= seed << 13; seed ^= seed >>> 17; seed ^= seed << 5; return ((seed >>> 0) / 4294967296) * 2 - 1; });
}
export class ViewIndex {
  constructor(rows, features, policy) {
    this.rows = new Map(rows.map(r => [r.id, r])); this.features = features; this.policy = policy;
    assert.equal(features.model, policy.embedding_model); assert.equal(features.dimension, policy.embedding_dimension);
    this.planes = Array.from({length: policy.index_tables}, (_, t) => Array.from({length: policy.index_bits}, (_, b) => plane(t, b, features.dimension)));
    this.buckets = new Map();
    for (const row of rows) {
      const f = features.rows[row.id];
      assert.ok(f && f.source_digest === row.source_digest && f.evidence_revision === row.evidence_revision, 'Stale feature index');
      for (const [view, entry] of Object.entries(f.views)) {
        assert.equal(entry.text_digest, digest(row.views[view]), 'Stale view'); validateVector(entry.vector, features.dimension);
        this.planes.forEach((_, t) => {
          const key = this.key(row, view, t, this.signature(entry.vector, t));
          if (!this.buckets.has(key)) this.buckets.set(key, []);
          this.buckets.get(key).push(row.id);
        });
      }
    }
  }
  key(row, view, t, signature) { return JSON.stringify([scopeKey(row), view, t, signature]); }
  signature(vector, table) { return this.planes[table].reduce((n, p, i) => n | ((vector.reduce((s, v, j) => s + v * p[j], 0) >= 0 ? 1 : 0) << i), 0); }
  similarity(a, b) {
    const av = this.features.rows[a]?.views.semantics, bv = this.features.rows[b]?.views.semantics;
    if (av && bv) return cosine(av.vector, bv.vector);
    const values = VIEWS.flatMap(v => this.features.rows[a]?.views[v] && this.features.rows[b]?.views[v]
      ? [cosine(this.features.rows[a].views[v].vector, this.features.rows[b].views[v].vector)] : []);
    return values.length ? Math.max(...values) : -1;
  }
  neighbours(id, constraints, {includeControls = false} = {}) {
    const row = this.rows.get(id), ranks = new Map();
    for (const [view, entry] of Object.entries(this.features.rows[id].views)) {
      let candidates;
      if (this.policy.index_mode === 'exact') candidates = [...this.rows.keys()];
      else {
        const found = new Set();
        this.planes.forEach((_, t) => {
          const s = this.signature(entry.vector, t);
          // Multi-probe one-bit neighbours improves recall without transitive identity chaining.
          for (const sig of [s, ...this.planes[t].map((_, bit) => s ^ (1 << bit))]) {
            for (const candidate of this.buckets.get(this.key(row, view, t, sig)) || []) found.add(candidate);
          }
        });
        candidates = [...found];
      }
      const list = candidates.filter(other => other !== id && sameScope(row, this.rows.get(other))
        && (includeControls ? this.rows.get(other).control : !pairSafety(row, this.rows.get(other), constraints))
        && this.features.rows[other]?.views[view])
        .map(other => ({id: other, similarity: cosine(entry.vector, this.features.rows[other].views[view].vector)}))
        .sort((a, b) => b.similarity - a.similarity || a.id.localeCompare(b.id)).slice(0, this.policy.neighbours_per_view);
      list.forEach((item, rank) => {
        const current = ranks.get(item.id) || {id: item.id, rank_score: 0, views: {}};
        current.rank_score += 1 / (60 + rank + 1); current.views[view] = item.similarity; ranks.set(item.id, current);
      });
    }
    if (this.policy.hybrid_retrieval && !includeControls) {
      // Label-free lexical retrieval. BM25 over the original occurrence statement;
      // keep codes/argument identifiers intact and fuse its rank with dense views.
      const tokenize = text => (text.toLowerCase().match(/[a-z0-9_]+/g) || []).filter(t => t.length > 1);
      const docs = [...this.rows.values()].filter(r => sameScope(row, r) && !r.control)
        .map(r => ({id: r.id, terms: tokenize(r.summary)}));
      const query = new Set(tokenize(row.summary)), avg = docs.reduce((s,d) => s+d.terms.length,0)/Math.max(1,docs.length);
      const df = new Map([...query].map(t => [t, docs.filter(d => d.terms.includes(t)).length]));
      const ranked = docs.filter(d => d.id !== id && !pairSafety(row,this.rows.get(d.id),constraints)).map(d => {
        let score=0;
        for(const term of query) { const tf=d.terms.filter(t=>t===term).length;
          if(tf) score+=Math.log(1+(docs.length-df.get(term)+0.5)/(df.get(term)+0.5))*tf*2.2/(tf+1.2*(0.25+0.75*d.terms.length/(avg||1))); }
        return {id:d.id,score};
      }).filter(x=>x.score>0).sort((a,b)=>b.score-a.score||a.id.localeCompare(b.id)).slice(0,this.policy.neighbours_per_view);
      ranked.forEach((item,rank)=>{const current=ranks.get(item.id)||{id:item.id,rank_score:0,views:{}};
        current.rank_score+=1/(60+rank+1);current.views.lexical=item.score;ranks.set(item.id,current);});
    }
    return [...ranks.values()].sort((a, b) => b.rank_score - a.rank_score || a.id.localeCompare(b.id));
  }
}

export function buildCohort(seed, index, constraints, policy) {
  const candidates = index.neighbours(seed, constraints).slice(0, policy.cohort_size - 1);
  const ids = [seed, ...candidates.map(r => r.id)];
  // Membership in a previous cohort does not remove an occurrence from this one.
  return {id: digest(['cohort', ids]), seed, members: ids, retrieval: candidates,
    cannot_links: [...constraints].map(s => JSON.parse(s)).filter(([a, b]) => ids.includes(a) && ids.includes(b))};
}

export function selectExamples(cohort, index, constraints, policy, scorePair, seen = new Set()) {
  const ids = cohort.members, selected = [], roles = {};
  const add = (id, role) => { if (!id) return; roles[id] ||= []; roles[id].push(role); if (!selected.includes(id) && selected.length < policy.representatives) selected.push(id); };
  add(cohort.seed, 'seed');
  const peers = ids.filter(id => id !== cohort.seed).sort((a,b) =>
    (policy.coverage_selection ? Number(seen.has(a))-Number(seen.has(b)) : 0)
    || index.similarity(cohort.seed,b)-index.similarity(cohort.seed,a) || a.localeCompare(b));
  for (const id of peers.slice(0, policy.protected_peers)) add(id, 'nearest_peer');
  const central = [...ids].sort((a, b) => ids.reduce((s, x) => s + index.similarity(b, x) - index.similarity(a, x), 0) || a.localeCompare(b))[0];
  add(central, 'central');
  const highImpact = ids.filter(id => Number.isFinite(index.rows.get(id).impact)).sort((a, b) => index.rows.get(b).impact - index.rows.get(a).impact)[0];
  add(highImpact, 'high_impact');
  const boundary = ids.filter(id => id !== cohort.seed).sort((a, b) => {
    const uncertainty = id => { const p = scorePair(cohort.seed, id).probability; return p === null ? 0 : Math.abs(p - 0.5); };
    return uncertainty(a) - uncertainty(b) || a.localeCompare(b);
  })[0];
  add(boundary, 'boundary');
  const counter = ids.find(a => ids.some(b => a !== b && (constraints.has(JSON.stringify([a, b].sort())) || scorePair(a, b).decision === 'reject')));
  add(counter, 'counterexample');
  while (selected.length < Math.min(policy.representatives, ids.length)) {
    const next = ids.filter(id => !selected.includes(id)).sort((a, b) => Math.max(...selected.map(s => index.similarity(a, s))) - Math.max(...selected.map(s => index.similarity(b, s))) || a.localeCompare(b))[0];
    add(next, 'diverse');
  }
  const controls = index.neighbours(cohort.seed, constraints, {includeControls: true}).slice(0, policy.controls).map(r => r.id);
  return {selected, roles: Object.fromEntries(selected.map(id => [id, unique(roles[id])])), controls,
    unreviewed: ids.filter(id => !selected.includes(id)), missing: [...(!controls.length ? ['verified_successful_comparisons_unavailable'] : []), ...(!highImpact ? ['impact_not_supplied'] : [])]};
}

export function refillSelection(selection, cohort, index, policy, eligible, seen = new Set()) {
  const out = structuredClone(selection);
  if (!policy.refill_selection) return out;
  const candidates = cohort.members.filter(id => !out.selected.includes(id) && eligible(id))
    .sort((a,b)=>(policy.coverage_selection ? Number(seen.has(a))-Number(seen.has(b)) : 0)
      || index.similarity(cohort.seed,b)-index.similarity(cohort.seed,a) || a.localeCompare(b));
  for (const id of candidates) {
    if(out.selected.length>=policy.representatives) break;
    out.selected.push(id); out.roles[id]=['refill_peer'];
  }
  out.unreviewed=cohort.members.filter(id=>!out.selected.includes(id));
  return out;
}

export function selectPrototypes(ids, index, cap) {
  if (ids.length <= cap) return unique(ids);
  const selected = [[...ids].sort((a, b) => ids.reduce((s, x) => s + index.similarity(b, x) - index.similarity(a, x), 0) || a.localeCompare(b))[0]];
  while (selected.length < cap) selected.push(ids.filter(id => !selected.includes(id)).sort((a, b) => Math.max(...selected.map(s => index.similarity(a, s))) - Math.max(...selected.map(s => index.similarity(b, s))) || a.localeCompare(b))[0]);
  return selected;
}
