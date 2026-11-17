#!/usr/bin/env node
// CDN runtime configuration is PUBLIC. Input is GitHub repository VARIABLES,
// never secrets. Environment-prefixed names keep DEV_, PROD_ and US2_ separate.
// A config-only dispatch updates this file without rebuilding the application.
import { mkdirSync, readFileSync, renameSync, writeFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const frontend = new URL('../', import.meta.url);
const entrypoint = readFileSync(new URL('docker-entrypoint.sh', frontend), 'utf8');
const match = entrypoint.match(/^CONFIG_NAMES="\n([\s\S]*?)\n"/m);
if (!match) throw new Error('Cannot read the container runtime configuration allow-list');
// Extend the container list for hosted embedding without copying the list.
export const CONFIG_NAMES = Object.freeze([...new Set([
  ...match[1].trim().split(/\s+/),
  'VITE_OMEGA_CONSOLE_URL',
  'VITE_OMEGA_EMBED_PARENT_ORIGINS',
])]);
if (CONFIG_NAMES.some(name => !/^VITE_[A-Z0-9_]+$/.test(name))) {
  throw new Error('Invalid runtime configuration allow-list');
}

// Defence in depth against accidentally pasting a credential into a public var.
// Errors name only the variable, never its value (including malformed JSON).
const secretShape = /-----BEGIN [A-Z ]*PRIVATE KEY-----|\b(?:sk-[a-z0-9_-]{16,}|gh[pousr]_[a-z0-9]{16,}|github_pat_[a-z0-9_]{16,}|(?:AKIA|ASIA)[A-Z0-9]{16}|AIza[a-z0-9_-]{30,}|xox[baprs]-[a-z0-9-]+)|\beyJ[a-z0-9_-]+\.[a-z0-9_-]+\.[a-z0-9_-]+|\b(?:Bearer|Basic)\s+\S+|[a-z][a-z0-9+.-]*:\/\/[^\s/]*@|(?:[?&#]|\b)(?:access_token|api[_-]?key|token|secret|password|credential|signature|x-amz-signature)\s*[=:]/i;
function validate(name, value, prefix) {
  const refuse = () => { throw new Error(`Runtime config refused ${prefix}${name}`); };
  if (typeof value !== 'string' || value.length > 8192) refuse();
  let decoded = value;
  // Inspect percent-encoded credential shapes too, without changing the value.
  for (let i = 0; i < 3; i += 1) {
    if (secretShape.test(decoded)) refuse();
    try {
      const next = decodeURIComponent(decoded);
      if (next === decoded) break;
      decoded = next;
    } catch { break; }
  }
  if (name === 'VITE_OMEGA_CONSOLE' && !['true', 'false'].includes(value)) refuse();
}

export function renderRuntimeConfig(variables, prefix) {
  if (!['DEV_', 'PROD_', 'US2_'].includes(prefix)) throw new Error('Invalid runtime config environment prefix');
  if (!variables || typeof variables !== 'object' || Array.isArray(variables)) throw new Error('Invalid runtime config variables object');
  const config = {};
  for (const name of CONFIG_NAMES) {
    const key = `${prefix}${name}`;
    if (!Object.hasOwn(variables, key) || variables[key] === '') continue;
    validate(name, variables[key], prefix);
    config[name] = variables[key];
  }
  if (!Object.keys(config).length) return readFileSync(new URL('public/config.js', frontend), 'utf8');
  const unsafe = new RegExp(`[<>&${String.fromCharCode(0x2028, 0x2029)}]`, 'g');
  const json = JSON.stringify(config, null, 2).replace(unsafe,
    char => `\\u${char.charCodeAt(0).toString(16).padStart(4, '0')}`);
  return `// Public runtime configuration. Generated; do not edit.\nwindow.__FUTURE_AGI_CONFIG__ = ${json};\n`;
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try {
    const args = process.argv.slice(2);
    if (args.length !== 4 || args[0] !== '--prefix' || args[2] !== '--output') {
      throw new Error('Usage: write-runtime-config.mjs --prefix DEV_|PROD_|US2_ --output dist/config.js');
    }
    let variables;
    try { variables = JSON.parse(process.env.RUNTIME_CONFIG_VARIABLES_JSON || '{}'); }
    catch { throw new Error('Invalid runtime config variables JSON'); }
    // Validate everything before touching dist or replacing an existing file.
    const content = renderRuntimeConfig(variables, args[1]);
    const output = resolve(args[3]);
    mkdirSync(dirname(output), { recursive: true });
    const temporary = `${output}.${process.pid}.tmp`;
    writeFileSync(temporary, content, { mode: 0o644, flag: 'wx' });
    renameSync(temporary, output);
  } catch (error) {
    console.error(error.message);
    process.exitCode = 1;
  }
}
