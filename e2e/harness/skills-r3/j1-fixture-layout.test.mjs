import assert from 'node:assert/strict';
import { dirname, relative, resolve } from 'node:path';
import test from 'node:test';
import { deriveDelivery } from '../../benchmarks/context-hub/drivers/j1-native.mjs';
import { layoutFor } from '../../benchmarks/context-hub/drivers/j1-fixture.mjs';

const ID = '38641d41-81fb-4a68-a1d6-e7276131a1ea';

test('no source AGENTS.md sits in the project directory or any ancestor up to the run directory', () => {
  const target = resolve('/runs/j1-cold-x');
  const layout = layoutFor(target);
  // Codex reads AGENTS.md from the git root down to the project directory; the
  // run directory may be inside a git work tree, so every ancestor counts.
  for (let dir = layout.project; relative(target, dir) !== '..' && dir !== dirname(dir); dir = dirname(dir)) {
    assert.notEqual(layout.agentsDirectory, dir, 'source AGENTS.md would precede the managed block');
    if (dir === target) break;
  }
  assert.ok(layout.agents.startsWith(`${layout.agentsDirectory}/`));
  assert.ok(!layout.agentsDirectory.startsWith(`${layout.project}/`) && layout.agentsDirectory !== layout.project);
});

const requestWith = (instructions) => ({ input: [{ content: [{ text: `# AGENTS.md instructions for /p\n\n<INSTRUCTIONS>\n${instructions}</INSTRUCTIONS>` }] }] });

test('an enclosing AGENTS.md before the managed block is named, not reported as a missing block', () => {
  const block = `<!-- futureagi:context:${ID}:begin -->\n# Pilot\n<!-- futureagi:context:${ID}:end -->\n# Local\n\n`;
  assert.throws(() => deriveDelivery([requestWith(`# Parent\n\n\n${block}`)], { names: [] }), /native_root_enclosing_instructions_precede_managed_block/);
  assert.throws(() => deriveDelivery([requestWith('# Only local\n')], { names: [] }), /native_root_managed_block_missing/);
  assert.equal(typeof deriveDelivery([requestWith(block)], { names: [] }).root_source_sha256, 'string');
});
