import { digest, sha256 } from '../../lib/skills-r3/canonical.mjs';

const NAMES = ['refund', 'chargeback', 'shipping', 'returns', 'billing', 'account', 'privacy', 'security', 'onboarding', 'invoices', 'cancellation', 'accessibility', 'incident', 'research', 'unsupported-media'];

/** Fifteen synthetic catalog members, including an explicitly unsupported binary member. */
export function libraryFixture({ unsupportedMember = true } = {}) {
  const skills = NAMES.map((name, index) => ({
    id: `00000000-0000-4000-8000-${String(index + 1).padStart(12, '0')}`,
    name: `e2e-r3-${name}`, source: { project: 'synthetic-library', resource: `source-${index + 1}`, revision: 'v1' },
    files: [{ path: 'SKILL.md', content: `---\nname: e2e-r3-${name}\ndescription: Use when the synthetic request concerns ${name}.\n---\n# ${name}\nUse only the reviewed synthetic policy.\n` },
      { path: 'references/policy.md', content: name === 'refund'
        ? 'Synthetic policy: refund 25 units when age is at most 30 days. Chargebacks require the chargeback procedure.\n'
        : `Synthetic ${name} policy; no external actions are authorized.\n` }],
    unsupported: unsupportedMember && index === 14 ? [{ path: 'assets/example.bin', bytes_base64: 'AAECAw==', reason: 'binary_asset' }] : [],
  }));
  const contexts = [0, 1, 2].map((index) => ({ name: `e2e-r3-context-${index + 1}`,
    files: [{ path: 'AGENTS.md', content: 'Follow the reviewed policy and select only relevant skills.\n' }],
    links: skills.filter((skill) => skill.unsupported.length === 0).map((skill) => ({ skill_id: skill.id, selector: index === 2 ? { version: 7 } : { label: 'production' } })),
  }));
  const fixture = { schema: 1, skills, contexts, cases: [
    { id: 'refund-boundary', prompt: 'Synthetic purchase: 25 units, 30 days ago. Is a refund eligible?', required: [skills[0].id], forbidden: [skills[1].id] },
    { id: 'chargeback-near-miss', prompt: 'I disputed the card transaction. What is the chargeback procedure?', required: [skills[1].id], forbidden: [skills[0].id] },
    { id: 'shipping-neighbor', prompt: 'How do I track the synthetic shipment?', required: [skills[2].id], forbidden: [skills[0].id, skills[1].id] },
  ] };
  return { ...fixture, fixture_sha256: digest(fixture) };
}

export function repositoryFixture() {
  return Array.from({ length: 40 }, (_, index) => ({
    id: String(1000 + index), name: `synthetic-repo-${String(index + 1).padStart(2, '0')}`,
    owner: 'synthetic', base: sha256(`base-${index}`).slice(0, 40),
    disposition: index < 32 ? 'follower' : index < 36 ? 'pin' : index < 38 ? 'local_modification' : index === 38 ? 'unavailable' : 'unknown_response',
    files: { 'AGENTS.md': index === 0 ? 'Local policy: never disclose synthetic customer records.\n' : 'Existing local instructions remain owned by the repository.\n',
      ...(index === 36 || index === 37 ? { 'references/local-policy.md': 'Locally modified: retain me.\n' } : {}) },
    // No compatible locks are preseeded. Cold adoption must make ownership explicit.
    lock: null,
  }));
}

export function uploadCases() {
  return {
    valid: [{ path: 'AGENTS.md', content: '# Synthetic standing instructions\n' }],
    traversal: [{ path: '../AGENTS.md', content: 'rejected' }],
    absolute: [{ path: '/AGENTS.md', content: 'rejected' }],
    nested: [{ path: 'AGENTS.md', content: 'root' }, { path: 'nested/AGENTS.md', content: 'unsupported nested scope' }],
    control: [{ path: 'AGENTS.md', content: 'root' }, { path: '.claude/settings.json', content: '{}' }],
    caseCollision: [{ path: 'AGENTS.md', content: 'root' }, { path: 'agents.md', content: 'collision' }],
    prefixCollision: [{ path: 'AGENTS.md', content: 'root' }, { path: 'references', content: 'file' }, { path: 'references/policy.md', content: 'collision' }],
    changedPreview: [{ path: 'AGENTS.md', content: 'changed after approval' }],
    multiRoot: [{ path: 'one/AGENTS.md', content: 'one' }, { path: 'two/AGENTS.md', content: 'two' }],
    longUtf8: [{ path: 'SKILL.md', content: 'Policy café 退款\n'.repeat(8000) }],
    pagedManifest: Array.from({ length: 28 }, (_, i) => ({ path: `references/part-${i}.md`, content: `Synthetic ${i}\n` })),
  };
}
