import { fileURLToPath } from 'node:url';
import { qualifyNative } from '../../lib/skills-r3/native-preflight.mjs';

export default async function nativeSetup() {
  const attestationPath = process.env.CTXHUB_NATIVE_ATTESTATION;
  if (!attestationPath) throw new Error('Native qualification required: CTXHUB_NATIVE_ATTESTATION is missing. This is not a skipped green flow.');
  await qualifyNative({ attestationPath,
    root: fileURLToPath(new URL('../../../', import.meta.url)),
    authToken: process.env.CTXHUB_NATIVE_RELAY_TOKEN });
}
