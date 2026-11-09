// The account's key pair as the prototype shows it (options-truth L1 G5,
// code:step:install-keys): one pair per organization, the same on the
// Keys page and in every "Set your keys" step. Pure; seeded by the org id.
import { seededChars } from "./snippets/lines";

// Leak scan (options-truth L3 G10): the prototype key prefixes exist only
// in prototype builds (and tests). The check is static, so a production
// bundle folds it to false and ships neither prefix; there the pair is
// null (real keys come from the Keys API, never from here).
const PROTOTYPE_BUILD =
  import.meta.env.MODE === "test" ||
  (import.meta.env.VITE_ENVIRONMENT === "local" &&
    import.meta.env.VITE_PROTOTYPE_MODE === "true");

/** `{apiKey, secretKey}` of an organization (constant per org). */
export function orgKeyPair(orgId) {
  if (!PROTOTYPE_BUILD) return { apiKey: null, secretKey: null };
  const seed = String(orgId ?? "org");
  return {
    apiKey: `fi_proto_${seededChars(`${seed}:api`, 24)}`,
    secretKey: `fs_proto_${seededChars(`${seed}:secret`, 24)}`,
  };
}
