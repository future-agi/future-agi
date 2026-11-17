// A synthetic api_key for a seeded org provider that hides none of its models.
//
// Django drops from a provider's saved models_list every model id that contains
// a window of the provider's credential (futureagi/agentcc/services/
// provider_model_safety.py, _credential_forms and _secret_windows): 8 characters
// of the key's text, 16 of its hex, 12 of its base64 (standard or URL-safe, no
// padding), each case-folded, and the same windows again with everything but
// a-z0-9 removed. A key shorter than its window hides only a model id equal to
// it. A key built from a flow's run suffix shares that suffix with the flow's
// minted models and loses all of them, so the key is a random token, checked
// against every model id it is saved with.
import { randomBytes } from 'node:crypto';

const compact = (text: string): string => text.toLowerCase().replace(/[^a-z0-9]/g, '');

const windows = (text: string, width: number): string[] =>
  Array.from({ length: Math.max(text.length - width + 1, 0) }, (_, i) => text.slice(i, i + width));

const forms = (key: string): [string, number][] => {
  const data = Buffer.from(key, 'utf8');
  return [
    [key, 8],
    [data.toString('hex'), 16],
    [data.toString('base64').replace(/=+$/, ''), 12],
    [data.toString('base64url'), 12],
  ];
};

/** Whether a provider saved with this key would lose this model id. */
export function keyHidesModel(key: string, modelId: string): boolean {
  const folded = modelId.toLowerCase();
  const compressed = compact(modelId);
  for (const secret of new Set([key, key.trim()])) {
    for (const [form, width] of forms(secret)) {
      const text = form.toLowerCase();
      if (text.length < width) {
        if (folded === text) return true;
        continue;
      }
      if ([text, ...windows(text, width)].some((w) => folded.includes(w))) return true;
      const short = compact(text);
      if (short && [short, ...windows(short, width)].some((w) => compressed.includes(w))) return true;
    }
  }
  return false;
}

/** A fresh synthetic provider key that hides none of models; redraws on a match. */
export function syntheticProviderKey(models: readonly string[]): string {
  for (let attempt = 0; attempt < 32; attempt++) {
    const key = `sk-e2e-synthetic-${randomBytes(12).toString('hex')}`;
    if (!models.some((model) => keyHidesModel(key, model))) return key;
  }
  throw new Error(`no synthetic provider key keeps all of ${models.length} model id(s)`);
}
