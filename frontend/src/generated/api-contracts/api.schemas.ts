/**
 * Auto-generated from the Django backend OpenAPI schema.
 * To modify these types, update Django serializers/views, regenerate OpenAPI, then run:
 *   yarn contracts:generate
 *
 * Future AGI Management API - management contracts
 * OpenAPI spec version: v1
 */
export interface TokenObtainPairApi {
  /** @minLength 1 */
  email: string;
  /** @minLength 1 */
  password: string;
}

export interface UserBriefApi {
  readonly id?: string;
  /** @minLength 1 */
  readonly name?: string;
  /** @minLength 1 */
  readonly email?: string;
}

export interface GraphListApi {
  readonly id?: string;
  /**
     * Display name
     * @minLength 1
     */
  readonly name?: string;
  /** @minLength 1 */
  readonly description?: string;
  readonly is_template?: boolean;
  readonly created_at?: string;
  readonly updated_at?: string;
  created_by?: UserBriefApi;
  readonly collaborators?: readonly UserBriefApi[];
  readonly active_version_id?: string;
  readonly active_version_number?: number;
  readonly node_count?: number;
}

export interface GraphCreateApi {
  /**
     * @minLength 1
     * @maxLength 255
     */
  name: string;
  description?: string;
}

export interface CellUpdateApi {
  value?: string;
}

export interface ExecuteRequestApi {
  /** Optional list of row IDs to execute. If omitted, all rows are executed. */
  row_ids?: string[];
  /** @minLength 1 */
  task_queue?: string;
}

export interface GraphDetailApi {
  readonly id?: string;
  /**
     * Display name
     * @minLength 1
     */
  readonly name?: string;
  /** @minLength 1 */
  readonly description?: string;
  readonly is_template?: boolean;
  readonly created_at?: string;
  readonly updated_at?: string;
  readonly active_version?: string;
}

export interface GraphUpdateApi {
  /**
     * @minLength 1
     * @maxLength 255
     */
  name?: string;
  description?: string;
}

export interface CreateNodeConnectionApi {
  /** FE-generated UUID */
  id: string;
  source_node_id: string;
  target_node_id: string;
}

export type CreateNodeApiType = typeof CreateNodeApiType[keyof typeof CreateNodeApiType];


export const CreateNodeApiType = {
  subgraph: 'subgraph',
  atomic: 'atomic',
} as const;

export type CreateNodeApiPosition = { [key: string]: unknown };

/**
 * Type of content item
 */
export type MessageContentItemApiType = typeof MessageContentItemApiType[keyof typeof MessageContentItemApiType];


export const MessageContentItemApiType = {
  text: 'text',
  image_url: 'image_url',
  audio_url: 'audio_url',
  pdf_url: 'pdf_url',
} as const;

/**
 * Array of content items
 */
export interface MessageContentItemApi {
  /** Type of content item */
  type: MessageContentItemApiType;
  /** Text content (required when type=text) */
  text?: string;
  /**
     * Image URL (required when type=image_url)
     * @minLength 1
     */
  image_url?: string;
  /**
     * Audio URL (required when type=audio_url)
     * @minLength 1
     */
  audio_url?: string;
  /**
     * PDF URL (required when type=pdf_url)
     * @minLength 1
     */
  pdf_url?: string;
}

/**
 * Array of message objects with id, role, and content array
 */
export interface MessageApi {
  /**
     * Unique identifier for the message (frontend-provided)
     * @minLength 1
     */
  id: string;
  /**
     * Message role (e.g., 'system', 'user', 'assistant')
     * @minLength 1
     */
  role: string;
  /** Array of content items */
  content: MessageContentItemApi[];
}

/**
 * LLM output format: 'text' (plain text), 'json' (free-form JSON), 'json_schema' (structured with schema), UUID string (saved schema reference), or object with 'id' field (prompt playground format). See class docstring for details.
 */
export type PromptTemplateDataApiResponseFormat = { [key: string]: unknown };

/**
 * JSON Schema (Draft 7) for structured outputs. Required when response_format='json_schema'. Example: {'type': 'object', 'properties': {...}, 'required': [...]}
 */
export type PromptTemplateDataApiResponseSchema = { [key: string]: unknown };

export type PromptTemplateDataApiToolsItem = {[key: string]: string};

export type PromptTemplateDataApiToolChoice = { [key: string]: unknown };

export type PromptTemplateDataApiModelDetail = {[key: string]: string};

export type PromptTemplateDataApiVariableNames = {[key: string]: string};

export type PromptTemplateDataApiMetadata = {[key: string]: string};

export interface PromptTemplateDataApi {
  prompt_template_id?: string;
  prompt_version_id?: string;
  /** Array of message objects with id, role, and content array */
  messages: MessageApi[];
  /** LLM output format: 'text' (plain text), 'json' (free-form JSON), 'json_schema' (structured with schema), UUID string (saved schema reference), or object with 'id' field (prompt playground format). See class docstring for details. */
  response_format?: PromptTemplateDataApiResponseFormat;
  /** JSON Schema (Draft 7) for structured outputs. Required when response_format='json_schema'. Example: {'type': 'object', 'properties': {...}, 'required': [...]} */
  response_schema?: PromptTemplateDataApiResponseSchema;
  model?: string;
  temperature?: number;
  max_tokens?: number;
  top_p?: number;
  frequency_penalty?: number;
  presence_penalty?: number;
  output_format?: string;
  tools?: PromptTemplateDataApiToolsItem[];
  tool_choice?: PromptTemplateDataApiToolChoice;
  model_detail?: PromptTemplateDataApiModelDetail;
  variable_names?: PromptTemplateDataApiVariableNames;
  metadata?: PromptTemplateDataApiMetadata;
  commit_message?: string;
  /** Template format: 'mustache' or 'jinja' */
  template_format?: string;
  save_prompt_version?: boolean;
}

export type PortCreateApiDirection = typeof PortCreateApiDirection[keyof typeof PortCreateApiDirection];


export const PortCreateApiDirection = {
  input: 'input',
  output: 'output',
} as const;

export type PortCreateApiDataSchema = { [key: string]: unknown };

export interface PortCreateApi {
  /** FE-generated UUID */
  id: string;
  /**
     * @minLength 1
     * @maxLength 100
     */
  key: string;
  /**
     * @minLength 1
     * @maxLength 100
     */
  display_name: string;
  direction: PortCreateApiDirection;
  data_schema?: PortCreateApiDataSchema;
  ref_port_id?: string;
}

/**
 * List of input mappings from port display_name to source reference
 */
export interface InputMappingApi {
  /**
     * Input port display_name
     * @minLength 1
     */
  key: string;
  /**
     * Source reference in format "NodeName.port_display_name" or null
     * @minLength 1
     */
  value?: string;
}

export interface CreateNodeApi {
  /** FE-generated UUID for the node */
  id: string;
  type: CreateNodeApiType;
  /**
     * @minLength 1
     * @maxLength 255
     */
  name: string;
  node_template_id?: string;
  ref_graph_version_id?: string;
  position?: CreateNodeApiPosition;
  source_node_id?: string;
  prompt_template?: PromptTemplateDataApi;
  ports?: PortCreateApi[];
  /** List of input mappings from port display_name to source reference */
  input_mappings?: InputMappingApi[];
}

/**
 * 'subgraph' for subgraph nodes, 'atomic' for nodes using a NodeTemplate
 */
export type NodeReadApiType = typeof NodeReadApiType[keyof typeof NodeReadApiType];


export const NodeReadApiType = {
  subgraph: 'subgraph',
  atomic: 'atomic',
} as const;

/**
 * Node-specific configuration (validated against node_template.config_schema for atomic nodes)
 */
export type NodeReadApiConfig = { [key: string]: unknown };

/**
 * UI coordinates {"x": 0, "y": 0}
 */
export type NodeReadApiPosition = { [key: string]: unknown };

export type PortReadApiDirection = typeof PortReadApiDirection[keyof typeof PortReadApiDirection];


export const PortReadApiDirection = {
  input: 'input',
  output: 'output',
} as const;

/**
 * JSON Schema for validation
 */
export type PortReadApiDataSchema = { [key: string]: unknown };

export type PortReadApiDefaultValue = { [key: string]: unknown };

export type PortReadApiMetadata = { [key: string]: unknown };

export interface PortReadApi {
  readonly id?: string;
  /**
     * Identifier (e.g., 'prompt', 'result')
     * @minLength 1
     */
  readonly key?: string;
  /**
     * User-facing name for the port
     * @minLength 1
     */
  readonly display_name?: string;
  readonly direction?: PortReadApiDirection;
  /** JSON Schema for validation */
  readonly data_schema?: PortReadApiDataSchema;
  readonly required?: boolean;
  readonly default_value?: PortReadApiDefaultValue;
  readonly metadata?: PortReadApiMetadata;
  readonly ref_port_id?: string;
}

export interface NodeReadApi {
  readonly id?: string;
  /** 'subgraph' for subgraph nodes, 'atomic' for nodes using a NodeTemplate */
  readonly type?: NodeReadApiType;
  /**
     * Display name
     * @minLength 1
     */
  readonly name?: string;
  /** Node-specific configuration (validated against node_template.config_schema for atomic nodes) */
  readonly config?: NodeReadApiConfig;
  /** UI coordinates {"x": 0, "y": 0} */
  readonly position?: NodeReadApiPosition;
  readonly node_template_id?: string;
  readonly ref_graph_version_id?: string;
  /** @minLength 1 */
  readonly ref_graph_name?: string;
  readonly ref_graph_id?: string;
  readonly prompt_template?: string;
  readonly node_connection?: string;
  readonly input_mappings?: string;
  readonly ports?: readonly PortReadApi[];
}

export type UpdateNodeApiPosition = { [key: string]: unknown };

export interface UpdateNodeApi {
  /**
     * @minLength 1
     * @maxLength 255
     */
  name?: string;
  position?: UpdateNodeApiPosition;
  prompt_template?: PromptTemplateDataApi;
  ref_graph_version_id?: string;
  /** List of input mappings from port display_name to source reference */
  input_mappings?: InputMappingApi[];
  /** Replace all OUTPUT ports with this new set (input ports preserved) */
  ports?: PortCreateApi[];
}

export interface UpdatePortApi {
  /**
     * @minLength 1
     * @maxLength 100
     */
  display_name: string;
}

export type NodeTemplateListApiCategories = { [key: string]: unknown };

export interface NodeTemplateListApi {
  readonly id?: string;
  /** @minLength 1 */
  readonly name?: string;
  /** @minLength 1 */
  readonly display_name?: string;
  /** @minLength 1 */
  readonly description?: string;
  /** @minLength 1 */
  readonly icon?: string;
  readonly categories?: NodeTemplateListApiCategories;
}

export type NodeTemplateDetailApiCategories = { [key: string]: unknown };

export type NodeTemplateDetailApiInputDefinition = { [key: string]: unknown };

export type NodeTemplateDetailApiOutputDefinition = { [key: string]: unknown };

export type NodeTemplateDetailApiInputMode = typeof NodeTemplateDetailApiInputMode[keyof typeof NodeTemplateDetailApiInputMode];


export const NodeTemplateDetailApiInputMode = {
  strict: 'strict',
  extensible: 'extensible',
  dynamic: 'dynamic',
} as const;

export type NodeTemplateDetailApiOutputMode = typeof NodeTemplateDetailApiOutputMode[keyof typeof NodeTemplateDetailApiOutputMode];


export const NodeTemplateDetailApiOutputMode = {
  strict: 'strict',
  extensible: 'extensible',
  dynamic: 'dynamic',
} as const;

/**
 * JSON Schema for Node.config validation
 */
export type NodeTemplateDetailApiConfigSchema = { [key: string]: unknown };

export interface NodeTemplateDetailApi {
  readonly id?: string;
  /** @minLength 1 */
  readonly name?: string;
  /** @minLength 1 */
  readonly display_name?: string;
  /** @minLength 1 */
  readonly description?: string;
  /** @minLength 1 */
  readonly icon?: string;
  readonly categories?: NodeTemplateDetailApiCategories;
  readonly input_definition?: NodeTemplateDetailApiInputDefinition;
  readonly output_definition?: NodeTemplateDetailApiOutputDefinition;
  readonly input_mode?: NodeTemplateDetailApiInputMode;
  readonly output_mode?: NodeTemplateDetailApiOutputMode;
  /** JSON Schema for Node.config validation */
  readonly config_schema?: NodeTemplateDetailApiConfigSchema;
}

export type AgentccRequestLogApiMetadata = { [key: string]: unknown };

export interface AgentccRequestLogApi {
  readonly id?: string;
  /** @minLength 1 */
  readonly request_id?: string;
  /** @minLength 1 */
  readonly model?: string;
  /** @minLength 1 */
  readonly provider?: string;
  /** @minLength 1 */
  readonly resolved_model?: string;
  readonly latency_ms?: number;
  readonly started_at?: string;
  readonly input_tokens?: number;
  readonly output_tokens?: number;
  readonly total_tokens?: number;
  readonly cost?: string;
  readonly status_code?: number;
  readonly is_stream?: boolean;
  readonly is_error?: boolean;
  /** @minLength 1 */
  readonly error_message?: string;
  readonly cache_hit?: boolean;
  readonly fallback_used?: boolean;
  readonly guardrail_triggered?: boolean;
  /** @minLength 1 */
  readonly api_key_id?: string;
  /** @minLength 1 */
  readonly user_id?: string;
  /** @minLength 1 */
  readonly session_id?: string;
  /** @minLength 1 */
  readonly routing_strategy?: string;
  readonly metadata?: AgentccRequestLogApiMetadata;
  readonly organization?: string;
  readonly workspace?: string;
  readonly created_at?: string;
}

export type AgentccAPIKeyApiStatus = typeof AgentccAPIKeyApiStatus[keyof typeof AgentccAPIKeyApiStatus];


export const AgentccAPIKeyApiStatus = {
  active: 'active',
  revoked: 'revoked',
  expired: 'expired',
} as const;

export type AgentccAPIKeyApiAllowedModels = { [key: string]: unknown };

export type AgentccAPIKeyApiAllowedProviders = { [key: string]: unknown };

export type AgentccAPIKeyApiMetadata = { [key: string]: unknown };

export interface AgentccAPIKeyApi {
  readonly id?: string;
  project?: string;
  user?: string;
  /** @minLength 1 */
  readonly gateway_key_id?: string;
  /** @minLength 1 */
  readonly key_prefix?: string;
  /**
     * @minLength 1
     * @maxLength 255
     */
  name: string;
  /** @maxLength 255 */
  owner?: string;
  readonly status?: AgentccAPIKeyApiStatus;
  allowed_models?: AgentccAPIKeyApiAllowedModels;
  allowed_providers?: AgentccAPIKeyApiAllowedProviders;
  metadata?: AgentccAPIKeyApiMetadata;
  last_used_at?: string;
  expires_at?: string;
  readonly organization?: string;
  readonly workspace?: string;
  readonly created_at?: string;
  readonly updated_at?: string;
}

export type AgentccBlocklistApiWords = { [key: string]: unknown };

export interface AgentccBlocklistApi {
  readonly id?: string;
  readonly organization?: string;
  /**
     * @minLength 1
     * @maxLength 255
     */
  name: string;
  description?: string;
  words?: AgentccBlocklistApiWords;
  is_active?: boolean;
  readonly created_at?: string;
  readonly updated_at?: string;
}

export type AgentccCustomPropertySchemaApiPropertyType = typeof AgentccCustomPropertySchemaApiPropertyType[keyof typeof AgentccCustomPropertySchemaApiPropertyType];


export const AgentccCustomPropertySchemaApiPropertyType = {
  string: 'string',
  number: 'number',
  boolean: 'boolean',
  enum: 'enum',
} as const;

export type AgentccCustomPropertySchemaApiAllowedValues = { [key: string]: unknown };

export type AgentccCustomPropertySchemaApiDefaultValue = { [key: string]: unknown };

export interface AgentccCustomPropertySchemaApi {
  readonly id?: string;
  readonly organization?: string;
  project?: string;
  /**
     * @minLength 1
     * @maxLength 255
     */
  name: string;
  description?: string;
  property_type?: AgentccCustomPropertySchemaApiPropertyType;
  required?: boolean;
  allowed_values?: AgentccCustomPropertySchemaApiAllowedValues;
  default_value?: AgentccCustomPropertySchemaApiDefaultValue;
  readonly created_at?: string;
  readonly updated_at?: string;
}

export type AgentccEmailAlertApiRecipients = { [key: string]: unknown };

export type AgentccEmailAlertApiEvents = { [key: string]: unknown };

export type AgentccEmailAlertApiThresholds = { [key: string]: unknown };

export type AgentccEmailAlertApiProvider = typeof AgentccEmailAlertApiProvider[keyof typeof AgentccEmailAlertApiProvider];


export const AgentccEmailAlertApiProvider = {
  sendgrid: 'sendgrid',
  resend: 'resend',
  smtp: 'smtp',
} as const;

export interface AgentccEmailAlertApi {
  readonly id?: string;
  readonly organization?: string;
  /**
     * @minLength 1
     * @maxLength 255
     */
  name: string;
  recipients?: AgentccEmailAlertApiRecipients;
  events?: AgentccEmailAlertApiEvents;
  thresholds?: AgentccEmailAlertApiThresholds;
  provider?: AgentccEmailAlertApiProvider;
  readonly provider_config?: string;
  is_active?: boolean;
  /**
     * @minimum -2147483648
     * @maximum 2147483647
     */
  cooldown_minutes?: number;
  readonly last_triggered_at?: string;
  readonly created_at?: string;
  readonly updated_at?: string;
}

export type AgentccGuardrailFeedbackApiFeedback = typeof AgentccGuardrailFeedbackApiFeedback[keyof typeof AgentccGuardrailFeedbackApiFeedback];


export const AgentccGuardrailFeedbackApiFeedback = {
  correct: 'correct',
  false_positive: 'false_positive',
  false_negative: 'false_negative',
  unsure: 'unsure',
} as const;

export interface AgentccGuardrailFeedbackApi {
  readonly id?: string;
  readonly organization?: string;
  request_log: string;
  /**
     * @minLength 1
     * @maxLength 255
     */
  check_name: string;
  feedback: AgentccGuardrailFeedbackApiFeedback;
  comment?: string;
  readonly created_by?: string;
  readonly created_at?: string;
  readonly updated_at?: string;
}

export type AgentccGuardrailPolicyApiScope = typeof AgentccGuardrailPolicyApiScope[keyof typeof AgentccGuardrailPolicyApiScope];


export const AgentccGuardrailPolicyApiScope = {
  global: 'global',
  project: 'project',
  key: 'key',
} as const;

export type AgentccGuardrailPolicyApiChecks = { [key: string]: unknown };

export type AgentccGuardrailPolicyApiMode = typeof AgentccGuardrailPolicyApiMode[keyof typeof AgentccGuardrailPolicyApiMode];


export const AgentccGuardrailPolicyApiMode = {
  enforce: 'enforce',
  monitor: 'monitor',
} as const;

export type AgentccGuardrailPolicyApiAppliedKeys = { [key: string]: unknown };

export type AgentccGuardrailPolicyApiAppliedProjects = { [key: string]: unknown };

export interface AgentccGuardrailPolicyApi {
  readonly id?: string;
  readonly organization?: string;
  /**
     * @minLength 1
     * @maxLength 255
     */
  name: string;
  description?: string;
  scope?: AgentccGuardrailPolicyApiScope;
  checks?: AgentccGuardrailPolicyApiChecks;
  mode?: AgentccGuardrailPolicyApiMode;
  is_active?: boolean;
  /**
     * @minimum -2147483648
     * @maximum 2147483647
     */
  priority?: number;
  applied_keys?: AgentccGuardrailPolicyApiAppliedKeys;
  applied_projects?: AgentccGuardrailPolicyApiAppliedProjects;
  readonly created_at?: string;
  readonly updated_at?: string;
}

export type AgentccOrgConfigApiGuardrails = { [key: string]: unknown };

export type AgentccOrgConfigApiRouting = { [key: string]: unknown };

export type AgentccOrgConfigApiCache = { [key: string]: unknown };

export type AgentccOrgConfigApiRateLimiting = { [key: string]: unknown };

export type AgentccOrgConfigApiBudgets = { [key: string]: unknown };

export type AgentccOrgConfigApiCostTracking = { [key: string]: unknown };

export type AgentccOrgConfigApiIpAcl = { [key: string]: unknown };

export type AgentccOrgConfigApiAlerting = { [key: string]: unknown };

export type AgentccOrgConfigApiPrivacy = { [key: string]: unknown };

export type AgentccOrgConfigApiToolPolicy = { [key: string]: unknown };

export type AgentccOrgConfigApiMcp = { [key: string]: unknown };

export type AgentccOrgConfigApiA2a = { [key: string]: unknown };

export type AgentccOrgConfigApiAudit = { [key: string]: unknown };

export type AgentccOrgConfigApiModelDatabase = { [key: string]: unknown };

export type AgentccOrgConfigApiModelMap = { [key: string]: unknown };

export interface AgentccOrgConfigApi {
  readonly id?: string;
  readonly organization?: string;
  readonly version?: number;
  guardrails?: AgentccOrgConfigApiGuardrails;
  routing?: AgentccOrgConfigApiRouting;
  cache?: AgentccOrgConfigApiCache;
  rate_limiting?: AgentccOrgConfigApiRateLimiting;
  budgets?: AgentccOrgConfigApiBudgets;
  cost_tracking?: AgentccOrgConfigApiCostTracking;
  ip_acl?: AgentccOrgConfigApiIpAcl;
  alerting?: AgentccOrgConfigApiAlerting;
  privacy?: AgentccOrgConfigApiPrivacy;
  tool_policy?: AgentccOrgConfigApiToolPolicy;
  mcp?: AgentccOrgConfigApiMcp;
  a2a?: AgentccOrgConfigApiA2a;
  audit?: AgentccOrgConfigApiAudit;
  model_database?: AgentccOrgConfigApiModelDatabase;
  model_map?: AgentccOrgConfigApiModelMap;
  readonly is_active?: boolean;
  readonly created_by?: string;
  change_description?: string;
  readonly created_at?: string;
  readonly updated_at?: string;
}

export type AgentccProviderCredentialApiModelsList = { [key: string]: unknown };

export type AgentccProviderCredentialApiExtraConfig = { [key: string]: unknown };

export interface AgentccProviderCredentialApi {
  readonly id?: string;
  readonly organization?: string;
  readonly workspace?: string;
  /**
     * @minLength 1
     * @maxLength 100
     */
  provider_name: string;
  /** @maxLength 255 */
  display_name?: string;
  readonly credentials?: string;
  /** @maxLength 500 */
  base_url?: string;
  /**
     * @minLength 1
     * @maxLength 50
     */
  api_format?: string;
  models_list?: AgentccProviderCredentialApiModelsList;
  /**
     * @minimum -2147483648
     * @maximum 2147483647
     */
  default_timeout_seconds?: number;
  /**
     * @minimum -2147483648
     * @maximum 2147483647
     */
  max_concurrent?: number;
  /**
     * @minimum -2147483648
     * @maximum 2147483647
     */
  conn_pool_size?: number;
  extra_config?: AgentccProviderCredentialApiExtraConfig;
  is_active?: boolean;
  last_rotated_at?: string;
  readonly created_at?: string;
  readonly updated_at?: string;
}

export type AgentccRequestLogDetailApiMetadata = { [key: string]: unknown };

export type AgentccRequestLogDetailApiRequestBody = { [key: string]: unknown };

export type AgentccRequestLogDetailApiResponseBody = { [key: string]: unknown };

export type AgentccRequestLogDetailApiRequestHeaders = { [key: string]: unknown };

export type AgentccRequestLogDetailApiResponseHeaders = { [key: string]: unknown };

export type AgentccRequestLogDetailApiGuardrailResults = { [key: string]: unknown };

export interface AgentccRequestLogDetailApi {
  readonly id?: string;
  /** @minLength 1 */
  readonly request_id?: string;
  /** @minLength 1 */
  readonly model?: string;
  /** @minLength 1 */
  readonly provider?: string;
  /** @minLength 1 */
  readonly resolved_model?: string;
  readonly latency_ms?: number;
  readonly started_at?: string;
  readonly input_tokens?: number;
  readonly output_tokens?: number;
  readonly total_tokens?: number;
  readonly cost?: string;
  readonly status_code?: number;
  readonly is_stream?: boolean;
  readonly is_error?: boolean;
  /** @minLength 1 */
  readonly error_message?: string;
  readonly cache_hit?: boolean;
  readonly fallback_used?: boolean;
  readonly guardrail_triggered?: boolean;
  /** @minLength 1 */
  readonly api_key_id?: string;
  /** @minLength 1 */
  readonly user_id?: string;
  /** @minLength 1 */
  readonly session_id?: string;
  /** @minLength 1 */
  readonly routing_strategy?: string;
  readonly metadata?: AgentccRequestLogDetailApiMetadata;
  readonly request_body?: AgentccRequestLogDetailApiRequestBody;
  readonly response_body?: AgentccRequestLogDetailApiResponseBody;
  readonly request_headers?: AgentccRequestLogDetailApiRequestHeaders;
  readonly response_headers?: AgentccRequestLogDetailApiResponseHeaders;
  readonly guardrail_results?: AgentccRequestLogDetailApiGuardrailResults;
  readonly organization?: string;
  readonly workspace?: string;
  readonly created_at?: string;
}

export type AgentccRoutingPolicyApiConfig = { [key: string]: unknown };

export interface AgentccRoutingPolicyApi {
  readonly id?: string;
  readonly organization?: string;
  /**
     * @minLength 1
     * @maxLength 255
     */
  name: string;
  description?: string;
  readonly version?: number;
  config?: AgentccRoutingPolicyApiConfig;
  is_active?: boolean;
  readonly created_by?: string;
  readonly created_at?: string;
  readonly updated_at?: string;
}

export type AgentccSessionApiStatus = typeof AgentccSessionApiStatus[keyof typeof AgentccSessionApiStatus];


export const AgentccSessionApiStatus = {
  active: 'active',
  closed: 'closed',
} as const;

export type AgentccSessionApiMetadata = { [key: string]: unknown };

export interface AgentccSessionApi {
  readonly id?: string;
  readonly organization?: string;
  /**
     * @minLength 1
     * @maxLength 255
     */
  session_id: string;
  /** @maxLength 255 */
  name?: string;
  status?: AgentccSessionApiStatus;
  metadata?: AgentccSessionApiMetadata;
  readonly created_at?: string;
  readonly updated_at?: string;
}

export type AgentccShadowExperimentApiStatus = typeof AgentccShadowExperimentApiStatus[keyof typeof AgentccShadowExperimentApiStatus];


export const AgentccShadowExperimentApiStatus = {
  active: 'active',
  paused: 'paused',
  completed: 'completed',
} as const;

/**
 * Extra configuration
 */
export type AgentccShadowExperimentApiConfig = { [key: string]: unknown };

export interface AgentccShadowExperimentApi {
  readonly id?: string;
  /**
     * @minLength 1
     * @maxLength 128
     */
  name: string;
  description?: string;
  /**
     * Production model being tested against
     * @minLength 1
     * @maxLength 255
     */
  source_model: string;
  /**
     * Shadow model receiving mirrored traffic
     * @minLength 1
     * @maxLength 255
     */
  shadow_model: string;
  /**
     * Provider for the shadow model
     * @minLength 1
     * @maxLength 128
     */
  shadow_provider: string;
  /** Fraction of traffic to mirror (0.0–1.0) */
  sample_rate?: number;
  status?: AgentccShadowExperimentApiStatus;
  readonly total_comparisons?: number;
  /** Extra configuration */
  config?: AgentccShadowExperimentApiConfig;
  readonly created_by?: string;
  readonly created_at?: string;
  readonly updated_at?: string;
}

export interface AgentccShadowResultApi {
  readonly id?: string;
  readonly experiment?: string;
  /** @minLength 1 */
  readonly request_id?: string;
  /** @minLength 1 */
  readonly source_model?: string;
  /** @minLength 1 */
  readonly shadow_model?: string;
  /** @minLength 1 */
  readonly source_response?: string;
  /** @minLength 1 */
  readonly shadow_response?: string;
  readonly source_latency_ms?: number;
  readonly shadow_latency_ms?: number;
  readonly source_tokens?: number;
  readonly shadow_tokens?: number;
  readonly source_status_code?: number;
  readonly shadow_status_code?: number;
  /** @minLength 1 */
  readonly shadow_error?: string;
  /** @minLength 1 */
  readonly prompt_hash?: string;
  readonly created_at?: string;
}

export type AgentccWebhookEventApiPayload = { [key: string]: unknown };

export type AgentccWebhookEventApiStatus = typeof AgentccWebhookEventApiStatus[keyof typeof AgentccWebhookEventApiStatus];


export const AgentccWebhookEventApiStatus = {
  pending: 'pending',
  delivered: 'delivered',
  failed: 'failed',
  dead_letter: 'dead_letter',
} as const;

export interface AgentccWebhookEventApi {
  readonly id?: string;
  readonly organization?: string;
  readonly webhook?: string;
  /** @minLength 1 */
  readonly webhook_name?: string;
  /** @minLength 1 */
  readonly event_type?: string;
  readonly payload?: AgentccWebhookEventApiPayload;
  readonly status?: AgentccWebhookEventApiStatus;
  readonly attempts?: number;
  readonly max_attempts?: number;
  readonly last_attempt_at?: string;
  readonly last_response_code?: number;
  /** @minLength 1 */
  readonly last_error?: string;
  readonly next_retry_at?: string;
  readonly created_at?: string;
}

export type AgentccWebhookApiEvents = { [key: string]: unknown };

export type AgentccWebhookApiHeaders = { [key: string]: unknown };

export interface AgentccWebhookApi {
  readonly id?: string;
  readonly organization?: string;
  /**
     * @minLength 1
     * @maxLength 255
     */
  name: string;
  /**
     * @minLength 1
     * @maxLength 2048
     */
  url: string;
  /** @maxLength 255 */
  secret?: string;
  events?: AgentccWebhookApiEvents;
  is_active?: boolean;
  headers?: AgentccWebhookApiHeaders;
  description?: string;
  readonly created_at?: string;
  readonly updated_at?: string;
}

export interface ToolParameterApi {
  /** @minLength 1 */
  readonly name?: string;
  /** @minLength 1 */
  readonly type?: string;
  readonly description?: string;
  readonly required?: boolean;
}

export type ToolDiscoveryItemApiReturns = { [key: string]: unknown };

export type ToolDiscoveryItemApiMetadata = { [key: string]: unknown };

export interface ToolDiscoveryItemApi {
  /** @minLength 1 */
  readonly name?: string;
  /** @minLength 1 */
  readonly category?: string;
  readonly description?: string;
  readonly parameters?: readonly ToolParameterApi[];
  readonly returns?: ToolDiscoveryItemApiReturns;
  readonly metadata?: ToolDiscoveryItemApiMetadata;
}

export interface ToolDiscoveryResultApi {
  readonly tools?: readonly ToolDiscoveryItemApi[];
  readonly categories?: readonly string[];
  readonly total?: number;
}

export interface ToolDiscoveryResponseApi {
  status?: boolean;
  result: ToolDiscoveryResultApi;
}

export type DeploymentInfoResultApiMode = typeof DeploymentInfoResultApiMode[keyof typeof DeploymentInfoResultApiMode];


export const DeploymentInfoResultApiMode = {
  oss: 'oss',
  ee: 'ee',
  cloud: 'cloud',
} as const;

export interface DeploymentInfoResultApi {
  mode: DeploymentInfoResultApiMode;
}

export interface DeploymentInfoResponseApi {
  status?: boolean;
  result: DeploymentInfoResultApi;
}

export type ClickHouseHealthResponseApiStatus = typeof ClickHouseHealthResponseApiStatus[keyof typeof ClickHouseHealthResponseApiStatus];


export const ClickHouseHealthResponseApiStatus = {
  healthy: 'healthy',
  degraded: 'degraded',
  unhealthy: 'unhealthy',
  disabled: 'disabled',
} as const;

export type ClickHouseHealthResponseApiCdcLag = {[key: string]: number};

export type ClickHouseHealthResponseApiRouting = {[key: string]: { [key: string]: unknown }};

export interface ClickHouseHealthResponseApi {
  status: ClickHouseHealthResponseApiStatus;
  clickhouse_connected: boolean;
  cdc_lag: ClickHouseHealthResponseApiCdcLag;
  routing: ClickHouseHealthResponseApiRouting;
  /** @minLength 1 */
  error?: string;
}

export type LangfuseHealthResponseApiStatus = typeof LangfuseHealthResponseApiStatus[keyof typeof LangfuseHealthResponseApiStatus];


export const LangfuseHealthResponseApiStatus = {
  OK: 'OK',
} as const;

export interface LangfuseHealthResponseApi {
  status: LangfuseHealthResponseApiStatus;
  /** @minLength 1 */
  version: string;
}

export type LangfuseIngestionEventApiBody = { [key: string]: unknown };

export interface LangfuseIngestionEventApi {
  id?: string;
  /** @minLength 1 */
  type: string;
  body?: LangfuseIngestionEventApiBody;
  timestamp?: string;
}

export interface LangfuseIngestionRequestApi {
  batch: LangfuseIngestionEventApi[];
}

export interface LangfuseIngestionSuccessApi {
  /** @minLength 1 */
  id: string;
  status: number;
}

export interface LangfuseIngestionErrorApi {
  /** @minLength 1 */
  id: string;
  status: number;
  /** @minLength 1 */
  message: string;
}

export interface LangfuseIngestionResponseApi {
  successes: LangfuseIngestionSuccessApi[];
  errors: LangfuseIngestionErrorApi[];
}

export interface OTLPHTTPTraceResponseApi { [key: string]: unknown }

export interface OTLPHTTPErrorResponseApi {
  /** @minLength 1 */
  detail: string;
}

export type LangfuseTracesResponseApiDataItem = { [key: string]: unknown };

export interface LangfuseTracesMetaApi {
  page: number;
  limit: number;
  total_items: number;
  total_pages: number;
}

export interface LangfuseTracesResponseApi {
  data: LangfuseTracesResponseApiDataItem[];
  meta: LangfuseTracesMetaApi;
}

export type SpanAttributeDetailResponseApiType = typeof SpanAttributeDetailResponseApiType[keyof typeof SpanAttributeDetailResponseApiType];


export const SpanAttributeDetailResponseApiType = {
  string: 'string',
  number: 'number',
  boolean: 'boolean',
} as const;

export type SpanAttributeTopValueApiValue = { [key: string]: unknown };

export interface SpanAttributeTopValueApi {
  value: SpanAttributeTopValueApiValue;
  count: number;
  percentage: number;
}

export interface SpanAttributeDetailResponseApi {
  /** @minLength 1 */
  key: string;
  type: SpanAttributeDetailResponseApiType;
  count: number;
  unique_values?: number;
  top_values?: SpanAttributeTopValueApi[];
  min?: number;
  max?: number;
  avg?: number;
  p50?: number;
  p95?: number;
}

export type ApiErrorResponseApiResult = { [key: string]: unknown };

export type ApiErrorResponseApiMessage = { [key: string]: unknown };

export type ApiErrorResponseApiError = { [key: string]: unknown };

export interface ApiErrorResponseApi {
  status?: boolean;
  result?: ApiErrorResponseApiResult;
  message?: ApiErrorResponseApiMessage;
  error?: ApiErrorResponseApiError;
}

export type SpanAttributeKeyApiType = typeof SpanAttributeKeyApiType[keyof typeof SpanAttributeKeyApiType];


export const SpanAttributeKeyApiType = {
  string: 'string',
  number: 'number',
  boolean: 'boolean',
} as const;

export interface SpanAttributeKeyApi {
  /** @minLength 1 */
  key: string;
  type: SpanAttributeKeyApiType;
  count: number;
}

export interface SpanAttributeKeysResponseApi {
  result: SpanAttributeKeyApi[];
}

export type SpanAttributeValueApiValue = { [key: string]: unknown };

export interface SpanAttributeValueApi {
  value: SpanAttributeValueApiValue;
  count: number;
}

export interface SpanAttributeValuesResponseApi {
  result: SpanAttributeValueApi[];
}

export interface CallWebsocketRequestApi {
  /** @minLength 1 */
  message: string;
  send_to_uuid?: boolean;
  uuid?: string;
}

export interface CallWebsocketResponseApi {
  status?: boolean;
  /** @minLength 1 */
  result: string;
}

export interface HealthCheckResponseApi {
  status?: boolean;
  /** @minLength 1 */
  result: string;
}

export type IntegrationConnectionListApiPlatform = typeof IntegrationConnectionListApiPlatform[keyof typeof IntegrationConnectionListApiPlatform];


export const IntegrationConnectionListApiPlatform = {
  langfuse: 'langfuse',
  datadog: 'datadog',
  posthog: 'posthog',
  pagerduty: 'pagerduty',
  mixpanel: 'mixpanel',
  cloud_storage: 'cloud_storage',
  message_queue: 'message_queue',
  linear: 'linear',
} as const;

export type IntegrationConnectionListApiStatus = typeof IntegrationConnectionListApiStatus[keyof typeof IntegrationConnectionListApiStatus];


export const IntegrationConnectionListApiStatus = {
  active: 'active',
  paused: 'paused',
  error: 'error',
  syncing: 'syncing',
  backfilling: 'backfilling',
} as const;

export type IntegrationConnectionListApiBackfillProgress = { [key: string]: unknown };

export interface IntegrationConnectionListApi {
  readonly id?: string;
  platform: IntegrationConnectionListApiPlatform;
  /**
     * @minLength 1
     * @maxLength 255
     */
  display_name: string;
  /**
     * @minLength 1
     * @maxLength 500
     */
  host_url: string;
  status?: IntegrationConnectionListApiStatus;
  status_message?: string;
  /**
     * @minLength 1
     * @maxLength 255
     */
  external_project_name: string;
  last_synced_at?: string;
  /**
     * @minimum 0
     * @maximum 2147483647
     */
  total_traces_synced?: number;
  /**
     * @minimum 0
     * @maximum 2147483647
     */
  total_spans_synced?: number;
  /**
     * @minimum 0
     * @maximum 2147483647
     */
  total_scores_synced?: number;
  backfill_completed?: boolean;
  backfill_progress?: IntegrationConnectionListApiBackfillProgress;
  /**
     * @minimum 60
     * @maximum 1800
     */
  sync_interval_seconds?: number;
  readonly created_at?: string;
}

export type IntegrationConnectionDetailApiPlatform = typeof IntegrationConnectionDetailApiPlatform[keyof typeof IntegrationConnectionDetailApiPlatform];


export const IntegrationConnectionDetailApiPlatform = {
  langfuse: 'langfuse',
  datadog: 'datadog',
  posthog: 'posthog',
  pagerduty: 'pagerduty',
  mixpanel: 'mixpanel',
  cloud_storage: 'cloud_storage',
  message_queue: 'message_queue',
  linear: 'linear',
} as const;

export type IntegrationConnectionDetailApiStatus = typeof IntegrationConnectionDetailApiStatus[keyof typeof IntegrationConnectionDetailApiStatus];


export const IntegrationConnectionDetailApiStatus = {
  active: 'active',
  paused: 'paused',
  error: 'error',
  syncing: 'syncing',
  backfilling: 'backfilling',
} as const;

export type IntegrationConnectionDetailApiSyncCursor = { [key: string]: unknown };

export type IntegrationConnectionDetailApiBackfillProgress = { [key: string]: unknown };

export interface IntegrationConnectionDetailApi {
  readonly id?: string;
  platform: IntegrationConnectionDetailApiPlatform;
  /**
     * @minLength 1
     * @maxLength 255
     */
  display_name: string;
  /**
     * @minLength 1
     * @maxLength 500
     */
  host_url: string;
  status?: IntegrationConnectionDetailApiStatus;
  status_message?: string;
  /**
     * @minLength 1
     * @maxLength 255
     */
  external_project_name: string;
  project?: string;
  readonly project_name?: string;
  readonly public_key_display?: string;
  readonly secret_key_display?: string;
  last_synced_at?: string;
  sync_cursor?: IntegrationConnectionDetailApiSyncCursor;
  /**
     * @minimum 60
     * @maximum 1800
     */
  sync_interval_seconds?: number;
  last_error_notified_at?: string;
  backfill_from?: string;
  backfill_completed?: boolean;
  backfill_progress?: IntegrationConnectionDetailApiBackfillProgress;
  /**
     * @minimum 0
     * @maximum 2147483647
     */
  total_traces_synced?: number;
  /**
     * @minimum 0
     * @maximum 2147483647
     */
  total_spans_synced?: number;
  /**
     * @minimum 0
     * @maximum 2147483647
     */
  total_scores_synced?: number;
  readonly created_at?: string;
  readonly updated_at?: string;
  created_by?: string;
}

export type SyncLogApiStatus = typeof SyncLogApiStatus[keyof typeof SyncLogApiStatus];


export const SyncLogApiStatus = {
  success: 'success',
  partial: 'partial',
  failed: 'failed',
  rate_limited: 'rate_limited',
  no_new_data: 'no_new_data',
} as const;

export type SyncLogApiErrorDetails = { [key: string]: unknown };

export interface SyncLogApi {
  readonly id?: string;
  readonly connection?: string;
  readonly status?: SyncLogApiStatus;
  readonly started_at?: string;
  readonly completed_at?: string;
  readonly traces_fetched?: number;
  readonly traces_created?: number;
  readonly traces_updated?: number;
  readonly spans_synced?: number;
  readonly scores_synced?: number;
  /** @minLength 1 */
  readonly error_message?: string;
  readonly error_details?: SyncLogApiErrorDetails;
  readonly sync_from?: string;
  readonly sync_to?: string;
}

export type AIFilterRequestApiMode = typeof AIFilterRequestApiMode[keyof typeof AIFilterRequestApiMode];


export const AIFilterRequestApiMode = {
  build_filters: 'build_filters',
  select_fields: 'select_fields',
  smart: 'smart',
} as const;

export type AIFilterRequestApiSource = typeof AIFilterRequestApiSource[keyof typeof AIFilterRequestApiSource];


export const AIFilterRequestApiSource = {
  traces: 'traces',
  dataset: 'dataset',
} as const;

export type AIFilterSchemaFieldApiChoicesItem = { [key: string]: unknown };

export type AIFilterSchemaFieldApiChoiceLabels = {[key: string]: string};

export interface AIFilterSchemaFieldApi {
  /** @minLength 1 */
  field: string;
  label?: string;
  type?: string;
  category?: string;
  operators?: string[];
  choices?: AIFilterSchemaFieldApiChoicesItem[];
  choice_labels?: AIFilterSchemaFieldApiChoiceLabels;
}

export interface AIFilterRequestApi {
  mode?: AIFilterRequestApiMode;
  /** @minLength 1 */
  query: string;
  schema: AIFilterSchemaFieldApi[];
  source?: AIFilterRequestApiSource;
  project_id?: string;
  dataset_id?: string;
}

export type AIFilterConditionApiValue = { [key: string]: unknown };

export interface AIFilterConditionApi {
  /** @minLength 1 */
  field: string;
  /** @minLength 1 */
  operator: string;
  value?: AIFilterConditionApiValue;
}

export interface AIFilterResultApi {
  filters?: AIFilterConditionApi[];
  fields?: string[];
}

export interface AIFilterResponseApi {
  status?: boolean;
  result: AIFilterResultApi;
}

export type AnnotationQueueApiStatus = typeof AnnotationQueueApiStatus[keyof typeof AnnotationQueueApiStatus];


export const AnnotationQueueApiStatus = {
  draft: 'draft',
  active: 'active',
  paused: 'paused',
  completed: 'completed',
} as const;

export type AnnotationQueueApiAssignmentStrategy = typeof AnnotationQueueApiAssignmentStrategy[keyof typeof AnnotationQueueApiAssignmentStrategy];


export const AnnotationQueueApiAssignmentStrategy = {
  manual: 'manual',
  round_robin: 'round_robin',
  load_balanced: 'load_balanced',
} as const;

export type AnnotationQueueApiAnnotatorRoles = {[key: string]: { [key: string]: unknown }};

export interface QueueLabelNestedApi {
  readonly id?: string;
  label_id: string;
  /** @minLength 1 */
  readonly name?: string;
  /** @minLength 1 */
  readonly type?: string;
  required?: boolean;
  /**
     * @minimum -2147483648
     * @maximum 2147483647
     */
  order?: number;
}

export interface QueueAnnotatorNestedApi {
  readonly id?: string;
  user_id: string;
  /** @minLength 1 */
  readonly name?: string;
  /** @minLength 1 */
  readonly email?: string;
  /** @minLength 1 */
  role?: string;
  readonly roles?: string;
}

export interface AnnotationQueueApi {
  readonly id?: string;
  /**
     * @minLength 1
     * @maxLength 255
     */
  name: string;
  description?: string;
  instructions?: string;
  readonly status?: AnnotationQueueApiStatus;
  assignment_strategy?: AnnotationQueueApiAssignmentStrategy;
  /**
     * @minimum -2147483648
     * @maximum 2147483647
     */
  annotations_required?: number;
  /**
     * @minimum -2147483648
     * @maximum 2147483647
     */
  reservation_timeout_minutes?: number;
  requires_review?: boolean;
  /** When enabled, all queue members can annotate any item without explicit assignment. */
  auto_assign?: boolean;
  readonly organization?: string;
  readonly project?: string;
  readonly dataset?: string;
  readonly agent_definition?: string;
  readonly is_default?: boolean;
  readonly labels?: readonly QueueLabelNestedApi[];
  readonly annotators?: readonly QueueAnnotatorNestedApi[];
  label_ids?: string[];
  annotator_ids?: string[];
  annotator_roles?: AnnotationQueueApiAnnotatorRoles;
  readonly label_count?: number;
  readonly annotator_count?: number;
  readonly item_count?: number;
  readonly completed_count?: number;
  readonly created_by?: string;
  /** @minLength 1 */
  readonly created_by_name?: string;
  readonly viewer_role?: string;
  readonly viewer_roles?: string;
  readonly created_at?: string;
}

export type QueueJsonResponseApiResult = { [key: string]: unknown };

export interface QueueJsonResponseApi {
  status?: boolean;
  result: QueueJsonResponseApiResult;
}

export interface QueueDefaultRequestApi {
  project_id?: string;
  dataset_id?: string;
  agent_definition_id?: string;
}

export interface QueueDefaultQueueApi {
  id: string;
  /** @minLength 1 */
  name: string;
  description?: string;
  instructions?: string;
  /** @minLength 1 */
  status: string;
  is_default: boolean;
}

export type QueueDefaultLabelApiSettings = { [key: string]: unknown };

export interface QueueDefaultLabelApi {
  id: string;
  /** @minLength 1 */
  name: string;
  /** @minLength 1 */
  type: string;
  settings: QueueDefaultLabelApiSettings;
  description?: string;
  allow_notes: boolean;
  required: boolean;
  order: number;
}

export type QueueDefaultResultApiAction = typeof QueueDefaultResultApiAction[keyof typeof QueueDefaultResultApiAction];


export const QueueDefaultResultApiAction = {
  created: 'created',
  restored: 'restored',
  fetched: 'fetched',
} as const;

export interface QueueDefaultResultApi {
  queue: QueueDefaultQueueApi;
  labels: QueueDefaultLabelApi[];
  created: boolean;
  action: QueueDefaultResultApiAction;
}

export interface QueueDefaultResponseApi {
  status?: boolean;
  result: QueueDefaultResultApi;
}

export interface QueueLabelRequestApi {
  label_id: string;
  required?: boolean;
}

export type QueueLabelResultApiSettings = { [key: string]: unknown };

export interface QueueLabelResultApi {
  id: string;
  /** @minLength 1 */
  name: string;
  /** @minLength 1 */
  type: string;
  settings: QueueLabelResultApiSettings;
  description?: string;
  allow_notes: boolean;
  required: boolean;
  order: number;
}

export interface QueueAddLabelResultApi {
  label: QueueLabelResultApi;
  created: boolean;
  reopened_items: number;
  /** @minLength 1 */
  queue_status: string;
}

export interface QueueAddLabelResponseApi {
  status?: boolean;
  result: QueueAddLabelResultApi;
}

export interface QueueExportColumnMappingApi {
  field?: string;
  id?: string;
  column?: string;
  enabled?: boolean;
}

export interface QueueExportToDatasetRequestApi {
  dataset_id?: string;
  dataset_name?: string;
  status_filter?: string;
  column_mapping?: QueueExportColumnMappingApi[];
}

export interface QueueExportToDatasetResultApi {
  dataset_id: string;
  /** @minLength 1 */
  dataset_name: string;
  rows_created: number;
  columns: string[];
}

export interface QueueExportToDatasetResponseApi {
  status?: boolean;
  result: QueueExportToDatasetResultApi;
}

export type QueueExportAnnotationsResponseApiResultItem = { [key: string]: unknown };

export interface QueueExportAnnotationsResponseApi {
  status?: boolean;
  result: QueueExportAnnotationsResponseApiResultItem[];
}

export interface QueueHardDeleteRequestApi {
  force: boolean;
  /** @minLength 1 */
  confirm_name: string;
}

export interface QueueHardDeleteResultApi {
  deleted: boolean;
  hard_deleted?: boolean;
  archived?: boolean;
  queue_id: string;
}

export interface QueueHardDeleteResponseApi {
  status?: boolean;
  result: QueueHardDeleteResultApi;
}

export interface QueueProgressAnnotatorStatApi {
  user_id: string;
  /** @minLength 1 */
  name?: string;
  completed: number;
  pending: number;
  in_progress: number;
  in_review: number;
  annotations_count: number;
}

export interface QueueProgressUserProgressApi {
  total: number;
  completed: number;
  pending: number;
  in_progress: number;
  in_review: number;
  skipped: number;
  progress_pct: number;
}

export interface QueueProgressResultApi {
  total: number;
  pending: number;
  in_progress: number;
  in_review: number;
  completed: number;
  skipped: number;
  progress_pct: number;
  annotator_stats: QueueProgressAnnotatorStatApi[];
  user_progress: QueueProgressUserProgressApi;
}

export interface QueueProgressResponseApi {
  status?: boolean;
  result: QueueProgressResultApi;
}

export type QueueRemoveLabelResponseApiResult = {[key: string]: boolean};

export interface QueueRemoveLabelResponseApi {
  status?: boolean;
  result: QueueRemoveLabelResponseApiResult;
}

export interface EmptyRequestApi { [key: string]: unknown }

export interface QueueStatusResponseApi {
  status?: boolean;
  result: AnnotationQueueApi;
}

export type QueueStatusRequestApiStatus = typeof QueueStatusRequestApiStatus[keyof typeof QueueStatusRequestApiStatus];


export const QueueStatusRequestApiStatus = {
  draft: 'draft',
  active: 'active',
  paused: 'paused',
  completed: 'completed',
} as const;

export interface QueueStatusRequestApi {
  status: QueueStatusRequestApiStatus;
}

export type AutomationRuleApiSourceType = typeof AutomationRuleApiSourceType[keyof typeof AutomationRuleApiSourceType];


export const AutomationRuleApiSourceType = {
  dataset_row: 'dataset_row',
  trace: 'trace',
  observation_span: 'observation_span',
  prototype_run: 'prototype_run',
  call_execution: 'call_execution',
  trace_session: 'trace_session',
} as const;

export type AutomationRuleApiConditions = { [key: string]: unknown };

export type AutomationRuleApiTriggerFrequency = typeof AutomationRuleApiTriggerFrequency[keyof typeof AutomationRuleApiTriggerFrequency];


export const AutomationRuleApiTriggerFrequency = {
  manual: 'manual',
  hourly: 'hourly',
  daily: 'daily',
  weekly: 'weekly',
  monthly: 'monthly',
} as const;

export interface AutomationRuleApi {
  readonly id?: string;
  /**
     * @minLength 1
     * @maxLength 255
     */
  name: string;
  readonly queue?: string;
  source_type: AutomationRuleApiSourceType;
  conditions?: AutomationRuleApiConditions;
  enabled?: boolean;
  trigger_frequency?: AutomationRuleApiTriggerFrequency;
  readonly organization?: string;
  readonly created_by?: string;
  /** @minLength 1 */
  readonly created_by_name?: string;
  readonly last_triggered_at?: string;
  readonly trigger_count?: number;
  readonly created_at?: string;
}

export interface AutomationRuleEvaluateAcceptedResponseApi {
  /** @minLength 1 */
  status: string;
  /** @minLength 1 */
  workflow_id: string;
  /** @minLength 1 */
  message: string;
}

export type QueueItemApiSourceType = typeof QueueItemApiSourceType[keyof typeof QueueItemApiSourceType];


export const QueueItemApiSourceType = {
  dataset_row: 'dataset_row',
  trace: 'trace',
  observation_span: 'observation_span',
  prototype_run: 'prototype_run',
  call_execution: 'call_execution',
  trace_session: 'trace_session',
} as const;

export type QueueItemApiStatus = typeof QueueItemApiStatus[keyof typeof QueueItemApiStatus];


export const QueueItemApiStatus = {
  pending: 'pending',
  in_progress: 'in_progress',
  completed: 'completed',
  skipped: 'skipped',
} as const;

export type QueueItemApiMetadata = { [key: string]: unknown };

export interface QueueItemApi {
  readonly id?: string;
  readonly queue?: string;
  source_type: QueueItemApiSourceType;
  /** @minLength 1 */
  source_id?: string;
  status?: QueueItemApiStatus;
  readonly workflow_status?: string;
  readonly workflow_status_label?: string;
  /**
     * @minimum -2147483648
     * @maximum 2147483647
     */
  priority?: number;
  /**
     * @minimum -2147483648
     * @maximum 2147483647
     */
  order?: number;
  metadata?: QueueItemApiMetadata;
  assigned_to?: string;
  /** @minLength 1 */
  readonly assigned_to_name?: string;
  readonly assigned_users?: string;
  reserved_by?: string;
  /** @minLength 1 */
  readonly reserved_by_name?: string;
  reservation_expires_at?: string;
  /** @maxLength 20 */
  review_status?: string;
  reviewed_by?: string;
  /** @minLength 1 */
  readonly reviewed_by_name?: string;
  reviewed_at?: string;
  review_notes?: string;
  readonly source_preview?: string;
  readonly created_at?: string;
}

export type AddItemsApiItemsItem = {[key: string]: string};

export type SelectionApiMode = typeof SelectionApiMode[keyof typeof SelectionApiMode];


export const SelectionApiMode = {
  filter: 'filter',
} as const;

export type SelectionApiSourceType = typeof SelectionApiSourceType[keyof typeof SelectionApiSourceType];


export const SelectionApiSourceType = {
  call_execution: 'call_execution',
  observation_span: 'observation_span',
  trace: 'trace',
  trace_session: 'trace_session',
} as const;

export type SelectionApiFilterItemFilterConfig = {
  /** Canonical field type, for example text, number, boolean, datetime, categorical, thumbs, annotator, or array. */
  filter_type: string;
  /** Canonical operator from api_contracts/filter_contract.json, for example equals, not_equals, in, not_in, between, not_between, is_null, or is_not_null. */
  filter_op: string;
  /** Scalar, list, range tuple, boolean, or null depending on filter_op and filter_type. */
  filter_value?: unknown;
  /** Column family such as SYSTEM_METRIC, SPAN_ATTRIBUTE, EVAL_METRIC, ANNOTATION, or NORMAL. */
  col_type?: string;
};

export type SelectionApiFilterItem = {
  /** Column or attribute id to filter on. */
  column_id: string;
  /** Optional UI label for chips and saved views. */
  display_name?: string;
  filter_config: SelectionApiFilterItemFilterConfig;
};

export interface SelectionApi {
  mode: SelectionApiMode;
  source_type: SelectionApiSourceType;
  project_id: string;
  filter?: SelectionApiFilterItem[];
  exclude_ids?: string[];
  remove_simulation_calls?: boolean;
  is_voice_call?: boolean;
}

export interface AddItemsApi {
  items?: AddItemsApiItemsItem[];
  selection?: SelectionApi;
}

export interface QueueAddItemsResultApi {
  added: number;
  duplicates: number;
  errors: string[];
  /** @minLength 1 */
  queue_status: string;
  total_matching?: number;
}

export interface QueueAddItemsResponseApi {
  status?: boolean;
  result: QueueAddItemsResultApi;
}

export type ApiSelectionTooLargeErrorApiResult = { [key: string]: unknown };

export type ApiSelectionTooLargeErrorApiMessage = { [key: string]: unknown };

export type ApiSelectionTooLargeErrorApiError = { [key: string]: unknown };

export interface ApiSelectionTooLargeErrorApi {
  status?: boolean;
  result?: ApiSelectionTooLargeErrorApiResult;
  message?: ApiSelectionTooLargeErrorApiMessage;
  code?: number;
  error?: ApiSelectionTooLargeErrorApiError;
}

export type AssignItemsApiAction = typeof AssignItemsApiAction[keyof typeof AssignItemsApiAction];


export const AssignItemsApiAction = {
  add: 'add',
  set: 'set',
  remove: 'remove',
} as const;

export interface AssignItemsApi {
  /** @minItems 1 */
  item_ids: string[];
  user_ids?: string[];
  user_id?: string;
  action?: AssignItemsApiAction;
}

export type QueueAssignItemsResponseApiResult = {[key: string]: number};

export interface QueueAssignItemsResponseApi {
  status?: boolean;
  result: QueueAssignItemsResponseApiResult;
}

export interface BulkRemoveItemsApi {
  /** @minItems 1 */
  item_ids: string[];
}

export type QueueBulkRemoveItemsResponseApiResult = {[key: string]: number};

export interface QueueBulkRemoveItemsResponseApi {
  status?: boolean;
  result: QueueBulkRemoveItemsResponseApiResult;
}

export type QueueNextItemResultApiItem = { [key: string]: unknown };

export interface QueueNextItemResultApi {
  item: QueueNextItemResultApiItem;
}

export interface QueueNextItemResponseApi {
  status?: boolean;
  result: QueueNextItemResultApi;
}

export type QueueAnnotateDetailResponseApiResult = { [key: string]: unknown };

export interface QueueAnnotateDetailResponseApi {
  status?: boolean;
  result: QueueAnnotateDetailResponseApiResult;
}

export type ScoreApiSourceType = typeof ScoreApiSourceType[keyof typeof ScoreApiSourceType];


export const ScoreApiSourceType = {
  dataset_row: 'dataset_row',
  trace: 'trace',
  observation_span: 'observation_span',
  prototype_run: 'prototype_run',
  call_execution: 'call_execution',
  trace_session: 'trace_session',
} as const;

export type ScoreApiScoreSource = typeof ScoreApiScoreSource[keyof typeof ScoreApiScoreSource];


export const ScoreApiScoreSource = {
  human: 'human',
  api: 'api',
  auto: 'auto',
  imported: 'imported',
} as const;

export type ScoreApiLabelSettings = { [key: string]: unknown };

export type ScoreApiValue = { [key: string]: unknown };

export interface ScoreApi {
  readonly id?: string;
  source_type: ScoreApiSourceType;
  readonly source_id?: string;
  readonly label_id?: string;
  /** @minLength 1 */
  readonly label_name?: string;
  /** @minLength 1 */
  readonly label_type?: string;
  readonly label_settings?: ScoreApiLabelSettings;
  readonly label_allow_notes?: boolean;
  value: ScoreApiValue;
  score_source?: ScoreApiScoreSource;
  notes?: string;
  readonly annotator?: string;
  /** @minLength 1 */
  readonly annotator_name?: string;
  /** @minLength 1 */
  readonly annotator_email?: string;
  readonly queue_item?: string;
  readonly queue_id?: string;
  readonly created_at?: string;
  readonly updated_at?: string;
}

export interface QueueItemAnnotationsResponseApi {
  status?: boolean;
  result: ScoreApi[];
}

export type ImportAnnotationEntryApiValue = { [key: string]: unknown };

export interface ImportAnnotationEntryApi {
  label_id: string;
  value: ImportAnnotationEntryApiValue;
  notes?: string;
  score_source?: string;
}

export interface ImportAnnotationsApi {
  annotations: ImportAnnotationEntryApi[];
  annotator_id?: string;
}

export type QueueImportAnnotationsResponseApiResult = {[key: string]: number};

export interface QueueImportAnnotationsResponseApi {
  status?: boolean;
  result: QueueImportAnnotationsResponseApiResult;
}

export type SubmitAnnotationsApiAnnotationsItem = {[key: string]: string};

export interface SubmitAnnotationsApi {
  /** @minItems 1 */
  annotations: SubmitAnnotationsApiAnnotationsItem[];
  notes?: string;
  item_notes?: string;
}

export type QueueSubmitAnnotationsResponseApiResult = {[key: string]: number};

export interface QueueSubmitAnnotationsResponseApi {
  status?: boolean;
  result: QueueSubmitAnnotationsResponseApiResult;
}

export interface QueueItemNavigationRequestApi {
  exclude?: string[];
  exclude_review_status?: string;
  include_completed?: boolean;
}

export type QueueNavigationResultApiNextItem = { [key: string]: unknown };

export interface QueueNavigationResultApi {
  completed_item_id?: string;
  skipped_item_id?: string;
  next_item: QueueNavigationResultApiNextItem;
}

export interface QueueNavigationResponseApi {
  status?: boolean;
  result: QueueNavigationResultApi;
}

export type QueueDiscussionResponseApiResult = { [key: string]: unknown };

export interface QueueDiscussionResponseApi {
  status?: boolean;
  result: QueueDiscussionResponseApiResult;
}

export interface DiscussionCommentRequestApi {
  comment?: string;
  content?: string;
  label_id?: string;
  label?: string;
  target_annotator_id?: string;
  thread_id?: string;
  thread?: string;
  mentioned_user_ids?: string[];
  mentions?: string[];
}

export interface DiscussionReactionRequestApi {
  /** @maxLength 16 */
  emoji?: string;
  /** @maxLength 16 */
  reaction?: string;
}

export interface DiscussionThreadStatusRequestApi {
  comment?: string;
}

export type QueueReleaseReservationResponseApiResult = {[key: string]: boolean};

export interface QueueReleaseReservationResponseApi {
  status?: boolean;
  result: QueueReleaseReservationResponseApiResult;
}

export type ReviewItemRequestApiAction = typeof ReviewItemRequestApiAction[keyof typeof ReviewItemRequestApiAction];


export const ReviewItemRequestApiAction = {
  approve: 'approve',
  request_changes: 'request_changes',
  reject: 'reject',
  comment: 'comment',
} as const;

export interface ReviewLabelCommentRequestApi {
  label_id?: string;
  label?: string;
  target_annotator_id?: string;
  annotator_id?: string;
  comment?: string;
  notes?: string;
}

export interface ReviewItemRequestApi {
  action: ReviewItemRequestApiAction;
  notes?: string;
  label_comments?: ReviewLabelCommentRequestApi[];
}

export type QueueReviewItemResultApiNextItem = { [key: string]: unknown };

export type QueueReviewItemResultApiReviewCommentsItem = { [key: string]: unknown };

export type QueueReviewItemResultApiReviewThreadsItem = { [key: string]: unknown };

export interface QueueReviewItemResultApi {
  reviewed_item_id: string;
  /** @minLength 1 */
  action: string;
  next_item: QueueReviewItemResultApiNextItem;
  review_comments: QueueReviewItemResultApiReviewCommentsItem[];
  review_threads: QueueReviewItemResultApiReviewThreadsItem[];
}

export interface QueueReviewItemResponseApi {
  status?: boolean;
  result: QueueReviewItemResultApi;
}

export type UserApiOrganizationRole = typeof UserApiOrganizationRole[keyof typeof UserApiOrganizationRole];


export const UserApiOrganizationRole = {
  Owner: 'Owner',
  Admin: 'Admin',
  Member: 'Member',
  Viewer: 'Viewer',
  workspace_admin: 'workspace_admin',
  workspace_member: 'workspace_member',
  workspace_viewer: 'workspace_viewer',
} as const;

export interface OrganizationApi {
  readonly id?: string;
  readonly created_at?: string;
  /**
     * @minLength 1
     * @maxLength 255
     */
  name: string;
  /** @maxLength 255 */
  display_name?: string;
  is_new?: boolean;
  ws_enabled?: boolean;
  /**
     * @minLength 1
     * @maxLength 16
     */
  region?: string;
  require_2fa?: boolean;
  /**
     * @minimum 0
     * @maximum 32767
     */
  require_2fa_grace_period_days?: number;
  require_2fa_enforced_at?: string;
}

/**
 * List of user's goals for using the platform
 */
export type UserApiGoals = { [key: string]: unknown };

export interface UserApi {
  readonly id?: string;
  /**
     * @minLength 1
     * @maxLength 254
     */
  email: string;
  /**
     * @minLength 1
     * @maxLength 255
     */
  name: string;
  organization_role?: UserApiOrganizationRole;
  organization?: OrganizationApi;
  readonly created_at?: string;
  readonly status?: string;
  /**
     * User's job role (e.g., Data Scientist, ML Engineer, or custom role)
     * @maxLength 255
     */
  role?: string;
  /** List of user's goals for using the platform */
  goals?: UserApiGoals;
}

export type MonitorApiMonitorType = typeof MonitorApiMonitorType[keyof typeof MonitorApiMonitorType];


export const MonitorApiMonitorType = {
  Analytics: 'Analytics',
  DataDrift: 'DataDrift',
  Performance: 'Performance',
} as const;

export interface MonitorApi {
  readonly id?: number;
  /** Indicates if the alert is executed */
  status?: boolean;
  /**
     * Name of the monitor
     * @minLength 1
     * @maxLength 255
     */
  name: string;
  monitor_type: MonitorApiMonitorType;
  /**
     * Dimension of the monitor
     * @minLength 1
     * @maxLength 255
     */
  dimension: string;
  /**
     * Metric used by the monitor
     * @minLength 1
     * @maxLength 255
     */
  metric: string;
  /** Current value of the metric */
  current_value: number;
  /** Value at which the alert is triggered */
  trigger_value: number;
  /** Indicates if the monitor is muted */
  is_mute?: boolean;
  readonly created_at?: string;
  readonly updated_at?: string;
}

export type AIModelApiModelType = typeof AIModelApiModelType[keyof typeof AIModelApiModelType];


export const AIModelApiModelType = {
  Numeric: 'Numeric',
  ScoreCategorical: 'ScoreCategorical',
  Ranking: 'Ranking',
  BinaryClassification: 'BinaryClassification',
  Regression: 'Regression',
  ObjectDetection: 'ObjectDetection',
  Segmentation: 'Segmentation',
  GenerativeLLM: 'GenerativeLLM',
  GenerativeImage: 'GenerativeImage',
  GenerativeVideo: 'GenerativeVideo',
  TTS: 'TTS',
  STT: 'STT',
  MultiModal: 'MultiModal',
} as const;

export type AIModelApiBaselineModelEnvironment = typeof AIModelApiBaselineModelEnvironment[keyof typeof AIModelApiBaselineModelEnvironment];


export const AIModelApiBaselineModelEnvironment = {
  Production: 'Production',
  Training: 'Training',
  Validation: 'Validation',
  Corpus: 'Corpus',
} as const;

export interface AIModelApi {
  readonly id?: string;
  readonly monitors?: readonly MonitorApi[];
  readonly created_at?: string;
  /**
     * @minLength 1
     * @maxLength 255
     */
  user_model_id: string;
  deleted?: boolean;
  model_type: AIModelApiModelType;
  baseline_model_environment?: AIModelApiBaselineModelEnvironment;
  /** @maxLength 255 */
  baseline_model_version?: string;
  default_metric?: string;
  organization: string;
  workspace?: string;
}

export interface AnnotationTaskApi {
  readonly id?: string;
  readonly assigned_users?: readonly UserApi[];
  readonly created_at?: string;
  readonly updated_at?: string;
  ai_model?: AIModelApi;
  /**
     * @minLength 1
     * @maxLength 255
     */
  task_name: string;
}

export type AnnotationsLabelsApiType = typeof AnnotationsLabelsApiType[keyof typeof AnnotationsLabelsApiType];


export const AnnotationsLabelsApiType = {
  text: 'text',
  numeric: 'numeric',
  categorical: 'categorical',
  star: 'star',
  thumbs_up_down: 'thumbs_up_down',
} as const;

export type AnnotationsLabelsApiSettings = { [key: string]: unknown };

export interface AnnotationsLabelsApi {
  readonly id?: string;
  /**
     * @minLength 1
     * @maxLength 255
     */
  name: string;
  type: AnnotationsLabelsApiType;
  readonly organization?: string;
  settings?: AnnotationsLabelsApiSettings;
  project?: string;
  description?: string;
  allow_notes?: boolean;
  readonly created_at?: string;
  readonly trace_annotations_count?: number;
  readonly annotation_count?: number;
}

export interface AnnotationLabelRestoreResponseApi {
  status?: boolean;
  result: AnnotationsLabelsApi;
}

export type AnnotationsApiStaticFields = { [key: string]: unknown };

export type AnnotationsApiResponseFields = { [key: string]: unknown };

export interface AnnotationsApi {
  readonly id?: string;
  /**
     * @minLength 1
     * @maxLength 255
     */
  name: string;
  readonly assigned_users?: string;
  readonly organization?: string;
  readonly labels?: string;
  columns?: string[];
  static_fields?: AnnotationsApiStaticFields;
  response_fields?: AnnotationsApiResponseFields;
  dataset?: string;
  readonly summary?: string;
  readonly created_at?: string;
  /**
     * @minimum -2147483648
     * @maximum 2147483647
     */
  responses?: number;
  readonly lowest_unfinished_row?: string;
  readonly label_requirements?: string;
}

export type ApiKeyApiConfigJson = { [key: string]: unknown };

export interface ApiKeyApi {
  readonly id?: string;
  /**
     * @minLength 1
     * @maxLength 50
     */
  provider: string;
  /** @maxLength 2500 */
  key?: string;
  readonly organization?: string;
  readonly masked_actual_key?: string;
  config_json?: ApiKeyApiConfigJson;
}

export type DatasetOptimizationListApiOptimizerAlgorithm = typeof DatasetOptimizationListApiOptimizerAlgorithm[keyof typeof DatasetOptimizationListApiOptimizerAlgorithm];


export const DatasetOptimizationListApiOptimizerAlgorithm = {
  random_search: 'random_search',
  bayesian: 'bayesian',
  metaprompt: 'metaprompt',
  protegi: 'protegi',
  promptwizard: 'promptwizard',
  gepa: 'gepa',
} as const;

export type DatasetOptimizationListApiStatus = typeof DatasetOptimizationListApiStatus[keyof typeof DatasetOptimizationListApiStatus];


export const DatasetOptimizationListApiStatus = {
  not_started: 'not_started',
  pending: 'pending',
  running: 'running',
  completed: 'completed',
  failed: 'failed',
  cancelled: 'cancelled',
} as const;

/**
 * Optimizer-specific configuration (num_trials, etc.)
 */
export type DatasetOptimizationListApiOptimizerConfig = { [key: string]: unknown };

export interface DatasetOptimizationListApi {
  readonly id?: string;
  /** @minLength 1 */
  optimization_name: string;
  started_at: string;
  readonly trial_count?: string;
  optimizer_algorithm?: DatasetOptimizationListApiOptimizerAlgorithm;
  readonly optimizer_model_id?: string;
  readonly column_id?: string;
  status?: DatasetOptimizationListApiStatus;
  error_message?: string;
  /** Optimizer-specific configuration (num_trials, etc.) */
  optimizer_config?: DatasetOptimizationListApiOptimizerConfig;
  best_score?: number;
  baseline_score?: number;
}

export type DatasetOptimizationCreateApiOptimizerAlgorithm = typeof DatasetOptimizationCreateApiOptimizerAlgorithm[keyof typeof DatasetOptimizationCreateApiOptimizerAlgorithm];


export const DatasetOptimizationCreateApiOptimizerAlgorithm = {
  random_search: 'random_search',
  bayesian: 'bayesian',
  metaprompt: 'metaprompt',
  protegi: 'protegi',
  promptwizard: 'promptwizard',
  gepa: 'gepa',
} as const;

/**
 * Optimizer-specific configuration (num_trials, etc.)
 */
export type DatasetOptimizationCreateApiOptimizerConfig = { [key: string]: unknown };

export interface DatasetOptimizationCreateApi {
  readonly id?: string;
  /**
     * @minLength 1
     * @maxLength 255
     */
  name: string;
  column_id: string;
  optimizer_algorithm: DatasetOptimizationCreateApiOptimizerAlgorithm;
  optimizer_model_id?: string;
  /** Optimizer-specific configuration (num_trials, etc.) */
  optimizer_config?: DatasetOptimizationCreateApiOptimizerConfig;
  user_eval_template_ids?: string[];
  readonly created_at?: string;
}

export type DatasetOptimizationDetailApiOptimizerAlgorithm = typeof DatasetOptimizationDetailApiOptimizerAlgorithm[keyof typeof DatasetOptimizationDetailApiOptimizerAlgorithm];


export const DatasetOptimizationDetailApiOptimizerAlgorithm = {
  random_search: 'random_search',
  bayesian: 'bayesian',
  metaprompt: 'metaprompt',
  protegi: 'protegi',
  promptwizard: 'promptwizard',
  gepa: 'gepa',
} as const;

/**
 * Optimizer-specific configuration (num_trials, etc.)
 */
export type DatasetOptimizationDetailApiOptimizerConfig = { [key: string]: unknown };

export type DatasetOptimizationDetailApiStatus = typeof DatasetOptimizationDetailApiStatus[keyof typeof DatasetOptimizationDetailApiStatus];


export const DatasetOptimizationDetailApiStatus = {
  not_started: 'not_started',
  pending: 'pending',
  running: 'running',
  completed: 'completed',
  failed: 'failed',
  cancelled: 'cancelled',
} as const;

export type DatasetOptimizationStepApiStatus = typeof DatasetOptimizationStepApiStatus[keyof typeof DatasetOptimizationStepApiStatus];


export const DatasetOptimizationStepApiStatus = {
  pending: 'pending',
  running: 'running',
  completed: 'completed',
  failed: 'failed',
} as const;

export type DatasetOptimizationStepApiMetadata = { [key: string]: unknown };

export interface DatasetOptimizationStepApi {
  readonly id?: string;
  /**
     * @minLength 1
     * @maxLength 255
     */
  name: string;
  description?: string;
  status?: DatasetOptimizationStepApiStatus;
  metadata?: DatasetOptimizationStepApiMetadata;
  /**
     * @minimum -2147483648
     * @maximum 2147483647
     */
  step_number: number;
  readonly created_at?: string;
  readonly updated_at?: string;
}

export interface DatasetOptimizationTrialListApi {
  readonly id?: string;
  /**
     * @minimum -2147483648
     * @maximum 2147483647
     */
  trial_number: number;
  is_baseline?: boolean;
  average_score: number;
  readonly created_at?: string;
}

export interface DatasetOptimizationDetailApi {
  readonly id?: string;
  /**
     * @minLength 1
     * @maxLength 255
     */
  name: string;
  /** Column being optimized */
  column?: string;
  optimizer_algorithm?: DatasetOptimizationDetailApiOptimizerAlgorithm;
  /** Model used for optimization (separate from eval model) */
  optimizer_model?: string;
  /** Optimizer-specific configuration (num_trials, etc.) */
  optimizer_config?: DatasetOptimizationDetailApiOptimizerConfig;
  status?: DatasetOptimizationDetailApiStatus;
  error_message?: string;
  best_score?: number;
  baseline_score?: number;
  optimized_k_prompts?: string[];
  readonly steps?: readonly DatasetOptimizationStepApi[];
  readonly trials?: readonly DatasetOptimizationTrialListApi[];
  readonly trial_count?: string;
  readonly created_at?: string;
}

export type DatasetOptimizationApiOptimizerAlgorithm = typeof DatasetOptimizationApiOptimizerAlgorithm[keyof typeof DatasetOptimizationApiOptimizerAlgorithm];


export const DatasetOptimizationApiOptimizerAlgorithm = {
  random_search: 'random_search',
  bayesian: 'bayesian',
  metaprompt: 'metaprompt',
  protegi: 'protegi',
  promptwizard: 'promptwizard',
  gepa: 'gepa',
} as const;

/**
 * Optimizer-specific configuration (num_trials, etc.)
 */
export type DatasetOptimizationApiOptimizerConfig = { [key: string]: unknown };

export type DatasetOptimizationApiStatus = typeof DatasetOptimizationApiStatus[keyof typeof DatasetOptimizationApiStatus];


export const DatasetOptimizationApiStatus = {
  not_started: 'not_started',
  pending: 'pending',
  running: 'running',
  completed: 'completed',
  failed: 'failed',
  cancelled: 'cancelled',
} as const;

export interface DatasetOptimizationApi {
  readonly id?: string;
  /**
     * @minLength 1
     * @maxLength 255
     */
  name: string;
  /** Column being optimized */
  column?: string;
  optimizer_algorithm?: DatasetOptimizationApiOptimizerAlgorithm;
  /** Model used for optimization (separate from eval model) */
  optimizer_model?: string;
  /** Optimizer-specific configuration (num_trials, etc.) */
  optimizer_config?: DatasetOptimizationApiOptimizerConfig;
  status?: DatasetOptimizationApiStatus;
  error_message?: string;
  best_score?: number;
  baseline_score?: number;
  optimized_k_prompts?: string[];
  readonly created_at?: string;
}

export interface AnnotationSummaryHeaderApi {
  dataset_coverage?: number;
  completion_eta?: number;
  overall_agreement?: number;
}

export type AnnotationSummaryResultApiLabelsItem = { [key: string]: unknown };

export type AnnotationSummaryResultApiAnnotatorsItem = { [key: string]: unknown };

export interface AnnotationSummaryResultApi {
  labels?: AnnotationSummaryResultApiLabelsItem[];
  annotators?: AnnotationSummaryResultApiAnnotatorsItem[];
  header?: AnnotationSummaryHeaderApi;
}

export interface AnnotationSummaryResponseApi {
  status?: boolean;
  result: AnnotationSummaryResultApi;
}

export interface EvalGroupApi {
  readonly id?: string;
  /**
     * @minLength 1
     * @maxLength 255
     */
  name: string;
  readonly organization?: string;
  readonly workspace?: string;
  readonly created_at?: string;
  readonly updated_at?: string;
  description?: string;
  readonly created_by?: string;
  is_sample?: boolean;
}

export interface ExperimentsTableGetApi {
  readonly id?: string;
  /**
     * @minLength 1
     * @maxLength 255
     */
  name: string;
}

export type ExperimentListApiStatus = typeof ExperimentListApiStatus[keyof typeof ExperimentListApiStatus];


export const ExperimentListApiStatus = {
  NotStarted: 'NotStarted',
  Queued: 'Queued',
  Running: 'Running',
  Completed: 'Completed',
  Editing: 'Editing',
  Inactive: 'Inactive',
  Failed: 'Failed',
  PartialRun: 'PartialRun',
  ExperimentEvaluation: 'ExperimentEvaluation',
  Uploading: 'Uploading',
  PartialExtracted: 'PartialExtracted',
  Processing: 'Processing',
  Deleting: 'Deleting',
  PartialCompleted: 'PartialCompleted',
  OptimizationEvaluation: 'OptimizationEvaluation',
  Error: 'Error',
  Cancelled: 'Cancelled',
} as const;

export interface ExperimentListApi {
  readonly id?: string;
  /**
     * @minLength 1
     * @maxLength 255
     */
  name: string;
  status?: ExperimentListApiStatus;
  readonly eval_templates_count?: string;
  readonly created_at?: string;
  readonly models_count?: string;
  dataset: string;
}

export type ExperimentListV2ApiStatus = typeof ExperimentListV2ApiStatus[keyof typeof ExperimentListV2ApiStatus];


export const ExperimentListV2ApiStatus = {
  NotStarted: 'NotStarted',
  Queued: 'Queued',
  Running: 'Running',
  Completed: 'Completed',
  Editing: 'Editing',
  Inactive: 'Inactive',
  Failed: 'Failed',
  PartialRun: 'PartialRun',
  ExperimentEvaluation: 'ExperimentEvaluation',
  Uploading: 'Uploading',
  PartialExtracted: 'PartialExtracted',
  Processing: 'Processing',
  Deleting: 'Deleting',
  PartialCompleted: 'PartialCompleted',
  OptimizationEvaluation: 'OptimizationEvaluation',
  Error: 'Error',
  Cancelled: 'Cancelled',
} as const;

/**
 * Determines how the experiment executes: llm, tts, stt, or image.
 */
export type ExperimentListV2ApiExperimentType = typeof ExperimentListV2ApiExperimentType[keyof typeof ExperimentListV2ApiExperimentType];


export const ExperimentListV2ApiExperimentType = {
  llm: 'llm',
  tts: 'tts',
  stt: 'stt',
  image: 'image',
} as const;

export interface ExperimentListV2Api {
  readonly id?: string;
  /**
     * @minLength 1
     * @maxLength 255
     */
  name: string;
  status?: ExperimentListV2ApiStatus;
  /** Determines how the experiment executes: llm, tts, stt, or image. */
  experiment_type?: ExperimentListV2ApiExperimentType;
  readonly eval_templates_count?: string;
  readonly created_at?: string;
  readonly models_count?: string;
  readonly agents_count?: string;
  dataset: string;
}

export type FeedbackApiSource = typeof FeedbackApiSource[keyof typeof FeedbackApiSource];


export const FeedbackApiSource = {
  dataset: 'dataset',
  prompt: 'prompt',
  sdk: 'sdk',
  trace: 'trace',
  experiment: 'experiment',
  observe: 'observe',
  eval_playground: 'eval_playground',
} as const;

export interface FeedbackApi {
  readonly id?: string;
  /**
     * @minLength 1
     * @maxLength 255
     */
  source_id: string;
  source: FeedbackApiSource;
  user_eval_metric?: string;
  /** @minLength 1 */
  value: string;
  explanation?: string;
  /** @maxLength 255 */
  row_id?: string;
  custom_eval_config_id?: string;
  feedback_improvement?: string;
  /** @maxLength 255 */
  action_type?: string;
}

export type KnowledgeBaseCreateApiEmbeddingModel = typeof KnowledgeBaseCreateApiEmbeddingModel[keyof typeof KnowledgeBaseCreateApiEmbeddingModel];


export const KnowledgeBaseCreateApiEmbeddingModel = {
  'BAAI/bge-small-en-v15': 'BAAI/bge-small-en-v1.5',
} as const;

export interface KnowledgeBaseCreateApi {
  readonly id?: string;
  /**
     * @minLength 1
     * @maxLength 2000
     */
  name: string;
  embedding_model?: KnowledgeBaseCreateApiEmbeddingModel;
  /**
     * @minimum 0
     * @maximum 2147483647
     */
  chunk_size: number;
  organization?: string;
  readonly created_at?: string;
  readonly updated_at?: string;
}

export type KnowledgeBaseApiEmbeddingModel = typeof KnowledgeBaseApiEmbeddingModel[keyof typeof KnowledgeBaseApiEmbeddingModel];


export const KnowledgeBaseApiEmbeddingModel = {
  'BAAI/bge-small-en-v15': 'BAAI/bge-small-en-v1.5',
} as const;

export interface KnowledgeBaseApi {
  readonly id?: string;
  /**
     * @minLength 1
     * @maxLength 2000
     */
  name: string;
  embedding_model?: KnowledgeBaseApiEmbeddingModel;
  /**
     * @minimum 0
     * @maximum 2147483647
     */
  chunk_size: number;
  readonly organization?: string;
  readonly created_at?: string;
  readonly updated_at?: string;
}

export type OptimizationDatasetApiMessagesItem = { [key: string]: unknown };

export type OptimizationDatasetApiModelConfig = { [key: string]: unknown };

export type OptimizationDatasetApiOptimizeType = typeof OptimizationDatasetApiOptimizeType[keyof typeof OptimizationDatasetApiOptimizeType];


export const OptimizationDatasetApiOptimizeType = {
  PROMPT_TEMPLATE: 'PROMPT_TEMPLATE',
  RIGHT_ANSWER: 'RIGHT_ANSWER',
  RAG_PROMPT_TEMPLATE: 'RAG_PROMPT_TEMPLATE',
} as const;

export type OptimizationDatasetApiUserEvalTemplateMapping = { [key: string]: unknown };

export type OptimizationDatasetApiStatus = typeof OptimizationDatasetApiStatus[keyof typeof OptimizationDatasetApiStatus];


export const OptimizationDatasetApiStatus = {
  NotStarted: 'NotStarted',
  Queued: 'Queued',
  Running: 'Running',
  Completed: 'Completed',
  Editing: 'Editing',
  Inactive: 'Inactive',
  Failed: 'Failed',
  PartialRun: 'PartialRun',
  ExperimentEvaluation: 'ExperimentEvaluation',
  Uploading: 'Uploading',
  PartialExtracted: 'PartialExtracted',
  Processing: 'Processing',
  Deleting: 'Deleting',
  PartialCompleted: 'PartialCompleted',
  OptimizationEvaluation: 'OptimizationEvaluation',
  Error: 'Error',
  Cancelled: 'Cancelled',
} as const;

export interface OptimizationDatasetApi {
  readonly id?: string;
  /**
     * @minLength 1
     * @maxLength 2000
     */
  name: string;
  dataset_id: string;
  column_id?: string;
  /** List of messages with format [{'role': 'user/assistant', 'content': 'text'}] */
  messages?: OptimizationDatasetApiMessagesItem[];
  user_eval_template_ids?: string[];
  model_config: OptimizationDatasetApiModelConfig;
  optimize_type: OptimizationDatasetApiOptimizeType;
  user_eval_template_mapping?: OptimizationDatasetApiUserEvalTemplateMapping;
  /** @maxLength 2000 */
  prompt_name?: string;
  readonly created_at?: string;
  status?: OptimizationDatasetApiStatus;
}

export type OptimizationDatasetGetApiMessagesItem = { [key: string]: unknown };

export type OptimizationDatasetGetApiModelConfig = { [key: string]: unknown };

export type OptimizationDatasetGetApiOptimizeType = typeof OptimizationDatasetGetApiOptimizeType[keyof typeof OptimizationDatasetGetApiOptimizeType];


export const OptimizationDatasetGetApiOptimizeType = {
  PROMPT_TEMPLATE: 'PROMPT_TEMPLATE',
  RIGHT_ANSWER: 'RIGHT_ANSWER',
  RAG_PROMPT_TEMPLATE: 'RAG_PROMPT_TEMPLATE',
} as const;

export type OptimizationDatasetGetApiStatus = typeof OptimizationDatasetGetApiStatus[keyof typeof OptimizationDatasetGetApiStatus];


export const OptimizationDatasetGetApiStatus = {
  NotStarted: 'NotStarted',
  Queued: 'Queued',
  Running: 'Running',
  Completed: 'Completed',
  Editing: 'Editing',
  Inactive: 'Inactive',
  Failed: 'Failed',
  PartialRun: 'PartialRun',
  ExperimentEvaluation: 'ExperimentEvaluation',
  Uploading: 'Uploading',
  PartialExtracted: 'PartialExtracted',
  Processing: 'Processing',
  Deleting: 'Deleting',
  PartialCompleted: 'PartialCompleted',
  OptimizationEvaluation: 'OptimizationEvaluation',
  Error: 'Error',
  Cancelled: 'Cancelled',
} as const;

export type OptimizationDatasetGetApiUserEvalTemplateMapping = { [key: string]: unknown };

export interface OptimizationDatasetGetApi {
  readonly id?: string;
  /**
     * @minLength 1
     * @maxLength 2000
     */
  name: string;
  dataset: string;
  column?: string;
  /** List of messages with format [{'role': 'user/assistant', 'content': 'text'}] */
  messages?: OptimizationDatasetGetApiMessagesItem[];
  user_eval_template_ids?: string[];
  model_config?: OptimizationDatasetGetApiModelConfig;
  optimize_type: OptimizationDatasetGetApiOptimizeType;
  readonly status?: OptimizationDatasetGetApiStatus;
  readonly created_at?: string;
  optimized_k_prompts?: string[];
  user_eval_template_mapping?: OptimizationDatasetGetApiUserEvalTemplateMapping;
  /** @maxLength 2000 */
  prompt_name?: string;
}

export type OptimizationDetailApiUserEvalTemplateMapping = { [key: string]: unknown };

export interface OptimizationDetailApi {
  readonly id?: string;
  readonly created_at?: string;
  readonly optimized_k_prompts?: string;
  user_eval_template_mapping?: OptimizationDetailApiUserEvalTemplateMapping;
  readonly optimized_columns?: string;
  readonly evaluation_columns?: string;
}

export type OptimizeDatasetKbApiKnowledgeBaseMetrics = { [key: string]: unknown };

export type OptimizeDatasetKbApiKnowledgeBaseFilters = { [key: string]: unknown };

export type OptimizeDatasetKbApiVariables = { [key: string]: unknown };

export type OptimizeDatasetKbApiStatus = typeof OptimizeDatasetKbApiStatus[keyof typeof OptimizeDatasetKbApiStatus];


export const OptimizeDatasetKbApiStatus = {
  not_started: 'not_started',
  pending: 'pending',
  running: 'running',
  completed: 'completed',
  failed: 'failed',
  cancelled: 'cancelled',
} as const;

export interface OptimizeDatasetKbApi {
  readonly id?: string;
  knowledge_base_metrics?: OptimizeDatasetKbApiKnowledgeBaseMetrics;
  knowledge_base_filters?: OptimizeDatasetKbApiKnowledgeBaseFilters;
  optimized_k_prompts?: string[];
  /**
     * @minLength 1
     * @maxLength 255
     */
  name: string;
  /** @maxLength 2000 */
  prompt?: string;
  variables?: OptimizeDatasetKbApiVariables;
  status?: OptimizeDatasetKbApiStatus;
}

export type DevelopAnnotationsUserApiOrganizationRole = typeof DevelopAnnotationsUserApiOrganizationRole[keyof typeof DevelopAnnotationsUserApiOrganizationRole];


export const DevelopAnnotationsUserApiOrganizationRole = {
  Owner: 'Owner',
  Admin: 'Admin',
  Member: 'Member',
  Viewer: 'Viewer',
  workspace_admin: 'workspace_admin',
  workspace_member: 'workspace_member',
  workspace_viewer: 'workspace_viewer',
} as const;

export interface DevelopAnnotationsUserApi {
  readonly id?: string;
  /**
     * @minLength 1
     * @maxLength 254
     */
  email: string;
  /**
     * @minLength 1
     * @maxLength 255
     */
  name: string;
  organization_role?: DevelopAnnotationsUserApiOrganizationRole;
  is_active?: boolean;
  is_staff?: boolean;
}

export type PromptBaseTemplateApiPromptConfigSnapshot = { [key: string]: unknown };

export interface PromptBaseTemplateApi {
  readonly id?: string;
  /**
     * @minLength 1
     * @maxLength 255
     */
  name: string;
  readonly organization?: string;
  readonly workspace?: string;
  readonly created_at?: string;
  readonly updated_at?: string;
  is_sample?: boolean;
  prompt_version?: string;
  /** @maxLength 255 */
  category?: string;
  prompt_config_snapshot?: PromptBaseTemplateApiPromptConfigSnapshot;
  readonly created_by?: string;
}

export interface PromptExecutionApi {
  readonly id?: string;
  /**
     * @minLength 1
     * @maxLength 2000
     */
  name: string;
  readonly updated_at?: string;
  readonly model?: string;
  readonly collaborators?: string;
  readonly model_detail?: string;
  prompt_folder?: string;
  is_sample?: boolean;
  readonly prompt_folder_name?: string;
  readonly created_by?: string;
}

export interface PromptFolderApi {
  readonly id?: string;
  /**
     * @minLength 1
     * @maxLength 255
     */
  name: string;
  readonly organization?: string;
  readonly workspace?: string;
  readonly created_at?: string;
  readonly updated_at?: string;
  is_sample?: boolean;
  parent_folder?: string;
  readonly created_by?: string;
}

export type PromptHistoryExecutionApiOutput = { [key: string]: unknown };

export type PromptHistoryExecutionApiMetadata = { [key: string]: unknown };

export type PromptHistoryExecutionApiEvaluationResults = { [key: string]: unknown };

export type PromptHistoryExecutionApiEvaluationConfigs = { [key: string]: unknown };

export type PromptHistoryExecutionApiPlaceholders = { [key: string]: unknown };

export interface PromptHistoryExecutionApi {
  readonly id?: string;
  /**
     * @minLength 1
     * @maxLength 50
     */
  template_version: string;
  readonly output?: PromptHistoryExecutionApiOutput;
  readonly prompt_config_snapshot?: string;
  readonly template_name?: string;
  original_template?: string;
  metadata?: PromptHistoryExecutionApiMetadata;
  readonly variable_names?: string;
  evaluation_results?: PromptHistoryExecutionApiEvaluationResults;
  evaluation_configs?: PromptHistoryExecutionApiEvaluationConfigs;
  readonly created_at?: string;
  is_default?: boolean;
  commit_message?: string;
  readonly updated_at?: string;
  is_draft?: boolean;
  readonly labels?: string;
  placeholders?: PromptHistoryExecutionApiPlaceholders;
  prompt_base_template?: string;
}

export type PromptLabelApiType = typeof PromptLabelApiType[keyof typeof PromptLabelApiType];


export const PromptLabelApiType = {
  system: 'system',
  custom: 'custom',
} as const;

export type PromptLabelApiMetadata = { [key: string]: unknown };

export interface PromptLabelApi {
  readonly id?: string;
  readonly organization?: string;
  /**
     * @minLength 1
     * @maxLength 2000
     */
  name: string;
  type: PromptLabelApiType;
  metadata?: PromptLabelApiMetadata;
  readonly created_at?: string;
  readonly updated_at?: string;
}

export type PromptTemplateApiVariableNames = { [key: string]: unknown };

export type PromptTemplateApiPlaceholders = { [key: string]: unknown };

export interface PromptTemplateApi {
  readonly id?: string;
  /**
     * @minLength 1
     * @maxLength 2000
     */
  name: string;
  description?: string;
  variable_names?: PromptTemplateApiVariableNames;
  organization?: string;
  prompt_folder?: string;
  placeholders?: PromptTemplateApiPlaceholders;
  created_by?: string;
}

export type UserResponseSchemaApiSchema = { [key: string]: unknown };

export type UserResponseSchemaApiSchemaType = typeof UserResponseSchemaApiSchemaType[keyof typeof UserResponseSchemaApiSchemaType];


export const UserResponseSchemaApiSchemaType = {
  json: 'json',
  yaml: 'yaml',
} as const;

export interface UserResponseSchemaApi {
  readonly id?: string;
  /**
     * @minLength 1
     * @maxLength 2000
     */
  name: string;
  description?: string;
  schema?: UserResponseSchemaApiSchema;
  readonly organization?: string;
  schema_type?: UserResponseSchemaApiSchemaType;
}

export type CreateScoreApiSourceType = typeof CreateScoreApiSourceType[keyof typeof CreateScoreApiSourceType];


export const CreateScoreApiSourceType = {
  dataset_row: 'dataset_row',
  trace: 'trace',
  observation_span: 'observation_span',
  prototype_run: 'prototype_run',
  call_execution: 'call_execution',
  trace_session: 'trace_session',
} as const;

export type CreateScoreApiValue = { [key: string]: unknown };

export type CreateScoreApiScoreSource = typeof CreateScoreApiScoreSource[keyof typeof CreateScoreApiScoreSource];


export const CreateScoreApiScoreSource = {
  human: 'human',
  api: 'api',
  auto: 'auto',
  imported: 'imported',
} as const;

export interface CreateScoreApi {
  source_type: CreateScoreApiSourceType;
  /** @minLength 1 */
  source_id: string;
  label_id: string;
  value: CreateScoreApiValue;
  notes?: string;
  score_source?: CreateScoreApiScoreSource;
  queue_item_id?: string;
}

export interface ScoreResponseApi {
  status?: boolean;
  result: ScoreApi;
}

export type BulkCreateScoresApiSourceType = typeof BulkCreateScoresApiSourceType[keyof typeof BulkCreateScoresApiSourceType];


export const BulkCreateScoresApiSourceType = {
  dataset_row: 'dataset_row',
  trace: 'trace',
  observation_span: 'observation_span',
  prototype_run: 'prototype_run',
  call_execution: 'call_execution',
  trace_session: 'trace_session',
} as const;

export type BulkCreateScoresApiScoresItem = {[key: string]: string};

export interface BulkCreateScoresApi {
  source_type: BulkCreateScoresApiSourceType;
  /** @minLength 1 */
  source_id: string;
  /** @minItems 1 */
  scores: BulkCreateScoresApiScoresItem[];
  notes?: string;
  span_notes?: string;
  span_notes_source_id?: string;
  queue_item_id?: string;
}

export interface BulkCreateScoresResultApi {
  scores: ScoreApi[];
  errors: string[];
}

export interface BulkCreateScoresResponseApi {
  status?: boolean;
  result: BulkCreateScoresResultApi;
}

export type ScoreForSourceResponseApiSpanNotesItem = { [key: string]: unknown };

export interface ScoreForSourceResponseApi {
  status?: boolean;
  result: ScoreApi[];
  span_notes?: ScoreForSourceResponseApiSpanNotesItem[];
}

export type ScoreDeleteResponseApiResult = {[key: string]: boolean};

export interface ScoreDeleteResponseApi {
  status?: boolean;
  result: ScoreDeleteResponseApiResult;
}

export type SecretApiSecretType = typeof SecretApiSecretType[keyof typeof SecretApiSecretType];


export const SecretApiSecretType = {
  API_KEY: 'API_KEY',
  PASSWORD: 'PASSWORD',
  TOKEN: 'TOKEN',
  OTHER: 'OTHER',
} as const;

export interface SecretApi {
  readonly id?: string;
  /**
     * @minLength 1
     * @maxLength 255
     */
  name: string;
  description?: string;
  secret_type?: SecretApiSecretType;
  /** @minLength 1 */
  readonly secret_type_display?: string;
  /** @maxLength 2500 */
  key?: string;
  readonly created_at?: string;
  readonly updated_at?: string;
}

export type ToolsApiConfig = { [key: string]: unknown };

export type ToolsApiConfigType = typeof ToolsApiConfigType[keyof typeof ToolsApiConfigType];


export const ToolsApiConfigType = {
  json: 'json',
  yaml: 'yaml',
} as const;

export interface ToolsApi {
  readonly id?: string;
  /**
     * @minLength 1
     * @maxLength 255
     */
  name: string;
  /**
     * @minLength 1
     * @maxLength 255
     */
  description: string;
  config: ToolsApiConfig;
  config_type?: ToolsApiConfigType;
  readonly organization?: string;
}

export type TTSVoiceApiVoiceType = typeof TTSVoiceApiVoiceType[keyof typeof TTSVoiceApiVoiceType];


export const TTSVoiceApiVoiceType = {
  system: 'system',
  custom: 'custom',
} as const;

export interface TTSVoiceApi {
  readonly id?: string;
  /**
     * @minLength 1
     * @maxLength 255
     */
  name: string;
  /** @maxLength 255 */
  description?: string;
  /**
     * @minLength 1
     * @maxLength 255
     */
  voice_id: string;
  /**
     * @minLength 1
     * @maxLength 255
     */
  provider: string;
  /**
     * @minLength 1
     * @maxLength 255
     */
  model: string;
  readonly voice_type?: TTSVoiceApiVoiceType;
  readonly created_at?: string;
  readonly updated_at?: string;
}

export type SamlApiIdentityType = typeof SamlApiIdentityType[keyof typeof SamlApiIdentityType];


export const SamlApiIdentityType = {
  NUMBER_1: 1,
  NUMBER_2: 2,
  NUMBER_3: 3,
} as const;

export interface SamlApi {
  /** @maxLength 250 */
  name?: string;
  /** @minLength 1 */
  readonly id?: string;
  readonly identity_type?: SamlApiIdentityType;
  is_enabled?: boolean;
}

export type AgentDefinitionListResponseApiAgentType = typeof AgentDefinitionListResponseApiAgentType[keyof typeof AgentDefinitionListResponseApiAgentType];


export const AgentDefinitionListResponseApiAgentType = {
  voice: 'voice',
  text: 'text',
} as const;

/**
 * Language of the agent
 */
export type AgentDefinitionListResponseApiLanguage = typeof AgentDefinitionListResponseApiLanguage[keyof typeof AgentDefinitionListResponseApiLanguage];


export const AgentDefinitionListResponseApiLanguage = {
  ar: 'ar',
  bg: 'bg',
  zh: 'zh',
  cs: 'cs',
  da: 'da',
  nl: 'nl',
  en: 'en',
  fi: 'fi',
  fr: 'fr',
  de: 'de',
  el: 'el',
  hi: 'hi',
  hu: 'hu',
  id: 'id',
  it: 'it',
  ja: 'ja',
  ko: 'ko',
  ms: 'ms',
  no: 'no',
  pl: 'pl',
  pt: 'pt',
  ro: 'ro',
  ru: 'ru',
  sk: 'sk',
  es: 'es',
  sv: 'sv',
  tr: 'tr',
  uk: 'uk',
  vi: 'vi',
} as const;

/**
 * Language of the agent
 */
export type AgentDefinitionListResponseApiLanguagesItem = typeof AgentDefinitionListResponseApiLanguagesItem[keyof typeof AgentDefinitionListResponseApiLanguagesItem];


export const AgentDefinitionListResponseApiLanguagesItem = {
  ar: 'ar',
  bg: 'bg',
  zh: 'zh',
  cs: 'cs',
  da: 'da',
  nl: 'nl',
  en: 'en',
  fi: 'fi',
  fr: 'fr',
  de: 'de',
  el: 'el',
  hi: 'hi',
  hu: 'hu',
  id: 'id',
  it: 'it',
  ja: 'ja',
  ko: 'ko',
  ms: 'ms',
  no: 'no',
  pl: 'pl',
  pt: 'pt',
  ro: 'ro',
  ru: 'ru',
  sk: 'sk',
  es: 'es',
  sv: 'sv',
  tr: 'tr',
  uk: 'uk',
  vi: 'vi',
} as const;

/**
 * Headers to be sent to the websocket server
 */
export type AgentDefinitionListResponseApiWebsocketHeaders = { [key: string]: unknown };

/**
 * Details of the model
 */
export type AgentDefinitionListResponseApiModelDetails = { [key: string]: unknown };

export interface AgentDefinitionListResponseApi {
  readonly id?: string;
  /**
     * Name of the AI agent
     * @minLength 1
     */
  readonly agent_name?: string;
  readonly agent_type?: AgentDefinitionListResponseApiAgentType;
  /**
     * Phone number associated with the AI agent
     * @minLength 1
     */
  readonly contact_number?: string;
  /** Whether the agent handles inbound calls */
  readonly inbound?: boolean;
  /**
     * Detailed description of the AI agent's purpose and capabilities
     * @minLength 1
     */
  readonly description?: string;
  /**
     * External identifier for the assistant
     * @minLength 1
     */
  readonly assistant_id?: string;
  /**
     * Provider of the AI agent
     * @minLength 1
     */
  readonly provider?: string;
  /** Language of the agent */
  readonly language?: AgentDefinitionListResponseApiLanguage;
  readonly languages?: readonly AgentDefinitionListResponseApiLanguagesItem[];
  /**
     * WebSocket URL for real-time communication with the agent
     * @minLength 1
     */
  readonly websocket_url?: string;
  /** Headers to be sent to the websocket server */
  readonly websocket_headers?: AgentDefinitionListResponseApiWebsocketHeaders;
  readonly workspace?: string;
  readonly knowledge_base?: string;
  /** Organization this agent definition belongs to */
  readonly organization?: string;
  readonly created_at?: string;
  readonly updated_at?: string;
  readonly latest_version?: string;
  readonly latest_version_id?: string;
  /** Details of the model */
  readonly model_details?: AgentDefinitionListResponseApiModelDetails;
  /**
     * Model of the agent
     * @minLength 1
     */
  readonly model?: string;
}

export interface AgentDefinitionBulkDeleteRequestApi {
  /**
     * List of agent definition UUIDs to delete.
     * @minItems 1
     */
  agent_ids: string[];
}

export interface AgentDefinitionBulkDeleteResponseApi {
  /** @minLength 1 */
  readonly message?: string;
  readonly agents_updated?: number;
  readonly versions_updated?: number;
}

/**
 * The type of agent. One of: voice, text.
 */
export type AgentDefinitionCreateRequestApiAgentType = typeof AgentDefinitionCreateRequestApiAgentType[keyof typeof AgentDefinitionCreateRequestApiAgentType];


export const AgentDefinitionCreateRequestApiAgentType = {
  voice: 'voice',
  text: 'text',
} as const;

export type AgentDefinitionCreateRequestApiAuthenticationMethod = typeof AgentDefinitionCreateRequestApiAuthenticationMethod[keyof typeof AgentDefinitionCreateRequestApiAuthenticationMethod];


export const AgentDefinitionCreateRequestApiAuthenticationMethod = {
  api_key: 'api_key',
} as const;

export type AgentDefinitionCreateRequestApiModelDetails = { [key: string]: unknown };

export type AgentDefinitionCreateRequestApiWebsocketHeaders = { [key: string]: unknown };

export type AgentDefinitionCreateRequestApiLivekitConfigJson = { [key: string]: unknown };

export interface AgentDefinitionCreateRequestApi {
  /**
     * @minLength 1
     * @maxLength 255
     */
  agent_name: string;
  /** The type of agent. One of: voice, text. */
  agent_type: AgentDefinitionCreateRequestApiAgentType;
  /** @minLength 1 */
  commit_message: string;
  inbound?: boolean;
  description?: string;
  provider?: string;
  api_key?: string;
  assistant_id?: string;
  authentication_method?: AgentDefinitionCreateRequestApiAuthenticationMethod;
  language?: string;
  languages?: string[];
  contact_number?: string;
  knowledge_base?: string;
  observability_enabled?: boolean;
  model?: string;
  model_details?: AgentDefinitionCreateRequestApiModelDetails;
  websocket_url?: string;
  websocket_headers?: AgentDefinitionCreateRequestApiWebsocketHeaders;
  replay_session_id?: string;
  /** @maxLength 500 */
  livekit_url?: string;
  livekit_api_key?: string;
  livekit_api_secret?: string;
  livekit_agent_name?: string;
  livekit_config_json?: AgentDefinitionCreateRequestApiLivekitConfigJson;
  /** @minimum 1 */
  livekit_max_concurrency?: number;
}

export type AgentDefinitionResponseApiAgentType = typeof AgentDefinitionResponseApiAgentType[keyof typeof AgentDefinitionResponseApiAgentType];


export const AgentDefinitionResponseApiAgentType = {
  voice: 'voice',
  text: 'text',
} as const;

/**
 * Language of the agent
 */
export type AgentDefinitionResponseApiLanguage = typeof AgentDefinitionResponseApiLanguage[keyof typeof AgentDefinitionResponseApiLanguage];


export const AgentDefinitionResponseApiLanguage = {
  ar: 'ar',
  bg: 'bg',
  zh: 'zh',
  cs: 'cs',
  da: 'da',
  nl: 'nl',
  en: 'en',
  fi: 'fi',
  fr: 'fr',
  de: 'de',
  el: 'el',
  hi: 'hi',
  hu: 'hu',
  id: 'id',
  it: 'it',
  ja: 'ja',
  ko: 'ko',
  ms: 'ms',
  no: 'no',
  pl: 'pl',
  pt: 'pt',
  ro: 'ro',
  ru: 'ru',
  sk: 'sk',
  es: 'es',
  sv: 'sv',
  tr: 'tr',
  uk: 'uk',
  vi: 'vi',
} as const;

/**
 * Language of the agent
 */
export type AgentDefinitionResponseApiLanguagesItem = typeof AgentDefinitionResponseApiLanguagesItem[keyof typeof AgentDefinitionResponseApiLanguagesItem];


export const AgentDefinitionResponseApiLanguagesItem = {
  ar: 'ar',
  bg: 'bg',
  zh: 'zh',
  cs: 'cs',
  da: 'da',
  nl: 'nl',
  en: 'en',
  fi: 'fi',
  fr: 'fr',
  de: 'de',
  el: 'el',
  hi: 'hi',
  hu: 'hu',
  id: 'id',
  it: 'it',
  ja: 'ja',
  ko: 'ko',
  ms: 'ms',
  no: 'no',
  pl: 'pl',
  pt: 'pt',
  ro: 'ro',
  ru: 'ru',
  sk: 'sk',
  es: 'es',
  sv: 'sv',
  tr: 'tr',
  uk: 'uk',
  vi: 'vi',
} as const;

export type AgentDefinitionResponseApiAuthenticationMethod = typeof AgentDefinitionResponseApiAuthenticationMethod[keyof typeof AgentDefinitionResponseApiAuthenticationMethod];


export const AgentDefinitionResponseApiAuthenticationMethod = {
  api_key: 'api_key',
} as const;

/**
 * Headers to be sent to the websocket server
 */
export type AgentDefinitionResponseApiWebsocketHeaders = { [key: string]: unknown };

/**
 * Details of the model
 */
export type AgentDefinitionResponseApiModelDetails = { [key: string]: unknown };

export interface AgentDefinitionResponseApi {
  readonly id?: string;
  /**
     * Name of the AI agent
     * @minLength 1
     */
  readonly agent_name?: string;
  readonly agent_type?: AgentDefinitionResponseApiAgentType;
  /**
     * Phone number associated with the AI agent
     * @minLength 1
     */
  readonly contact_number?: string;
  /** Whether the agent handles inbound calls */
  readonly inbound?: boolean;
  /**
     * Detailed description of the AI agent's purpose and capabilities
     * @minLength 1
     */
  readonly description?: string;
  /**
     * External identifier for the assistant
     * @minLength 1
     */
  readonly assistant_id?: string;
  /**
     * Provider of the AI agent
     * @minLength 1
     */
  readonly provider?: string;
  /** Language of the agent */
  readonly language?: AgentDefinitionResponseApiLanguage;
  readonly languages?: readonly AgentDefinitionResponseApiLanguagesItem[];
  readonly authentication_method?: AgentDefinitionResponseApiAuthenticationMethod;
  /**
     * WebSocket URL for real-time communication with the agent
     * @minLength 1
     */
  readonly websocket_url?: string;
  /** Headers to be sent to the websocket server */
  readonly websocket_headers?: AgentDefinitionResponseApiWebsocketHeaders;
  readonly workspace?: string;
  readonly knowledge_base?: string;
  /** Organization this agent definition belongs to */
  readonly organization?: string;
  /**
     * API key for the agent
     * @minLength 1
     */
  readonly api_key?: string;
  readonly observability_provider?: string;
  readonly created_at?: string;
  readonly updated_at?: string;
  /**
     * Model of the agent
     * @minLength 1
     */
  readonly model?: string;
  /** Details of the model */
  readonly model_details?: AgentDefinitionResponseApiModelDetails;
  readonly livekit_url?: string;
  readonly livekit_api_key?: string;
  readonly livekit_agent_name?: string;
  readonly livekit_config_json?: string;
  readonly livekit_max_concurrency?: string;
}

export interface AgentDefinitionCreateResponseApi {
  /** @minLength 1 */
  readonly message?: string;
  agent?: AgentDefinitionResponseApi;
}

export interface AgentDefinitionDeleteResponseApi {
  /** @minLength 1 */
  readonly message?: string;
}

export type AgentDefinitionEditRequestApiAgentType = typeof AgentDefinitionEditRequestApiAgentType[keyof typeof AgentDefinitionEditRequestApiAgentType];


export const AgentDefinitionEditRequestApiAgentType = {
  voice: 'voice',
  text: 'text',
} as const;

export type AgentDefinitionEditRequestApiAuthenticationMethod = typeof AgentDefinitionEditRequestApiAuthenticationMethod[keyof typeof AgentDefinitionEditRequestApiAuthenticationMethod];


export const AgentDefinitionEditRequestApiAuthenticationMethod = {
  api_key: 'api_key',
} as const;

export type AgentDefinitionEditRequestApiModelDetails = { [key: string]: unknown };

export type AgentDefinitionEditRequestApiWebsocketHeaders = { [key: string]: unknown };

export type AgentDefinitionEditRequestApiLivekitConfigJson = { [key: string]: unknown };

export interface AgentDefinitionEditRequestApi {
  /**
     * @minLength 1
     * @maxLength 255
     */
  agent_name?: string;
  agent_type?: AgentDefinitionEditRequestApiAgentType;
  description?: string;
  provider?: string;
  api_key?: string;
  assistant_id?: string;
  authentication_method?: AgentDefinitionEditRequestApiAuthenticationMethod;
  language?: string;
  languages?: string[];
  contact_number?: string;
  inbound?: boolean;
  knowledge_base?: string;
  model?: string;
  model_details?: AgentDefinitionEditRequestApiModelDetails;
  websocket_url?: string;
  websocket_headers?: AgentDefinitionEditRequestApiWebsocketHeaders;
  /** @maxLength 500 */
  livekit_url?: string;
  livekit_api_key?: string;
  livekit_api_secret?: string;
  livekit_agent_name?: string;
  livekit_config_json?: AgentDefinitionEditRequestApiLivekitConfigJson;
  /** @minimum 1 */
  livekit_max_concurrency?: number;
}

export interface AgentDefinitionEditResponseApi {
  /** @minLength 1 */
  readonly message?: string;
  agent?: AgentDefinitionResponseApi;
}

/**
 * Current status of this version
 */
export type AgentVersionListResponseApiStatus = typeof AgentVersionListResponseApiStatus[keyof typeof AgentVersionListResponseApiStatus];


export const AgentVersionListResponseApiStatus = {
  draft: 'draft',
  active: 'active',
  archived: 'archived',
  deprecated: 'deprecated',
} as const;

export interface AgentVersionListResponseApi {
  readonly id?: string;
  /** Version number of the agent */
  readonly version_number?: number;
  /**
     * Human-readable version name (e.g., 'v1.2.3')
     * @minLength 1
     */
  readonly version_name?: string;
  readonly version_name_display?: string;
  /** Current status of this version */
  readonly status?: AgentVersionListResponseApiStatus;
  /** @minLength 1 */
  readonly status_display?: string;
  /** Performance score (0.0 to 10.0) */
  readonly score?: string;
  /** Number of tests run for this version */
  readonly test_count?: number;
  /** Test pass rate percentage */
  readonly pass_rate?: string;
  /**
     * Description of changes in this version
     * @minLength 1
     */
  readonly description?: string;
  /**
     * Commit message for the agent version
     * @minLength 1
     */
  readonly commit_message?: string;
  readonly is_active?: string;
  readonly is_latest?: string;
  readonly created_at?: string;
}

export type AgentVersionCreateRequestApiAgentType = typeof AgentVersionCreateRequestApiAgentType[keyof typeof AgentVersionCreateRequestApiAgentType];


export const AgentVersionCreateRequestApiAgentType = {
  voice: 'voice',
  text: 'text',
} as const;

export type AgentVersionCreateRequestApiAuthenticationMethod = typeof AgentVersionCreateRequestApiAuthenticationMethod[keyof typeof AgentVersionCreateRequestApiAuthenticationMethod];


export const AgentVersionCreateRequestApiAuthenticationMethod = {
  api_key: 'api_key',
} as const;

export type AgentVersionCreateRequestApiModelDetails = { [key: string]: unknown };

export type AgentVersionCreateRequestApiLivekitConfigJson = { [key: string]: unknown };

export interface AgentVersionCreateRequestApi {
  /**
     * @minLength 1
     * @maxLength 255
     */
  agent_name?: string;
  agent_type?: AgentVersionCreateRequestApiAgentType;
  description?: string;
  provider?: string;
  api_key?: string;
  assistant_id?: string;
  authentication_method?: AgentVersionCreateRequestApiAuthenticationMethod;
  language?: string;
  languages?: string[];
  contact_number?: string;
  inbound?: boolean;
  knowledge_base?: string;
  model?: string;
  model_details?: AgentVersionCreateRequestApiModelDetails;
  /** @maxLength 500 */
  livekit_url?: string;
  /** @maxLength 255 */
  livekit_api_key?: string;
  /** @maxLength 500 */
  livekit_api_secret?: string;
  /** @maxLength 255 */
  livekit_agent_name?: string;
  livekit_config_json?: AgentVersionCreateRequestApiLivekitConfigJson;
  /** @minimum 1 */
  livekit_max_concurrency?: number;
  commit_message?: string;
  observability_enabled?: boolean;
}

/**
 * Current status of this version
 */
export type AgentVersionResponseApiStatus = typeof AgentVersionResponseApiStatus[keyof typeof AgentVersionResponseApiStatus];


export const AgentVersionResponseApiStatus = {
  draft: 'draft',
  active: 'active',
  archived: 'archived',
  deprecated: 'deprecated',
} as const;

/**
 * Snapshot of agent configuration at this version
 */
export type AgentVersionResponseApiConfigurationSnapshot = { [key: string]: unknown };

export interface AgentVersionResponseApi {
  readonly id?: string;
  /** Version number of the agent */
  readonly version_number?: number;
  /**
     * Human-readable version name (e.g., 'v1.2.3')
     * @minLength 1
     */
  readonly version_name?: string;
  readonly version_name_display?: string;
  /** Current status of this version */
  readonly status?: AgentVersionResponseApiStatus;
  /** @minLength 1 */
  readonly status_display?: string;
  /** Performance score (0.0 to 10.0) */
  readonly score?: string;
  /** Number of tests run for this version */
  readonly test_count?: number;
  /** Test pass rate percentage */
  readonly pass_rate?: string;
  /**
     * Description of changes in this version
     * @minLength 1
     */
  readonly description?: string;
  /**
     * Commit message for the agent version
     * @minLength 1
     */
  readonly commit_message?: string;
  /**
     * Detailed release notes for this version
     * @minLength 1
     */
  readonly release_notes?: string;
  /** Parent agent definition */
  readonly agent_definition?: string;
  /** Organization this version belongs to */
  readonly organization?: string;
  /** Snapshot of agent configuration at this version */
  readonly configuration_snapshot?: AgentVersionResponseApiConfigurationSnapshot;
  readonly is_active?: string;
  readonly is_latest?: string;
  readonly created_at?: string;
  readonly updated_at?: string;
}

export interface AgentVersionCreateResponseApi {
  /** @minLength 1 */
  readonly message?: string;
  version?: AgentVersionResponseApi;
}

export interface AgentVersionActivateResponseApi {
  /** @minLength 1 */
  readonly message?: string;
  version?: AgentVersionResponseApi;
}

/**
 * Current status of the call
 */
export type CallExecutionApiStatus = typeof CallExecutionApiStatus[keyof typeof CallExecutionApiStatus];


export const CallExecutionApiStatus = {
  pending: 'pending',
  queued: 'queued',
  ongoing: 'ongoing',
  completed: 'completed',
  failed: 'failed',
  analyzing: 'analyzing',
  cancelled: 'cancelled',
} as const;

/**
 * Additional metadata about the call
 */
export type CallExecutionApiCallMetadata = { [key: string]: unknown };

/**
 * Complete call data from the provider. Format: dict[provider_name, data] where provider_name must be from SupportedProviders
 */
export type CallExecutionApiProviderCallData = { [key: string]: unknown };

/**
 * Call analysis data from the service provider
 */
export type CallExecutionApiAnalysisData = { [key: string]: unknown };

/**
 * Call evaluation data from the service provider
 */
export type CallExecutionApiEvaluationData = { [key: string]: unknown };

/**
 * Evaluation output
 */
export type CallExecutionApiEvalOutputs = { [key: string]: unknown };

/**
 * Type of simulation call
 */
export type CallExecutionApiSimulationCallType = typeof CallExecutionApiSimulationCallType[keyof typeof CallExecutionApiSimulationCallType];


export const CallExecutionApiSimulationCallType = {
  voice: 'voice',
  text: 'text',
} as const;

export interface CallExecutionApi {
  readonly id?: string;
  /**
     * Phone number called (null for TEXT/chat simulations)
     * @maxLength 20
     */
  phone_number?: string;
  /** @minLength 1 */
  readonly service_provider_call_id?: string;
  /** Current status of the call */
  status?: CallExecutionApiStatus;
  /** When the call started */
  started_at?: string;
  /** When the call completed */
  completed_at?: string;
  /**
     * Duration of the call in seconds
     * @minimum -2147483648
     * @maximum 2147483647
     */
  duration_seconds?: number;
  /**
     * URL to the call recording
     * @maxLength 500
     */
  recording_url?: string;
  /**
     * Cost of the call in cents
     * @minimum -2147483648
     * @maximum 2147483647
     */
  cost_cents?: number;
  /** Additional metadata about the call */
  call_metadata?: CallExecutionApiCallMetadata;
  /** Error message if the call failed */
  error_message?: string;
  /** @minLength 1 */
  readonly scenario_name?: string;
  readonly transcripts?: string;
  readonly created_at?: string;
  readonly updated_at?: string;
  /** Complete call data from the provider. Format: dict[provider_name, data] where provider_name must be from SupportedProviders */
  provider_call_data?: CallExecutionApiProviderCallData;
  /**
     * Stereo recording URL from Vapi
     * @maxLength 500
     */
  stereo_recording_url?: string;
  /**
     * Reason why the call ended
     * @maxLength 10000
     */
  ended_reason?: string;
  /**
     * STT cost in cents
     * @minimum -2147483648
     * @maximum 2147483647
     */
  stt_cost_cents?: number;
  /**
     * LLM cost in cents
     * @minimum -2147483648
     * @maximum 2147483647
     */
  llm_cost_cents?: number;
  /**
     * TTS cost in cents
     * @minimum -2147483648
     * @maximum 2147483647
     */
  tts_cost_cents?: number;
  /** Overall call performance score */
  overall_score?: number;
  /**
     * Average response time in milliseconds
     * @minimum -2147483648
     * @maximum 2147483647
     */
  response_time_ms?: number;
  readonly response_time_seconds?: string;
  /**
     * Assistant ID used for the call (system side)
     * @maxLength 255
     */
  assistant_id?: string;
  /**
     * Customer phone number (E.164 format)
     * @maxLength 20
     */
  customer_number?: string;
  /**
     * Type of call (e.g., outboundPhoneCall)
     * @maxLength 50
     */
  call_type?: string;
  /** When the call ended */
  ended_at?: string;
  /** Call analysis data from the service provider */
  analysis_data?: CallExecutionApiAnalysisData;
  /** Call evaluation data from the service provider */
  evaluation_data?: CallExecutionApiEvaluationData;
  /**
     * Number of messages in the call
     * @minimum -2147483648
     * @maximum 2147483647
     */
  message_count?: number;
  /** Whether transcript is available */
  transcript_available?: boolean;
  /** Whether recording is available */
  recording_available?: boolean;
  /** Evaluation output */
  eval_outputs?: CallExecutionApiEvalOutputs;
  readonly error_localizer_tasks?: string;
  /** Call summary from the service */
  call_summary?: string;
  agent_version?: string;
  /**
     * Total customer-reported cost in cents
     * @minimum -2147483648
     * @maximum 2147483647
     */
  customer_cost_cents?: number;
  readonly system_metrics?: string;
  readonly cost_breakdown?: string;
  /**
     * Customer call ID if available
     * @maxLength 255
     */
  customer_call_id?: string;
  /** Type of simulation call */
  simulation_call_type?: CallExecutionApiSimulationCallType;
  readonly processing_skipped?: string;
  readonly processing_skip_reason?: string;
}

export interface AgentVersionDeleteResponseApi {
  /** @minLength 1 */
  readonly message?: string;
}

export type EvalTemplateSummaryApiOutput = { [key: string]: unknown };

export interface EvalTemplateSummaryApi {
  /** @minLength 1 */
  name: string;
  /** @minLength 1 */
  id: string;
  total_cells: number;
  output: EvalTemplateSummaryApiOutput;
}

export interface EvalSummaryResponseApi {
  status?: boolean;
  result: EvalTemplateSummaryApi[];
}

export type EvalErrorResponseApiDetails = {[key: string]: string};

export interface EvalErrorResponseApi {
  /** @minLength 1 */
  error: string;
  details?: EvalErrorResponseApiDetails;
}

export type AgentVersionRestoreResponseApiAgent = {[key: string]: string};

export interface AgentVersionRestoreResponseApi {
  /** @minLength 1 */
  readonly message?: string;
  readonly agent?: AgentVersionRestoreResponseApiAgent;
  version?: AgentVersionResponseApi;
}

/**
 * Voice provider. One of: vapi, retell, eleven_labs, others.
 */
export type FetchAssistantRequestApiProvider = typeof FetchAssistantRequestApiProvider[keyof typeof FetchAssistantRequestApiProvider];


export const FetchAssistantRequestApiProvider = {
  vapi: 'vapi',
  retell: 'retell',
  eleven_labs: 'eleven_labs',
  others: 'others',
} as const;

export interface FetchAssistantRequestApi {
  /** @minLength 1 */
  assistant_id: string;
  /** @minLength 1 */
  api_key: string;
  /** Voice provider. One of: vapi, retell, eleven_labs, others. */
  provider?: FetchAssistantRequestApiProvider;
}

export interface FetchAssistantResponseApi {
  /** @minLength 1 */
  readonly name?: string;
  /** @minLength 1 */
  readonly assistant_id?: string;
  /** @minLength 1 */
  readonly api_key?: string;
  /** @minLength 1 */
  readonly prompt?: string;
  /** @minLength 1 */
  readonly provider?: string;
  /** @minLength 1 */
  readonly commit_message?: string;
}

export type AgentPromptOptimiserRunListApiOptimiserType = typeof AgentPromptOptimiserRunListApiOptimiserType[keyof typeof AgentPromptOptimiserRunListApiOptimiserType];


export const AgentPromptOptimiserRunListApiOptimiserType = {
  random_search: 'random_search',
  gepa: 'gepa',
  protegi: 'protegi',
  bayesian: 'bayesian',
  metaprompt: 'metaprompt',
  promptwizard: 'promptwizard',
} as const;

export type AgentPromptOptimiserRunListApiStatus = typeof AgentPromptOptimiserRunListApiStatus[keyof typeof AgentPromptOptimiserRunListApiStatus];


export const AgentPromptOptimiserRunListApiStatus = {
  pending: 'pending',
  running: 'running',
  completed: 'completed',
  failed: 'failed',
} as const;

export type AgentPromptOptimiserRunListApiConfiguration = { [key: string]: unknown };

export interface AgentPromptOptimiserRunListApi {
  readonly id?: string;
  /** @minLength 1 */
  optimisation_name: string;
  started_at: string;
  readonly no_of_trials?: string;
  optimiser_type: AgentPromptOptimiserRunListApiOptimiserType;
  status?: AgentPromptOptimiserRunListApiStatus;
  error_message?: string;
  configuration?: AgentPromptOptimiserRunListApiConfiguration;
  /**
     * LLM model used for the optimiser run
     * @minLength 1
     * @maxLength 255
     */
  model: string;
}

export type AgentPromptOptimiserRunCreateApiOptimiserType = typeof AgentPromptOptimiserRunCreateApiOptimiserType[keyof typeof AgentPromptOptimiserRunCreateApiOptimiserType];


export const AgentPromptOptimiserRunCreateApiOptimiserType = {
  random_search: 'random_search',
  gepa: 'gepa',
  protegi: 'protegi',
  bayesian: 'bayesian',
  metaprompt: 'metaprompt',
  promptwizard: 'promptwizard',
} as const;

export type AgentPromptOptimiserRunCreateApiConfiguration = { [key: string]: unknown };

export interface AgentPromptOptimiserRunCreateApi {
  readonly id?: string;
  /**
     * @minLength 1
     * @maxLength 255
     */
  name: string;
  test_execution_id: string;
  optimiser_type: AgentPromptOptimiserRunCreateApiOptimiserType;
  /**
     * LLM model used for the optimiser run
     * @minLength 1
     * @maxLength 255
     */
  model: string;
  configuration?: AgentPromptOptimiserRunCreateApiConfiguration;
  readonly created_at?: string;
  readonly updated_at?: string;
}

export type AgentPromptOptimiserRunApiOptimiserType = typeof AgentPromptOptimiserRunApiOptimiserType[keyof typeof AgentPromptOptimiserRunApiOptimiserType];


export const AgentPromptOptimiserRunApiOptimiserType = {
  random_search: 'random_search',
  gepa: 'gepa',
  protegi: 'protegi',
  bayesian: 'bayesian',
  metaprompt: 'metaprompt',
  promptwizard: 'promptwizard',
} as const;

export type AgentPromptOptimiserRunApiStatus = typeof AgentPromptOptimiserRunApiStatus[keyof typeof AgentPromptOptimiserRunApiStatus];


export const AgentPromptOptimiserRunApiStatus = {
  pending: 'pending',
  running: 'running',
  completed: 'completed',
  failed: 'failed',
} as const;

export type AgentPromptOptimiserRunApiResult = { [key: string]: unknown };

export type AgentPromptOptimiserRunApiConfiguration = { [key: string]: unknown };

export interface AgentPromptOptimiserRunApi {
  readonly id?: string;
  agent_optimiser: string;
  agent_optimiser_run: string;
  test_execution: string;
  optimiser_type: AgentPromptOptimiserRunApiOptimiserType;
  /**
     * LLM model used for the optimiser run
     * @minLength 1
     * @maxLength 255
     */
  model: string;
  status?: AgentPromptOptimiserRunApiStatus;
  result?: AgentPromptOptimiserRunApiResult;
  configuration?: AgentPromptOptimiserRunApiConfiguration;
}

export type CallExecutionErrorResponseApiDetails = {[key: string]: string};

export interface CallExecutionErrorResponseApi {
  /** @minLength 1 */
  readonly error?: string;
  readonly details?: CallExecutionErrorResponseApiDetails;
}

export interface LiveKitErrorResponseApi {
  /** @minLength 1 */
  error: string;
}

export type LiveKitCallExecutionUpdateRequestApiProviderCallData = { [key: string]: unknown };

export interface LiveKitCallExecutionUpdateRequestApi {
  provider_call_data?: LiveKitCallExecutionUpdateRequestApiProviderCallData;
  started_at?: string;
  completed_at?: string;
  ended_at?: string;
  /** @minimum 0 */
  duration_seconds?: number;
  ended_reason?: string;
  service_provider_call_id?: string;
}

export interface LiveKitOkResponseApi {
  ok: boolean;
}

export interface LiveKitListenerTokenResultApi {
  /** @minLength 1 */
  token: string;
  /** @minLength 1 */
  url: string;
  /** @minLength 1 */
  room_name: string;
}

export interface LiveKitListenerTokenResponseApi {
  status?: boolean;
  result: LiveKitListenerTokenResultApi;
}

export interface LiveKitTemporalSignalRequestApi {
  workflow_id?: string;
  call_id?: string;
  /** @minLength 1 */
  status?: string;
  /** @minimum 0 */
  duration_seconds?: number;
  end_reason?: string;
}

export interface LiveKitTranscriptRowApi {
  /** @minLength 1 */
  role?: string;
  /** @minLength 1 */
  content?: string;
  start_time_ms?: number;
  end_time_ms?: number;
}

export interface LiveKitTranscriptsRequestApi {
  /** @minLength 1 */
  role?: string;
  /** @minLength 1 */
  content?: string;
  start_time_ms?: number;
  end_time_ms?: number;
  transcripts?: LiveKitTranscriptRowApi[];
}

export interface ValidateLiveKitCredentialsRequestApi {
  /** @minLength 1 */
  livekit_url: string;
  /** @minLength 1 */
  api_key: string;
  /** @minLength 1 */
  api_secret: string;
  agent_name?: string;
  agent_definition_id?: string;
}

export interface ValidateLiveKitCredentialsResultApi {
  valid: boolean;
  error?: string;
}

export interface ValidateLiveKitCredentialsResponseApi {
  status?: boolean;
  result: ValidateLiveKitCredentialsResultApi;
}

/**
 * Type of persona (system or workspace-level)
 */
export type PersonaListApiPersonaType = typeof PersonaListApiPersonaType[keyof typeof PersonaListApiPersonaType];


export const PersonaListApiPersonaType = {
  system: 'system',
  workspace: 'workspace',
} as const;

/**
 * List of genders for the persona (e.g., ['male'], ['female'])
 */
export type PersonaListApiGender = { [key: string]: unknown };

/**
 * List of age groups for the persona (e.g., ['18-25'], ['25-32'])
 */
export type PersonaListApiAgeGroup = { [key: string]: unknown };

/**
 * List of occupations/professions for the persona (e.g., ['Engineer'], ['Teacher'])
 */
export type PersonaListApiOccupation = { [key: string]: unknown };

/**
 * List of locations for the persona (e.g., ['United States'], ['Canada'])
 */
export type PersonaListApiLocation = { [key: string]: unknown };

/**
 * List of personality types for the persona (e.g., ['Friendly and cooperative'])
 */
export type PersonaListApiPersonality = { [key: string]: unknown };

/**
 * List of communication styles for the persona (e.g., ['Direct and concise'])
 */
export type PersonaListApiCommunicationStyle = { [key: string]: unknown };

/**
 * List of languages the persona speaks (e.g., ['English', 'Hindi'])
 */
export type PersonaListApiLanguages = { [key: string]: unknown };

/**
 * List of accents for the persona (e.g., ['American'], ['Australian'])
 */
export type PersonaListApiAccent = { [key: string]: unknown };

/**
 * List of conversation speeds (e.g., ['1.0'], ['1.25'])
 */
export type PersonaListApiConversationSpeed = { [key: string]: unknown };

/**
 * List of sensitivities for detecting when persona finished speaking (e.g., ['5'], ['6'])
 */
export type PersonaListApiFinishedSpeakingSensitivity = { [key: string]: unknown };

/**
 * List of sensitivities for allowing interruptions (e.g., ['5'], ['6'])
 */
export type PersonaListApiInterruptSensitivity = { [key: string]: unknown };

/**
 * List of keywords/tags describing the persona (e.g., ['Knowledgeable', 'Patient', 'Helpful'])
 */
export type PersonaListApiKeywords = { [key: string]: unknown };

/**
 * Additional metadata for the persona (speech clarity, base emotion, etc.)
 */
export type PersonaListApiMetadata = { [key: string]: unknown };

/**
 * Punctuation style for the persona
 */
export type PersonaListApiPunctuation = typeof PersonaListApiPunctuation[keyof typeof PersonaListApiPunctuation];


export const PersonaListApiPunctuation = {
  clean: 'clean',
  minimal: 'minimal',
  expressive: 'expressive',
  erratic: 'erratic',
} as const;

/**
 * Slang usage for the persona
 */
export type PersonaListApiSlangUsage = typeof PersonaListApiSlangUsage[keyof typeof PersonaListApiSlangUsage];


export const PersonaListApiSlangUsage = {
  none: 'none',
  moderate: 'moderate',
  heavy: 'heavy',
  light: 'light',
} as const;

/**
 * Typos frequency for the persona
 */
export type PersonaListApiTyposFrequency = typeof PersonaListApiTyposFrequency[keyof typeof PersonaListApiTyposFrequency];


export const PersonaListApiTyposFrequency = {
  none: 'none',
  rare: 'rare',
  occasional: 'occasional',
  frequent: 'frequent',
} as const;

/**
 * Regional mix for the persona
 */
export type PersonaListApiRegionalMix = typeof PersonaListApiRegionalMix[keyof typeof PersonaListApiRegionalMix];


export const PersonaListApiRegionalMix = {
  none: 'none',
  moderate: 'moderate',
  heavy: 'heavy',
  light: 'light',
} as const;

/**
 * Emoji usage for the persona
 */
export type PersonaListApiEmojiUsage = typeof PersonaListApiEmojiUsage[keyof typeof PersonaListApiEmojiUsage];


export const PersonaListApiEmojiUsage = {
  never: 'never',
  light: 'light',
  regular: 'regular',
  heavy: 'heavy',
} as const;

/**
 * Tone for the persona
 */
export type PersonaListApiTone = typeof PersonaListApiTone[keyof typeof PersonaListApiTone];


export const PersonaListApiTone = {
  formal: 'formal',
  casual: 'casual',
  neutral: 'neutral',
} as const;

/**
 * Verbosity for the persona
 */
export type PersonaListApiVerbosity = typeof PersonaListApiVerbosity[keyof typeof PersonaListApiVerbosity];


export const PersonaListApiVerbosity = {
  brief: 'brief',
  balanced: 'balanced',
  detailed: 'detailed',
} as const;

export interface PersonaListApi {
  readonly id?: string;
  /** Type of persona (system or workspace-level) */
  readonly persona_type?: PersonaListApiPersonaType;
  /** @minLength 1 */
  readonly persona_type_display?: string;
  /**
     * Name of the persona
     * @minLength 1
     */
  readonly name?: string;
  /**
     * Description of the persona
     * @minLength 1
     */
  readonly description?: string;
  /** List of genders for the persona (e.g., ['male'], ['female']) */
  readonly gender?: PersonaListApiGender;
  /** List of age groups for the persona (e.g., ['18-25'], ['25-32']) */
  readonly age_group?: PersonaListApiAgeGroup;
  /** List of occupations/professions for the persona (e.g., ['Engineer'], ['Teacher']) */
  readonly occupation?: PersonaListApiOccupation;
  /** List of locations for the persona (e.g., ['United States'], ['Canada']) */
  readonly location?: PersonaListApiLocation;
  /** List of personality types for the persona (e.g., ['Friendly and cooperative']) */
  readonly personality?: PersonaListApiPersonality;
  /** List of communication styles for the persona (e.g., ['Direct and concise']) */
  readonly communication_style?: PersonaListApiCommunicationStyle;
  /** Whether the persona supports multiple languages */
  readonly multilingual?: boolean;
  /** List of languages the persona speaks (e.g., ['English', 'Hindi']) */
  readonly languages?: PersonaListApiLanguages;
  /** List of accents for the persona (e.g., ['American'], ['Australian']) */
  readonly accent?: PersonaListApiAccent;
  /** List of conversation speeds (e.g., ['1.0'], ['1.25']) */
  readonly conversation_speed?: PersonaListApiConversationSpeed;
  /** Whether background sound is enabled (null=not specified, True/False for enabled/disabled) */
  readonly background_sound?: boolean;
  /** List of sensitivities for detecting when persona finished speaking (e.g., ['5'], ['6']) */
  readonly finished_speaking_sensitivity?: PersonaListApiFinishedSpeakingSensitivity;
  /** List of sensitivities for allowing interruptions (e.g., ['5'], ['6']) */
  readonly interrupt_sensitivity?: PersonaListApiInterruptSensitivity;
  /** List of keywords/tags describing the persona (e.g., ['Knowledgeable', 'Patient', 'Helpful']) */
  readonly keywords?: PersonaListApiKeywords;
  /** Additional metadata for the persona (speech clarity, base emotion, etc.) */
  readonly metadata?: PersonaListApiMetadata;
  /**
     * Additional instructions for how this persona should behave
     * @minLength 1
     */
  readonly additional_instruction?: string;
  /** Whether this is a default/recommended persona */
  readonly is_default?: boolean;
  readonly created_at?: string;
  readonly updated_at?: string;
  readonly simulation_type?: string;
  /** Punctuation style for the persona */
  readonly punctuation?: PersonaListApiPunctuation;
  /** Slang usage for the persona */
  readonly slang_usage?: PersonaListApiSlangUsage;
  /** Typos frequency for the persona */
  readonly typos_frequency?: PersonaListApiTyposFrequency;
  /** Regional mix for the persona */
  readonly regional_mix?: PersonaListApiRegionalMix;
  /** Emoji usage for the persona */
  readonly emoji_usage?: PersonaListApiEmojiUsage;
  /** Tone for the persona */
  readonly tone?: PersonaListApiTone;
  /** Verbosity for the persona */
  readonly verbosity?: PersonaListApiVerbosity;
}

export type PersonaCreateApiCustomProperties = { [key: string]: unknown };

export interface PersonaCreateApi {
  /**
     * @minLength 1
     * @maxLength 255
     */
  name: string;
  /** @minLength 1 */
  description: string;
  gender?: string[];
  age_group?: string[];
  location?: string[];
  profession?: string[];
  personality?: string[];
  communication_style?: string[];
  accent?: string[];
  multilingual?: boolean;
  language?: string[];
  conversation_speed?: string[];
  background_sound?: boolean;
  finished_speaking_sensitivity?: string[];
  interrupt_sensitivity?: string[];
  keywords?: string[];
  custom_properties?: PersonaCreateApiCustomProperties;
  additional_instruction?: string;
  simulation_type?: string;
  tone?: string;
  punctuation?: string;
  slang_usage?: string;
  typos_frequency?: string;
  regional_mix?: string;
  emoji_usage?: string;
  verbosity?: string;
}

export interface PersonaDuplicateRequestApi {
  /**
     * @minLength 1
     * @maxLength 255
     */
  name: string;
}

/**
 * Type of persona (system or workspace-level)
 */
export type PersonaApiPersonaType = typeof PersonaApiPersonaType[keyof typeof PersonaApiPersonaType];


export const PersonaApiPersonaType = {
  system: 'system',
  workspace: 'workspace',
} as const;

/**
 * Type of simulation for the persona
 */
export type PersonaApiSimulationType = typeof PersonaApiSimulationType[keyof typeof PersonaApiSimulationType];


export const PersonaApiSimulationType = {
  voice: 'voice',
  text: 'text',
} as const;

/**
 * Punctuation style for the persona
 */
export type PersonaApiPunctuation = typeof PersonaApiPunctuation[keyof typeof PersonaApiPunctuation];


export const PersonaApiPunctuation = {
  clean: 'clean',
  minimal: 'minimal',
  expressive: 'expressive',
  erratic: 'erratic',
} as const;

/**
 * Slang usage for the persona
 */
export type PersonaApiSlangUsage = typeof PersonaApiSlangUsage[keyof typeof PersonaApiSlangUsage];


export const PersonaApiSlangUsage = {
  none: 'none',
  moderate: 'moderate',
  heavy: 'heavy',
  light: 'light',
} as const;

/**
 * Typos frequency for the persona
 */
export type PersonaApiTyposFrequency = typeof PersonaApiTyposFrequency[keyof typeof PersonaApiTyposFrequency];


export const PersonaApiTyposFrequency = {
  none: 'none',
  rare: 'rare',
  occasional: 'occasional',
  frequent: 'frequent',
} as const;

/**
 * Regional mix for the persona
 */
export type PersonaApiRegionalMix = typeof PersonaApiRegionalMix[keyof typeof PersonaApiRegionalMix];


export const PersonaApiRegionalMix = {
  none: 'none',
  moderate: 'moderate',
  heavy: 'heavy',
  light: 'light',
} as const;

/**
 * Emoji usage for the persona
 */
export type PersonaApiEmojiUsage = typeof PersonaApiEmojiUsage[keyof typeof PersonaApiEmojiUsage];


export const PersonaApiEmojiUsage = {
  never: 'never',
  light: 'light',
  regular: 'regular',
  heavy: 'heavy',
} as const;

/**
 * Tone for the persona
 */
export type PersonaApiTone = typeof PersonaApiTone[keyof typeof PersonaApiTone];


export const PersonaApiTone = {
  formal: 'formal',
  casual: 'casual',
  neutral: 'neutral',
} as const;

/**
 * Verbosity for the persona
 */
export type PersonaApiVerbosity = typeof PersonaApiVerbosity[keyof typeof PersonaApiVerbosity];


export const PersonaApiVerbosity = {
  brief: 'brief',
  balanced: 'balanced',
  detailed: 'detailed',
} as const;

/**
 * List of genders for the persona (e.g., ['male'], ['female'])
 */
export type PersonaApiGender = { [key: string]: unknown };

/**
 * List of age groups for the persona (e.g., ['18-25'], ['25-32'])
 */
export type PersonaApiAgeGroup = { [key: string]: unknown };

/**
 * List of occupations/professions for the persona (e.g., ['Engineer'], ['Teacher'])
 */
export type PersonaApiOccupation = { [key: string]: unknown };

/**
 * List of locations for the persona (e.g., ['United States'], ['Canada'])
 */
export type PersonaApiLocation = { [key: string]: unknown };

/**
 * List of personality types for the persona (e.g., ['Friendly and cooperative'])
 */
export type PersonaApiPersonality = { [key: string]: unknown };

/**
 * List of communication styles for the persona (e.g., ['Direct and concise'])
 */
export type PersonaApiCommunicationStyle = { [key: string]: unknown };

/**
 * List of languages the persona speaks (e.g., ['English', 'Hindi'])
 */
export type PersonaApiLanguages = { [key: string]: unknown };

/**
 * List of accents for the persona (e.g., ['American'], ['Australian'])
 */
export type PersonaApiAccent = { [key: string]: unknown };

/**
 * List of conversation speeds (e.g., ['1.0'], ['1.25'])
 */
export type PersonaApiConversationSpeed = { [key: string]: unknown };

/**
 * List of sensitivities for detecting when persona finished speaking (e.g., ['5'], ['6'])
 */
export type PersonaApiFinishedSpeakingSensitivity = { [key: string]: unknown };

/**
 * List of sensitivities for allowing interruptions (e.g., ['5'], ['6'])
 */
export type PersonaApiInterruptSensitivity = { [key: string]: unknown };

/**
 * List of keywords/tags describing the persona (e.g., ['Knowledgeable', 'Patient', 'Helpful'])
 */
export type PersonaApiKeywords = { [key: string]: unknown };

/**
 * Additional metadata for the persona (speech clarity, base emotion, etc.)
 */
export type PersonaApiMetadata = { [key: string]: unknown };

export type PersonaApiCustomProperties = { [key: string]: unknown };

export interface PersonaApi {
  readonly id?: string;
  /** Type of persona (system or workspace-level) */
  readonly persona_type?: PersonaApiPersonaType;
  /** @minLength 1 */
  readonly persona_type_display?: string;
  /**
     * Name of the persona
     * @minLength 1
     * @maxLength 255
     */
  name: string;
  /** Description of the persona */
  description?: string;
  /** List of genders for the persona (e.g., ['male'], ['female']) */
  gender?: PersonaApiGender;
  /** List of age groups for the persona (e.g., ['18-25'], ['25-32']) */
  age_group?: PersonaApiAgeGroup;
  /** List of occupations/professions for the persona (e.g., ['Engineer'], ['Teacher']) */
  occupation?: PersonaApiOccupation;
  /** List of locations for the persona (e.g., ['United States'], ['Canada']) */
  location?: PersonaApiLocation;
  /** List of personality types for the persona (e.g., ['Friendly and cooperative']) */
  personality?: PersonaApiPersonality;
  /** List of communication styles for the persona (e.g., ['Direct and concise']) */
  communication_style?: PersonaApiCommunicationStyle;
  /** Whether the persona supports multiple languages */
  multilingual?: boolean;
  /** List of languages the persona speaks (e.g., ['English', 'Hindi']) */
  languages?: PersonaApiLanguages;
  /** List of accents for the persona (e.g., ['American'], ['Australian']) */
  accent?: PersonaApiAccent;
  /** List of conversation speeds (e.g., ['1.0'], ['1.25']) */
  conversation_speed?: PersonaApiConversationSpeed;
  /** Whether background sound is enabled (null=not specified, True/False for enabled/disabled) */
  background_sound?: boolean;
  /** List of sensitivities for detecting when persona finished speaking (e.g., ['5'], ['6']) */
  finished_speaking_sensitivity?: PersonaApiFinishedSpeakingSensitivity;
  /** List of sensitivities for allowing interruptions (e.g., ['5'], ['6']) */
  interrupt_sensitivity?: PersonaApiInterruptSensitivity;
  /** List of keywords/tags describing the persona (e.g., ['Knowledgeable', 'Patient', 'Helpful']) */
  keywords?: PersonaApiKeywords;
  /** Additional metadata for the persona (speech clarity, base emotion, etc.) */
  metadata?: PersonaApiMetadata;
  /** Additional instructions for how this persona should behave */
  additional_instruction?: string;
  /** Whether this is a default/recommended persona */
  readonly is_default?: boolean;
  readonly created_at?: string;
  readonly updated_at?: string;
  profession?: string[];
  language?: string[];
  custom_properties?: PersonaApiCustomProperties;
  /** Type of simulation for the persona */
  readonly simulation_type?: PersonaApiSimulationType;
  /** Punctuation style for the persona */
  punctuation?: PersonaApiPunctuation;
  /** Slang usage for the persona */
  slang_usage?: PersonaApiSlangUsage;
  /** Typos frequency for the persona */
  typos_frequency?: PersonaApiTyposFrequency;
  /** Regional mix for the persona */
  regional_mix?: PersonaApiRegionalMix;
  /** Emoji usage for the persona */
  emoji_usage?: PersonaApiEmojiUsage;
  /** Tone for the persona */
  tone?: PersonaApiTone;
  /** Verbosity for the persona */
  verbosity?: PersonaApiVerbosity;
}

export interface PersonaDuplicateResponseApi {
  status?: boolean;
  result?: PersonaApi;
}

export interface PersonaFieldOptionsApi {
  readonly gender_choices?: string;
  readonly age_group_choices?: string;
  readonly location_choices?: string;
  readonly profession_choices?: string;
  readonly personality_choices?: string;
  readonly communication_style_choices?: string;
  readonly accent_choices?: string;
  readonly language_choices?: string;
  readonly conversation_speed_choices?: string;
  readonly tone_choices?: string;
  readonly verbosity_choices?: string;
  readonly punctuation_choices?: string;
  readonly emoji_usage_choices?: string;
  readonly slang_usage_choices?: string;
  readonly typos_frequency_choices?: string;
  readonly regional_mix_choices?: string;
}

export type RunTestResponseApiAgentVersion = {[key: string]: string};

export type RunTestResponseApiAgentDefinitionDetail = {[key: string]: string};

/**
 * Source type for the test run: agent_definition or prompt
 */
export type RunTestResponseApiSourceType = typeof RunTestResponseApiSourceType[keyof typeof RunTestResponseApiSourceType];


export const RunTestResponseApiSourceType = {
  agent_definition: 'agent_definition',
  prompt: 'prompt',
} as const;

export type RunTestResponseApiPromptTemplateDetail = {[key: string]: string};

export type RunTestResponseApiPromptVersionDetail = {[key: string]: string};

export type RunTestResponseApiScenariosDetailItem = {[key: string]: string};

export type RunTestResponseApiSimulatorAgentDetail = {[key: string]: string};

export type SimulateEvalConfigResponseApiConfig = {[key: string]: string};

export type SimulateEvalConfigResponseApiMapping = {[key: string]: string};

export type SimulateEvalConfigResponseApiFilters = {[key: string]: string};

export interface SimulateEvalConfigResponseApi {
  readonly id?: string;
  /** @minLength 1 */
  readonly name?: string;
  readonly config?: SimulateEvalConfigResponseApiConfig;
  readonly mapping?: SimulateEvalConfigResponseApiMapping;
  readonly filters?: SimulateEvalConfigResponseApiFilters;
  readonly error_localizer?: boolean;
  /** @minLength 1 */
  readonly model?: string;
  /** @minLength 1 */
  readonly status?: string;
  /** @minLength 1 */
  readonly eval_group?: string;
  readonly template_id?: string;
}

export interface RunTestResponseApi {
  readonly id?: string;
  /**
     * Name of the test run
     * @minLength 1
     */
  readonly name?: string;
  /**
     * Description of the test run
     * @minLength 1
     */
  readonly description?: string;
  /** Agent definition for this test run */
  readonly agent_definition?: string;
  readonly agent_version?: RunTestResponseApiAgentVersion;
  readonly agent_definition_detail?: RunTestResponseApiAgentDefinitionDetail;
  /** Source type for the test run: agent_definition or prompt */
  readonly source_type?: RunTestResponseApiSourceType;
  /** @minLength 1 */
  readonly source_type_display?: string;
  /** Prompt template for this test run (only for prompt source type) */
  readonly prompt_template?: string;
  readonly prompt_template_detail?: RunTestResponseApiPromptTemplateDetail;
  /** Prompt version for this test run (only for prompt source type) */
  readonly prompt_version?: string;
  readonly prompt_version_detail?: RunTestResponseApiPromptVersionDetail;
  /** Scenarios to run in this test */
  readonly scenarios?: readonly string[];
  readonly scenarios_detail?: readonly RunTestResponseApiScenariosDetailItem[];
  /** IDs of dataset rows to run evaluations on */
  readonly dataset_row_ids?: readonly string[];
  /** Simulator agent for this test run (derived from scenarios) */
  readonly simulator_agent?: string;
  readonly simulator_agent_detail?: RunTestResponseApiSimulatorAgentDetail;
  readonly simulate_eval_configs?: readonly string[];
  readonly simulate_eval_configs_detail?: readonly SimulateEvalConfigResponseApi[];
  readonly evals_detail?: readonly SimulateEvalConfigResponseApi[];
  /** Organization this test run belongs to */
  readonly organization?: string;
  /** Enable automatic tool evaluation for this test run */
  readonly enable_tool_evaluation?: boolean;
  readonly created_at?: string;
  readonly updated_at?: string;
  readonly last_run_at?: string;
  readonly deleted?: boolean;
  readonly deleted_at?: string;
}

export type RunTestErrorResponseApiDetails = {[key: string]: string};

export interface RunTestErrorResponseApi {
  /** @minLength 1 */
  readonly error?: string;
  readonly details?: RunTestErrorResponseApiDetails;
}

/**
 * Current status of the test execution
 */
export type TestExecutionApiStatus = typeof TestExecutionApiStatus[keyof typeof TestExecutionApiStatus];


export const TestExecutionApiStatus = {
  pending: 'pending',
  running: 'running',
  completed: 'completed',
  failed: 'failed',
  cancelled: 'cancelled',
  cancelling: 'cancelling',
  evaluating: 'evaluating',
} as const;

/**
 * Additional metadata about the execution
 */
export type TestExecutionApiExecutionMetadata = { [key: string]: unknown };

/**
 * List of scenario IDs that were executed in this run
 */
export type TestExecutionApiScenarioIds = { [key: string]: unknown };

export interface TestExecutionApi {
  readonly id?: string;
  /** The run test being executed */
  run_test: string;
  /** @minLength 1 */
  readonly run_test_name?: string;
  /** @minLength 1 */
  readonly agent_definition_name?: string;
  /** Current status of the test execution */
  status?: TestExecutionApiStatus;
  error_reason?: string;
  /** When the test execution started */
  started_at?: string;
  /** When the test execution completed */
  completed_at?: string;
  /**
     * Total number of scenarios in this execution
     * @minimum -2147483648
     * @maximum 2147483647
     */
  total_scenarios?: number;
  /**
     * Total number of calls to be made
     * @minimum -2147483648
     * @maximum 2147483647
     */
  total_calls?: number;
  /**
     * Number of successfully completed calls
     * @minimum -2147483648
     * @maximum 2147483647
     */
  completed_calls?: number;
  /**
     * Number of failed calls
     * @minimum -2147483648
     * @maximum 2147483647
     */
  failed_calls?: number;
  /** Additional metadata about the execution */
  execution_metadata?: TestExecutionApiExecutionMetadata;
  readonly duration_seconds?: string;
  readonly success_rate?: string;
  readonly calls?: readonly CallExecutionApi[];
  readonly created_at?: string;
  /** List of scenario IDs that were executed in this run */
  scenario_ids?: TestExecutionApiScenarioIds;
  /** @minLength 1 */
  readonly simulator_agent_name?: string;
  readonly simulator_agent_id?: string;
  /** @minLength 1 */
  readonly agent_definition_used_name?: string;
  readonly agent_definition_used_id?: string;
  readonly calls_attempted?: string;
  readonly calls_connected_percentage?: string;
}

/**
 * Current status of the call
 */
export type CallExecutionDetailApiStatus = typeof CallExecutionDetailApiStatus[keyof typeof CallExecutionDetailApiStatus];


export const CallExecutionDetailApiStatus = {
  pending: 'pending',
  queued: 'queued',
  ongoing: 'ongoing',
  completed: 'completed',
  failed: 'failed',
  analyzing: 'analyzing',
  cancelled: 'cancelled',
} as const;

/**
 * Tool evaluation output - separate from standard evaluations
 */
export type CallExecutionDetailApiToolOutputs = { [key: string]: unknown };

/**
 * Detailed cost breakdown from customer call data
 */
export type CallExecutionDetailApiCustomerCostBreakdown = { [key: string]: unknown };

/**
 * Latency metrics from customer call data
 */
export type CallExecutionDetailApiCustomerLatencyMetrics = { [key: string]: unknown };

/**
 * Type of simulation call
 */
export type CallExecutionDetailApiSimulationCallType = typeof CallExecutionDetailApiSimulationCallType[keyof typeof CallExecutionDetailApiSimulationCallType];


export const CallExecutionDetailApiSimulationCallType = {
  voice: 'voice',
  text: 'text',
} as const;

export interface CallExecutionDetailApi {
  readonly id?: string;
  /** @minLength 1 */
  readonly service_provider_call_id?: string;
  readonly session_id?: string;
  readonly timestamp?: string;
  readonly call_type?: string;
  /** Current status of the call */
  status?: CallExecutionDetailApiStatus;
  readonly duration?: string;
  /**
     * Duration of the call in seconds
     * @minimum -2147483648
     * @maximum 2147483647
     */
  duration_seconds?: number;
  readonly start_time?: string;
  readonly transcript?: string;
  /** @minLength 1 */
  readonly scenario?: string;
  readonly overall_score?: string;
  readonly response_time?: string;
  /**
     * Average response time in milliseconds
     * @minimum -2147483648
     * @maximum 2147483647
     */
  response_time_ms?: number;
  /** @minLength 1 */
  readonly audio_url?: string;
  /** @minLength 1 */
  readonly customer_name?: string;
  readonly eval_outputs?: string;
  readonly eval_metrics?: string;
  readonly scenario_columns?: string;
  /**
     * Reason why the call ended
     * @maxLength 10000
     */
  ended_reason?: string;
  /** @minLength 1 */
  readonly simulator_agent_name?: string;
  readonly simulator_agent_id?: string;
  /** @minLength 1 */
  readonly agent_definition_used_name?: string;
  readonly agent_definition_used_id?: string;
  /** Call summary from the service */
  call_summary?: string;
  readonly recordings?: string;
  readonly scenario_id?: string;
  readonly avg_agent_latency?: number;
  /**
     * Average agent latency in milliseconds (time taken by agent to respond after user's pause)
     * @minimum -2147483648
     * @maximum 2147483647
     */
  avg_agent_latency_ms?: number;
  /**
     * Number of times user interrupted the AI
     * @minimum -2147483648
     * @maximum 2147483647
     */
  user_interruption_count?: number;
  /** Rate of user interruptions (interruptions per minute) */
  user_interruption_rate?: number;
  /** User's words per minute */
  user_wpm?: number;
  /** Bot's words per minute */
  bot_wpm?: number;
  /** Ratio of bot speaking time to user speaking time */
  talk_ratio?: number;
  /**
     * Number of times AI interrupted the user
     * @minimum -2147483648
     * @maximum 2147483647
     */
  ai_interruption_count?: number;
  /** Rate of AI interruptions (interruptions per minute) */
  ai_interruption_rate?: number;
  readonly avg_stop_time_after_interruption?: number;
  readonly total_tokens?: string;
  readonly input_tokens?: string;
  readonly output_tokens?: string;
  readonly avg_latency_ms?: string;
  readonly turn_count?: string;
  readonly agent_talk_percentage?: string;
  readonly csat_score?: string;
  readonly processing_skipped?: string;
  readonly processing_skip_reason?: string;
  readonly rerun_snapshots?: string;
  readonly is_snapshot?: string;
  readonly snapshot_timestamp?: string;
  readonly rerun_type?: string;
  readonly original_call_execution_id?: string;
  /** Tool evaluation output - separate from standard evaluations */
  tool_outputs?: CallExecutionDetailApiToolOutputs;
  /**
     * Cost of the call in cents
     * @minimum -2147483648
     * @maximum 2147483647
     */
  cost_cents?: number;
  /**
     * Total customer-reported cost in cents
     * @minimum -2147483648
     * @maximum 2147483647
     */
  customer_cost_cents?: number;
  /** Detailed cost breakdown from customer call data */
  customer_cost_breakdown?: CallExecutionDetailApiCustomerCostBreakdown;
  /** Latency metrics from customer call data */
  customer_latency_metrics?: CallExecutionDetailApiCustomerLatencyMetrics;
  /**
     * Customer call ID if available
     * @maxLength 255
     */
  customer_call_id?: string;
  /** Type of simulation call */
  simulation_call_type?: CallExecutionDetailApiSimulationCallType;
  readonly provider?: string;
  /**
     * Phone number called (null for TEXT/chat simulations)
     * @maxLength 20
     */
  phone_number?: string;
}

export type CallExecutionStatusUpdateApiStatus = typeof CallExecutionStatusUpdateApiStatus[keyof typeof CallExecutionStatusUpdateApiStatus];


export const CallExecutionStatusUpdateApiStatus = {
  pending: 'pending',
  queued: 'queued',
  ongoing: 'ongoing',
  completed: 'completed',
  failed: 'failed',
  analyzing: 'analyzing',
  cancelled: 'cancelled',
} as const;

export interface CallExecutionStatusUpdateApi {
  status: CallExecutionStatusUpdateApiStatus;
  ended_reason?: string;
}

export type CallBranchAnalysisResponseApiAnalysis = {[key: string]: string};

export interface CallBranchAnalysisResponseApi {
  readonly call_execution_id?: string;
  readonly scenario_id?: string;
  /** @minLength 1 */
  readonly scenario_name?: string;
  readonly analysis?: CallBranchAnalysisResponseApiAnalysis;
  readonly analyzed_at?: string;
}

export interface ErrorResponseApi {
  /** @minLength 1 */
  error: string;
}

export type CallBranchDeviationCreateResponseApiDeviationData = {[key: string]: string};

export interface CallBranchDeviationCreateResponseApi {
  readonly call_execution_id?: string;
  readonly scenario_graph_id?: string;
  readonly deviation_data?: CallBranchDeviationCreateResponseApiDeviationData;
  /** @minLength 1 */
  readonly message?: string;
}

export type SendChatRequestApiMetrics = {[key: string]: string};

export type ChatMessageContractApiRole = typeof ChatMessageContractApiRole[keyof typeof ChatMessageContractApiRole];


export const ChatMessageContractApiRole = {
  user: 'user',
  assistant: 'assistant',
  tool: 'tool',
} as const;

export interface ChatToolCallFunctionApi {
  /** @minLength 1 */
  name: string;
  /** @minLength 1 */
  arguments: string;
}

export interface ChatToolCallApi {
  /** @minLength 1 */
  id: string;
  /** @minLength 1 */
  type: string;
  function: ChatToolCallFunctionApi;
}

export type ChatMessageContractApiMetadata = {[key: string]: string};

export interface ChatMessageContractApi {
  role: ChatMessageContractApiRole;
  content?: string;
  tool_call_id?: string;
  name?: string;
  metadata?: ChatMessageContractApiMetadata;
  tool_calls?: ChatToolCallApi[];
}

export interface SendChatRequestApi {
  messages?: ChatMessageContractApi[];
  metrics?: SendChatRequestApiMetrics;
  initiate_chat?: boolean;
}

export interface ChatSendMessageResultApi {
  input_message?: ChatMessageContractApi[];
  output_message?: ChatMessageContractApi[];
  message_history: ChatMessageContractApi[];
  chat_ended?: boolean;
}

export interface ChatSendMessageResponseApi {
  status?: boolean;
  result: ChatSendMessageResultApi;
}

export interface CallExecutionDeleteResponseApi {
  /** @minLength 1 */
  readonly message?: string;
}

export type ErrorLocalizerTaskResponseApiEvalResult = { [key: string]: unknown };

export type ErrorLocalizerTaskResponseApiInputData = { [key: string]: unknown };

export type ErrorLocalizerTaskResponseApiInputKeys = { [key: string]: unknown };

export type ErrorLocalizerTaskResponseApiInputTypes = { [key: string]: unknown };

export type ErrorLocalizerTaskResponseApiErrorAnalysis = { [key: string]: unknown };

export interface ErrorLocalizerTaskResponseApi {
  readonly task_id?: string;
  /** @minLength 1 */
  readonly eval_config_id?: string;
  readonly status?: string;
  readonly eval_result?: ErrorLocalizerTaskResponseApiEvalResult;
  /** @minLength 1 */
  readonly eval_explanation?: string;
  readonly input_data?: ErrorLocalizerTaskResponseApiInputData;
  readonly input_keys?: ErrorLocalizerTaskResponseApiInputKeys;
  readonly input_types?: ErrorLocalizerTaskResponseApiInputTypes;
  /** @minLength 1 */
  readonly rule_prompt?: string;
  readonly error_analysis?: ErrorLocalizerTaskResponseApiErrorAnalysis;
  /** @minLength 1 */
  readonly selected_input_key?: string;
  /** @minLength 1 */
  readonly error_message?: string;
  readonly created_at?: string;
  readonly updated_at?: string;
  /** @minLength 1 */
  readonly eval_template_name?: string;
  readonly eval_template_id?: string;
}

export interface CallExecutionErrorLocalizerTasksResponseApi {
  readonly call_execution_id?: string;
  readonly error_localizer_tasks?: readonly ErrorLocalizerTaskResponseApi[];
  readonly total_tasks?: number;
}

export type CallLogEntryResponseApiAttributes = {[key: string]: string};

export type CallLogEntryResponseApiPayload = {[key: string]: string};

export interface CallLogEntryResponseApi {
  /** @minLength 1 */
  readonly id?: string;
  /** @minLength 1 */
  readonly logged_at?: string;
  /** @minLength 1 */
  readonly level?: string;
  /** @minLength 1 */
  readonly severity_text?: string;
  /** @minLength 1 */
  readonly category?: string;
  /** @minLength 1 */
  readonly body?: string;
  readonly attributes?: CallLogEntryResponseApiAttributes;
  readonly payload?: CallLogEntryResponseApiPayload;
}

export interface CallExecutionLogsResponseApi {
  readonly results?: readonly CallLogEntryResponseApi[];
  /** @minLength 1 */
  readonly source?: string;
  readonly ingestion_pending?: boolean;
}

export type SessionComparisonResultApiComparisonMetrics = { [key: string]: unknown };

export type SessionComparisonResultApiComparisonTranscripts = { [key: string]: unknown };

export type SessionComparisonResultApiComparisonRecordings = { [key: string]: unknown };

export interface SessionComparisonResultApi {
  readonly comparison_metrics?: SessionComparisonResultApiComparisonMetrics;
  readonly comparison_transcripts?: SessionComparisonResultApiComparisonTranscripts;
  readonly comparison_recordings?: SessionComparisonResultApiComparisonRecordings;
}

export interface SessionComparisonResponseApi {
  status?: boolean;
  result: SessionComparisonResultApi;
}

/**
 * Role of the speaker (user or assistant)
 */
export type CallTranscriptApiSpeakerRole = typeof CallTranscriptApiSpeakerRole[keyof typeof CallTranscriptApiSpeakerRole];


export const CallTranscriptApiSpeakerRole = {
  user: 'user',
  assistant: 'assistant',
  system: 'system',
  tool_calls: 'tool_calls',
  tool_call_result: 'tool_call_result',
  unknown: 'unknown',
} as const;

export interface CallTranscriptApi {
  readonly id?: string;
  /** Role of the speaker (user or assistant) */
  speaker_role?: CallTranscriptApiSpeakerRole;
  /**
     * Transcript content
     * @minLength 1
     */
  content: string;
  /**
     * Start time of this transcript segment in milliseconds
     * @minimum -9223372036854776000
     * @maximum 9223372036854776000
     */
  start_time_ms?: number;
  readonly start_time_seconds?: string;
  /**
     * End time of this transcript segment in milliseconds
     * @minimum -9223372036854776000
     * @maximum 9223372036854776000
     */
  end_time_ms?: number;
  readonly end_time_seconds?: string;
  /** Confidence score for this transcript segment */
  confidence_score?: number;
  readonly created_at?: string;
}

export interface CallTranscriptResponseApi {
  readonly call_execution_id?: string;
  /** @minLength 1 */
  readonly phone_number?: string;
  /** @minLength 1 */
  readonly status?: string;
  readonly transcripts?: readonly CallTranscriptApi[];
  readonly total_transcripts?: number;
}

export interface PromptSimulationScenarioItemApi {
  readonly id?: string;
  /** @minLength 1 */
  readonly name?: string;
  readonly description?: string;
  /** @minLength 1 */
  readonly scenario_type?: string;
  readonly dataset_id?: string;
  readonly created_at?: string;
}

export interface PromptSimulationScenariosResultApi {
  readonly count?: number;
  readonly page?: number;
  readonly limit?: number;
  readonly results?: readonly PromptSimulationScenarioItemApi[];
}

export interface PromptSimulationScenariosResponseApi {
  status?: boolean;
  result: PromptSimulationScenariosResultApi;
}

export interface PromptSimulationTemplateSummaryApi {
  readonly id?: string;
  /** @minLength 1 */
  readonly name?: string;
}

export interface PromptSimulationListResultApi {
  readonly count?: number;
  readonly page?: number;
  readonly limit?: number;
  readonly results?: readonly RunTestResponseApi[];
  prompt_template?: PromptSimulationTemplateSummaryApi;
}

export interface PromptSimulationListResponseApi {
  status?: boolean;
  result: PromptSimulationListResultApi;
}

export type CreatePromptSimulationApiEvaluationsConfigItem = {[key: string]: string};

export interface CreatePromptSimulationApi {
  /**
     * @minLength 1
     * @maxLength 255
     */
  name: string;
  description?: string;
  /** Prompt template to use as the agent source */
  prompt_template_id: string;
  /**
     * Prompt version ID (UUID) or template_version string
     * @minLength 1
     * @maxLength 255
     */
  prompt_version_id: string;
  scenario_ids: string[];
  dataset_row_ids?: string[];
  /** Evaluation configurations to create */
  evaluations_config?: CreatePromptSimulationApiEvaluationsConfigItem[];
  /** Enable automatic tool evaluation for this simulation run */
  enable_tool_evaluation?: boolean;
}

export interface PromptSimulationRunResponseApi {
  status?: boolean;
  result: RunTestResponseApi;
}

export interface PromptSimulationUpdateRequestApi {
  /**
     * @minLength 1
     * @maxLength 255
     */
  prompt_version_id?: string;
  scenario_ids?: string[];
  /**
     * @minLength 1
     * @maxLength 255
     */
  name?: string;
  description?: string;
  enable_tool_evaluation?: boolean;
}

export interface ExecutePromptSimulationRequestApi {
  scenario_ids?: string[];
  select_all?: boolean;
}

export interface ExecutePromptSimulationResultApi {
  /** @minLength 1 */
  readonly message?: string;
  readonly execution_id?: string;
  readonly run_test_id?: string;
  /** @minLength 1 */
  readonly status?: string;
  readonly total_scenarios?: number;
  readonly total_calls?: number;
  scenario_ids: string[];
}

export interface ExecutePromptSimulationResponseApi {
  status?: boolean;
  result: ExecutePromptSimulationResultApi;
}

export type AllActiveTestsApiActiveTests = {[key: string]: string};

export interface AllActiveTestsApi {
  active_tests: AllActiveTestsApiActiveTests;
  total_active: number;
}

export type CreateRunTestApiEvaluationsConfigItem = {[key: string]: string};

export interface CreateRunTestApi {
  /**
     * @minLength 1
     * @maxLength 255
     */
  name: string;
  description?: string;
  agent_definition_id: string;
  scenario_ids: string[];
  dataset_row_ids?: string[];
  eval_config_ids?: string[];
  /** Evaluation configurations to create */
  evaluations_config?: CreateRunTestApiEvaluationsConfigItem[];
  /** Enable automatic tool evaluation for this test run */
  enable_tool_evaluation?: boolean;
  /** Optional replay session ID to mark as completed after run test creation */
  replay_session_id?: string;
}

export interface RunTestNameResultApi {
  run_test_id: string;
  /** @minLength 1 */
  run_test_name: string;
}

export interface RunTestNameResponseApi {
  status?: boolean;
  result: RunTestNameResultApi;
}

export interface UpdateRunTestApi {
  /**
     * @minLength 1
     * @maxLength 255
     */
  name?: string;
  description?: string;
  agent_definition_id?: string;
  scenario_ids?: string[];
  dataset_row_ids?: string[];
  eval_config_ids?: string[];
}

export interface RunTestMessageResponseApi {
  /** @minLength 1 */
  readonly message?: string;
}

/**
 * Run test metadata
 */
export type RunTestAnalyticsApiRunTestInfo = {[key: string]: string};

export type RunTestAnalyticsApiFailRateTrendsItem = {[key: string]: string};

export type RunTestAnalyticsApiEvaluationScoreTrendsItem = {[key: string]: string};

export type RunTestAnalyticsApiPerformanceComparisonItem = {[key: string]: string};

/**
 * Aggregate performance summary
 */
export type RunTestAnalyticsApiSummaryStats = {[key: string]: string};

export interface RunTestAnalyticsApi {
  /** Run test metadata */
  run_test_info: RunTestAnalyticsApiRunTestInfo;
  /** Fail-rate trend points */
  fail_rate_trends: RunTestAnalyticsApiFailRateTrendsItem[];
  /** Evaluation score trend points */
  evaluation_score_trends: RunTestAnalyticsApiEvaluationScoreTrendsItem[];
  /** Per-execution performance rows */
  performance_comparison: RunTestAnalyticsApiPerformanceComparisonItem[];
  /** Aggregate performance summary */
  summary_stats?: RunTestAnalyticsApiSummaryStats;
}

export type RunTestCallExecutionsResponseApiResultsItem = {[key: string]: string};

export interface RunTestCallExecutionsResponseApi {
  readonly count?: number;
  /** @minLength 1 */
  readonly next?: string;
  /** @minLength 1 */
  readonly previous?: string;
  readonly results?: readonly RunTestCallExecutionsResponseApiResultsItem[];
  readonly total_pages?: number;
  readonly current_page?: number;
}

export interface RunTestChatExecutionResultApi {
  /** @minLength 1 */
  message: string;
  execution_id: string;
  run_test_id: string;
  /** @minLength 1 */
  status: string;
  total_scenarios: string[];
}

export interface RunTestChatExecutionResponseApi {
  status?: boolean;
  result: RunTestChatExecutionResultApi;
}

export interface RunTestComponentsUpdateApi {
  agent_definition_id?: string;
  version?: string;
  simulator_agent_id?: string;
  scenarios?: string[];
  enable_tool_evaluation?: boolean;
}

export interface TestExecutionBulkDeleteApi {
  /** List of specific test execution IDs to delete */
  test_execution_ids?: string[];
  /** Whether to delete all test executions in the run test */
  select_all?: boolean;
}

export interface TestExecutionBulkDeleteResponseApi {
  /** @minLength 1 */
  readonly message?: string;
  readonly run_test_id?: string;
  readonly deleted_count?: number;
  readonly deleted_ids?: readonly string[];
}

/**
 * Template-specific configuration parameters.
 */
export type EvalConfigDefinitionApiConfig = {[key: string]: string};

/**
 * Maps test execution data fields to the evaluation template's expected inputs.
 */
export type EvalConfigDefinitionApiMapping = {[key: string]: string};

/**
 * Filter criteria to restrict which test results are evaluated.
 */
export type EvalConfigDefinitionApiFilters = {[key: string]: string};

export interface EvalConfigDefinitionApi {
  /** UUID of the evaluation template to use. */
  template_id: string;
  /** Name for this evaluation configuration. Defaults to 'Eval-<template_id>' if omitted. */
  name?: string;
  /** Template-specific configuration parameters. */
  config?: EvalConfigDefinitionApiConfig;
  /** Maps test execution data fields to the evaluation template's expected inputs. */
  mapping?: EvalConfigDefinitionApiMapping;
  /** Filter criteria to restrict which test results are evaluated. */
  filters?: EvalConfigDefinitionApiFilters;
  /** Enables granular error localization on evaluation failures. */
  error_localizer?: boolean;
  /**
     * Model to use for running this evaluation.
     * @minLength 1
     */
  model?: string;
}

export interface AddEvalConfigsRequestApi {
  /**
     * Array of evaluation configuration objects to add. At least one required.
     * @minItems 1
     */
  evaluations_config: EvalConfigDefinitionApi[];
}

export type EvalConfigResponseApiModel = typeof EvalConfigResponseApiModel[keyof typeof EvalConfigResponseApiModel];


export const EvalConfigResponseApiModel = {
  turing_large: 'turing_large',
  turing_small: 'turing_small',
  protect: 'protect',
  protect_flash: 'protect_flash',
  turing_flash: 'turing_flash',
} as const;

export type EvalConfigResponseApiStatus = typeof EvalConfigResponseApiStatus[keyof typeof EvalConfigResponseApiStatus];


export const EvalConfigResponseApiStatus = {
  NotStarted: 'NotStarted',
  Queued: 'Queued',
  Running: 'Running',
  Completed: 'Completed',
  Editing: 'Editing',
  Inactive: 'Inactive',
  Failed: 'Failed',
  PartialRun: 'PartialRun',
  ExperimentEvaluation: 'ExperimentEvaluation',
  Uploading: 'Uploading',
  PartialExtracted: 'PartialExtracted',
  Processing: 'Processing',
  Deleting: 'Deleting',
  PartialCompleted: 'PartialCompleted',
  OptimizationEvaluation: 'OptimizationEvaluation',
  Error: 'Error',
  Cancelled: 'Cancelled',
} as const;

export type EvalConfigResponseApiConfig = { [key: string]: unknown };

export type EvalConfigResponseApiMapping = { [key: string]: unknown };

export type EvalConfigResponseApiFilters = { [key: string]: unknown };

export interface EvalConfigResponseApi {
  readonly id?: string;
  /** @maxLength 255 */
  name?: string;
  config?: EvalConfigResponseApiConfig;
  mapping?: EvalConfigResponseApiMapping;
  filters?: EvalConfigResponseApiFilters;
  error_localizer?: boolean;
  model?: EvalConfigResponseApiModel;
  status?: EvalConfigResponseApiStatus;
  readonly eval_group?: string;
  readonly template_id?: string;
}

export interface AddEvalConfigsResponseApi {
  /** @minLength 1 */
  message: string;
  created_eval_configs: EvalConfigResponseApi[];
  run_test_id: string;
  /** Non-fatal issues encountered while processing individual configs. */
  warnings?: string[];
}

export interface DeleteEvalConfigResponseApi {
  /** @minLength 1 */
  message: string;
}

export type EvalConfigStructureApiEvalTags = { [key: string]: unknown };

export type EvalConfigStructureApiMapping = {[key: string]: string};

export type EvalConfigStructureApiConfig = {[key: string]: string};

export type EvalConfigStructureApiParams = { [key: string]: unknown };

export type EvalConfigStructureApiFunctionParamsSchema = { [key: string]: unknown };

export type EvalConfigStructureApiModels = { [key: string]: unknown };

export type EvalConfigStructureApiOutput = { [key: string]: unknown };

export type EvalConfigStructureApiConfigParamsDesc = {[key: string]: string};

export type EvalConfigStructureApiConfigParamsOption = {[key: string]: string};

export interface EvalConfigStructureApi {
  readonly id?: string;
  readonly template_id?: string;
  /** @minLength 1 */
  readonly name?: string;
  readonly reason_column?: boolean;
  readonly eval_tags?: EvalConfigStructureApiEvalTags;
  readonly description?: string;
  required_keys: string[];
  optional_keys: string[];
  variable_keys: string[];
  readonly run_prompt_column?: boolean;
  /** @minLength 1 */
  readonly template_name?: string;
  readonly mapping?: EvalConfigStructureApiMapping;
  readonly config?: EvalConfigStructureApiConfig;
  readonly params?: EvalConfigStructureApiParams;
  readonly function_params_schema?: EvalConfigStructureApiFunctionParamsSchema;
  readonly models?: EvalConfigStructureApiModels;
  /** @minLength 1 */
  readonly selected_model?: string;
  readonly error_localizer?: boolean;
  readonly kb_id?: string;
  readonly output?: EvalConfigStructureApiOutput;
  readonly config_params_desc?: EvalConfigStructureApiConfigParamsDesc;
  readonly config_params_option?: EvalConfigStructureApiConfigParamsOption;
  readonly api_key_available?: boolean;
}

export interface EvalConfigStructureResultApi {
  eval: EvalConfigStructureApi;
}

export interface EvalConfigStructureResponseApi {
  status?: boolean;
  result: EvalConfigStructureResultApi;
}

/**
 * Updated evaluation configuration parameters.
 */
export type EvalConfigUpdateRequestApiConfig = {[key: string]: string};

/**
 * Updated field mapping between test data and evaluation inputs.
 */
export type EvalConfigUpdateRequestApiMapping = {[key: string]: string};

export interface EvalConfigUpdateRequestApi {
  /** Updated evaluation configuration parameters. */
  config?: EvalConfigUpdateRequestApiConfig;
  /** Updated field mapping between test data and evaluation inputs. */
  mapping?: EvalConfigUpdateRequestApiMapping;
  /**
     * Model to use for evaluations.
     * @minLength 1
     */
  model?: string;
  /** Enable granular error localization in evaluation results. */
  error_localizer?: boolean;
  /** UUID of a knowledge base to use for grounding. Pass null to clear. */
  kb_id?: string;
  /**
     * Updated name for the evaluation configuration.
     * @minLength 1
     */
  name?: string;
  /** When true, triggers an immediate rerun after updating. Defaults to false. */
  run?: boolean;
  /** UUID of the test execution to rerun against. Required when run is true. */
  test_execution_id?: string;
}

export interface EvalConfigUpdateResponseApi {
  /** @minLength 1 */
  message: string;
  eval_config_id: string;
  run_test_id: string;
  test_execution_id?: string;
  call_execution_count?: number;
  /** @minLength 1 */
  note?: string;
}

export interface EvalSummaryComparisonResponseApi { [key: string]: unknown }

export interface TestExecutionItemResponseApi {
  /** @minLength 1 */
  readonly id?: string;
  /** @minLength 1 */
  readonly status?: string;
  /** @minLength 1 */
  readonly scenarios?: string;
  /** @minLength 1 */
  readonly start_time?: string;
  readonly duration?: number;
  /** @minLength 1 */
  readonly error_reason?: string;
  readonly success_rate?: number;
  readonly avg_response_time?: number;
  readonly calls?: number;
  readonly calls_attempted?: number;
  readonly connected_calls?: number;
  /** @minLength 1 */
  readonly agent_version?: string;
  /** @minLength 1 */
  readonly agent_definition?: string;
  readonly calls_connected_percentage?: number;
  readonly total_chats?: number;
  /** @minLength 1 */
  readonly agent_type?: string;
  readonly total_number_of_fagi_agent_turns?: number;
  /** @minLength 1 */
  readonly source_type?: string;
}

/**
 * Type of rerun: evaluation only or call plus evaluation
 */
export type TestExecutionRerunApiRerunType = typeof TestExecutionRerunApiRerunType[keyof typeof TestExecutionRerunApiRerunType];


export const TestExecutionRerunApiRerunType = {
  eval_only: 'eval_only',
  call_and_eval: 'call_and_eval',
} as const;

export interface TestExecutionRerunApi {
  /** Type of rerun: evaluation only or call plus evaluation */
  rerun_type: TestExecutionRerunApiRerunType;
  /** List of specific test execution IDs to rerun */
  test_execution_ids?: string[];
  /** Whether to rerun all test executions in the run test */
  select_all?: boolean;
}

export type TestExecutionRerunResultApiFailedRerunsItem = {[key: string]: string};

export interface TestExecutionRerunResultApi {
  readonly test_execution_id?: string;
  readonly success_count?: number;
  readonly failure_count?: number;
  readonly successful_reruns?: readonly string[];
  readonly failed_reruns?: readonly TestExecutionRerunResultApiFailedRerunsItem[];
  readonly skipped?: boolean;
  /** @minLength 1 */
  readonly reason?: string;
}

export interface TestExecutionRerunResponseApi {
  /** @minLength 1 */
  readonly message?: string;
  readonly run_test_id?: string;
  /** @minLength 1 */
  readonly rerun_type?: string;
  readonly total_test_executions?: number;
  readonly results?: readonly TestExecutionRerunResultApi[];
  readonly overall_success_count?: number;
  readonly overall_failure_count?: number;
}

export interface RunNewEvalsOnTestExecutionApi {
  /** List of specific test execution IDs to run evaluations on */
  test_execution_ids?: string[];
  /** Whether to run evaluations on all test executions in the run test */
  select_all?: boolean;
  /** List of SimulateEvalConfig IDs to run on the test executions */
  eval_config_ids: string[];
  /** Whether to enable tool evaluation for this run (if not provided, uses the run test's current setting) */
  enable_tool_evaluation?: boolean;
}

export interface RunNewEvalsResponseApi {
  /** @minLength 1 */
  message: string;
  run_test_id: string;
  call_execution_count: number;
}

export interface RunTestScenarioItemResponseApi {
  /** @minLength 1 */
  readonly id?: string;
  /** @minLength 1 */
  readonly name?: string;
  readonly row_count?: number;
}

export interface ChatSDKCodeResultApi {
  /** @minLength 1 */
  installation_guide: string;
  /** @minLength 1 */
  sdk_code: string;
  run_test_id: string;
  /** @minLength 1 */
  run_test_name: string;
}

export interface ChatSDKCodeResponseApi {
  status?: boolean;
  result: ChatSDKCodeResultApi;
}

export type TestExecutionStatusApiScenariosItem = {[key: string]: string};

export interface TestExecutionStatusApi {
  /** @minLength 1 */
  run_test_id: string;
  /** @minLength 1 */
  execution_id: string;
  /** @minLength 1 */
  status: string;
  total_scenarios: number;
  total_calls: number;
  completed_calls: number;
  failed_calls: number;
  success_rate: number;
  start_time: string;
  end_time: string;
  scenarios: TestExecutionStatusApiScenariosItem[];
  /** @minLength 1 */
  error: string;
}

/**
 * Type of scenario (graph, script, or dataset)
 */
export type ScenarioResponseApiScenarioType = typeof ScenarioResponseApiScenarioType[keyof typeof ScenarioResponseApiScenarioType];


export const ScenarioResponseApiScenarioType = {
  graph: 'graph',
  script: 'script',
  dataset: 'dataset',
} as const;

/**
 * Source type for the scenario: agent_definition or prompt
 */
export type ScenarioResponseApiSourceType = typeof ScenarioResponseApiSourceType[keyof typeof ScenarioResponseApiSourceType];


export const ScenarioResponseApiSourceType = {
  agent_definition: 'agent_definition',
  prompt: 'prompt',
} as const;

/**
 * Status of the scenario
 */
export type ScenarioResponseApiStatus = typeof ScenarioResponseApiStatus[keyof typeof ScenarioResponseApiStatus];


export const ScenarioResponseApiStatus = {
  NotStarted: 'NotStarted',
  Queued: 'Queued',
  Running: 'Running',
  Completed: 'Completed',
  Editing: 'Editing',
  Inactive: 'Inactive',
  Failed: 'Failed',
  PartialRun: 'PartialRun',
  ExperimentEvaluation: 'ExperimentEvaluation',
  Uploading: 'Uploading',
  PartialExtracted: 'PartialExtracted',
  Processing: 'Processing',
  Deleting: 'Deleting',
  PartialCompleted: 'PartialCompleted',
  OptimizationEvaluation: 'OptimizationEvaluation',
  Error: 'Error',
  Cancelled: 'Cancelled',
} as const;

export interface ScenarioResponseApi {
  readonly id?: string;
  /**
     * Name of the scenario
     * @minLength 1
     * @maxLength 255
     */
  name: string;
  /** Optional description of the scenario */
  description?: string;
  /**
     * Source content or reference for the scenario
     * @minLength 1
     */
  source: string;
  /** Type of scenario (graph, script, or dataset) */
  scenario_type?: ScenarioResponseApiScenarioType;
  /** @minLength 1 */
  readonly scenario_type_display?: string;
  /** Source type for the scenario: agent_definition or prompt */
  source_type?: ScenarioResponseApiSourceType;
  /** @minLength 1 */
  readonly source_type_display?: string;
  /** Organization this scenario belongs to */
  readonly organization?: string;
  /** Dataset associated with this scenario (only for dataset type scenarios) */
  dataset?: string;
  readonly dataset_rows?: string;
  readonly dataset_column_config?: string;
  readonly graph?: string;
  readonly agent?: string;
  /** Prompt template associated with this scenario (only for prompt source type) */
  prompt_template?: string;
  readonly prompt_template_detail?: string;
  /** Prompt version associated with this scenario (only for prompt source type) */
  prompt_version?: string;
  readonly prompt_version_detail?: string;
  readonly created_at?: string;
  readonly updated_at?: string;
  readonly deleted?: boolean;
  /** Status of the scenario */
  status?: ScenarioResponseApiStatus;
  readonly deleted_at?: string;
  readonly agent_type?: string;
}

export interface ScenarioListResponseApi {
  readonly count?: number;
  /** @minLength 1 */
  readonly next?: string;
  /** @minLength 1 */
  readonly previous?: string;
  readonly results?: readonly ScenarioResponseApi[];
}

export type ScenarioErrorResponseApiDetails = {[key: string]: string};

export interface ScenarioErrorResponseApi {
  /** @minLength 1 */
  readonly error?: string;
  readonly details?: ScenarioErrorResponseApiDetails;
}

export type ScenarioCreateRequestApiKind = typeof ScenarioCreateRequestApiKind[keyof typeof ScenarioCreateRequestApiKind];


export const ScenarioCreateRequestApiKind = {
  graph: 'graph',
  script: 'script',
  dataset: 'dataset',
} as const;

export type ScenarioCreateRequestApiGraph = { [key: string]: unknown };

export type ScenarioCreateRequestApiSourceType = typeof ScenarioCreateRequestApiSourceType[keyof typeof ScenarioCreateRequestApiSourceType];


export const ScenarioCreateRequestApiSourceType = {
  agent_definition: 'agent_definition',
  prompt: 'prompt',
} as const;

export type ColumnDefinitionApiDataType = typeof ColumnDefinitionApiDataType[keyof typeof ColumnDefinitionApiDataType];


export const ColumnDefinitionApiDataType = {
  text: 'text',
  boolean: 'boolean',
  integer: 'integer',
  float: 'float',
  json: 'json',
  array: 'array',
  image: 'image',
  images: 'images',
  datetime: 'datetime',
  audio: 'audio',
  document: 'document',
  others: 'others',
  persona: 'persona',
} as const;

export interface ColumnDefinitionApi {
  /**
     * @minLength 1
     * @maxLength 50
     */
  name: string;
  data_type: ColumnDefinitionApiDataType;
  /**
     * @minLength 1
     * @maxLength 200
     */
  description: string;
}

export interface ScenarioCreateRequestApi {
  /**
     * @minLength 1
     * @maxLength 255
     */
  name: string;
  description?: string;
  dataset_id?: string;
  kind?: ScenarioCreateRequestApiKind;
  /** @minLength 1 */
  script_url?: string;
  agent_definition_id?: string;
  agent_definition_version_id?: string;
  custom_instruction?: string;
  /**
     * @minimum 10
     * @maximum 20000
     */
  no_of_rows?: number;
  generate_graph?: boolean;
  graph?: ScenarioCreateRequestApiGraph;
  source_type?: ScenarioCreateRequestApiSourceType;
  prompt_template_id?: string;
  prompt_version_id?: string;
  add_persona_automatically?: boolean;
  personas?: string[];
  /** @maxItems 10 */
  custom_columns?: ColumnDefinitionApi[];
  /**
     * @minLength 1
     * @maxLength 255
     */
  agent_name?: string;
  agent_prompt?: string;
  /**
     * @minLength 1
     * @maxLength 100
     */
  voice_provider?: string;
  /**
     * @minLength 1
     * @maxLength 100
     */
  voice_name?: string;
  /**
     * @minLength 1
     * @maxLength 100
     */
  model?: string;
  llm_temperature?: number;
  initial_message?: string;
  max_call_duration_in_minutes?: number;
  interrupt_sensitivity?: number;
  conversation_speed?: number;
  finished_speaking_sensitivity?: number;
  initial_message_delay?: number;
}

export type ScenarioCreateResponseApiStatus = typeof ScenarioCreateResponseApiStatus[keyof typeof ScenarioCreateResponseApiStatus];


export const ScenarioCreateResponseApiStatus = {
  processing: 'processing',
} as const;

export interface ScenarioCreateResponseApi {
  /** @minLength 1 */
  readonly message?: string;
  scenario?: ScenarioResponseApi;
  readonly status?: ScenarioCreateResponseApiStatus;
}

export type ScenarioDetailResponseApiScenarioType = typeof ScenarioDetailResponseApiScenarioType[keyof typeof ScenarioDetailResponseApiScenarioType];


export const ScenarioDetailResponseApiScenarioType = {
  graph: 'graph',
  script: 'script',
  dataset: 'dataset',
} as const;

export type ScenarioDetailResponseApiStatus = typeof ScenarioDetailResponseApiStatus[keyof typeof ScenarioDetailResponseApiStatus];


export const ScenarioDetailResponseApiStatus = {
  NotStarted: 'NotStarted',
  Queued: 'Queued',
  Running: 'Running',
  Completed: 'Completed',
  Editing: 'Editing',
  Inactive: 'Inactive',
  Failed: 'Failed',
  PartialRun: 'PartialRun',
  ExperimentEvaluation: 'ExperimentEvaluation',
  Uploading: 'Uploading',
  PartialExtracted: 'PartialExtracted',
  Processing: 'Processing',
  Deleting: 'Deleting',
  PartialCompleted: 'PartialCompleted',
  OptimizationEvaluation: 'OptimizationEvaluation',
  Error: 'Error',
  Cancelled: 'Cancelled',
} as const;

export type ScenarioDetailResponseApiGraph = {[key: string]: string};

export type ScenarioPromptItemApiRole = typeof ScenarioPromptItemApiRole[keyof typeof ScenarioPromptItemApiRole];


export const ScenarioPromptItemApiRole = {
  system: 'system',
  user: 'user',
  assistant: 'assistant',
} as const;

export interface ScenarioPromptItemApi {
  readonly role?: ScenarioPromptItemApiRole;
  /** @minLength 1 */
  readonly content?: string;
}

export interface ScenarioDetailResponseApi {
  readonly id?: string;
  /** @minLength 1 */
  readonly name?: string;
  /** @minLength 1 */
  readonly description?: string;
  /** @minLength 1 */
  readonly source?: string;
  readonly scenario_type?: ScenarioDetailResponseApiScenarioType;
  readonly dataset_id?: string;
  readonly organization?: string;
  readonly dataset?: string;
  readonly created_at?: string;
  readonly updated_at?: string;
  readonly deleted?: boolean;
  readonly deleted_at?: string;
  readonly status?: ScenarioDetailResponseApiStatus;
  /** @minLength 1 */
  readonly agent_type?: string;
  readonly graph?: ScenarioDetailResponseApiGraph;
  readonly prompts?: readonly ScenarioPromptItemApi[];
  readonly dataset_rows?: number;
}

export interface ScenarioAddColumnsRequestApi {
  columns: ColumnDefinitionApi[];
}

export interface ScenarioAddColumnsResponseApi {
  /** @minLength 1 */
  readonly message?: string;
  readonly scenario_id?: string;
  readonly dataset_id?: string;
  readonly columns?: readonly string[];
}

export interface ScenarioAddRowsRequestApi {
  /**
     * @minimum 10
     * @maximum 20000
     */
  num_rows: number;
  description?: string;
}

export interface ScenarioAddRowsResponseApi {
  /** @minLength 1 */
  readonly message?: string;
  readonly scenario_id?: string;
  readonly dataset_id?: string;
  readonly num_rows?: number;
}

export interface ScenarioDeleteResponseApi {
  /** @minLength 1 */
  readonly message?: string;
}

export type ScenarioEditRequestApiGraph = { [key: string]: unknown };

export interface ScenarioEditRequestApi {
  /** @maxLength 255 */
  name?: string;
  description?: string;
  graph?: ScenarioEditRequestApiGraph;
  prompt?: string;
}

export interface ScenarioEditResponseApi {
  /** @minLength 1 */
  readonly message?: string;
  scenario?: ScenarioResponseApi;
}

export interface ScenarioEditPromptsRequestApi {
  /**
     * @minLength 1
     * @maxLength 10000
     */
  prompts: string;
}

export interface ScenarioPromptsUpdateResponseApi {
  /** @minLength 1 */
  readonly message?: string;
  /** @minLength 1 */
  readonly prompts?: string;
}

export interface SimulatorAgentApi {
  readonly id?: string;
  /**
     * Name of the simulator agent
     * @minLength 1
     * @maxLength 255
     */
  name: string;
  /**
     * System prompt for the agent
     * @minLength 1
     */
  prompt: string;
  /**
     * Voice service provider
     * @minLength 1
     * @maxLength 100
     */
  voice_provider: string;
  /**
     * Specific voice to use
     * @minLength 1
     * @maxLength 100
     */
  voice_name: string;
  /**
     * Sensitivity for interruption detection (0-1)
     * @minimum 0
     * @maximum 11
     */
  interrupt_sensitivity?: number;
  /**
     * Speed of conversation (0.1-3.0)
     * @minimum 0.1
     * @maximum 2
     */
  conversation_speed?: number;
  /**
     * Sensitivity for detecting when speaker has finished (0-1)
     * @minimum 0
     * @maximum 11
     */
  finished_speaking_sensitivity?: number;
  /**
     * LLM model to use
     * @minLength 1
     * @maxLength 100
     */
  model: string;
  /**
     * Temperature setting for LLM (0-2)
     * @minimum 0
     * @maximum 2
     */
  llm_temperature?: number;
  /**
     * Maximum call duration in minutes (1-180)
     * @minimum 0
     * @maximum 180
     */
  max_call_duration_in_minutes?: number;
  /**
     * Delay before initial message in seconds (0-60)
     * @minimum 0
     * @maximum 60
     */
  initial_message_delay?: number;
  /** Initial message to send when conversation starts */
  initial_message?: string;
  readonly created_at?: string;
  readonly updated_at?: string;
  /** Organization this simulator agent belongs to */
  readonly organization?: string;
  readonly deleted?: boolean;
  readonly deleted_at?: string;
  readonly logo_url?: string;
}

export interface SimulatorAgentListResponseApi {
  readonly count?: number;
  /** @minLength 1 */
  readonly next?: string;
  /** @minLength 1 */
  readonly previous?: string;
  readonly results?: readonly SimulatorAgentApi[];
  readonly total_pages?: number;
  readonly current_page?: number;
}

export interface SimulatorAgentValidationErrorResponseApi {[key: string]: string[]}

/**
 * Fail rate data for scatter plot chart
 */
export type TestExecutionAnalyticsApiFailRateOverTestRuns = {[key: string]: string};

/**
 * Evaluation categories data for line graph chart
 */
export type TestExecutionAnalyticsApiEvaluationCategoriesOverTestRuns = {[key: string]: string};

/**
 * Metadata about the analytics data
 */
export type TestExecutionAnalyticsApiMetadata = {[key: string]: string};

export interface TestExecutionAnalyticsApi {
  /** Fail rate data for scatter plot chart */
  fail_rate_over_test_runs: TestExecutionAnalyticsApiFailRateOverTestRuns;
  /** Evaluation categories data for line graph chart */
  evaluation_categories_over_test_runs: TestExecutionAnalyticsApiEvaluationCategoriesOverTestRuns;
  /** Metadata about the analytics data */
  metadata: TestExecutionAnalyticsApiMetadata;
}

export interface CancelTestExecutionResponseApi {
  success: boolean;
  /** @minLength 1 */
  message: string;
  test_execution_id: string;
}

export interface TestExecutionChatBatchResultApi {
  call_execution_ids: string[];
  has_more: boolean;
  batched_scenarios: string[];
}

export interface TestExecutionChatBatchResponseApi {
  status?: boolean;
  result: TestExecutionChatBatchResultApi;
}

export interface ColumnOrderApi {
  /** @minLength 1 */
  column_name: string;
  /** @minLength 1 */
  id: string;
  visible: boolean;
}

export interface TestExecutionColumnOrderApi {
  column_order: ColumnOrderApi[];
}

export interface TestExecutionColumnOrderResponseApi {
  /** @minLength 1 */
  readonly message?: string;
  readonly column_order?: readonly ColumnOrderApi[];
}

export type ApiSuccessResponseApiResult = { [key: string]: unknown };

export interface ApiSuccessResponseApi {
  status?: boolean;
  result?: ApiSuccessResponseApiResult;
}

/**
 * Performance metrics including pass rate, total test runs, and latest fail rate
 */
export type PerformanceSummaryApiTestRunPerformanceMetrics = {[key: string]: number};

/**
 * List of top performing scenarios with their performance scores
 */
export type PerformanceSummaryApiTopPerformingScenariosItem = {[key: string]: string};

export interface PerformanceSummaryApi {
  /** Performance metrics including pass rate, total test runs, and latest fail rate */
  test_run_performance_metrics: PerformanceSummaryApiTestRunPerformanceMetrics;
  /** List of top performing scenarios */
  top_performing_scenarios: PerformanceSummaryApiTopPerformingScenariosItem[];
}

/**
 * Type of rerun: evaluation only or call plus evaluation
 */
export type CallExecutionRerunApiRerunType = typeof CallExecutionRerunApiRerunType[keyof typeof CallExecutionRerunApiRerunType];


export const CallExecutionRerunApiRerunType = {
  eval_only: 'eval_only',
  call_and_eval: 'call_and_eval',
} as const;

export interface CallExecutionRerunApi {
  /** Type of rerun: evaluation only or call plus evaluation */
  rerun_type: CallExecutionRerunApiRerunType;
  /** List of specific call execution IDs to rerun */
  call_execution_ids?: string[];
  /** Whether to rerun all call executions in the test execution */
  select_all?: boolean;
}

export interface FailedRerunItemApi {
  call_execution_id: string;
  /** @minLength 1 */
  error: string;
}

export interface RerunCallsResponseApi {
  /** @minLength 1 */
  message: string;
  test_execution_id: string;
  /** @minLength 1 */
  rerun_type: string;
  total_processed: number;
  successful_reruns: string[];
  failed_reruns: FailedRerunItemApi[];
  success_count: number;
  failure_count: number;
}

export interface TestExecutionTranscriptCallApi {
  readonly call_execution_id?: string;
  /** @minLength 1 */
  readonly phone_number?: string;
  /** @minLength 1 */
  readonly status?: string;
  readonly transcripts?: readonly CallTranscriptApi[];
  readonly total_transcripts?: number;
  /** @minLength 1 */
  readonly scenario_name?: string;
}

export interface TestExecutionTranscriptsResponseApi {
  readonly test_execution_id?: string;
  readonly calls?: readonly TestExecutionTranscriptCallApi[];
  readonly total_calls?: number;
  readonly total_transcripts?: number;
}

export interface BulkAnnotationAnnotationRequestApi {
  annotation_label_id: string;
  value?: string;
  value_float?: number;
  value_bool?: boolean;
  value_str_list?: string[];
}

export interface BulkAnnotationNoteRequestApi {
  /** @minLength 1 */
  text: string;
}

export interface BulkAnnotationRecordRequestApi {
  /** @minLength 1 */
  observation_span_id: string;
  annotations?: BulkAnnotationAnnotationRequestApi[];
  notes?: BulkAnnotationNoteRequestApi[];
}

export interface BulkAnnotationRequestApi {
  records: BulkAnnotationRecordRequestApi[];
}

export type BulkAnnotationResponseResultApiWarningsItem = { [key: string]: unknown };

export type BulkAnnotationResponseResultApiErrorsItem = { [key: string]: unknown };

export interface BulkAnnotationResponseResultApi {
  /** @minLength 1 */
  message: string;
  annotations_created: number;
  annotations_updated: number;
  notes_created: number;
  succeeded_count: number;
  errors_count: number;
  warnings_count: number;
  warnings?: BulkAnnotationResponseResultApiWarningsItem[];
  errors?: BulkAnnotationResponseResultApiErrorsItem[];
}

export interface BulkAnnotationResponseApi {
  status?: boolean;
  result: BulkAnnotationResponseResultApi;
}

export type FetchGraphApiFiltersItemFilterConfig = {
  /** Canonical field type, for example text, number, boolean, datetime, categorical, thumbs, annotator, or array. */
  filter_type: string;
  /** Canonical operator from api_contracts/filter_contract.json, for example equals, not_equals, in, not_in, between, not_between, is_null, or is_not_null. */
  filter_op: string;
  /** Scalar, list, range tuple, boolean, or null depending on filter_op and filter_type. */
  filter_value?: unknown;
  /** Column family such as SYSTEM_METRIC, SPAN_ATTRIBUTE, EVAL_METRIC, ANNOTATION, or NORMAL. */
  col_type?: string;
};

export type FetchGraphApiFiltersItem = {
  /** Column or attribute id to filter on. */
  column_id: string;
  /** Optional UI label for chips and saved views. */
  display_name?: string;
  filter_config: FetchGraphApiFiltersItemFilterConfig;
};

export type FetchGraphApiReqDataConfig = { [key: string]: unknown };

export interface FetchGraphApi {
  /** @minLength 1 */
  interval: string;
  filters: FetchGraphApiFiltersItem[];
  /** @minLength 1 */
  property: string;
  req_data_config: FetchGraphApiReqDataConfig;
  /** @minLength 1 */
  project_id: string;
}

export type CustomEvalConfigApiConfig = { [key: string]: unknown };

export type CustomEvalConfigApiMapping = { [key: string]: unknown };

export type CustomEvalConfigApiFilters = { [key: string]: unknown };

export type CustomEvalConfigApiModel = typeof CustomEvalConfigApiModel[keyof typeof CustomEvalConfigApiModel];


export const CustomEvalConfigApiModel = {
  turing_large: 'turing_large',
  turing_small: 'turing_small',
  protect: 'protect',
  protect_flash: 'protect_flash',
  turing_flash: 'turing_flash',
} as const;

export interface CustomEvalConfigApi {
  readonly id?: string;
  eval_template: string;
  /** @maxLength 255 */
  name?: string;
  config?: CustomEvalConfigApiConfig;
  mapping?: CustomEvalConfigApiMapping;
  project: string;
  filters?: CustomEvalConfigApiFilters;
  error_localizer?: boolean;
  kb_id?: string;
  model?: CustomEvalConfigApiModel;
  readonly eval_group?: string;
}

export interface DashboardApi {
  readonly id?: string;
  /**
     * @minLength 1
     * @maxLength 255
     */
  name: string;
  description?: string;
  readonly workspace?: string;
  created_by?: UserApi;
  updated_by?: UserApi;
  readonly created_at?: string;
  readonly updated_at?: string;
  readonly widget_count?: string;
}

export interface DashboardCreateUpdateApi {
  /**
     * @minLength 1
     * @maxLength 255
     */
  name: string;
  description?: string;
}

export type DashboardWidgetApiQueryConfig = { [key: string]: unknown };

export type DashboardWidgetApiChartConfig = { [key: string]: unknown };

export interface DashboardWidgetApi {
  readonly id?: string;
  /**
     * @minLength 1
     * @maxLength 255
     */
  name?: string;
  description?: string;
  /**
     * @minimum -2147483648
     * @maximum 2147483647
     */
  position?: number;
  /**
     * @minimum -2147483648
     * @maximum 2147483647
     */
  width?: number;
  /**
     * @minimum -2147483648
     * @maximum 2147483647
     */
  height?: number;
  query_config?: DashboardWidgetApiQueryConfig;
  chart_config?: DashboardWidgetApiChartConfig;
  readonly created_by?: string;
  readonly created_at?: string;
  readonly updated_at?: string;
}

export interface DashboardDetailApi {
  readonly id?: string;
  /**
     * @minLength 1
     * @maxLength 255
     */
  name: string;
  description?: string;
  readonly workspace?: string;
  created_by?: UserApi;
  updated_by?: UserApi;
  readonly created_at?: string;
  readonly updated_at?: string;
  readonly widgets?: string;
}

export type DatasetApiModelType = typeof DatasetApiModelType[keyof typeof DatasetApiModelType];


export const DatasetApiModelType = {
  Numeric: 'Numeric',
  ScoreCategorical: 'ScoreCategorical',
  Ranking: 'Ranking',
  BinaryClassification: 'BinaryClassification',
  Regression: 'Regression',
  ObjectDetection: 'ObjectDetection',
  Segmentation: 'Segmentation',
  GenerativeLLM: 'GenerativeLLM',
  GenerativeImage: 'GenerativeImage',
  GenerativeVideo: 'GenerativeVideo',
  TTS: 'TTS',
  STT: 'STT',
  MultiModal: 'MultiModal',
} as const;

export type DatasetApiSource = typeof DatasetApiSource[keyof typeof DatasetApiSource];


export const DatasetApiSource = {
  demo: 'demo',
  build: 'build',
  sdk: 'sdk',
  observe: 'observe',
  knowledge_base: 'knowledge_base',
  scenario: 'scenario',
  experiment_snapshot: 'experiment_snapshot',
  graph: 'graph',
} as const;

export interface DatasetApi {
  readonly id?: string;
  /**
     * @minLength 1
     * @maxLength 2000
     */
  name: string;
  organization: string;
  model_type?: DatasetApiModelType;
  source?: DatasetApiSource;
  user?: string;
}

export type EvalTaskApiFiltersSpanAttributesFiltersItemFilterConfig = {
  /** Canonical field type, for example text, number, boolean, datetime, categorical, thumbs, annotator, or array. */
  filter_type: string;
  /** Canonical operator from api_contracts/filter_contract.json, for example equals, not_equals, in, not_in, between, not_between, is_null, or is_not_null. */
  filter_op: string;
  /** Scalar, list, range tuple, boolean, or null depending on filter_op and filter_type. */
  filter_value?: unknown;
  /** Column family such as SYSTEM_METRIC, SPAN_ATTRIBUTE, EVAL_METRIC, ANNOTATION, or NORMAL. */
  col_type?: string;
};

export type EvalTaskApiFiltersSpanAttributesFiltersItem = {
  /** Column or attribute id to filter on. */
  column_id: string;
  /** Optional UI label for chips and saved views. */
  display_name?: string;
  filter_config: EvalTaskApiFiltersSpanAttributesFiltersItemFilterConfig;
};

export type EvalTaskApiFilters = {
  /** Project scope for the evaluation task. */
  project_id?: string;
  /**
     * Inclusive start/end ISO timestamps.
     * @minItems 2
     * @maxItems 2
     */
  date_range?: string[];
  /** Lower-bound ISO timestamp for legacy task filters. */
  created_at?: string;
  /** Trace session id to constrain the task. */
  session_id?: string;
  /** Observation span type(s), for example llm, tool, or chain. */
  observation_type?: string[];
  span_attributes_filters?: EvalTaskApiFiltersSpanAttributesFiltersItem[];
};

export type EvalTaskApiRunType = typeof EvalTaskApiRunType[keyof typeof EvalTaskApiRunType];


export const EvalTaskApiRunType = {
  continuous: 'continuous',
  historical: 'historical',
} as const;

export type EvalTaskApiRowType = typeof EvalTaskApiRowType[keyof typeof EvalTaskApiRowType];


export const EvalTaskApiRowType = {
  spans: 'spans',
  traces: 'traces',
  sessions: 'sessions',
  voiceCalls: 'voiceCalls',
} as const;

export type EvalTaskApiStatus = typeof EvalTaskApiStatus[keyof typeof EvalTaskApiStatus];


export const EvalTaskApiStatus = {
  pending: 'pending',
  running: 'running',
  completed: 'completed',
  failed: 'failed',
  paused: 'paused',
  deleted: 'deleted',
} as const;

export type EvalTaskApiEvalsDetails = { [key: string]: unknown };

export type EvalTaskApiFailedSpans = { [key: string]: unknown };

export interface EvalTaskApi {
  readonly id?: string;
  project: string;
  /**
     * @minLength 1
     * @maxLength 255
     */
  name: string;
  filters?: EvalTaskApiFilters;
  /**
     * @minimum 1
     * @maximum 100
     */
  sampling_rate: number;
  last_run?: string;
  /**
     * @minimum 1
     * @maximum 1000000
     */
  spans_limit?: number;
  run_type: EvalTaskApiRunType;
  row_type?: EvalTaskApiRowType;
  status?: EvalTaskApiStatus;
  start_time?: string;
  end_time?: string;
  readonly created_at?: string;
  readonly updated_at?: string;
  evals_details?: EvalTaskApiEvalsDetails;
  evals: string[];
  failed_spans?: EvalTaskApiFailedSpans;
  readonly progress?: string;
}

export interface LinearTeamApi {
  /** @minLength 1 */
  id: string;
  /** @minLength 1 */
  name: string;
  key?: string;
}

export interface LinearTeamsResultApi {
  connected: boolean;
  teams: LinearTeamApi[];
}

export interface LinearTeamsResponseApi {
  status?: boolean;
  result: LinearTeamsResultApi;
}

export interface ErrorNameApi {
  /** @minLength 1 */
  name: string;
  type: string;
}

export interface TrendPointApi {
  timestamp: string;
  value: number;
  users: number;
}

export interface FeedListRowApi {
  /** @minLength 1 */
  cluster_id: string;
  /** @minLength 1 */
  source: string;
  error: ErrorNameApi;
  /** @minLength 1 */
  status: string;
  /** @minLength 1 */
  severity: string;
  occurrences: number;
  trace_count: number;
  /** @minLength 1 */
  fix_layer: string;
  users_affected: number;
  sessions: number;
  first_seen: string;
  last_seen: string;
  trends: TrendPointApi[];
  assignees: string[];
  /** @minLength 1 */
  model: string;
  /** @minLength 1 */
  model_version: string;
  /** @minLength 1 */
  project: string;
  /** @minLength 1 */
  project_id: string;
  /** @minLength 1 */
  environment: string;
  eval_score: number;
  /** @minLength 1 */
  trace_id: string;
  /** @minLength 1 */
  external_issue_url: string;
  /** @minLength 1 */
  external_issue_id: string;
}

export interface FeedListResponseApi {
  data: FeedListRowApi[];
  total: number;
  limit: number;
  offset: number;
}

export interface FeedListApiResponseApi {
  status?: boolean;
  result: FeedListResponseApi;
}

export interface FeedStatsApi {
  total_errors: number;
  escalating: number;
  for_review: number;
  acknowledged: number;
  resolved: number;
  affected_users: number;
}

export interface FeedStatsApiResponseApi {
  status?: boolean;
  result: FeedStatsApi;
}

export interface TracePreviewApi {
  /** @minLength 1 */
  trace_id: string;
  /** @minLength 1 */
  input: string;
  /** @minLength 1 */
  output: string;
}

export interface FeedDetailCoreApi {
  row: FeedListRowApi;
  /** @minLength 1 */
  description: string;
  success_trace: TracePreviewApi;
  representative_trace: TracePreviewApi;
}

export interface FeedDetailApiResponseApi {
  status?: boolean;
  result: FeedDetailCoreApi;
}

export type FeedUpdateBodyApiStatus = typeof FeedUpdateBodyApiStatus[keyof typeof FeedUpdateBodyApiStatus];


export const FeedUpdateBodyApiStatus = {
  escalating: 'escalating',
  for_review: 'for_review',
  acknowledged: 'acknowledged',
  resolved: 'resolved',
} as const;

export type FeedUpdateBodyApiSeverity = typeof FeedUpdateBodyApiSeverity[keyof typeof FeedUpdateBodyApiSeverity];


export const FeedUpdateBodyApiSeverity = {
  critical: 'critical',
  high: 'high',
  medium: 'medium',
  low: 'low',
} as const;

export interface FeedUpdateBodyApi {
  project_id?: string;
  status?: FeedUpdateBodyApiStatus;
  severity?: FeedUpdateBodyApiSeverity;
  /** @minLength 1 */
  assignee?: string;
}

export interface CreateLinearIssueApi {
  /** @minLength 1 */
  team_id: string;
  title?: string;
  description?: string;
  priority?: number;
}

export interface CreateLinearIssueResultApi {
  already_linked?: boolean;
  /** @minLength 1 */
  issue_id?: string;
  /** @minLength 1 */
  issue_url?: string;
  /** @minLength 1 */
  issue_title?: string;
}

export interface CreateLinearIssueResponseApi {
  status?: boolean;
  result: CreateLinearIssueResultApi;
}

export interface DeepAnalysisBodyApi {
  /** @minLength 1 */
  trace_id: string;
  force?: boolean;
}

export interface DeepAnalysisDispatchResponseApi {
  /** @minLength 1 */
  status: string;
  /** @minLength 1 */
  trace_id: string;
}

export interface DeepAnalysisDispatchApiResponseApi {
  status?: boolean;
  result: DeepAnalysisDispatchResponseApi;
}

export interface EventsOverTimePointApi {
  /** @minLength 1 */
  date: string;
  errors: number;
  passing: number;
  users: number;
}

export interface PatternInsightApi {
  /** @minLength 1 */
  value: string;
  /** @minLength 1 */
  caption: string;
}

export interface KeyMomentApi {
  /** @minLength 1 */
  kevinified: string;
  verbatim: string;
}

export interface PatternSummaryApi {
  insights: PatternInsightApi[];
  key_moments: KeyMomentApi[];
}

export interface TraceSummaryApi {
  eval_score: number;
  latency_ms: number;
  turns: number;
  /** @minLength 1 */
  model: string;
  input_tokens: number;
  output_tokens: number;
}

export type TraceEvidenceApiFailReelItem = {[key: string]: string};

export type TraceEvidenceApiPassReelItem = {[key: string]: string};

export interface TraceEvidenceApi {
  /** @minLength 1 */
  input: string;
  /** @minLength 1 */
  output: string;
  fail_reel: TraceEvidenceApiFailReelItem[];
  pass_reel: TraceEvidenceApiPassReelItem[];
}

export type AgentFlowGraphApiNodesItem = {[key: string]: string};

export type AgentFlowGraphApiEdgesItem = {[key: string]: string};

export interface AgentFlowGraphApi {
  nodes: AgentFlowGraphApiNodesItem[];
  edges: AgentFlowGraphApiEdgesItem[];
}

export type RepresentativeTraceApiRootCausesItem = {[key: string]: string};

export type RepresentativeTraceApiRecommendationsItem = {[key: string]: string};

export type RepresentativeTraceApiWhatChanged = {[key: string]: string};

export interface RepresentativeTraceApi {
  /** @minLength 1 */
  id: string;
  /** @minLength 1 */
  status: string;
  timestamp: string;
  summary: TraceSummaryApi;
  evidence: TraceEvidenceApi;
  agent_flow: AgentFlowGraphApi;
  root_causes: RepresentativeTraceApiRootCausesItem[];
  recommendations: RepresentativeTraceApiRecommendationsItem[];
  what_changed: RepresentativeTraceApiWhatChanged;
}

export interface OverviewResponseApi {
  events_over_time: EventsOverTimePointApi[];
  pattern_summary: PatternSummaryApi;
  representative_traces: RepresentativeTraceApi[];
}

export interface OverviewApiResponseApi {
  status?: boolean;
  result: OverviewResponseApi;
}

export interface RootCauseApi {
  rank: number;
  /** @minLength 1 */
  title: string;
  /** @minLength 1 */
  description: string;
}

export interface RecommendationApi {
  /** @minLength 1 */
  id: string;
  /** @minLength 1 */
  title: string;
  description: string;
  /** @minLength 1 */
  priority: string;
  root_cause_link: number;
  /** @minLength 1 */
  immediate_fix: string;
  /** @minLength 1 */
  insights: string;
  evidence: string[];
}

export interface DeepAnalysisResponseApi {
  /** @minLength 1 */
  status: string;
  /** @minLength 1 */
  trace_id: string;
  root_causes: RootCauseApi[];
  recommendations: RecommendationApi[];
  /** @minLength 1 */
  immediate_fix: string;
}

export interface DeepAnalysisApiResponseApi {
  status?: boolean;
  result: DeepAnalysisResponseApi;
}

export interface SidebarTimelineApi {
  first_seen: string;
  last_seen: string;
  age_days: number;
}

export interface SidebarAIMetadataApi {
  /** @minLength 1 */
  model: string;
  /** @minLength 1 */
  model_version: string;
  /** @minLength 1 */
  project: string;
  eval_score: number;
  /** @minLength 1 */
  trace_id: string;
}

export interface EvaluationResultApi {
  /** @minLength 1 */
  label: string;
  /** @minLength 1 */
  type: string;
  /** @minLength 1 */
  result: string;
  score: number;
  /** @minLength 1 */
  value: string;
}

export interface CoOccurringIssueApi {
  /** @minLength 1 */
  id: string;
  /** @minLength 1 */
  title: string;
  type: string;
  co_occurrence: number;
  count: number;
  /** @minLength 1 */
  severity: string;
}

export interface FeedSidebarApi {
  timeline: SidebarTimelineApi;
  ai_metadata: SidebarAIMetadataApi;
  evaluations: EvaluationResultApi[];
  co_occurring_issues: CoOccurringIssueApi[];
}

export interface FeedSidebarApiResponseApi {
  status?: boolean;
  result: FeedSidebarApi;
}

export interface TracesAggregatesApi {
  total_traces: number;
  failing_traces: number;
  passing_traces: number;
  avg_score: number;
  p50_latency: number;
  p95_latency: number;
  avg_turns: number;
}

export interface TracesListRowApi {
  /** @minLength 1 */
  id: string;
  /** @minLength 1 */
  input: string;
  timestamp: string;
  latency_ms: number;
  tokens: number;
  cost: number;
  score: number;
  turns: number;
}

export interface TracesTabResponseApi {
  aggregates: TracesAggregatesApi;
  traces: TracesListRowApi[];
  total: number;
}

export interface TracesTabApiResponseApi {
  status?: boolean;
  result: TracesTabResponseApi;
}

export interface TrendMetricApi {
  /** @minLength 1 */
  label: string;
  /** @minLength 1 */
  value: string;
  delta: number;
  unit: string;
}

export interface ScoreTrendApi {
  /** @minLength 1 */
  label: string;
  current: number;
  prev: number;
  sparkline: number[];
}

export interface HeatmapCellApi {
  day: number;
  hour: number;
  value: number;
}

export interface TrendsTabResponseApi {
  metrics: TrendMetricApi[];
  events_over_time: EventsOverTimePointApi[];
  score_trends: ScoreTrendApi[];
  activity_heatmap: HeatmapCellApi[][];
}

export interface TrendsTabApiResponseApi {
  status?: boolean;
  result: TrendsTabResponseApi;
}

export type AnnotationLabelResponseApiSettings = { [key: string]: unknown };

export interface AnnotationLabelResponseApi {
  id: string;
  /** @minLength 1 */
  name: string;
  /** @minLength 1 */
  type: string;
  description?: string;
  settings?: AnnotationLabelResponseApiSettings;
}

export interface GetAnnotationLabelsResponseApi {
  status?: boolean;
  result: AnnotationLabelResponseApi[];
}

export type ImagineAnalysisItemApiStatus = typeof ImagineAnalysisItemApiStatus[keyof typeof ImagineAnalysisItemApiStatus];


export const ImagineAnalysisItemApiStatus = {
  pending: 'pending',
  running: 'running',
  completed: 'completed',
  failed: 'failed',
} as const;

export interface ImagineAnalysisItemApi {
  id: string;
  /**
     * @minLength 1
     * @maxLength 100
     */
  widget_id: string;
  status: ImagineAnalysisItemApiStatus;
  content?: string;
  error?: string;
}

export interface ImagineAnalysisResultApi {
  analyses: ImagineAnalysisItemApi[];
}

export interface ImagineAnalysisResponseApi {
  status?: boolean;
  result: ImagineAnalysisResultApi;
}

export interface WidgetAnalysisApi {
  /**
     * @minLength 1
     * @maxLength 100
     */
  widget_id: string;
  /**
     * @minLength 1
     * @maxLength 8000
     */
  prompt: string;
}

export interface TriggerAnalysisApi {
  saved_view_id: string;
  /**
     * @minLength 1
     * @maxLength 255
     */
  trace_id: string;
  project_id: string;
  widgets: WidgetAnalysisApi[];
}

export type ObservabilityProviderApiProvider = typeof ObservabilityProviderApiProvider[keyof typeof ObservabilityProviderApiProvider];


export const ObservabilityProviderApiProvider = {
  vapi: 'vapi',
  eleven_labs: 'eleven_labs',
  retell: 'retell',
  livekit: 'livekit',
  others: 'others',
} as const;

export type ObservabilityProviderApiMetadata = { [key: string]: unknown };

export interface ObservabilityProviderApi {
  readonly id?: string;
  readonly project?: string;
  /**
     * Name of the project. If it doesn't exist, it will be created.
     * @minLength 1
     */
  project_name?: string;
  provider: ObservabilityProviderApiProvider;
  enabled?: boolean;
  readonly organization?: string;
  readonly workspace?: string;
  metadata?: ObservabilityProviderApiMetadata;
  readonly created_at?: string;
  readonly updated_at?: string;
}

export type ObservationSpanApiObservationType = typeof ObservationSpanApiObservationType[keyof typeof ObservationSpanApiObservationType];


export const ObservationSpanApiObservationType = {
  tool: 'tool',
  chain: 'chain',
  llm: 'llm',
  retriever: 'retriever',
  embedding: 'embedding',
  agent: 'agent',
  reranker: 'reranker',
  unknown: 'unknown',
  guardrail: 'guardrail',
  evaluator: 'evaluator',
  conversation: 'conversation',
} as const;

export type ObservationSpanApiInput = { [key: string]: unknown };

export type ObservationSpanApiOutput = { [key: string]: unknown };

export type ObservationSpanApiModelParameters = { [key: string]: unknown };

export type ObservationSpanApiStatus = typeof ObservationSpanApiStatus[keyof typeof ObservationSpanApiStatus];


export const ObservationSpanApiStatus = {
  UNSET: 'UNSET',
  OK: 'OK',
  ERROR: 'ERROR',
} as const;

export type ObservationSpanApiTags = { [key: string]: unknown };

export type ObservationSpanApiMetadata = { [key: string]: unknown };

export type ObservationSpanApiSpanEvents = { [key: string]: unknown };

export type ObservationSpanApiEvalStatus = typeof ObservationSpanApiEvalStatus[keyof typeof ObservationSpanApiEvalStatus];


export const ObservationSpanApiEvalStatus = {
  NotStarted: 'NotStarted',
  Queued: 'Queued',
  Running: 'Running',
  Completed: 'Completed',
  Editing: 'Editing',
  Inactive: 'Inactive',
  Failed: 'Failed',
  PartialRun: 'PartialRun',
  ExperimentEvaluation: 'ExperimentEvaluation',
  Uploading: 'Uploading',
  PartialExtracted: 'PartialExtracted',
  Processing: 'Processing',
  Deleting: 'Deleting',
  PartialCompleted: 'PartialCompleted',
  OptimizationEvaluation: 'OptimizationEvaluation',
  Error: 'Error',
  Cancelled: 'Cancelled',
} as const;

export interface ObservationSpanApi {
  /** @minLength 1 */
  readonly id?: string;
  project: string;
  project_version?: string;
  trace: string;
  /** @maxLength 255 */
  parent_span_id?: string;
  /**
     * @minLength 1
     * @maxLength 2000
     */
  name: string;
  observation_type: ObservationSpanApiObservationType;
  start_time?: string;
  end_time?: string;
  input?: ObservationSpanApiInput;
  output?: ObservationSpanApiOutput;
  /** @maxLength 255 */
  model?: string;
  model_parameters?: ObservationSpanApiModelParameters;
  /**
     * @minimum -2147483648
     * @maximum 2147483647
     */
  latency_ms?: number;
  org_id?: string;
  org_user_id?: string;
  /**
     * @minimum -2147483648
     * @maximum 2147483647
     */
  prompt_tokens?: number;
  /**
     * @minimum -2147483648
     * @maximum 2147483647
     */
  completion_tokens?: number;
  /**
     * @minimum -2147483648
     * @maximum 2147483647
     */
  total_tokens?: number;
  response_time?: number;
  /** @maxLength 255 */
  eval_id?: string;
  cost?: number;
  status?: ObservationSpanApiStatus;
  status_message?: string;
  tags?: ObservationSpanApiTags;
  metadata?: ObservationSpanApiMetadata;
  span_events?: ObservationSpanApiSpanEvents;
  /** @maxLength 255 */
  provider?: string;
  readonly provider_logo?: string;
  readonly span_attributes?: string;
  custom_eval_config?: string;
  eval_status?: ObservationSpanApiEvalStatus;
  prompt_version?: string;
}

export type AddObservationSpanAnnotationsApiAnnotationValues = {[key: string]: { [key: string]: unknown }};

export interface AddObservationSpanAnnotationsApi {
  observation_span_id?: string;
  trace_id?: string;
  annotation_values: AddObservationSpanAnnotationsApiAnnotationValues;
  notes?: string;
}

export type ProjectVersionApiMetadata = { [key: string]: unknown };

export type ProjectVersionApiError = { [key: string]: unknown };

export type ProjectVersionApiEvalTags = { [key: string]: unknown };

export interface ProjectVersionApi {
  readonly id?: string;
  project: string;
  /**
     * @minLength 1
     * @maxLength 255
     */
  name: string;
  metadata?: ProjectVersionApiMetadata;
  start_time?: string;
  end_time?: string;
  error?: ProjectVersionApiError;
  eval_tags?: ProjectVersionApiEvalTags;
  avg_eval_score?: number;
  /** @minLength 1 */
  readonly version?: string;
  annotations?: string;
}

export type ProjectApiModelType = typeof ProjectApiModelType[keyof typeof ProjectApiModelType];


export const ProjectApiModelType = {
  Numeric: 'Numeric',
  ScoreCategorical: 'ScoreCategorical',
  Ranking: 'Ranking',
  BinaryClassification: 'BinaryClassification',
  Regression: 'Regression',
  ObjectDetection: 'ObjectDetection',
  Segmentation: 'Segmentation',
  GenerativeLLM: 'GenerativeLLM',
  GenerativeImage: 'GenerativeImage',
  GenerativeVideo: 'GenerativeVideo',
  TTS: 'TTS',
  STT: 'STT',
  MultiModal: 'MultiModal',
} as const;

export type ProjectApiTraceType = typeof ProjectApiTraceType[keyof typeof ProjectApiTraceType];


export const ProjectApiTraceType = {
  experiment: 'experiment',
  observe: 'observe',
} as const;

export type ProjectApiMetadata = { [key: string]: unknown };

export type ProjectApiConfig = { [key: string]: unknown };

export type ProjectApiSource = typeof ProjectApiSource[keyof typeof ProjectApiSource];


export const ProjectApiSource = {
  demo: 'demo',
  prototype: 'prototype',
  simulator: 'simulator',
} as const;

export type ProjectApiSessionConfig = { [key: string]: unknown };

export type ProjectApiTags = { [key: string]: unknown };

export interface ProjectApi {
  readonly id?: string;
  model_type: ProjectApiModelType;
  /**
     * @minLength 1
     * @maxLength 255
     */
  name: string;
  trace_type: ProjectApiTraceType;
  metadata?: ProjectApiMetadata;
  readonly organization?: string;
  readonly workspace?: string;
  readonly created_at?: string;
  readonly updated_at?: string;
  config?: ProjectApiConfig;
  source?: ProjectApiSource;
  session_config?: ProjectApiSessionConfig;
  tags?: ProjectApiTags;
}

export type ReplaySessionListApiReplayType = typeof ReplaySessionListApiReplayType[keyof typeof ReplaySessionListApiReplayType];


export const ReplaySessionListApiReplayType = {
  session: 'session',
  trace: 'trace',
} as const;

export type ReplaySessionListApiCurrentStep = typeof ReplaySessionListApiCurrentStep[keyof typeof ReplaySessionListApiCurrentStep];


export const ReplaySessionListApiCurrentStep = {
  init: 'init',
  generating: 'generating',
  completed: 'completed',
} as const;

export interface ReplaySessionListApi {
  readonly id?: string;
  project: string;
  /** @minLength 1 */
  readonly project_name?: string;
  replay_type: ReplaySessionListApiReplayType;
  current_step?: ReplaySessionListApiCurrentStep;
  readonly created_at?: string;
}

export type CreateReplaySessionApiReplayType = typeof CreateReplaySessionApiReplayType[keyof typeof CreateReplaySessionApiReplayType];


export const CreateReplaySessionApiReplayType = {
  session: 'session',
  trace: 'trace',
} as const;

export interface CreateReplaySessionApi {
  project_id: string;
  replay_type?: CreateReplaySessionApiReplayType;
  ids?: string[];
  select_all?: boolean;
}

export type ReplaySessionApiReplayType = typeof ReplaySessionApiReplayType[keyof typeof ReplaySessionApiReplayType];


export const ReplaySessionApiReplayType = {
  session: 'session',
  trace: 'trace',
} as const;

export type ReplaySessionApiIds = { [key: string]: unknown };

export type ReplaySessionApiCurrentStep = typeof ReplaySessionApiCurrentStep[keyof typeof ReplaySessionApiCurrentStep];


export const ReplaySessionApiCurrentStep = {
  init: 'init',
  generating: 'generating',
  completed: 'completed',
} as const;

export type AgentDefinitionNestedApiAgentType = typeof AgentDefinitionNestedApiAgentType[keyof typeof AgentDefinitionNestedApiAgentType];


export const AgentDefinitionNestedApiAgentType = {
  voice: 'voice',
  text: 'text',
} as const;

export interface AgentDefinitionNestedApi {
  readonly id?: string;
  /**
     * Name of the AI agent
     * @minLength 1
     * @maxLength 255
     */
  agent_name: string;
  agent_type?: AgentDefinitionNestedApiAgentType;
  /**
     * Detailed description of the AI agent's purpose and capabilities
     * @minLength 1
     */
  description: string;
  readonly version_name?: string;
}

/**
 * Status of the scenario
 */
export type ScenarioNestedApiStatus = typeof ScenarioNestedApiStatus[keyof typeof ScenarioNestedApiStatus];


export const ScenarioNestedApiStatus = {
  NotStarted: 'NotStarted',
  Queued: 'Queued',
  Running: 'Running',
  Completed: 'Completed',
  Editing: 'Editing',
  Inactive: 'Inactive',
  Failed: 'Failed',
  PartialRun: 'PartialRun',
  ExperimentEvaluation: 'ExperimentEvaluation',
  Uploading: 'Uploading',
  PartialExtracted: 'PartialExtracted',
  Processing: 'Processing',
  Deleting: 'Deleting',
  PartialCompleted: 'PartialCompleted',
  OptimizationEvaluation: 'OptimizationEvaluation',
  Error: 'Error',
  Cancelled: 'Cancelled',
} as const;

export interface ScenarioNestedApi {
  readonly id?: string;
  /**
     * Name of the scenario
     * @minLength 1
     * @maxLength 255
     */
  name: string;
  /** Status of the scenario */
  status?: ScenarioNestedApiStatus;
  /** Optional description of the scenario */
  description?: string;
}

export interface RunTestNestedApi {
  readonly id?: string;
  /**
     * Name of the test run
     * @minLength 1
     * @maxLength 255
     */
  name: string;
  /** Description of the test run */
  description?: string;
}

export interface ReplaySessionApi {
  readonly id?: string;
  project: string;
  replay_type: ReplaySessionApiReplayType;
  ids?: ReplaySessionApiIds;
  select_all?: boolean;
  readonly current_step?: ReplaySessionApiCurrentStep;
  agent_definition?: AgentDefinitionNestedApi;
  scenario?: ScenarioNestedApi;
  run_test?: RunTestNestedApi;
}

export type GenerateScenarioApiAgentType = typeof GenerateScenarioApiAgentType[keyof typeof GenerateScenarioApiAgentType];


export const GenerateScenarioApiAgentType = {
  text: 'text',
  voice: 'voice',
} as const;

export type GenerateScenarioApiCustomColumnsItem = {[key: string]: string};

export type GenerateScenarioApiGraph = {[key: string]: string};

export interface GenerateScenarioApi {
  /**
     * @minLength 1
     * @maxLength 255
     */
  agent_name: string;
  agent_description?: string;
  /**
     * @minLength 1
     * @maxLength 255
     */
  scenario_name: string;
  agent_type?: GenerateScenarioApiAgentType;
  /**
     * @minimum 1
     * @maximum 1000
     */
  no_of_rows?: number;
  personas?: string[];
  custom_columns?: GenerateScenarioApiCustomColumnsItem[];
  graph?: GenerateScenarioApiGraph;
  generate_graph?: boolean;
}

export type SavedViewListApiTabType = typeof SavedViewListApiTabType[keyof typeof SavedViewListApiTabType];


export const SavedViewListApiTabType = {
  traces: 'traces',
  spans: 'spans',
  voice: 'voice',
  imagine: 'imagine',
  users: 'users',
  user_detail: 'user_detail',
  sessions: 'sessions',
} as const;

export type SavedViewListApiVisibility = typeof SavedViewListApiVisibility[keyof typeof SavedViewListApiVisibility];


export const SavedViewListApiVisibility = {
  personal: 'personal',
  project: 'project',
} as const;

export type SavedViewListApiConfig = { [key: string]: unknown };

export interface SavedViewCreatorApi {
  readonly id?: string;
  /** @minLength 1 */
  readonly name?: string;
  /** @minLength 1 */
  readonly email?: string;
}

export interface SavedViewListApi {
  readonly id?: string;
  /**
     * @minLength 1
     * @maxLength 255
     */
  name: string;
  tab_type: SavedViewListApiTabType;
  visibility?: SavedViewListApiVisibility;
  /**
     * @minimum -2147483648
     * @maximum 2147483647
     */
  position?: number;
  /** @maxLength 50 */
  icon?: string;
  config?: SavedViewListApiConfig;
  created_by?: SavedViewCreatorApi;
  readonly created_at?: string;
  readonly updated_at?: string;
}

export type SavedViewDetailApiTabType = typeof SavedViewDetailApiTabType[keyof typeof SavedViewDetailApiTabType];


export const SavedViewDetailApiTabType = {
  traces: 'traces',
  spans: 'spans',
  voice: 'voice',
  imagine: 'imagine',
  users: 'users',
  user_detail: 'user_detail',
  sessions: 'sessions',
} as const;

export type SavedViewDetailApiVisibility = typeof SavedViewDetailApiVisibility[keyof typeof SavedViewDetailApiVisibility];


export const SavedViewDetailApiVisibility = {
  personal: 'personal',
  project: 'project',
} as const;

export type SavedViewDetailApiConfig = { [key: string]: unknown };

export interface SavedViewDetailApi {
  readonly id?: string;
  /**
     * @minLength 1
     * @maxLength 255
     */
  name: string;
  tab_type: SavedViewDetailApiTabType;
  visibility?: SavedViewDetailApiVisibility;
  /**
     * @minimum -2147483648
     * @maximum 2147483647
     */
  position?: number;
  /** @maxLength 50 */
  icon?: string;
  config?: SavedViewDetailApiConfig;
  readonly project?: string;
  created_by?: SavedViewCreatorApi;
  updated_by?: SavedViewCreatorApi;
  readonly created_at?: string;
  readonly updated_at?: string;
}

export type SharedLinkListApiResourceType = typeof SharedLinkListApiResourceType[keyof typeof SharedLinkListApiResourceType];


export const SharedLinkListApiResourceType = {
  trace: 'trace',
  dashboard: 'dashboard',
  eval_run: 'eval_run',
  dataset: 'dataset',
  project: 'project',
} as const;

export type SharedLinkListApiAccessType = typeof SharedLinkListApiAccessType[keyof typeof SharedLinkListApiAccessType];


export const SharedLinkListApiAccessType = {
  public: 'public',
  restricted: 'restricted',
} as const;

export interface SharedLinkListApi {
  readonly id?: string;
  readonly resource_type?: SharedLinkListApiResourceType;
  /** @minLength 1 */
  readonly resource_id?: string;
  /** @minLength 1 */
  readonly token?: string;
  readonly access_type?: SharedLinkListApiAccessType;
  readonly is_active?: boolean;
  readonly expires_at?: string;
  readonly created_by?: string;
  readonly created_at?: string;
  readonly access_count?: string;
  readonly share_url?: string;
}

export type SharedLinkDetailApiResourceType = typeof SharedLinkDetailApiResourceType[keyof typeof SharedLinkDetailApiResourceType];


export const SharedLinkDetailApiResourceType = {
  trace: 'trace',
  dashboard: 'dashboard',
  eval_run: 'eval_run',
  dataset: 'dataset',
  project: 'project',
} as const;

export type SharedLinkDetailApiAccessType = typeof SharedLinkDetailApiAccessType[keyof typeof SharedLinkDetailApiAccessType];


export const SharedLinkDetailApiAccessType = {
  public: 'public',
  restricted: 'restricted',
} as const;

export interface SharedLinkAccessApi {
  readonly id?: string;
  /**
     * @minLength 1
     * @maxLength 254
     */
  email: string;
  readonly user?: string;
  readonly granted_by?: string;
  readonly created_at?: string;
}

export interface SharedLinkDetailApi {
  readonly id?: string;
  readonly resource_type?: SharedLinkDetailApiResourceType;
  /** @minLength 1 */
  readonly resource_id?: string;
  /** @minLength 1 */
  readonly token?: string;
  readonly access_type?: SharedLinkDetailApiAccessType;
  readonly is_active?: boolean;
  readonly expires_at?: string;
  readonly created_by?: string;
  readonly created_at?: string;
  readonly access_list?: readonly SharedLinkAccessApi[];
  readonly share_url?: string;
}

export type SharedLinkResolveResponseApiResourceType = typeof SharedLinkResolveResponseApiResourceType[keyof typeof SharedLinkResolveResponseApiResourceType];


export const SharedLinkResolveResponseApiResourceType = {
  trace: 'trace',
  dashboard: 'dashboard',
  eval_run: 'eval_run',
  dataset: 'dataset',
  project: 'project',
} as const;

export type SharedLinkResolveResponseApiAccessType = typeof SharedLinkResolveResponseApiAccessType[keyof typeof SharedLinkResolveResponseApiAccessType];


export const SharedLinkResolveResponseApiAccessType = {
  public: 'public',
  restricted: 'restricted',
} as const;

export type SharedLinkResolveResponseApiData = { [key: string]: unknown };

export interface SharedLinkResolveResponseApi {
  resource_type: SharedLinkResolveResponseApiResourceType;
  /** @minLength 1 */
  resource_id: string;
  access_type: SharedLinkResolveResponseApiAccessType;
  data: SharedLinkResolveResponseApiData;
}

export interface SharedLinkResolveErrorApi {
  /** @minLength 1 */
  error: string;
}

export interface GetTraceAnnotationApi {
  /**
     * @minLength 1
     * @maxLength 255
     */
  observation_span_id?: string;
  trace_id?: string;
  annotators?: string[];
  exclude_annotators?: string[];
}

export type TraceErrorAnalysisResultApiSummary = { [key: string]: unknown };

export type TraceErrorAnalysisResultApiErrorsItem = { [key: string]: unknown };

export type TraceErrorAnalysisResultApiGroupedErrorsItem = { [key: string]: unknown };

export type TraceErrorAnalysisResultApiScores = { [key: string]: unknown };

export type TraceErrorAnalysisResultApiMemoryContext = { [key: string]: unknown };

export interface TraceErrorAnalysisResultApi {
  analysis_exists: boolean;
  /** @minLength 1 */
  trace_id: string;
  /** @minLength 1 */
  message?: string;
  analysis_id?: string;
  analysis_date?: string;
  agent_version?: string;
  memory_enhanced?: boolean;
  summary?: TraceErrorAnalysisResultApiSummary;
  errors?: TraceErrorAnalysisResultApiErrorsItem[];
  grouped_errors?: TraceErrorAnalysisResultApiGroupedErrorsItem[];
  scores?: TraceErrorAnalysisResultApiScores;
  memory_context?: TraceErrorAnalysisResultApiMemoryContext;
}

export interface TraceErrorAnalysisResponseApi {
  status?: boolean;
  result: TraceErrorAnalysisResultApi;
}

export type TraceErrorTaskResponseResultApiStatus = typeof TraceErrorTaskResponseResultApiStatus[keyof typeof TraceErrorTaskResponseResultApiStatus];


export const TraceErrorTaskResponseResultApiStatus = {
  running: 'running',
  waiting: 'waiting',
  paused: 'paused',
} as const;

export interface TraceErrorTaskResponseResultApi {
  project_id: string;
  /** @minLength 1 */
  project_name: string;
  sampling_rate: number;
  status: TraceErrorTaskResponseResultApiStatus;
  is_active?: boolean;
  total_traces_analyzed?: number;
  total_errors_found?: number;
  failed_analyses?: number;
  last_run_at?: string;
  created?: boolean;
}

export interface TraceErrorTaskResponseApi {
  status?: boolean;
  result: TraceErrorTaskResponseResultApi;
}

export type TraceErrorTaskUpdateRequestApiStatus = typeof TraceErrorTaskUpdateRequestApiStatus[keyof typeof TraceErrorTaskUpdateRequestApiStatus];


export const TraceErrorTaskUpdateRequestApiStatus = {
  waiting: 'waiting',
  paused: 'paused',
} as const;

export interface TraceErrorTaskUpdateRequestApi {
  /**
     * @minimum 0
     * @maximum 1
     */
  sampling_rate: number;
  status?: TraceErrorTaskUpdateRequestApiStatus;
}

export type TraceErrorTaskUpdateResultApiStatus = typeof TraceErrorTaskUpdateResultApiStatus[keyof typeof TraceErrorTaskUpdateResultApiStatus];


export const TraceErrorTaskUpdateResultApiStatus = {
  running: 'running',
  waiting: 'waiting',
  paused: 'paused',
} as const;

export interface TraceErrorTaskUpdateResultApi {
  /** @minLength 1 */
  message: string;
  project_id: string;
  /** @minLength 1 */
  project_name: string;
  sampling_rate: number;
  status: TraceErrorTaskUpdateResultApiStatus;
  /** @minLength 1 */
  action: string;
  old_rate: number;
  new_rate: number;
}

export interface TraceErrorTaskUpdateResponseApi {
  status?: boolean;
  result: TraceErrorTaskUpdateResultApi;
}

export interface TraceSessionApi {
  readonly id?: string;
  project: string;
  bookmarked?: boolean;
  /** @maxLength 255 */
  name?: string;
  readonly created_at?: string;
}

export type TraceApiMetadata = { [key: string]: unknown };

export type TraceApiInput = { [key: string]: unknown };

export type TraceApiOutput = { [key: string]: unknown };

export type TraceApiError = { [key: string]: unknown };

export type TraceApiTags = { [key: string]: unknown };

export interface TraceApi {
  readonly id?: string;
  project: string;
  project_version?: string;
  /** @maxLength 2000 */
  name?: string;
  metadata?: TraceApiMetadata;
  input?: TraceApiInput;
  output?: TraceApiOutput;
  error?: TraceApiError;
  session?: string;
  /** @maxLength 255 */
  external_id?: string;
  tags?: TraceApiTags;
}

export interface TraceTagsUpdateApi {
  tags: string[];
}

export type UserAlertMonitorLogApiType = typeof UserAlertMonitorLogApiType[keyof typeof UserAlertMonitorLogApiType];


export const UserAlertMonitorLogApiType = {
  critical: 'critical',
  warning: 'warning',
} as const;

export interface UserAlertMonitorLogApi {
  readonly id?: string;
  resolved_by?: UserApi;
  readonly created_at?: string;
  type: UserAlertMonitorLogApiType;
  /** @minLength 1 */
  message: string;
  resolved?: boolean;
  resolved_at?: string;
  /** @maxLength 200 */
  link?: string;
  time_window_start?: string;
  time_window_end?: string;
}

export type UserAlertMonitorApiMetricType = typeof UserAlertMonitorApiMetricType[keyof typeof UserAlertMonitorApiMetricType];


export const UserAlertMonitorApiMetricType = {
  count_of_errors: 'count_of_errors',
  error_rates_for_function_calling: 'error_rates_for_function_calling',
  error_free_session_rates: 'error_free_session_rates',
  service_provider_error_rates: 'service_provider_error_rates',
  llm_api_failure_rates: 'llm_api_failure_rates',
  span_response_time: 'span_response_time',
  llm_response_time: 'llm_response_time',
  token_usage: 'token_usage',
  daily_tokens_spent: 'daily_tokens_spent',
  monthly_tokens_spent: 'monthly_tokens_spent',
  evaluation_metrics: 'evaluation_metrics',
} as const;

export type UserAlertMonitorApiThresholdOperator = typeof UserAlertMonitorApiThresholdOperator[keyof typeof UserAlertMonitorApiThresholdOperator];


export const UserAlertMonitorApiThresholdOperator = {
  greater_than: 'greater_than',
  less_than: 'less_than',
} as const;

/**
 * Method to set the threshold for the monitor (Static or Percentage change).
 */
export type UserAlertMonitorApiThresholdType = typeof UserAlertMonitorApiThresholdType[keyof typeof UserAlertMonitorApiThresholdType];


export const UserAlertMonitorApiThresholdType = {
  static: 'static',
  percentage_change: 'percentage_change',
} as const;

export type UserAlertMonitorApiFilters = { [key: string]: unknown };

export type UserAlertMonitorApiLogsItem = { [key: string]: unknown };

export interface UserAlertMonitorApi {
  readonly id?: string;
  project: string;
  /** @minLength 1 */
  name: string;
  readonly metric_name?: string;
  readonly created_at?: string;
  readonly updated_at?: string;
  deleted?: boolean;
  deleted_at?: string;
  metric_type: UserAlertMonitorApiMetricType;
  /**
     * Id of the evaluation template.
     * @maxLength 2556
     */
  metric?: string;
  threshold_operator: UserAlertMonitorApiThresholdOperator;
  /** Method to set the threshold for the monitor (Static or Percentage change). */
  threshold_type?: UserAlertMonitorApiThresholdType;
  /**
     * For choice and pass/fail evals, the specific metric value to monitor.
     * @maxLength 255
     */
  threshold_metric_value?: string;
  /** @minimum 0 */
  critical_threshold_value?: number;
  /** @minimum 0 */
  warning_threshold_value?: number;
  /**
     * Frequency of alert checks in minutes.
     * @minimum 5
     * @maximum 2147483647
     */
  alert_frequency?: number;
  /**
     * For auto-thresholding. The time window in minutes to calculate the historical mean
     * @minimum 0
     * @maximum 2147483647
     */
  auto_threshold_time_window?: number;
  /** The last time the monitor was checked for alerts. */
  last_checked_at?: string;
  notification_emails?: string[];
  /** @maxLength 200 */
  slack_webhook_url?: string;
  slack_notes?: string;
  is_mute?: boolean;
  filters?: UserAlertMonitorApiFilters;
  logs?: UserAlertMonitorApiLogsItem[];
  organization: string;
  workspace?: string;
  created_by?: string;
}

export type UsersResultApiTableItem = { [key: string]: unknown };

export interface UsersResultApi {
  table: UsersResultApiTableItem[];
  total_count: number;
  total_pages: number;
}

export interface UsersResponseApi {
  status?: boolean;
  result: UsersResultApi;
}

export interface UserCodeExampleResponseApi {
  status?: boolean;
  /** @minLength 1 */
  result: string;
}

export type OTLPHealthResponseApiStatus = typeof OTLPHealthResponseApiStatus[keyof typeof OTLPHealthResponseApiStatus];


export const OTLPHealthResponseApiStatus = {
  healthy: 'healthy',
} as const;

export interface OTLPHealthResponseApi {
  status: OTLPHealthResponseApiStatus;
  /** @minLength 1 */
  service: string;
}

export interface OTLPPartialSuccessApi {
  rejected_spans?: number;
  error_message?: string;
}

export interface OTLPTraceResponseApi {
  partial_success?: OTLPPartialSuccessApi;
}

export type WebhookRequestApiCall = { [key: string]: unknown };

export interface WebhookRequestApi {
  call: WebhookRequestApiCall;
}

export interface WebhookResponseApi {
  status?: boolean;
  /** @minLength 1 */
  result: string;
}

export type EELicenseGrantApiBand = typeof EELicenseGrantApiBand[keyof typeof EELicenseGrantApiBand];


export const EELicenseGrantApiBand = {
  team: 'team',
  business: 'business',
  enterprise: 'enterprise',
  enterprise_plus: 'enterprise_plus',
} as const;

export interface EELicenseGrantApi {
  id: string;
  /** @minLength 1 */
  customer_name: string;
  band: EELicenseGrantApiBand;
  /** @minLength 1 */
  billing_interval: string;
  features: string[];
  max_traces_monthly?: number;
  max_gateway_monthly?: number;
  issued_at: string;
  expires_at: string;
  /** @minLength 1 */
  status: string;
}

export interface EELicenseListResultApi {
  licenses: EELicenseGrantApi[];
}

export interface EELicenseListResponseApi {
  status: boolean;
  result: EELicenseListResultApi;
}

export type EELicenseCreateRequestApiBand = typeof EELicenseCreateRequestApiBand[keyof typeof EELicenseCreateRequestApiBand];


export const EELicenseCreateRequestApiBand = {
  team: 'team',
  business: 'business',
  enterprise: 'enterprise',
  enterprise_plus: 'enterprise_plus',
} as const;

export type EELicenseCreateRequestApiBillingInterval = typeof EELicenseCreateRequestApiBillingInterval[keyof typeof EELicenseCreateRequestApiBillingInterval];


export const EELicenseCreateRequestApiBillingInterval = {
  monthly: 'monthly',
  yearly: 'yearly',
} as const;

export interface EELicenseCreateRequestApi {
  band: EELicenseCreateRequestApiBand;
  customer_name?: string;
  billing_interval?: EELicenseCreateRequestApiBillingInterval;
}

export interface EELicenseCreateResultApi {
  grant_id: string;
  /** @minLength 1 */
  jwt_key: string;
  /** @minLength 1 */
  key_hash: string;
  /** @minLength 1 */
  band: string;
  expires_at: string;
  features: string[];
}

export interface EELicenseCreateResponseApi {
  status: boolean;
  result: EELicenseCreateResultApi;
}

export interface EELicenseRevokeRequestApi {
  reason?: string;
}

export interface EELicenseRevokeResultApi {
  revoked: boolean;
  grant_id: string;
}

export interface EELicenseRevokeResponseApi {
  status: boolean;
  result: EELicenseRevokeResultApi;
}

export type AgentPlaygroundGraphsListParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type AgentPlaygroundGraphsList200 = {
  count: number;
  next?: string;
  previous?: string;
  results: GraphListApi[];
};

export type AgentPlaygroundGraphsExecutionsListParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type AgentPlaygroundGraphsVersionsReadParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type AgentPlaygroundGraphsVersionsRead200 = {
  count: number;
  next?: string;
  previous?: string;
  results: GraphListApi[];
};

export type AgentPlaygroundGraphsVersionsReadParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type AgentPlaygroundGraphsVersionsRead200 = {
  count: number;
  next?: string;
  previous?: string;
  results: GraphListApi[];
};

export type AgentPlaygroundGraphsVersionsNodesPossibleEdgeMappingsParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type AgentPlaygroundGraphsVersionsNodesPossibleEdgeMappings200 = {
  count: number;
  next?: string;
  previous?: string;
  results: NodeReadApi[];
};

export type AgentPlaygroundNodeTemplatesListParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type AgentPlaygroundNodeTemplatesList200 = {
  count: number;
  next?: string;
  previous?: string;
  results: NodeTemplateListApi[];
};

export type AgentccAnalyticsCostBreakdownParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type AgentccAnalyticsCostBreakdown200 = {
  count: number;
  next?: string;
  previous?: string;
  results: AgentccRequestLogApi[];
};

export type AgentccAnalyticsErrorBreakdownParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type AgentccAnalyticsErrorBreakdown200 = {
  count: number;
  next?: string;
  previous?: string;
  results: AgentccRequestLogApi[];
};

export type AgentccAnalyticsGuardrailOverviewParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type AgentccAnalyticsGuardrailOverview200 = {
  count: number;
  next?: string;
  previous?: string;
  results: AgentccRequestLogApi[];
};

export type AgentccAnalyticsGuardrailRulesParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type AgentccAnalyticsGuardrailRules200 = {
  count: number;
  next?: string;
  previous?: string;
  results: AgentccRequestLogApi[];
};

export type AgentccAnalyticsGuardrailTrendsParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type AgentccAnalyticsGuardrailTrends200 = {
  count: number;
  next?: string;
  previous?: string;
  results: AgentccRequestLogApi[];
};

export type AgentccAnalyticsLatencyStatsParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type AgentccAnalyticsLatencyStats200 = {
  count: number;
  next?: string;
  previous?: string;
  results: AgentccRequestLogApi[];
};

export type AgentccAnalyticsModelComparisonParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type AgentccAnalyticsModelComparison200 = {
  count: number;
  next?: string;
  previous?: string;
  results: AgentccRequestLogApi[];
};

export type AgentccAnalyticsOverviewParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type AgentccAnalyticsOverview200 = {
  count: number;
  next?: string;
  previous?: string;
  results: AgentccRequestLogApi[];
};

export type AgentccAnalyticsUsageTimeseriesParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type AgentccAnalyticsUsageTimeseries200 = {
  count: number;
  next?: string;
  previous?: string;
  results: AgentccRequestLogApi[];
};

export type AgentccApiKeysListParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type AgentccApiKeysList200 = {
  count: number;
  next?: string;
  previous?: string;
  results: AgentccAPIKeyApi[];
};

export type AgentccBlocklistsListParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type AgentccBlocklistsList200 = {
  count: number;
  next?: string;
  previous?: string;
  results: AgentccBlocklistApi[];
};

export type AgentccCustomPropertiesListParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type AgentccCustomPropertiesList200 = {
  count: number;
  next?: string;
  previous?: string;
  results: AgentccCustomPropertySchemaApi[];
};

export type AgentccEmailAlertsListParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type AgentccEmailAlertsList200 = {
  count: number;
  next?: string;
  previous?: string;
  results: AgentccEmailAlertApi[];
};

export type AgentccGuardrailConfigsPiiEntitiesParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type AgentccGuardrailConfigsTopicsParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type AgentccGuardrailFeedbackListParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type AgentccGuardrailFeedbackList200 = {
  count: number;
  next?: string;
  previous?: string;
  results: AgentccGuardrailFeedbackApi[];
};

export type AgentccGuardrailFeedbackSummaryParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type AgentccGuardrailFeedbackSummary200 = {
  count: number;
  next?: string;
  previous?: string;
  results: AgentccGuardrailFeedbackApi[];
};

export type AgentccGuardrailPoliciesListParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type AgentccGuardrailPoliciesList200 = {
  count: number;
  next?: string;
  previous?: string;
  results: AgentccGuardrailPolicyApi[];
};

export type AgentccOrgConfigsListParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type AgentccOrgConfigsList200 = {
  count: number;
  next?: string;
  previous?: string;
  results: AgentccOrgConfigApi[];
};

export type AgentccOrgConfigsActiveParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type AgentccOrgConfigsActive200 = {
  count: number;
  next?: string;
  previous?: string;
  results: AgentccOrgConfigApi[];
};

export type AgentccProviderCredentialsListParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type AgentccProviderCredentialsList200 = {
  count: number;
  next?: string;
  previous?: string;
  results: AgentccProviderCredentialApi[];
};

export type AgentccRequestLogsListParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type AgentccRequestLogsList200 = {
  count: number;
  next?: string;
  previous?: string;
  results: AgentccRequestLogApi[];
};

export type AgentccRequestLogsExportParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type AgentccRequestLogsExport200 = {
  count: number;
  next?: string;
  previous?: string;
  results: AgentccRequestLogApi[];
};

export type AgentccRequestLogsSearchParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type AgentccRequestLogsSearch200 = {
  count: number;
  next?: string;
  previous?: string;
  results: AgentccRequestLogApi[];
};

export type AgentccRequestLogsSessionsParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type AgentccRequestLogsSessions200 = {
  count: number;
  next?: string;
  previous?: string;
  results: AgentccRequestLogApi[];
};

export type AgentccRequestLogsSessionDetailParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type AgentccRequestLogsSessionDetail200 = {
  count: number;
  next?: string;
  previous?: string;
  results: AgentccRequestLogApi[];
};

export type AgentccRoutingPoliciesListParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type AgentccRoutingPoliciesList200 = {
  count: number;
  next?: string;
  previous?: string;
  results: AgentccRoutingPolicyApi[];
};

export type AgentccSessionsListParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type AgentccSessionsList200 = {
  count: number;
  next?: string;
  previous?: string;
  results: AgentccSessionApi[];
};

export type AgentccShadowExperimentsListParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type AgentccShadowExperimentsList200 = {
  count: number;
  next?: string;
  previous?: string;
  results: AgentccShadowExperimentApi[];
};

export type AgentccShadowResultsListParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type AgentccShadowResultsList200 = {
  count: number;
  next?: string;
  previous?: string;
  results: AgentccShadowResultApi[];
};

export type AgentccWebhookEventsListParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type AgentccWebhookEventsList200 = {
  count: number;
  next?: string;
  previous?: string;
  results: AgentccWebhookEventApi[];
};

export type AgentccWebhooksListParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type AgentccWebhooksList200 = {
  count: number;
  next?: string;
  previous?: string;
  results: AgentccWebhookApi[];
};

/**
 * Legacy OTLP JSON/protobuf trace payload. Prefer /tracer/v1/traces for new integrations.
 */
export type ApiPublicOtelV1TracesCreateBodyOne = { [key: string]: unknown };

/**
 * Legacy OTLP JSON/protobuf trace payload. Prefer /tracer/v1/traces for new integrations.
 */
export type ApiPublicOtelV1TracesCreateBodyTwo = { [key: string]: unknown };

export type ApiTracesSpanAttributeDetailListParams = {
project_id: string;
/**
 * @minLength 1
 */
key: string;
};

export type ApiTracesSpanAttributeKeysListParams = {
project_id: string;
};

export type ApiTracesSpanAttributeValuesListParams = {
project_id: string;
/**
 * @minLength 1
 */
key: string;
q?: string;
/**
 * @minimum 1
 * @maximum 500
 */
limit?: number;
};

export type IntegrationsConnectionsListParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type IntegrationsConnectionsList200 = {
  count: number;
  next?: string;
  previous?: string;
  results: IntegrationConnectionListApi[];
};

export type IntegrationsSyncLogsListParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type IntegrationsSyncLogsList200 = {
  count: number;
  next?: string;
  previous?: string;
  results: SyncLogApi[];
};

export type ModelHubAnnotationQueuesListParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
status?: string;
search?: string;
include_counts?: boolean;
};

export type ModelHubAnnotationQueuesList200 = {
  count: number;
  next?: string;
  previous?: string;
  results: AnnotationQueueApi[];
};

export type ModelHubAnnotationQueuesForSourceParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
source_type?: string;
source_id?: string;
sources?: string;
};

export type ModelHubAnnotationQueuesExportAnnotationsParams = {
export_format?: ModelHubAnnotationQueuesExportAnnotationsExportFormat;
format?: ModelHubAnnotationQueuesExportAnnotationsFormat;
status?: string;
};

export type ModelHubAnnotationQueuesExportAnnotationsExportFormat = typeof ModelHubAnnotationQueuesExportAnnotationsExportFormat[keyof typeof ModelHubAnnotationQueuesExportAnnotationsExportFormat];


export const ModelHubAnnotationQueuesExportAnnotationsExportFormat = {
  json: 'json',
  csv: 'csv',
} as const;

export type ModelHubAnnotationQueuesExportAnnotationsFormat = typeof ModelHubAnnotationQueuesExportAnnotationsFormat[keyof typeof ModelHubAnnotationQueuesExportAnnotationsFormat];


export const ModelHubAnnotationQueuesExportAnnotationsFormat = {
  json: 'json',
  csv: 'csv',
} as const;

export type ModelHubAnnotationQueuesAutomationRulesListParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type ModelHubAnnotationQueuesAutomationRulesList200 = {
  count: number;
  next?: string;
  previous?: string;
  results: AutomationRuleApi[];
};

export type ModelHubAnnotationQueuesItemsListParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
status?: string;
source_type?: string;
assigned_to?: string;
review_status?: string;
ordering?: ModelHubAnnotationQueuesItemsListOrdering;
};

export type ModelHubAnnotationQueuesItemsListOrdering = typeof ModelHubAnnotationQueuesItemsListOrdering[keyof typeof ModelHubAnnotationQueuesItemsListOrdering];


export const ModelHubAnnotationQueuesItemsListOrdering = {
  created_at: 'created_at',
  '-created_at': '-created_at',
} as const;

export type ModelHubAnnotationQueuesItemsList200 = {
  count: number;
  next?: string;
  previous?: string;
  results: QueueItemApi[];
};

export type ModelHubAnnotationQueuesItemsNextItemParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
exclude?: string;
before?: string;
review_status?: string;
exclude_review_status?: string;
include_completed?: boolean;
view_mode?: string;
};

export type ModelHubAnnotationQueuesItemsAnnotateDetailParams = {
annotator_id?: string;
include_completed?: boolean;
view_mode?: string;
mode?: string;
review_status?: string;
exclude_review_status?: string;
include_all_annotations?: boolean;
};

export type ModelHubAnnotationTasksListParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type ModelHubAnnotationTasksList200 = {
  count: number;
  next?: string;
  previous?: string;
  results: AnnotationTaskApi[];
};

export type ModelHubAnnotationsLabelsListParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type ModelHubAnnotationsLabelsList200 = {
  count: number;
  next?: string;
  previous?: string;
  results: AnnotationsLabelsApi[];
};

export type ModelHubAnnotationsListParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type ModelHubAnnotationsList200 = {
  count: number;
  next?: string;
  previous?: string;
  results: AnnotationsApi[];
};

export type ModelHubApiKeysListParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type ModelHubApiKeysList200 = {
  count: number;
  next?: string;
  previous?: string;
  results: ApiKeyApi[];
};

export type ModelHubDatasetOptimizationListParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type ModelHubDatasetOptimizationList200 = {
  count: number;
  next?: string;
  previous?: string;
  results: DatasetOptimizationListApi[];
};

export type ModelHubEvalGroupsListParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type ModelHubEvalGroupsList200 = {
  count: number;
  next?: string;
  previous?: string;
  results: EvalGroupApi[];
};

export type ModelHubExperimentDetailListParams = {
/**
 * A search term.
 */
search?: string;
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type ModelHubExperimentDetailList200 = {
  count: number;
  next?: string;
  previous?: string;
  results: ExperimentsTableGetApi[];
};

export type ModelHubExperimentsDataListParams = {
created_at?: string;
status?: string;
dataset_id?: string;
/**
 * Which field to use when ordering the results.
 */
ordering?: string;
/**
 * A search term.
 */
search?: string;
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type ModelHubExperimentsDataList200 = {
  count: number;
  next?: string;
  previous?: string;
  results: ExperimentListApi[];
};

export type ModelHubExperimentsV2ListListParams = {
created_at?: string;
status?: string;
dataset_id?: string;
/**
 * A search term.
 */
search?: string;
/**
 * Which field to use when ordering the results.
 */
ordering?: string;
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type ModelHubExperimentsV2ListList200 = {
  count: number;
  next?: string;
  previous?: string;
  results: ExperimentListV2Api[];
};

export type ModelHubFeedbackListParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type ModelHubFeedbackList200 = {
  count: number;
  next?: string;
  previous?: string;
  results: FeedbackApi[];
};

export type ModelHubFeedbackGetFeedbackDetailsParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type ModelHubFeedbackGetFeedbackDetails200 = {
  count: number;
  next?: string;
  previous?: string;
  results: FeedbackApi[];
};

export type ModelHubFeedbackGetFeedbackSummaryParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type ModelHubFeedbackGetFeedbackSummary200 = {
  count: number;
  next?: string;
  previous?: string;
  results: FeedbackApi[];
};

export type ModelHubFeedbackGetTemplateParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type ModelHubFeedbackGetTemplate200 = {
  count: number;
  next?: string;
  previous?: string;
  results: FeedbackApi[];
};

export type ModelHubKbListParams = {
/**
 * A search term.
 */
search?: string;
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type ModelHubKbSupportedEmbeddingModelsParams = {
/**
 * A search term.
 */
search?: string;
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type ModelHubKbSupportedEmbeddingModelsParams = {
/**
 * A search term.
 */
search?: string;
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type ModelHubOptimisationListParams = {
optimize_type?: string;
status?: string;
/**
 * A search term.
 */
search?: string;
/**
 * Which field to use when ordering the results.
 */
ordering?: string;
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type ModelHubOptimisationList200 = {
  count: number;
  next?: string;
  previous?: string;
  results: OptimizationDatasetApi[];
};

export type ModelHubOptimizeDatasetListParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type ModelHubOptimizeDatasetList200 = {
  count: number;
  next?: string;
  previous?: string;
  results: OptimizeDatasetKbApi[];
};

export type ModelHubOrganizationsUsersListParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type ModelHubOrganizationsUsersList200 = {
  count: number;
  next?: string;
  previous?: string;
  results: DevelopAnnotationsUserApi[];
};

export type ModelHubPromptBaseTemplatesListParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type ModelHubPromptBaseTemplatesList200 = {
  count: number;
  next?: string;
  previous?: string;
  results: PromptBaseTemplateApi[];
};

export type ModelHubPromptBaseTemplatesGetAllCategoriesParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type ModelHubPromptBaseTemplatesGetAllCategories200 = {
  count: number;
  next?: string;
  previous?: string;
  results: PromptBaseTemplateApi[];
};

export type ModelHubPromptExecutionsListParams = {
name?: string;
/**
 * A search term.
 */
search?: string;
/**
 * Which field to use when ordering the results.
 */
ordering?: string;
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type ModelHubPromptExecutionsList200 = {
  count: number;
  next?: string;
  previous?: string;
  results: PromptExecutionApi[];
};

export type ModelHubPromptFoldersListParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type ModelHubPromptFoldersList200 = {
  count: number;
  next?: string;
  previous?: string;
  results: PromptFolderApi[];
};

export type ModelHubPromptHistoryExecutionsListParams = {
template_name?: string;
template_version?: string;
created_at?: string;
/**
 * A search term.
 */
search?: string;
/**
 * Which field to use when ordering the results.
 */
ordering?: string;
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type ModelHubPromptHistoryExecutionsList200 = {
  count: number;
  next?: string;
  previous?: string;
  results: PromptHistoryExecutionApi[];
};

export type ModelHubPromptHistoryExecutionsGetExecutionDetailsParams = {
template_name?: string;
template_version?: string;
created_at?: string;
/**
 * A search term.
 */
search?: string;
/**
 * Which field to use when ordering the results.
 */
ordering?: string;
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type ModelHubPromptHistoryExecutionsGetExecutionDetails200 = {
  count: number;
  next?: string;
  previous?: string;
  results: PromptHistoryExecutionApi[];
};

export type ModelHubPromptLabelsListParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type ModelHubPromptLabelsList200 = {
  count: number;
  next?: string;
  previous?: string;
  results: PromptLabelApi[];
};

export type ModelHubPromptLabelsGetByNameParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type ModelHubPromptLabelsGetByName200 = {
  count: number;
  next?: string;
  previous?: string;
  results: PromptLabelApi[];
};

export type ModelHubPromptLabelsTemplateLabelsParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type ModelHubPromptLabelsTemplateLabels200 = {
  count: number;
  next?: string;
  previous?: string;
  results: PromptLabelApi[];
};

export type ModelHubPromptTemplatesListParams = {
name?: string;
version?: string;
created_at?: string;
/**
 * A search term.
 */
search?: string;
/**
 * Which field to use when ordering the results.
 */
ordering?: string;
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type ModelHubPromptTemplatesList200 = {
  count: number;
  next?: string;
  previous?: string;
  results: PromptTemplateApi[];
};

export type ModelHubPromptTemplatesGetTemplateByNameParams = {
name?: string;
version?: string;
created_at?: string;
/**
 * A search term.
 */
search?: string;
/**
 * Which field to use when ordering the results.
 */
ordering?: string;
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type ModelHubPromptTemplatesGetTemplateByName200 = {
  count: number;
  next?: string;
  previous?: string;
  results: PromptTemplateApi[];
};

export type ModelHubResponseSchemaListParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type ModelHubResponseSchemaList200 = {
  count: number;
  next?: string;
  previous?: string;
  results: UserResponseSchemaApi[];
};

export type ModelHubScoresListParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
source_type?: ModelHubScoresListSourceType;
source_id?: string;
label_id?: string;
annotator_id?: string;
};

export type ModelHubScoresListSourceType = typeof ModelHubScoresListSourceType[keyof typeof ModelHubScoresListSourceType];


export const ModelHubScoresListSourceType = {
  dataset_row: 'dataset_row',
  trace: 'trace',
  observation_span: 'observation_span',
  prototype_run: 'prototype_run',
  call_execution: 'call_execution',
  trace_session: 'trace_session',
} as const;

export type ModelHubScoresList200 = {
  count: number;
  next?: string;
  previous?: string;
  results: ScoreApi[];
};

export type ModelHubScoresForSourceParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
source_type: ModelHubScoresForSourceSourceType;
/**
 * @minLength 1
 */
source_id: string;
};

export type ModelHubScoresForSourceSourceType = typeof ModelHubScoresForSourceSourceType[keyof typeof ModelHubScoresForSourceSourceType];


export const ModelHubScoresForSourceSourceType = {
  dataset_row: 'dataset_row',
  trace: 'trace',
  observation_span: 'observation_span',
  prototype_run: 'prototype_run',
  call_execution: 'call_execution',
  trace_session: 'trace_session',
} as const;

export type ModelHubSecretsListParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type ModelHubSecretsList200 = {
  count: number;
  next?: string;
  previous?: string;
  results: SecretApi[];
};

export type ModelHubToolsListParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type ModelHubToolsList200 = {
  count: number;
  next?: string;
  previous?: string;
  results: ToolsApi[];
};

export type ModelHubTtsVoicesListParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type ModelHubTtsVoicesList200 = {
  count: number;
  next?: string;
  previous?: string;
  results: TTSVoiceApi[];
};

export type Saml2AuthIdpUploadsListParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type Saml2AuthIdpUploadsList200 = {
  count: number;
  next?: string;
  previous?: string;
  results: SamlApi[];
};

export type SimulateAgentDefinitionsListParams = {
search?: string;
agent_type?: SimulateAgentDefinitionsListAgentType;
agent_definition_id?: string;
/**
 * @minimum 1
 */
page?: number;
/**
 * @minimum 1
 */
limit?: number;
};

export type SimulateAgentDefinitionsListAgentType = typeof SimulateAgentDefinitionsListAgentType[keyof typeof SimulateAgentDefinitionsListAgentType];


export const SimulateAgentDefinitionsListAgentType = {
  voice: 'voice',
  text: 'text',
} as const;

export type SimulateApiAgentDefinitionOperationsListParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type SimulateApiAgentDefinitionOperationsList200 = {
  count: number;
  next?: string;
  previous?: string;
  results: AgentDefinitionResponseApi[];
};

export type SimulateApiAgentPromptOptimiserListParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type SimulateApiAgentPromptOptimiserList200 = {
  count: number;
  next?: string;
  previous?: string;
  results: AgentPromptOptimiserRunListApi[];
};

export type SimulateApiCallExecutionsListParams = {
search?: string;
status?: string;
test_execution_id?: string;
/**
 * @minimum 1
 */
page?: number;
/**
 * @minimum 1
 */
limit?: number;
};

export type SimulateApiLivekitCallConfigRead200 = { [key: string]: unknown };

export type SimulateApiLivekitPhoneResolutionRead200 = { [key: string]: unknown };

export type SimulateApiLivekitTranscriptsCreate201 = {[key: string]: { [key: string]: unknown }};

/**
 * LiveKit webhook payload verified against the Authorization JWT.
 */
export type SimulateApiLivekitWebhookCreateBody = { [key: string]: unknown };

export type SimulateApiPersonasListParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type SimulateApiPersonasList200 = {
  count: number;
  next?: string;
  previous?: string;
  results: PersonaListApi[];
};

export type SimulateApiPersonasFieldOptionsParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type SimulateApiPersonasFieldOptions200 = {
  count: number;
  next?: string;
  previous?: string;
  results: PersonaFieldOptionsApi[];
};

export type SimulateApiPersonasSystemPersonasParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type SimulateApiPersonasSystemPersonas200 = {
  count: number;
  next?: string;
  previous?: string;
  results: PersonaApi[];
};

export type SimulateApiPersonasWorkspacePersonasParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type SimulateApiPersonasWorkspacePersonas200 = {
  count: number;
  next?: string;
  previous?: string;
  results: PersonaApi[];
};

export type SimulateApiRunTestsListParams = {
search?: string;
simulation_type?: SimulateApiRunTestsListSimulationType;
prompt_template_id?: string;
/**
 * @minimum 1
 */
page?: number;
/**
 * @minimum 1
 */
limit?: number;
};

export type SimulateApiRunTestsListSimulationType = typeof SimulateApiRunTestsListSimulationType[keyof typeof SimulateApiRunTestsListSimulationType];


export const SimulateApiRunTestsListSimulationType = {
  agent_definition: 'agent_definition',
  prompt: 'prompt',
} as const;

export type SimulateExportReadParams = {
/**
 * Export source type.
 */
type: SimulateExportReadType;
/**
 * Optional call-execution search term.
 */
search?: string;
/**
 * Optional call-execution status filter.
 */
status?: string;
};

export type SimulateExportReadType = typeof SimulateExportReadType[keyof typeof SimulateExportReadType];


export const SimulateExportReadType = {
  runtest: 'runtest',
  testexecution: 'testexecution',
} as const;

export type SimulateRunTestsListParams = {
search?: string;
simulation_type?: SimulateRunTestsListSimulationType;
prompt_template_id?: string;
/**
 * @minimum 1
 */
page?: number;
/**
 * @minimum 1
 */
limit?: number;
};

export type SimulateRunTestsListSimulationType = typeof SimulateRunTestsListSimulationType[keyof typeof SimulateRunTestsListSimulationType];


export const SimulateRunTestsListSimulationType = {
  agent_definition: 'agent_definition',
  prompt: 'prompt',
} as const;

export type SimulateRunTestsEvalSummaryComparisonListParams = {
/**
 * JSON-encoded array of test execution UUIDs to compare. Example: ["uuid1","uuid2"]. Must be URL-encoded.
 * @minLength 1
 */
execution_ids: string;
};

export type SimulateRunTestsEvalSummaryListParams = {
/**
 * UUID of a specific test execution to scope the summary to. If omitted, aggregates across all executions.
 */
execution_id?: string;
};

export type SimulateScenariosListParams = {
search?: string;
agent_definition_id?: string;
/**
 * @minLength 1
 */
agent_type?: string;
/**
 * @minimum 1
 */
page?: number;
/**
 * @minimum 1
 */
limit?: number;
};

export type SimulateScenariosGetColumnsListParams = {
search?: string;
agent_definition_id?: string;
/**
 * @minLength 1
 */
agent_type?: string;
/**
 * @minimum 1
 */
page?: number;
/**
 * @minimum 1
 */
limit?: number;
};

export type SimulateTestExecutionsRead200 = { [key: string]: unknown };

export type SimulateTestExecutionsEvalExplanationSummaryList200 = { [key: string]: unknown };

export type SimulateTestExecutionsKpisList200 = { [key: string]: unknown };

export type TracerChartsListParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type TracerChartsList200 = {
  count: number;
  next?: string;
  previous?: string;
  results: FetchGraphApi[];
};

export type TracerChartsFetchGraphParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type TracerChartsFetchGraph200 = {
  count: number;
  next?: string;
  previous?: string;
  results: FetchGraphApi[];
};

export type TracerCustomEvalConfigListParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type TracerCustomEvalConfigList200 = {
  count: number;
  next?: string;
  previous?: string;
  results: CustomEvalConfigApi[];
};

export type TracerCustomEvalConfigListCustomEvalConfigsParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type TracerCustomEvalConfigListCustomEvalConfigs200 = {
  count: number;
  next?: string;
  previous?: string;
  results: CustomEvalConfigApi[];
};

export type TracerDashboardListParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type TracerDashboardList200 = {
  count: number;
  next?: string;
  previous?: string;
  results: DashboardApi[];
};

export type TracerDashboardFilterValuesParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type TracerDashboardFilterValues200 = {
  count: number;
  next?: string;
  previous?: string;
  results: DashboardApi[];
};

export type TracerDashboardMetricsParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type TracerDashboardMetrics200 = {
  count: number;
  next?: string;
  previous?: string;
  results: DashboardApi[];
};

export type TracerDashboardSimulationAgentsParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type TracerDashboardSimulationAgents200 = {
  count: number;
  next?: string;
  previous?: string;
  results: DashboardApi[];
};

export type TracerDashboardWidgetsListParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type TracerDashboardWidgetsList200 = {
  count: number;
  next?: string;
  previous?: string;
  results: DashboardWidgetApi[];
};

export type TracerDatasetListParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type TracerDatasetList200 = {
  count: number;
  next?: string;
  previous?: string;
  results: DatasetApi[];
};

export type TracerEvalTaskListParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type TracerEvalTaskList200 = {
  count: number;
  next?: string;
  previous?: string;
  results: EvalTaskApi[];
};

export type TracerEvalTaskGetEvalDetailsParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type TracerEvalTaskGetEvalDetails200 = {
  count: number;
  next?: string;
  previous?: string;
  results: EvalTaskApi[];
};

export type TracerEvalTaskGetEvalTaskLogsParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type TracerEvalTaskGetEvalTaskLogs200 = {
  count: number;
  next?: string;
  previous?: string;
  results: EvalTaskApi[];
};

export type TracerEvalTaskGetUsageParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type TracerEvalTaskGetUsage200 = {
  count: number;
  next?: string;
  previous?: string;
  results: EvalTaskApi[];
};

export type TracerEvalTaskListEvalTasksParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type TracerEvalTaskListEvalTasks200 = {
  count: number;
  next?: string;
  previous?: string;
  results: EvalTaskApi[];
};

export type TracerEvalTaskListEvalTasksWithProjectNameParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type TracerEvalTaskListEvalTasksWithProjectName200 = {
  count: number;
  next?: string;
  previous?: string;
  results: EvalTaskApi[];
};

export type TracerFeedIssuesListParams = {
project_id?: string;
search?: string;
status?: TracerFeedIssuesListStatus;
fix_layer?: string;
source?: TracerFeedIssuesListSource;
issue_group?: string;
/**
 * @minimum 1
 */
time_range_days?: number;
sort_by?: TracerFeedIssuesListSortBy;
sort_dir?: TracerFeedIssuesListSortDir;
/**
 * @minimum 1
 * @maximum 200
 */
limit?: number;
/**
 * @minimum 0
 */
offset?: number;
};

export type TracerFeedIssuesListStatus = typeof TracerFeedIssuesListStatus[keyof typeof TracerFeedIssuesListStatus];


export const TracerFeedIssuesListStatus = {
  escalating: 'escalating',
  for_review: 'for_review',
  acknowledged: 'acknowledged',
  resolved: 'resolved',
} as const;

export type TracerFeedIssuesListSource = typeof TracerFeedIssuesListSource[keyof typeof TracerFeedIssuesListSource];


export const TracerFeedIssuesListSource = {
  scanner: 'scanner',
  eval: 'eval',
} as const;

export type TracerFeedIssuesListSortBy = typeof TracerFeedIssuesListSortBy[keyof typeof TracerFeedIssuesListSortBy];


export const TracerFeedIssuesListSortBy = {
  last_seen: 'last_seen',
  first_seen: 'first_seen',
  error_count: 'error_count',
  unique_traces: 'unique_traces',
} as const;

export type TracerFeedIssuesListSortDir = typeof TracerFeedIssuesListSortDir[keyof typeof TracerFeedIssuesListSortDir];


export const TracerFeedIssuesListSortDir = {
  asc: 'asc',
  desc: 'desc',
} as const;

export type TracerFeedIssuesStatsListParams = {
project_id?: string;
/**
 * @minimum 1
 */
time_range_days?: number;
};

export type TracerFeedIssuesReadParams = {
project_id?: string;
};

export type TracerFeedIssuesRootCauseListParams = {
/**
 * @minLength 1
 */
trace_id: string;
};

export type TracerFeedIssuesSidebarListParams = {
/**
 * @minLength 1
 */
trace_id?: string;
};

export type TracerFeedIssuesTracesListParams = {
/**
 * @minimum 1
 * @maximum 500
 */
limit?: number;
/**
 * @minimum 0
 */
offset?: number;
};

export type TracerFeedIssuesTrendsListParams = {
/**
 * @minimum 1
 * @maximum 90
 */
days?: number;
};

export type TracerImagineAnalysisListParams = {
saved_view_id: string;
/**
 * @minLength 1
 * @maxLength 255
 */
trace_id: string;
};

export type TracerObservabilityProviderListParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type TracerObservabilityProviderList200 = {
  count: number;
  next?: string;
  previous?: string;
  results: ObservabilityProviderApi[];
};

export type TracerObservationSpanListParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type TracerObservationSpanList200 = {
  count: number;
  next?: string;
  previous?: string;
  results: ObservationSpanApi[];
};

export type TracerObservationSpanGetEvalAttributesListParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type TracerObservationSpanGetEvalAttributesList200 = {
  count: number;
  next?: string;
  previous?: string;
  results: ObservationSpanApi[];
};

export type TracerObservationSpanGetEvaluationDetailsParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type TracerObservationSpanGetEvaluationDetails200 = {
  count: number;
  next?: string;
  previous?: string;
  results: ObservationSpanApi[];
};

export type TracerObservationSpanGetObservationSpanFieldsParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type TracerObservationSpanGetObservationSpanFields200 = {
  count: number;
  next?: string;
  previous?: string;
  results: ObservationSpanApi[];
};

export type TracerObservationSpanGetSpanAttributesListParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type TracerObservationSpanGetSpanAttributesList200 = {
  count: number;
  next?: string;
  previous?: string;
  results: ObservationSpanApi[];
};

export type TracerObservationSpanGetSpansExportDataParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type TracerObservationSpanGetSpansExportData200 = {
  count: number;
  next?: string;
  previous?: string;
  results: ObservationSpanApi[];
};

export type TracerObservationSpanGetTraceIdByIndexSpansAsBaseParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type TracerObservationSpanGetTraceIdByIndexSpansAsBase200 = {
  count: number;
  next?: string;
  previous?: string;
  results: ObservationSpanApi[];
};

export type TracerObservationSpanGetTraceIdByIndexSpansAsObserveParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type TracerObservationSpanGetTraceIdByIndexSpansAsObserve200 = {
  count: number;
  next?: string;
  previous?: string;
  results: ObservationSpanApi[];
};

export type TracerObservationSpanListSpansParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type TracerObservationSpanListSpans200 = {
  count: number;
  next?: string;
  previous?: string;
  results: ObservationSpanApi[];
};

export type TracerObservationSpanListSpansObserveParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type TracerObservationSpanListSpansObserve200 = {
  count: number;
  next?: string;
  previous?: string;
  results: ObservationSpanApi[];
};

export type TracerObservationSpanRetrieveLoadingParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type TracerObservationSpanRetrieveLoading200 = {
  count: number;
  next?: string;
  previous?: string;
  results: ObservationSpanApi[];
};

export type TracerObservationSpanRootSpansParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type TracerObservationSpanRootSpans200 = {
  count: number;
  next?: string;
  previous?: string;
  results: ObservationSpanApi[];
};

/**
 * Legacy OTLP JSON/protobuf trace payload. Prefer /tracer/v1/traces for new integrations.
 */
export type TracerOtlpV1TracesCreateBodyOne = { [key: string]: unknown };

/**
 * Legacy OTLP JSON/protobuf trace payload. Prefer /tracer/v1/traces for new integrations.
 */
export type TracerOtlpV1TracesCreateBodyTwo = { [key: string]: unknown };

export type TracerProjectVersionListParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type TracerProjectVersionList200 = {
  count: number;
  next?: string;
  previous?: string;
  results: ProjectVersionApi[];
};

export type TracerProjectVersionGetProjectVersionIdsParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type TracerProjectVersionGetProjectVersionIds200 = {
  count: number;
  next?: string;
  previous?: string;
  results: ProjectVersionApi[];
};

export type TracerProjectVersionGetRunInsightsParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type TracerProjectVersionGetRunInsights200 = {
  count: number;
  next?: string;
  previous?: string;
  results: ProjectVersionApi[];
};

export type TracerProjectVersionListRunsParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type TracerProjectVersionListRuns200 = {
  count: number;
  next?: string;
  previous?: string;
  results: ProjectVersionApi[];
};

export type TracerProjectListParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type TracerProjectList200 = {
  count: number;
  next?: string;
  previous?: string;
  results: ProjectApi[];
};

export type TracerProjectFetchSystemMetricsParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type TracerProjectFetchSystemMetrics200 = {
  count: number;
  next?: string;
  previous?: string;
  results: ProjectApi[];
};

export type TracerProjectGetGraphDataParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type TracerProjectGetGraphData200 = {
  count: number;
  next?: string;
  previous?: string;
  results: ProjectApi[];
};

export type TracerProjectListProjectIdsParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type TracerProjectListProjectIds200 = {
  count: number;
  next?: string;
  previous?: string;
  results: ProjectApi[];
};

export type TracerProjectListProjectsParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type TracerProjectListProjects200 = {
  count: number;
  next?: string;
  previous?: string;
  results: ProjectApi[];
};

export type TracerProjectProjectSdkCodeParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type TracerProjectProjectSdkCode200 = {
  count: number;
  next?: string;
  previous?: string;
  results: ProjectApi[];
};

export type TracerSavedViewsListParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type TracerSavedViewsList200 = {
  count: number;
  next?: string;
  previous?: string;
  results: SavedViewListApi[];
};

export type TracerSharedLinksListParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type TracerSharedLinksList200 = {
  count: number;
  next?: string;
  previous?: string;
  results: SharedLinkListApi[];
};

export type TracerTraceAnnotationListParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type TracerTraceAnnotationList200 = {
  count: number;
  next?: string;
  previous?: string;
  results: GetTraceAnnotationApi[];
};

export type TracerTraceAnnotationGetAnnotationValuesParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type TracerTraceAnnotationGetAnnotationValues200 = {
  count: number;
  next?: string;
  previous?: string;
  results: GetTraceAnnotationApi[];
};

export type TracerTraceSessionListParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type TracerTraceSessionList200 = {
  count: number;
  next?: string;
  previous?: string;
  results: TraceSessionApi[];
};

export type TracerTraceSessionGetSessionFilterValuesParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type TracerTraceSessionGetSessionFilterValues200 = {
  count: number;
  next?: string;
  previous?: string;
  results: TraceSessionApi[];
};

export type TracerTraceSessionGetTraceSessionExportDataParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type TracerTraceSessionGetTraceSessionExportData200 = {
  count: number;
  next?: string;
  previous?: string;
  results: TraceSessionApi[];
};

export type TracerTraceSessionListSessionsParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type TracerTraceSessionListSessions200 = {
  count: number;
  next?: string;
  previous?: string;
  results: TraceSessionApi[];
};

export type TracerTraceListParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type TracerTraceList200 = {
  count: number;
  next?: string;
  previous?: string;
  results: TraceApi[];
};

export type TracerTraceAgentGraphParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type TracerTraceAgentGraph200 = {
  count: number;
  next?: string;
  previous?: string;
  results: TraceApi[];
};

export type TracerTraceGetEvalNamesParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type TracerTraceGetEvalNames200 = {
  count: number;
  next?: string;
  previous?: string;
  results: TraceApi[];
};

export type TracerTraceGetPropertiesParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type TracerTraceGetProperties200 = {
  count: number;
  next?: string;
  previous?: string;
  results: TraceApi[];
};

export type TracerTraceGetTraceExportDataParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type TracerTraceGetTraceExportData200 = {
  count: number;
  next?: string;
  previous?: string;
  results: TraceApi[];
};

export type TracerTraceGetTraceIdByIndexParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type TracerTraceGetTraceIdByIndex200 = {
  count: number;
  next?: string;
  previous?: string;
  results: TraceApi[];
};

export type TracerTraceGetTraceIdByIndexObserveParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type TracerTraceGetTraceIdByIndexObserve200 = {
  count: number;
  next?: string;
  previous?: string;
  results: TraceApi[];
};

export type TracerTraceListTracesParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type TracerTraceListTraces200 = {
  count: number;
  next?: string;
  previous?: string;
  results: TraceApi[];
};

export type TracerTraceListTracesOfSessionParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type TracerTraceListTracesOfSession200 = {
  count: number;
  next?: string;
  previous?: string;
  results: TraceApi[];
};

export type TracerTraceListVoiceCallsParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type TracerTraceListVoiceCalls200 = {
  count: number;
  next?: string;
  previous?: string;
  results: TraceApi[];
};

export type TracerTraceVoiceCallDetailParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type TracerTraceVoiceCallDetail200 = {
  count: number;
  next?: string;
  previous?: string;
  results: TraceApi[];
};

export type TracerUserAlertLogsListParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type TracerUserAlertLogsList200 = {
  count: number;
  next?: string;
  previous?: string;
  results: UserAlertMonitorLogApi[];
};

export type TracerUserAlertLogsListAllParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type TracerUserAlertLogsListAll200 = {
  count: number;
  next?: string;
  previous?: string;
  results: UserAlertMonitorLogApi[];
};

export type TracerUserAlertsListParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type TracerUserAlertsList200 = {
  count: number;
  next?: string;
  previous?: string;
  results: UserAlertMonitorApi[];
};

export type TracerUserAlertsListMonitorsParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type TracerUserAlertsListMonitors200 = {
  count: number;
  next?: string;
  previous?: string;
  results: UserAlertMonitorApi[];
};

export type TracerUsersListParams = {
project_id?: string;
search?: string;
/**
 * @minimum 1
 * @maximum 500
 */
page_size?: number;
/**
 * @minimum 0
 */
current_page_index?: number;
sort_params?: string;
/**
 * @minLength 1
 */
filters?: string;
};
