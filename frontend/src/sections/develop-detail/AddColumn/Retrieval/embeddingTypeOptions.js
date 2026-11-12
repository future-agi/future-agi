// Which embedding types the Retrieval form offers (one key, R1-04). A type is
// enabled by its managed-or-BYOK entitlement: the server's credential_mode for
// that type (Common/modelAccess.js). Without a recognised verdict the
// provider-key rule applies exactly as before: a type is disabled only when
// provider status reports hasKey === false. A managed type never comes from a
// missing, failed or unexpected verdict. A disabled type carries the reason
// the form shows for it (W10-EXPERIMENTS-DATASETS-U02).
import {
  CREDENTIAL_MODE,
  readModelAccess,
} from "src/sections/develop-detail/Common/modelAccess";
import {
  SENTENCE_TRANSFORMERS_UNAVAILABLE,
  embeddingKeyNeeded,
  embeddingTypeUnavailable,
} from "./retrievalCopy";

const ENTITLED_TYPES = Object.freeze([
  { label: "Open AI", value: "openai", keyName: "OpenAI" },
  { label: "Hugging Face", value: "huggingface", keyName: "Hugging Face" },
]);

// Model serving has no sentence_transformers servable (it answers 404 for
// every model), so the type is listed for saved columns but never selectable.
const SENTENCE_TRANSFORMERS = Object.freeze({
  label: "Sentence Transformers",
  value: "sentence_transformers",
  disabled: true,
  managed: false,
  reason: SENTENCE_TRANSFORMERS_UNAVAILABLE,
});

/**
 * @param {{ providerStatus?: Array<any>, access?: Record<string, any> | null }} input
 *   providerStatus: normalized provider status (useApiKeysStatus);
 *   access: the raw model-list item (or null) per type (useEmbeddingAccess).
 * @returns {Array<{ label: string, value: string, disabled: boolean, managed: boolean, reason: string | null }>}
 */
export const buildEmbeddingTypeOptions = ({ providerStatus, access }) => [
  ...ENTITLED_TYPES.map(({ label, value, keyName }) => {
    const keyReason = (disabled) =>
      disabled ? embeddingKeyNeeded(keyName, label) : null;
    const verdict = readModelAccess(access?.[value]);
    if (verdict?.contract && verdict.mode === CREDENTIAL_MODE.MANAGED) {
      // Managed always means Future AGI credits pay (the contract's payer).
      const disabled = !providerStatus?.some(
        (entry) => entry.provider === value && entry.hasKey === true,
      );
      return { label, value, disabled, managed: false, reason: keyReason(disabled) };
    }
    if (verdict?.contract && verdict.mode === CREDENTIAL_MODE.BYOK) {
      return { label, value, disabled: false, managed: false, reason: null };
    }
    if (verdict?.contract && (verdict.mode === CREDENTIAL_MODE.UNAVAILABLE || verdict.mode === null)) {
      return {
        label,
        value,
        disabled: true,
        managed: false,
        reason: embeddingTypeUnavailable(label),
      };
    }
    const disabled = Boolean(
      providerStatus?.find((d) => d.provider === value && d.hasKey === false),
    );
    return { label, value, disabled, managed: false, reason: keyReason(disabled) };
  }),
  { ...SENTENCE_TRANSFORMERS },
];
