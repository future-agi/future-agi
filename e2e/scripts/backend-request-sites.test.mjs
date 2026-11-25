import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';

const root = fileURLToPath(new URL('../', import.meta.url));
const read = file => readFileSync(join(root, file), 'utf8');
function sources() {
  return execFileSync('git', ['ls-files', '-z', '--cached', '--others', '--exclude-standard', '--', '.'], { cwd: root, encoding: 'utf8' })
    .split('\0').filter(file => /\.(?:[cm]?[jt]sx?)$/.test(file));
}

// A count per expression, NOT permission to create arbitrary contexts in a known file.
// Bounded to conventional JS/TS spellings, not data flow through arbitrary aliases or redirects.
function* sites(file, text) {
  const receivers = new Set(file === 'lib/otlp-delivery.ts' ? ['req'] : []);
  for (const m of text.matchAll(/\b(?:([\w$]+)\s*(?:\.\s*newContext|\[\s*['"]newContext['"]\s*\])|newContext)\s*\(/g)) {
    const owner = text.slice(0, m.index).match(/([\w$]+)\s*=\s*await\s*$/)?.[1] ?? '-';
    yield `factory ${owner}=${m[1] ?? '?'}`;
    if (m[1] !== 'browser' && owner !== '-') receivers.add(owner);
  }
  for (const m of text.matchAll(/\b(?:const|let)\s*\{[^{}]*\bnewContext\b[^{}]*\}\s*=/g)) yield 'destructured factory';
  for (const m of text.matchAll(/\(\s*\{([^{}()]*)\}\s*(?:,\s*[\w$]+){0,2}\s*\)\s*=>|\bfunction\s*[\w$]*\(\s*\{([^{}()]*)\}/g)) {
    const args = (m[1] ?? m[2]).split(',').map(s => s.trim().replace(/\s*:\s*/g, ':')).filter(Boolean);
    const request = args.find(s => /^request(?::|$)/.test(s));
    if (request) {
      yield `binding ${args.join(',')}`;
      receivers.add(request.split(':')[1] ?? 'request');
    }
  }
  for (const m of text.matchAll(/\b(?:page|context)(?:\.context\(\))?\.request\b(?!\s*\()|\broute\s*\.\s*fetch\s*\(/g)) yield `attached ${m[0]}`;
  for (const m of text.matchAll(/\b([\w$]+)\s*\.\s*(get|head|post|put|patch|delete|fetch)\s*\(\s*([^,\n)]*)/g)) {
    if (receivers.has(m[1])) yield `send ${m[1]}.${m[2]} ${m[3].trim()}`;
  }
}

test('three-argument request fixtures expose their binding and raw send', () => {
  for (const binding of ['request', 'request: api']) {
    const receiver = binding === 'request' ? 'request' : 'api';
    const source = 'async (' + '{ ' + binding + ' }, use, testInfo) => { await '
      + receiver + '.get' + '(E2E.apiUrl); }';
    assert.deepEqual([...sites('new-fixture.ts', source)], [
      `binding ${binding.replace(/\s+/g, '')}`,
      `send ${receiver}.get E2E.apiUrl`,
    ]);
  }
});

test('new API producers and direct sends on inventoried raw contexts need origin review', () => {
  const expected = new Map();
  for (const line of read('scripts/fixtures/backend-request-sites.tsv').split('\n').filter(s => s && !s.startsWith('#'))) {
    const [file, signature, count] = line.split('\t');
    expected.set(`${file}\t${signature}`, Number(count));
  }
  for (const file of sources()) {
    for (const signature of sites(file, read(file))) {
      const key = `${file}\t${signature}`;
      const left = expected.get(key) ?? 0;
      assert.ok(left > 0, `${file}: unreviewed ${signature.split(' ')[0]} site; classify its first hop`);
      if (left === 1) expected.delete(key); else expected.set(key, left - 1);
    }
  }
  assert.deepEqual([...expected.keys()].map(k => k.split('\t')[0]), [], 'retired or changed request sites');
});

test('shared backend boundaries still own the per-send transport header', () => {
  const required = {
    'lib/backend-transport.ts': [/name\.toLowerCase\(\) !== 'connection'/, /Connection: 'close'/, /url\.origin !== new URL\(backendURL\)\.origin/,
      /firstHopOptions\(url, backendURL, opts, baseURL\)/,
      ...['get', 'head', 'post', 'put', 'patch', 'delete', 'fetch'].map(verb => new RegExp(`req\\.${verb}\\(url, options\\(url, opts\\)\\)`))],
    'lib/backend-request.ts': [/use\(backendRequestContext\(request, E2E\.apiUrl, baseURL\)/, /backendRequestContext\(await request\.newContext\(options\)/,
      /route\.fetch\(firstHopOptions\(route\.request\(\), E2E\.apiUrl/],
    'lib/api-client.ts': [/req\[method\]\(url, firstHopOptions\(url, this\.baseURL/],
    'lib/scope-actors.ts': [/test as base, expect \} from '.\/backend-request'/, /return firstHopOptions\(E2E\.apiUrl, E2E\.apiUrl, \{ headers \}\)\.headers/, /headers: this\.headers\(actor\)/],
    'lib/fixtures.ts': [/test as base, expect \} from '.\/backend-request'/],
    'flows/auth/saml-tenant-isolation.spec.ts': [/expect, test \} from '..\/..\/lib\/backend-request'/],
    'flows/observe/catalog-permission-boundaries.spec.ts': [/req\.post\(`\$\{E2E.apiUrl\}\$\{UPLOAD\}`, \{ headers: scopes\.headers\(s.owner\), multipart:/],
    'flows/dashboards/array-membership-widget.spec.ts': [/extraHTTPHeaders: scopeActors\.headers\(other\)/, /reader\.get\(`\$\{E2E.apiUrl\}\$\{path\}`, \{ maxRedirects: 0 \}\)/],
  };
  for (const [file, patterns] of Object.entries(required)) {
    for (const pattern of patterns) assert.ok(pattern.test(read(file)), `${file}: missing first-hop boundary`);
  }
});
