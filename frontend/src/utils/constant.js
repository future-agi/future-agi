import { Events } from "./Mixpanel";

export const ModelTypeOptions = [
  { label: "Generative LLM", value: 8, disabled: false },
  { label: "Generative Image", value: 9, disabled: false },
  { label: "Numeric", value: 1, disabled: true },
  { label: "Score Categorical", value: 2, disabled: true },
  { label: "Ranking", value: 3, disabled: true },
  { label: "Binary Classification", value: 4, disabled: true },
  { label: "Regression", value: 5, disabled: true },
  { label: "Object Detection", value: 6, disabled: true },
  { label: "Segmentation", value: 7, disabled: true },
  { label: "Generative Video", value: 10, disabled: true },
  { label: "TTS", value: 11, disabled: true },
  { label: "STT", value: 12, disabled: true },
  { label: "Multi Modal", value: 13, disabled: true },
];

export const BigQueryMappedDummyData = [
  {
    input: "conversation_id",
    output: "",
  },
  {
    input: "timestamp",
    output: "",
  },
  {
    input: "model_input_text",
    output: "",
  },
  {
    input: "model_output_text",
    output: "",
  },
  {
    input: "tag",
    output: "",
  },
];

export const chatEvalColumns = [
  {
    id: 1,
    headerName: "",
  },
  {
    id: 2,
    headerName: "Model Input",
  },
  {
    id: 3,
    headerName: "Model Output",
  },
  {
    id: 4,
    headerName: "Score",
  },
  {
    id: 5,
    headerName: "Explanation",
  },
  {
    id: 6,
    headerName: "Tags",
  },
  {
    id: 7,
    headerName: "Date",
  },
];

export const chatContextColumns = [
  {
    id: 1,
    headerName: "",
  },
  {
    id: 2,
    headerName: "Model Input",
  },
  {
    id: 3,
    headerName: "Context",
  },
  {
    id: 4,
    headerName: "Score",
  },
  {
    id: 5,
    headerName: "Explanation",
  },
  {
    id: 6,
    headerName: "Tags",
  },
  {
    id: 7,
    headerName: "Date",
  },
];

export const prePromptTemplateColumns = [
  {
    id: 1,
    headerName: "",
  },
  {
    id: 2,
    headerName: "Model Input",
  },
  {
    id: 3,
    headerName: "Model Output",
  },
  {
    id: 4,
    headerName: "Context",
  },
  {
    id: 5,
    headerName: "Prompt Template",
  },
];

export const postPromptTemplateColumns = [
  {
    id: 5,
    headerName: "Score",
  },
  {
    id: 6,
    headerName: "Explanation",
  },
  {
    id: 7,
    headerName: "Tags",
  },
  {
    id: 8,
    headerName: "Date",
  },
];
export const EnvironmentOptions = [
  { label: "Production", value: "Production" },
  { label: "Training", value: "Training" },
  { label: "Validation", value: "Validation" },
  { label: "Corpus", value: "Corpus" },
];

export const EnvironmentMapper = {
  Production: 3,
  Training: 1,
  Validation: 2,
  Corpus: 4,
};

export const EnvironmentNumberMapper = {
  3: "Production",
  1: "Training",
  2: "Validation",
  4: "Corpus",
};

export const PYTHON_DOCUMENTATION_URL = "https://docs.futureagi.com/docs/sdk";

export const PATH_SEARCH_EVENT_MAPPING = [
  {
    path: "/dashboard/models/:id/performance",
    query: {},
    event: Events.metricPerformancePage,
  },
  {
    path: "/dashboard/models/:id/custom-metrics",
    query: {},
    event: Events.customMetricPage,
  },
  {
    path: "/dashboard/models/:id/datasets",
    query: {},
    event: Events.datasetsPage,
  },
  {
    path: "/dashboard/models/:id/dataset/:dataset",
    query: {},
    event: Events.datasetDetailPage,
  },
  {
    path: "/dashboard/models/:id/optimize",
    query: {},
    event: Events.optimizePage,
  },
  {
    path: "/dashboard/models/:id/optimize/:optimizeId",
    query: {},
    event: Events.optimizeDetailPage,
  },
  {
    path: "/dashboard/models/:id/config",
    query: {},
    event: Events.configPage,
  },
];

export const commonEmailProviders = [
  "gmail.com",
  "yahoo.com",
  "hotmail.com",
  "outlook.com",
  "aol.com",
  "icloud.com",
  "protonmail.com",
  "mail.com",
  "zoho.com",
  "yandex.com",
];

export const AGGridCellDataType = {
  integer: "integer",
  text: "text",
  float: "float",
  // boolean: "boolean",
  // datetime: "dateString",
  // array: "object",
  // object: "object",
  // image: "text",
};

export const AllModelOptions = [
  "gpt-4",
  "gpt-4o",
  "gpt-4o-audio-preview",
  "gpt-4o-audio-preview-2024-10-01",
  "gpt-4o-mini",
  "gpt-4o-mini-2024-07-18",
  "o1-mini",
  "o1-mini-2024-09-12",
  "o1-preview",
  "o1-preview-2024-09-12",
  "chatgpt-4o-latest",
  "gpt-4o-2024-05-13",
  "gpt-4o-2024-08-06",
  "gpt-4-turbo-preview",
  "gpt-4-0314",
  "gpt-4-0613",
  "gpt-4-32k",
  "gpt-4-32k-0314",
  "gpt-4-32k-0613",
  "gpt-4-turbo",
  "gpt-4-turbo-2024-04-09",
  "gpt-4-1106-preview",
  "gpt-4-0125-preview",
  "gpt-4-vision-preview",
  "gpt-4-1106-vision-preview",
  "gpt-3.5-turbo",
  "gpt-3.5-turbo-0301",
  "gpt-3.5-turbo-0613",
  "gpt-3.5-turbo-1106",
  "gpt-3.5-turbo-0125",
  "gpt-3.5-turbo-16k",
  "gpt-3.5-turbo-16k-0613",
  "ft:gpt-3.5-turbo",
  "ft:gpt-3.5-turbo-0125",
  "ft:gpt-3.5-turbo-1106",
  "ft:gpt-3.5-turbo-0613",
  "ft:gpt-4-0613",
  "ft:gpt-4o-2024-08-06",
  "ft:gpt-4o-mini-2024-07-18",
  "text-embedding-3-large",
  "text-embedding-3-small",
  "text-embedding-ada-002",
  "text-embedding-ada-002-v2",
  "text-moderation-stable",
  "text-moderation-007",
  "text-moderation-latest",
  "256-x-256/dall-e-2",
  "512-x-512/dall-e-2",
  "1024-x-1024/dall-e-2",
  "hd/1024-x-1792/dall-e-3",
  "hd/1792-x-1024/dall-e-3",
  "hd/1024-x-1024/dall-e-3",
  "standard/1024-x-1792/dall-e-3",
  "standard/1792-x-1024/dall-e-3",
  "standard/1024-x-1024/dall-e-3",
  "whisper-1",
  "tts-1",
  "tts-1-hd",
];

export const RunPromptOutputTypeOptions = [
  { value: "string", label: "String" },
  { value: "array", label: "Array" },
  { value: "object", label: "Object" },
  { value: "number", label: "Number" },
];

export const SpanChipColors = {
  chain: { backgroundColor: "#FF3EFA14", borderColor: "#FF3EFA29" },
  retriever: { backgroundColor: "#2F7CF714", borderColor: "#2F7CF729" },
  llm: { backgroundColor: "#FF950014", borderColor: "#FF950029" },
  tool: { backgroundColor: "#F5E65F29", borderColor: "#F5E65F" },
  agent: { backgroundColor: "action.selected", borderColor: "primary.light" },
  embedding: { backgroundColor: "#4CAF5014", borderColor: "#4CAF5029" },
  reranker: { backgroundColor: "#E9690C1F", borderColor: "#E9690C66" },
  unknown: { backgroundColor: "#D92D201F", borderColor: "#D92D2066" },
  guardrail: { backgroundColor: "#8237941F", borderColor: "#82379466" },
  evaluator: { backgroundColor: "background.neutral", borderColor: "divider" },
  conversation: { backgroundColor: "#0EA5E91F", borderColor: "#0EA5E966" },
};

export const SpanTypes = [
  { label: "Chain", value: "chain" },
  { label: "Retriever", value: "retriever" },
  { label: "LLM", value: "llm" },
  { label: "Tool", value: "tool" },
  { label: "Agent", value: "agent" },
  { label: "Embedding", value: "embedding" },
  { label: "Reranker", value: "reranker" },
  { label: "Guardrail", value: "guardrail" },
  { label: "Evaluator", value: "evaluator" },
  { label: "Conversation", value: "conversation" },
  { label: "Unknown", value: "unknown" },
];

export const AllowedEvalSpanTypes = [
  { label: "Tool", value: "tool" },
  { label: "LLM", value: "llm" },
  { label: "Retriever", value: "retriever" },
  { label: "Embedding", value: "embedding" },
  { label: "Agent", value: "agent" },
  { label: "Reranker", value: "reranker" },
  { label: "Chain", value: "chain" },
  { label: "Unknown", value: "unknown" },
  { label: "Guardrail", value: "guardrail" },
  { label: "Evaluator", value: "evaluator" },
  { label: "Conversation", value: "conversation" },
];

export const DevelopDataBlockedChangeDataType = [
  "evaluation",
  "evaluation_tags",
  "evaluation_reason",
  "run_prompt",
  "experiment",
  "optimisation",
  "experiment_evaluation",
  "experiment_evaluation_tags",
  "optimisation_evaluation",
  "annotation_label",
  "optimisation_evaluation_tags",
  "vector_db",
  "extracted_entities",
  "extracted_json",
  "python_code",
  "classification",
  "api_call",
  "conditional",
];

export const ComputerVisionTasks = [
  "Depth Estimation",
  "Image Classification",
  "Object Detection",
  "Image Segmentation",
  "Image To Text",
  "Text to Image",
  "Unconditional Image Generation",
  "Video Classification",
  "Text to Video",
  "Zero Shot Image Classification",
  "Zero Shot Object Detection",
  "Mask Generation",
  "Text to 3D",
  "Image to 3D",
  "Image Feature Extraction",
];

export const AudioTasks = [
  "Text to Speech",
  "Text to Audio",
  "Automatic Speech Recognition",
  "Audio to Audio",
  "Audio Classification",
];

export const NLPTasks = [
  "Text Classification",
  "Token Classification",
  "Table Question Answering",
  "Question Answering",
  "Zero Shot Classification",
  "Translation",
  "Summarization",
  "Feature Extraction",
  "Text Generation",
  "Text2Text Generation",
  "Fill Mask",
  "Sentence Similarity",
  "Table to Text",
  "Multiple Choice",
  "Text Retrieval",
];

export const SizeRanges = [
  { min: 0, max: 1000, label: "size_categories:n<1K" },
  { min: 1000, max: 10000, label: "size_categories:1K<n<10K" },
  { min: 10000, max: 100000, label: "size_categories:10K<n<100K" },
  { min: 100000, max: 1000000, label: "size_categories:100K<n<1M" },
  { min: 1000000, max: 10000000, label: "size_categories:1M<n<10M" },
  { min: 10000000, max: 100000000, label: "size_categories:10M<n<100M" },
  { min: 100000000, max: 1000000000, label: "size_categories:100M<n<1B" },
];

export const SortOptions = [
  "Trending",
  "Likes",
  "Downloads",
  "Most Rows",
  "Least Rows",
];

export const TabularTasks = [
  "Tabular Classification",
  "Tabular Regression",
  "Table to Text",
  "Time Series Forecasting",
];

export const ReinforcementLearningTasks = [
  "Reinforcement Learning",
  "Robotics",
];

export const GraphMLTasks = ["Graph Machine Learning"];

export const ANNOTATION_TYPES = {
  THUMBS_UP_DOWN: "thumbs_up_down",
  STAR: "star",
  TEXT: "text",
  NUMERIC: "numeric",
  CATEGORICAL: "categorical",
};
export const EvaluationReasonFallbackMessage =
  "Something went wrong. Please rerun the evaluation.";
