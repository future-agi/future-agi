// Retrieval copy that keeps two kinds of access apart (one key, R1-05). A
// vector database is the customer's own external service: its key
// authenticates to that store only. Model access (Future AGI credits or a
// provider key) pays for embeddings and never stands in for it.

/**
 * Helper text for a vector database's own API key field.
 * @param {string} store  e.g. "Pinecone"
 * @param {string} target what the key opens, e.g. "index"
 */
export const vectorStoreAuthHelp = (store, target) =>
  `Your ${store} API key, used only to query this ${target}. It is separate from model access (Future AGI credits or provider keys).`;

// Always shown under the embedding fields, with the Configure API keys link.
export const EMBEDDING_CREDITS_DISCLOSURE =
  "Future AGI credits do not pay for embeddings. Add your own provider key.";

// Shown under the embedding type when Future AGI credits pay for it.
export const EMBEDDING_PAYER_HINT =
  "Embedding calls for this type are billed to Future AGI credits. Your vector database still uses its own key.";

// Why an embedding type cannot be picked, shown under the field before it is
// picked (W10-EXPERIMENTS-DATASETS-U02). Model serving has no Sentence
// Transformers model (it answers 404 for every model).
export const SENTENCE_TRANSFORMERS_UNAVAILABLE =
  "Sentence Transformers embeddings are not available on this server. Use Hugging Face instead.";

/**
 * @param {string} keyName provider key, e.g. "OpenAI"
 * @param {string} label   embedding type as listed, e.g. "Open AI"
 */
export const embeddingKeyNeeded = (keyName, label) =>
  `Add your ${keyName} API key to use ${label}.`;

/** @param {string} label embedding type as listed, e.g. "Open AI" */
export const embeddingTypeUnavailable = (label) =>
  `${label} is not available for this workspace.`;
