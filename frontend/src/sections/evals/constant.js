export const EvalsFeatures = [
  {
    image: "/assets/evals/create-evaluations.svg",
    title: "Create Evaluations",
    description:
      "Create your own evaluations or choose from our list of Future AGI evaluations",
  },
  {
    image: "/assets/evals/test-evaluations.svg",
    title: "Test evaluations",
    description:
      "Test the evaluation with multimodal inputs and outputs in our evaluation playground",
  },
  {
    image: "/assets/evals/add-feedbacks.svg",
    title: "Add Feedbacks",
    description:
      "Add feedback/ golden truth to fine-tune evaluations and improve accuracy",
  },
];
export const Evals_Docs_mapping = {
  synthetic_image_evaluator: "synthetic-image-evaluator",
  ocr_evaluation: "ocr-evaluation",
  rouge_score: "rouge",
  semantic_list_contains: "semantic-list-contains",
  embedding_similarity: "embedding-similarity",
  numeric_similarity: "numeric-similarity",
  levenshtein_similarity: "lavenshtein-similarity",
  text_to_sql: "text-to-sql",
  recall_score: "recall-score",
  bleu_score: "bleu",
  caption_hallucination: "caption-hallucination",
  task_completion: "task-completion",

  evaluate_function_calling: "llm-function-calling",

  is_informal_tone: "is-informal-tone",
  is_compliant: "is-compliant",
  is_factually_consistent: "is-factually-consistent",
  is_good_summary: "is-good-summary",
  content_safety_violation: "content-safety-violation",
  is_harmful_advice: "is-harmful-advice",
  clinically_inappropriate_tone: "clinically-inappropriate-tone",
  no_harmful_therapeutic_guidance: "no-harmful-therapeutic-guidance",
  detect_hallucination: "detect-hallucination",
  answer_refusal: "answer-refusal",
  fuzzy_match: "fuzzy-match", // fixed typo
  contains_code: "is-code",
  "ASR/STT_accuracy": "audio-transcription",
  is_helpful: "is-helpful",
  is_concise: "is-concise",
  is_polite: "is-polite",

  no_apologies: "no-apologies",
  no_llm_reference: "no-llm-reference",
  no_age_bias: "no-age-bias",
  no_gender_bias: "no-gender-bias",
  no_racial_bias: "no-racial-bias",

  audio_quality: "audio-quality",
  audio_transcription: "audio-transcription",
  chunk_utilization: "chunk-utilization",
  chunk_attribution: "chunk-attribution",
  prompt_instruction_adherence: "instruction-adherence",
  sexist: "sexist",

  tone: "tone",
  toxicity: "toxicity",
  context_relevance: "context-relevance",
  context_adherence: "context-adherence",

  conversation_resolution: "conversation-resolution",
  conversation_coherence: "conversation-coherence",
  data_privacy_compliance: "data-privacy",
  bias_detection: "bias-detection",
  cultural_sensitivity: "cultural-sensitivity",
  translation_accuracy: "translation-accuracy",
  factual_accuracy: "factual-accuracy",
  summary_quality: "summary-quality",
  eval_ranking: "eval-ranking",
  groundedness: "groundedness",
  completeness: "completeness",

  no_invalid_links: "no-invalid-links",
  contains_valid_link: "contains-valid-link",

  is_email: "is-email",
  is_json: "is-json",
  one_line: "contain-evals",
  pii: "pii",
  content_moderation: "content-moderation",
  prompt_injection: "prompt-injection",

  precision_at_k: "precision-at-k",
  recall_at_k: "recall-at-k",
  ndcg_at_k: "ndcg-at-k",
  mrr: "mrr",
  hit_rate: "hit-rate",

  customer_agent_loop_detection: "customer-agent-loop-detection",
  customer_agent_context_retention: "customer-agent-context-retention",
  customer_agent_query_handling: "customer-agent-query-handling",
  customer_agent_termination_handling: "customer-agent-termination-handling",
  customer_agent_interruption_handling: "customer-agent-interruption-handling",
  customer_agent_conversation_quality: "customer-agent-conversation-quality",
  customer_agent_objection_handling: "customer-agent-objection-handling",
  customer_agent_language_handling: "customer-agent-language-handling",
  customer_agent_human_escalation: "customer-agent-human-escalation",
  customer_agent_clarification_seeking: "customer-agent-clarification-seeking",
  customer_agent_prompt_conformance: "customer-agent-prompt-conformance",

  tts_accuracy: "tts-accuracy",
  ground_truth_match: "ground-truth-match",
  fid_score: "fid-score",
  clip_score: "clip-score",
  image_instruction_adherence: "image-instruction-adherence",
};
export const evalsDoc = "https://docs.futureagi.com/docs/evaluation";

// Tag definitions with icons. `match` arrays list all DB variants (mixed case, underscores, etc.)
export const EVAL_TAGS = [
  {
    value: "RED_TEAMING",
    label: "Red Teaming",
    icon: "mdi:shield-alert-outline",
    match: ["RED_TEAMING", "red_teaming", "Red Teaming"],
  },
  {
    value: "RETRIEVAL_SYSTEMS",
    label: "Retrieval Systems",
    icon: "mdi:database-search-outline",
    match: ["RETRIEVAL_SYSTEMS", "retrieval_systems", "Retrieval Systems"],
  },
  {
    value: "HARMFUL_OBJECTS",
    label: "Harmful Objects",
    icon: "mdi:alert-octagon-outline",
    match: ["HARMFUL_OBJECTS", "harmful_objects", "Harmful Objects"],
  },
  {
    value: "CHATBOT_BEHAVIORS",
    label: "Chatbot behaviors",
    icon: "mdi:robot-outline",
    match: ["CHATBOT_BEHAVIORS", "chatbot_behaviors", "Chatbot behaviors"],
  },
  {
    value: "OUTPUT_FORMAT",
    label: "Output Format",
    icon: "mdi:format-list-bulleted",
    match: ["OUTPUT_FORMAT", "output_format", "Output Format"],
  },
  {
    value: "NLP_METRICS",
    label: "NLP Metrics",
    icon: "mdi:chart-bar",
    match: ["NLP_METRICS", "nlp_metrics", "NLP Metrics"],
  },
  {
    value: "DATA_LEAKAGE",
    label: "Data Leakage",
    icon: "mdi:leak",
    match: ["DATA_LEAKAGE", "data_leakage", "Data Leakage"],
  },
  {
    value: "OUTPUT_VALIDATION",
    label: "Output Validation",
    icon: "mdi:check-decagram-outline",
    match: ["OUTPUT_VALIDATION", "output_validation", "Output Validation"],
  },
  {
    value: "IMAGE",
    label: "Image",
    icon: "mdi:image-outline",
    match: ["IMAGE", "image", "Image"],
  },
  {
    value: "AUDIO",
    label: "Audio",
    icon: "mdi:volume-high",
    match: ["AUDIO", "audio", "Audio"],
  },
  {
    value: "MEDICAL",
    label: "Medical",
    icon: "mdi:medical-bag",
    match: ["MEDICAL", "Medical"],
  },
  {
    value: "FINANCE",
    label: "Finance",
    icon: "mdi:currency-usd",
    match: ["FINANCE", "Finance"],
  },
  {
    value: "AGENTS",
    label: "Agents",
    icon: "mdi:robot-excited-outline",
    match: ["AGENTS", "Agents"],
  },
  {
    value: "SAFETY",
    label: "Safety",
    icon: "mdi:shield-check-outline",
    match: ["SAFETY", "Safety"],
  },
  {
    value: "RAG",
    label: "RAG",
    icon: "mdi:text-search",
    match: ["RAG", "Rag"],
  },
  {
    value: "HALLUCINATION",
    label: "Hallucination",
    icon: "mdi:head-question-outline",
    match: ["HALLUCINATION", "Hallucination"],
  },
  {
    value: "CONVERSATION",
    label: "Conversation",
    icon: "mdi:message-text-outline",
    match: ["CONVERSATION", "Conversation", "CHAT"],
  },
  {
    value: "CODE",
    label: "Code",
    icon: "mdi:code-braces",
    match: ["code", "CODE", "Code"],
  },
  {
    value: "PDF",
    label: "PDF",
    icon: "mdi:file-pdf-box",
    match: ["PDF", "Pdf"],
  },
  {
    value: "TEXT",
    label: "Text",
    icon: "mdi:format-text",
    match: ["TEXT", "Text"],
  },
];

// Lookup: any DB tag string → EVAL_TAG entry
export const TAG_LOOKUP = (() => {
  const map = {};
  EVAL_TAGS.forEach((tag) => {
    (tag.match || []).forEach((m) => {
      map[m] = tag;
    });
    map[tag.value] = tag;
    map[tag.label] = tag;
  });
  return map;
})();

// Normalize free-text tag input into a canonical value (UPPERCASE_UNDERSCORE),
// matching the convention used by EVAL_TAGS values and backend semantic tags.
export const canonicalizeTag = (s) =>
  String(s || "")
    .trim()
    .replace(/\s+/g, "_")
    .replace(/_+/g, "_")
    .toUpperCase();

// Turn a stored tag value (e.g. "CUSTOMER_SUPPORT") into a readable label.
export const humanizeTag = (s) =>
  String(s || "")
    .toLowerCase()
    .replace(/_/g, " ")
    .replace(/\b\w/g, (m) => m.toUpperCase());
