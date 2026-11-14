import { readFileSync, writeFileSync } from 'node:fs';
import { pathToFileURL } from 'node:url';

// The product's pinned @monaco-editor/loader defaults to Monaco 0.43.0 on a
// CDN. E2E loads those same editor bytes locally before the unchanged app entry;
// the loader then discovers window.monaco instead of requesting the CDN.
export function withLocalEditor(html) {
  const entries = [...html.matchAll(/<script\b(?=[^>]*\btype="module")(?=[^>]*\bsrc="([^"]+)")[^>]*>\s*<\/script>/g)];
  if (entries.length !== 1 || !/^\/assets\/[^?#]+\.js$/.test(entries[0][1])) {
    throw new Error('Expected one local Vite entry module');
  }
  const [tag, entry] = entries[0];
  return {
    html: html.replace(tag, tag.replace(`src="${entry}"`, 'src="/e2e-monaco/bootstrap.js"')),
    bootstrap: `await new Promise((resolve, reject) => {
  const script = document.createElement('script');
  script.src = '/e2e-monaco/vs/loader.js';
  script.onload = resolve;
  script.onerror = reject;
  document.head.append(script);
});
window.require.config({ paths: { vs: '/e2e-monaco/vs' } });
await new Promise((resolve, reject) => window.require(['vs/editor/editor.main'], resolve, reject));
await import(${JSON.stringify(entry)});
`,
  };
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const [, , index, bootstrap] = process.argv;
  if (!index || !bootstrap) throw new Error('Expected index and bootstrap output paths');
  const result = withLocalEditor(readFileSync(index, 'utf8'));
  writeFileSync(index, result.html);
  writeFileSync(bootstrap, result.bootstrap);
}
