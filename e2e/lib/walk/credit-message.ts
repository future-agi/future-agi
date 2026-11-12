import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { createHash } from 'node:crypto';
import { parseDocument } from 'yaml';
import { inspectManagedMock, profileFile } from '../managed-mock';

const sourcePath = fileURLToPath(
  new URL('../../../agentcc-gateway/internal/models/credit_refusals.go', import.meta.url),
);

/** Read the current product's prose and select its remedy from the inspected,
 * read-only MOUNTED gateway config, not merely an env profile declaration.
 * W01 step 4 has never been funded. Product 97fe75b74e sets grant_only_credits
 * true in Standalone: its remedy is an operator grant, not hosted checkout.
 * Lazy so offline discovery requires neither Docker nor a running stack.
 */
export function exhaustedMessage(): string {
  const inspected = inspectManagedMock(); // attests bind source and startup time
  const mounted = readFileSync(profileFile(inspected.profile), 'utf8');
  if (createHash('sha256').update(mounted).digest('hex') !== inspected.gatewaySha) {
    throw new Error('credit message config: changed after mounted-source inspection');
  }
  return parseNeverFundedMessage(readFileSync(sourcePath, 'utf8'), grantOnlyCredits(mounted));
}

export function grantOnlyCredits(source: string): boolean {
  const doc = parseDocument(source, { uniqueKeys: true });
  if (doc.errors.length) throw new Error('credit message config: invalid YAML');
  let cfg;
  try {
    cfg = doc.toJS({ maxAliasCount: 0 });
  } catch {
    throw new Error('credit message config: aliases are refused');
  }
  if (!cfg || typeof cfg !== 'object' || JSON.stringify(cfg).includes('$')) {
    throw new Error('credit message config: unsupported config');
  }
  const value = cfg.managed?.grant_only_credits;
  if (value === undefined) return false; // product DefaultConfig / Go bool default
  if (typeof value !== 'boolean') throw new Error('credit message config: grant_only_credits must be boolean');
  return value;
}

/** Deliberately narrow Go-source reader, not a Go interpreter. Any change to the
 * function's shape fails closed for review. Every word AND the format string
 * come from product source; no fallback message is embedded in the harness.
 */
export function parseNeverFundedMessage(source: string, grantOnly: boolean): string {
  const one = (pattern: RegExp, label: string): string => {
    const matches = [...source.matchAll(pattern)];
    if (matches.length !== 1) throw new Error(`credit message source: expected one ${label}`);
    return matches[0][1];
  };
  const literal = '("(?:[^"\\\\\r\n]|\\\\.)*")';
  const branch = grantOnly ? 'creditAddGrantOnly' : 'creditAddHosted';
  const remedy = JSON.parse(one(new RegExp(`^\\s*${branch} = ${literal}\\s*$`, 'gm'), branch)) as string;
  const own = JSON.parse(one(new RegExp(`^\\s*creditOwnKeys = ${literal}\\s*$`, 'gm'), 'creditOwnKeys')) as string;
  const body = one(
    /func CreditsExhaustedMessage\(neverFunded, grantOnly bool\) string \{([\s\S]*?)^\}/gm,
    'CreditsExhaustedMessage',
  );
  const stateMatches = [...body.matchAll(new RegExp(`if neverFunded \\{\\s*state = ${literal}\\s*\\}`, 'g'))];
  const formatMatches = [
    ...body.matchAll(
      new RegExp(`return fmt\\.Sprintf\\(${literal}, state, creditAdd\\(grantOnly\\), creditOwnKeys\\)`, 'g'),
    ),
  ];
  if (
    stateMatches.length !== 1 ||
    formatMatches.length !== 1 ||
    !/func creditAdd\(grantOnly bool\) string \{\s*if grantOnly \{\s*return creditAddGrantOnly\s*\}\s*return creditAddHosted\s*\}/.test(
      source,
    )
  ) {
    throw new Error('credit message source: unsupported product function shape');
  }
  const state = JSON.parse(stateMatches[0][1]) as string;
  const format = JSON.parse(formatMatches[0][1]) as string;
  if (format.split('%s').length !== 4 || format.replaceAll('%s', '').includes('%')) {
    throw new Error('credit message source: unsupported format');
  }
  const values = [state, remedy, own];
  return format.replace(/%s/g, () => values.shift()!);
}
