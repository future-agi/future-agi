/**
 * Auto-generated from the Django backend OpenAPI schema.
 * To modify these types, update Django serializers/views, regenerate OpenAPI, then run:
 *   yarn contracts:generate
 *
 * Future AGI Management API - annotation/filter contracts
 * OpenAPI spec version: v1
 */
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

export interface ApiErrorResponseApi {
  status?: boolean;
  result?: ApiErrorResponseApiResult;
  message?: ApiErrorResponseApiMessage;
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
  [key: string]: unknown;
 };

export type SelectionApiFilterItem = {
  /** Column or attribute id to filter on. */
  column_id: string;
  /** Optional UI label for chips and saved views. */
  display_name?: string;
  filter_config: SelectionApiFilterItemFilterConfig;
  [key: string]: unknown;
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
filters?: string;
};
