export const DateRangeButtonOptions = [
  { title: "Custom" },
  { title: "Today" },
  { title: "Yesterday" },
  { title: "7D" },
  { title: "30D" },
  { title: "3M" },
  { title: "6M" },
  { title: "12M" },
];

export const AggregationOption = [
  { label: "Hourly", value: "hourly" },
  { label: "Daily", value: "daily" },
  { label: "Weekly", value: "weekly" },
  { label: "Monthly", value: "monthly" },
];

export const GraphTypes = [
  { label: "Line", value: "line" },
  { label: "Stacked Line", value: "stackedLine" },
  { label: "Column", value: "column" },
  { label: "Stacked Column", value: "stackedColumn" },
  // { label: "Bar", value: "bar" },
  // { label: "Stacked Bar", value: "stackedBar" },
  // { label: "Pie", value: "pie" },
];

//=, !=, >, <, >=, <=, in, not in
export const ReportOperators = [
  { label: "Is", value: "equal" },
  { label: "Is not", value: "notEqual" },
  { label: "Is greater than", value: "greaterThan" },
  { label: "Is less than", value: "lessThan" },
  { label: "Is greater than equal", value: "greaterThanEqualTo" },
  { label: "Is less than equal", value: "lessThanEqualTo" },
];

export const NumberFilterOperators = [
  { label: "Equal", value: "equal" },
  { label: "Not Equal", value: "notEqual" },
  { label: "Greater than", value: "greaterThan" },
  { label: "Greater than or equal to", value: "greaterThanEqualTo" },
  { label: "Less than", value: "lessThan" },
  { label: "Less than or equal to", value: "lessThanEqualTo" },
];

export const AdvanceNumberFilterOperators = [
  { label: "Greater Than", value: "greater_than" },
  { label: "Less Than", value: "less_than" },
  { label: "Equals", value: "equals" },
  { label: "Not Equals", value: "not_equals" },
  { label: "Greater Than Or Equal", value: "greater_than_or_equal" },
  { label: "Less Than Or Equal", value: "less_than_or_equal" },
  { label: "Between", value: "between" },
  { label: "Not In Between", value: "not_in_between" },
];

export const TextFilterOperators = [
  { label: "Contains", value: "contains" }, // Case-insensitive
  { label: "Does Not Contain", value: "not_contains" }, // Case-insensitive
  { label: "Equals", value: "equals" },
  { label: "Not Equals", value: "not_equals" },
  { label: "Starts With", value: "starts_with" },
  { label: "Ends With", value: "ends_with" },
  { label: "Is Null", value: "is_null" },
  { label: "Is Not Null", value: "is_not_null" },
];

export const BooleanFilterOperators = [
  {
    label: "Equals",
    value: "equals",
  },
  {
    label: "Not Equals",
    value: "not_equals",
  },
];

export const FilterDefaultOperators = {
  text: "equals",
  number: "equals",
  date: "equals",
  boolean: "equals",
  enum: "equals",
  option: "equals",
  array: "contains",
};

export const FilterDefaultValues = {
  number: [],
  text: "",
  option: "",
  date: "",
  boolean: null,
  array: [],
};

export const FilterColTypes = {
  Attribute: "SPAN_ATTRIBUTE",
  "System Metrics": "SYSTEM_METRIC",
};

export const FilterTypeMapper = {
  option: "text",
  text: "text",
  number: "number",
  date: "datetime",
  boolean: "boolean",
  array: "array",
};

export const RESPONSE_CODES = {
  SUCCESS: 200,
  SUCCESS_NEW_RESOURCE: 201,
  SUCCESS_WITHOUT_RESPONSE: 204,
  BAD_REQUEST: 400,
  UNAUTHORIZED: 401,
  PAYMENT_REQUIRED: 402,
  NOT_FOUND: 404,
  FORBIDDEN: 403,
  LIMIT_REACHED: 429,
  TOKEN_INVALID: 498,
  INTERNAL_SERVER: 500,
  MAINTENANCE: 503,
};

//Prompt Constants

export const PromptRoles = {
  USER: "user",
  ASSISTANT: "assistant",
  SYSTEM: "system",
};

export const PROMPT_ROLES_DISPLAY_NAMES = {
  [PromptRoles.USER]: "User",
  [PromptRoles.ASSISTANT]: "Assistant",
  [PromptRoles.SYSTEM]: "System",
};
export const PromptContentTypes = {
  TEXT: "text",
  IMAGE_URL: "image_url",
  AUDIO_URL: "audio_url",
  PDF_URL: "pdf_url",
};

export const PromptEditorPlaceholder = {
  user: "Enter instructions or prompt, use {{ to access variables",
  assistant: "Enter response",
  system: "Enter instructions or prompt",
};

export const PrototypeObserveColType = {
  "Annotation Metrics": "ANNOTATION_RUNS",
};

export const TraceSpanColType = {
  "Annotation Metrics": "ANNOTATION",
  Attribute: "SPAN_ATTRIBUTE",
  "System Metrics": "SYSTEM_METRIC",
};

export const AnnotationLabelTypes = {
  STAR: "star",
  CATEGORICAL: "categorical",
  THUMBS_UP_DOWN: "thumbs_up_down",
  TEXT: "text",
  NUMERIC: "numeric",
};

export const PROJECT_SOURCE = {
  SIMULATOR: "simulator",
  OBSERVE: "observe",
  PROTOTYPE: "prototype",
};

export const defaultRowHeightMapping = {
  Short: {
    height: 100,
    autoHeight: false,
  },
  Medium: {
    height: 140,
    autoHeight: false,
  },
  Large: {
    height: 180,
    autoHeight: false,
  },
  "Extra Large": {
    height: 220,
    autoHeight: false,
  },
  // "Full Cell": {
  //     height: undefined,
  //     autoHeight: true,
  // }
};

export const GENERATE_PROMPT_LOADING_STAGES = {
  UNDERSTANDING: {
    icon: "/assets/icons/app/ic_search.svg",
    text: "Analyzing your prompt description...",
  },
  analyze_prompt_requirements: {
    icon: "/assets/icons/components/ic_assemble.svg",
    text: "Gathering requirement...",
  },
  generate_initial_prompt: {
    icon: "/assets/icons/components/ic_falling_start.svg",
    text: "Generating your prompt...",
  },
};

export const IMPROVE_PROMPT_LOADING_STAGES = {
  generate_planning: {
    icon: "/assets/icons/components/ic_think.svg",
    text: "Understanding your prompt...",
  },
  validate_planning: {
    icon: "/assets/icons/components/ic_scope.svg",
    text: "Scoping improvements to prompt...",
  },
  generate_initial_draft: {
    icon: "/assets/icons/components/ic_polish.svg",
    text: "Refining your prompt...",
  },
  generate_refinement_planning: {
    icon: "/assets/icons/components/ic_checking.svg",
    text: "Running checks on improvement...",
  },
  generate_refined_prompt: {
    icon: "/assets/icons/components/ic_falling_start.svg",
    text: "Finalizing your prompt...",
  },
};

export const GENERATE_PROMPT_BUTTONS = [
  {
    name: "Write me an email",
    src: "/assets/icons/generate_prompt/ic_write_email.svg",
  },
  {
    name: "Summarize a document",
    src: "/assets/icons/generate_prompt/ic_summarize_doc.svg",
  },
  {
    name: "Translate code",
    src: "/assets/icons/generate_prompt/ic_translate_code.svg",
  },
  {
    name: "Recommend a product",
    src: "/assets/icons/generate_prompt/ic_recommend_product.svg",
  },
  {
    name: "Content moderation",
    src: "/assets/icons/generate_prompt/ic_content_moderation.svg",
  },
];

export const fileIconByMimeType = {
  "audio/mp3": "/assets/icons/custom/mp3.svg",
  "audio/mpeg": "/assets/icons/custom/mp3.svg", // officially recognized MIMI type for MP3
  "audio/wav": "/assets/icons/custom/wav.svg",
  "audio/x-wav": "/assets/icons/custom/wav.svg",
  "audio/mp4": "/assets/icons/custom/mp4.svg",
  "audio/x-m4a": "/assets/icons/custom/mp4.svg",
  "audio/ogg": "/assets/icons/custom/ogg.svg",
};
export const FILTER_INPUT_TYPES = [
  {
    label: "Number",
    value: "number",
  },
  {
    label: "Text",
    value: "text",
  },
  {
    label: "Boolean",
    value: "boolean",
  },
];

export const BOOLEAN_VALUE_OPTIONS = [
  { label: "True", value: true },
  { label: "False", value: false },
];

export const APP_CONSTANTS = {
  AG_GRID_SELECTION_COLUMN: "ag-Grid-SelectionColumn",
};

export const ORIGIN_OF_COLUMNS = {
  EXPERIMENT: "experiment",
};

export const ROW_TYPE_LABELS = {
  spans: "Spans",
  traces: "Traces",
  sessions: "Sessions",
  voiceCalls: "Voice Calls",
};
