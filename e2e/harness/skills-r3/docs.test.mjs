import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile, stat } from 'node:fs/promises';
import { join, resolve } from 'node:path';

// This gate must be invoked with the separately pinned docs checkout. Missing docs are
// an error, not a silent skip that would make package examples appear qualified.
const root = process.env.CTXHUB_DOCS_ROOT;
if (!root) throw new Error('Set CTXHUB_DOCS_ROOT to the isolated cloud docs checkout for the documentation gate');
const slugs = ['reversible-pilot', 'agent-contexts', 'reviewed-releases', 'safe-installation', 'rollout-recovery', 'compatibility'];
const slugify = (heading) => heading.toLowerCase().replace(/[^\p{L}\p{N}\s-]/gu, '').replace(/\s/g, '-');
const pages = Object.fromEntries(await Promise.all(slugs.map(async (slug) => [slug, await readFile(join(root, 'src/pages/docs/skills', `${slug}.mdx`), 'utf8')])));

for (const [slug, source] of Object.entries(pages)) {
  test(`${slug}: customer-safe preview, complete frontmatter, no fictional installation command`, () => {
    assert.match(source, /^---\ntitle: "[^"]+"\ndescription: "[^"]+"\n---/);
    assert.match(source, /\*\*Pilot preview\.\*\*/);
    assert.doesNotMatch(source, /localhost|127\.0\.0\.1|\/Volumes\/|\/home\/|sk-live-|ghp_[A-Za-z0-9]/);
    assert.doesNotMatch(source, /```(?:bash|sh|python|typescript)[\s\S]*?(?:pip install|npm install|python -m fi\.context)/);
    assert.equal((source.match(/^```/gm) ?? []).length % 2, 0, 'closed code fences');
  });
  test(`${slug}: every internal page and heading link exists`, async () => {
    for (const [, href] of source.matchAll(/\]\((\/docs\/[^)]+)\)/g)) {
      const [path, fragment] = href.split('#');
      const destination = resolve(root, 'src/pages', `${path.slice(1)}.mdx`);
      assert.ok(destination.startsWith(resolve(root, 'src/pages') + '/'));
      assert.ok((await stat(destination)).isFile(), href);
      if (fragment) {
        const target = await readFile(destination, 'utf8');
        const headings = [...target.matchAll(/^#{1,6} (.+)$/gm)].map((match) => slugify(match[1]));
        assert.ok(headings.includes(fragment), `${href}: heading missing`);
      }
    }
  });
}

test('all pilot surfaces and deferred boundaries have a canonical customer explanation', () => {
  assert.match(pages['agent-contexts'], /accepted lock/);
  assert.match(pages['agent-contexts'], /content origin v4/);
  assert.match(pages['agent-contexts'], /checkpoint/i);
  assert.match(pages['reversible-pilot'], /Base \/ Source \/ Hub/);
  assert.match(pages['reversible-pilot'], /Dataset/);
  assert.match(pages['reversible-pilot'], /Not reproduced/);
  assert.match(pages['reviewed-releases'], /Skills → Reviews/);
  assert.match(pages['reviewed-releases'], /Pre-move review currentness/);
  assert.match(pages['reviewed-releases'], /Disagree/);
  assert.match(pages['reviewed-releases'], /period allowance/);
  assert.match(pages['safe-installation'], /X-Context-Resolution/);
  assert.match(pages['safe-installation'], /not a safe updater/);
  assert.match(pages['rollout-recovery'], /original UUID/);
  assert.match(pages['rollout-recovery'], /unknown Git write/i);
  assert.match(pages.compatibility, /isolated Context-token MCP ingress/);
  assert.match(pages.compatibility, /Not measured \/ Not qualified/);
});
