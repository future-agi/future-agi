// Narrow source readers for mutable public prose. Shape drift requires review.
// Product pin 651ab5bb8ac3a6c54d82c59bdd5a1f1271049aec; never import product code.
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
const root = fileURLToPath(new URL('../../../', import.meta.url));
const source = (path: string) => readFileSync(`${root}${path}`, 'utf8');
function one(text: string, pattern: RegExp, name: string): string {
  const matches = [...text.matchAll(pattern)];
  if (matches.length !== 1) throw new Error(`money source: expected one ${name}`);
  return matches[0][1];
}
export function consentVersion(): string {
  return JSON.parse(
    one(
      source('futureagi/agentcc/auto_reload_terms.py'),
      /^AUTO_RELOAD_CONSENT_VERSION = ("[^"\r\n]+")$/gm,
      'consent version',
    ),
  );
}
export function consentRefusalMessages(): Record<string, string> {
  const text = source('futureagi/agentcc/auto_reload_terms.py');
  return Object.fromEntries(
    ['auto_reload_consent_required', 'auto_reload_consent_outdated'].map((code) => {
      const pattern = new RegExp(`raise AutoReloadConsentError\\(\\s*"${code}",\\s*("[^"\\r\\n]+"),\\s*\\)`, 'g');
      return [code, JSON.parse(one(text, pattern, code))];
    }),
  );
}
export function billingNoAccessMessage(): string {
  return JSON.parse(
    one(
      source('frontend/src/constants/usageCopy.js'),
      /export const BILLING_NO_ACCESS_MESSAGE\s*=\s*("[^"\r\n]+");/g,
      'BillingNoAccess copy',
    ),
  );
}
/** Decimal(str(schema default)); the JSON summary number would lose trailing .0. */
export function minimumTopupText(): string {
  return one(
    source('futureagi/agentcc/contracts/gateway_admin.py'),
    /"ManagedTopupConfig": \{\s*"min_usd": (\d+(?:\.\d+)?),/g,
    'minimum topup default',
  );
}
