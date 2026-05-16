/**
 * Auto-generated from the Django backend OpenAPI schema.
 * To modify these types, update Django serializers/views, regenerate OpenAPI, then run:
 *   yarn contracts:generate
 *
 * TFC Management API - annotation/filter contracts
 * OpenAPI spec version: v1
 */
import type {
  AIFilterRequestApi,
  AIFilterResponseApi,
  AddItemsApi,
  AddObservationSpanAnnotationsApi,
  AnnotationQueueApi,
  AnnotationSummaryResponseApi,
  AnnotationTaskApi,
  AnnotationsApi,
  AnnotationsLabelsApi,
  ApiErrorResponseApi,
  ApiSelectionTooLargeErrorApi,
  ApiTracesSpanAttributeDetailListParams,
  ApiTracesSpanAttributeKeysListParams,
  ApiTracesSpanAttributeValuesListParams,
  AssignItemsApi,
  AutomationRuleApi,
  AutomationRuleEvaluateAcceptedResponseApi,
  BulkAnnotationRequestApi,
  BulkAnnotationResponseApi,
  BulkCreateScoresApi,
  BulkCreateScoresResponseApi,
  BulkRemoveItemsApi,
  CreateScoreApi,
  DashboardApi,
  DashboardCreateUpdateApi,
  DashboardDetailApi,
  DashboardWidgetApi,
  DiscussionCommentRequestApi,
  DiscussionReactionRequestApi,
  DiscussionThreadStatusRequestApi,
  GetAnnotationLabelsResponseApi,
  GetTraceAnnotationApi,
  ImportAnnotationsApi,
  ModelHubAnnotationQueuesAutomationRulesList200,
  ModelHubAnnotationQueuesAutomationRulesListParams,
  ModelHubAnnotationQueuesExportAnnotationsParams,
  ModelHubAnnotationQueuesForSourceParams,
  ModelHubAnnotationQueuesItemsAnnotateDetailParams,
  ModelHubAnnotationQueuesItemsList200,
  ModelHubAnnotationQueuesItemsListParams,
  ModelHubAnnotationQueuesItemsNextItemParams,
  ModelHubAnnotationQueuesList200,
  ModelHubAnnotationQueuesListParams,
  ModelHubAnnotationTasksList200,
  ModelHubAnnotationTasksListParams,
  ModelHubAnnotationsLabelsList200,
  ModelHubAnnotationsLabelsListParams,
  ModelHubAnnotationsList200,
  ModelHubAnnotationsListParams,
  ModelHubScoresForSourceParams,
  ModelHubScoresList200,
  ModelHubScoresListParams,
  ObservationSpanApi,
  ProjectApi,
  ProjectVersionApi,
  QueueAddItemsResponseApi,
  QueueAddLabelResponseApi,
  QueueAnnotateDetailResponseApi,
  QueueAssignItemsResponseApi,
  QueueBulkRemoveItemsResponseApi,
  QueueDefaultRequestApi,
  QueueDefaultResponseApi,
  QueueDiscussionResponseApi,
  QueueExportAnnotationsResponseApi,
  QueueExportToDatasetRequestApi,
  QueueExportToDatasetResponseApi,
  QueueHardDeleteRequestApi,
  QueueHardDeleteResponseApi,
  QueueImportAnnotationsResponseApi,
  QueueItemAnnotationsResponseApi,
  QueueItemApi,
  QueueItemNavigationRequestApi,
  QueueJsonResponseApi,
  QueueLabelRequestApi,
  QueueNavigationResponseApi,
  QueueNextItemResponseApi,
  QueueProgressResponseApi,
  QueueReleaseReservationResponseApi,
  QueueRemoveLabelResponseApi,
  QueueReviewItemResponseApi,
  QueueStatusRequestApi,
  QueueStatusResponseApi,
  QueueSubmitAnnotationsResponseApi,
  ReviewItemRequestApi,
  ScoreApi,
  ScoreDeleteResponseApi,
  ScoreForSourceResponseApi,
  ScoreResponseApi,
  SpanAttributeDetailResponseApi,
  SpanAttributeKeysResponseApi,
  SpanAttributeValuesResponseApi,
  SubmitAnnotationsApi,
  TraceApi,
  TraceSessionApi,
  TraceTagsUpdateApi,
  TracerDashboardFilterValues200,
  TracerDashboardFilterValuesParams,
  TracerDashboardList200,
  TracerDashboardListParams,
  TracerDashboardMetrics200,
  TracerDashboardMetricsParams,
  TracerDashboardSimulationAgents200,
  TracerDashboardSimulationAgentsParams,
  TracerDashboardWidgetsList200,
  TracerDashboardWidgetsListParams,
  TracerObservationSpanGetEvalAttributesList200,
  TracerObservationSpanGetEvalAttributesListParams,
  TracerObservationSpanGetEvaluationDetails200,
  TracerObservationSpanGetEvaluationDetailsParams,
  TracerObservationSpanGetObservationSpanFields200,
  TracerObservationSpanGetObservationSpanFieldsParams,
  TracerObservationSpanGetSpanAttributesList200,
  TracerObservationSpanGetSpanAttributesListParams,
  TracerObservationSpanGetSpansExportData200,
  TracerObservationSpanGetSpansExportDataParams,
  TracerObservationSpanGetTraceIdByIndexSpansAsBase200,
  TracerObservationSpanGetTraceIdByIndexSpansAsBaseParams,
  TracerObservationSpanGetTraceIdByIndexSpansAsObserve200,
  TracerObservationSpanGetTraceIdByIndexSpansAsObserveParams,
  TracerObservationSpanList200,
  TracerObservationSpanListParams,
  TracerObservationSpanListSpans200,
  TracerObservationSpanListSpansObserve200,
  TracerObservationSpanListSpansObserveParams,
  TracerObservationSpanListSpansParams,
  TracerObservationSpanRetrieveLoading200,
  TracerObservationSpanRetrieveLoadingParams,
  TracerObservationSpanRootSpans200,
  TracerObservationSpanRootSpansParams,
  TracerProjectFetchSystemMetrics200,
  TracerProjectFetchSystemMetricsParams,
  TracerProjectGetGraphData200,
  TracerProjectGetGraphDataParams,
  TracerProjectList200,
  TracerProjectListParams,
  TracerProjectListProjectIds200,
  TracerProjectListProjectIdsParams,
  TracerProjectListProjects200,
  TracerProjectListProjectsParams,
  TracerProjectProjectSdkCode200,
  TracerProjectProjectSdkCodeParams,
  TracerTraceAgentGraph200,
  TracerTraceAgentGraphParams,
  TracerTraceAnnotationGetAnnotationValues200,
  TracerTraceAnnotationGetAnnotationValuesParams,
  TracerTraceAnnotationList200,
  TracerTraceAnnotationListParams,
  TracerTraceGetEvalNames200,
  TracerTraceGetEvalNamesParams,
  TracerTraceGetProperties200,
  TracerTraceGetPropertiesParams,
  TracerTraceGetTraceExportData200,
  TracerTraceGetTraceExportDataParams,
  TracerTraceGetTraceIdByIndex200,
  TracerTraceGetTraceIdByIndexObserve200,
  TracerTraceGetTraceIdByIndexObserveParams,
  TracerTraceGetTraceIdByIndexParams,
  TracerTraceList200,
  TracerTraceListParams,
  TracerTraceListTraces200,
  TracerTraceListTracesOfSession200,
  TracerTraceListTracesOfSessionParams,
  TracerTraceListTracesParams,
  TracerTraceListVoiceCalls200,
  TracerTraceListVoiceCallsParams,
  TracerTraceSessionGetSessionFilterValues200,
  TracerTraceSessionGetSessionFilterValuesParams,
  TracerTraceSessionGetTraceSessionExportData200,
  TracerTraceSessionGetTraceSessionExportDataParams,
  TracerTraceSessionList200,
  TracerTraceSessionListParams,
  TracerTraceSessionListSessions200,
  TracerTraceSessionListSessionsParams,
  TracerTraceVoiceCallDetail200,
  TracerTraceVoiceCallDetailParams,
  TracerUsersListParams,
  UserCodeExampleResponseApi,
  UsersResponseApi
} from './api.schemas';

import { apiMutator } from '../../api/contracts/openapi-mutator';

// https://stackoverflow.com/questions/49579094/typescript-conditional-types-filter-out-readonly-properties-pick-only-requir/49579497#49579497
type IfEquals<X, Y, A = X, B = never> = (<T>() => T extends X ? 1 : 2) extends <
T,
>() => T extends Y ? 1 : 2
? A
: B;

type WritableKeys<T> = {
[P in keyof T]-?: IfEquals<
  { [Q in P]: T[P] },
  { -readonly [Q in P]: T[P] },
  P
>;
}[keyof T];

type UnionToIntersection<U> =
  (U extends any ? (k: U)=>void : never) extends ((k: infer I)=>void) ? I : never;
type DistributeReadOnlyOverUnions<T> = T extends any ? NonReadonly<T> : never;

type Writable<T> = Pick<T, WritableKeys<T>>;
type NonReadonly<T> = [T] extends [UnionToIntersection<T>] ? {
  [P in keyof Writable<T>]: T[P] extends object
    ? NonReadonly<NonNullable<T[P]>>
    : T[P];
} : DistributeReadOnlyOverUnions<T>;


export type apiTracesSpanAttributeDetailListResponse200 = {
  data: SpanAttributeDetailResponseApi
  status: 200
}

export type apiTracesSpanAttributeDetailListResponse400 = {
  data: ApiErrorResponseApi
  status: 400
}

export type apiTracesSpanAttributeDetailListResponse404 = {
  data: ApiErrorResponseApi
  status: 404
}

export type apiTracesSpanAttributeDetailListResponse500 = {
  data: ApiErrorResponseApi
  status: 500
}

export type apiTracesSpanAttributeDetailListResponse503 = {
  data: ApiErrorResponseApi
  status: 503
}

export type apiTracesSpanAttributeDetailListResponseSuccess = (apiTracesSpanAttributeDetailListResponse200) & {
  headers: Headers;
};
export type apiTracesSpanAttributeDetailListResponseError = (apiTracesSpanAttributeDetailListResponse400 | apiTracesSpanAttributeDetailListResponse404 | apiTracesSpanAttributeDetailListResponse500 | apiTracesSpanAttributeDetailListResponse503) & {
  headers: Headers;
};

export type apiTracesSpanAttributeDetailListResponse = (apiTracesSpanAttributeDetailListResponseSuccess | apiTracesSpanAttributeDetailListResponseError)

export const getApiTracesSpanAttributeDetailListUrl = (params: ApiTracesSpanAttributeDetailListParams,) => {
  const normalizedParams = new URLSearchParams();

  Object.entries(params || {}).forEach(([key, value]) => {

    if (value !== undefined) {
      normalizedParams.append(key, value === null ? 'null' : value.toString())
    }
  });

  const stringifiedParams = normalizedParams.toString();

  return stringifiedParams.length > 0 ? `/api/traces/span-attribute-detail/?${stringifiedParams}` : `/api/traces/span-attribute-detail/`
}

/**
 * Determines the attribute type by probing which map contains the key, then
returns type-appropriate statistics:
  - string: top values with percentages
  - number: min, max, avg, p50, p95
  - boolean: true/false distribution

GET /api/traces/span-attribute-detail/?project_id=<uuid>&key=<attr_key>
 * @summary Full detail for a specific span attribute key.
 */
export const apiTracesSpanAttributeDetailList = async (params: ApiTracesSpanAttributeDetailListParams, options?: RequestInit): Promise<apiTracesSpanAttributeDetailListResponse> => {

  return apiMutator<apiTracesSpanAttributeDetailListResponse>(getApiTracesSpanAttributeDetailListUrl(params),
  {
    ...options,
    method: 'GET'


  }
);}



export type apiTracesSpanAttributeKeysListResponse200 = {
  data: SpanAttributeKeysResponseApi
  status: 200
}

export type apiTracesSpanAttributeKeysListResponse400 = {
  data: ApiErrorResponseApi
  status: 400
}

export type apiTracesSpanAttributeKeysListResponse404 = {
  data: ApiErrorResponseApi
  status: 404
}

export type apiTracesSpanAttributeKeysListResponse500 = {
  data: ApiErrorResponseApi
  status: 500
}

export type apiTracesSpanAttributeKeysListResponse503 = {
  data: ApiErrorResponseApi
  status: 503
}

export type apiTracesSpanAttributeKeysListResponseSuccess = (apiTracesSpanAttributeKeysListResponse200) & {
  headers: Headers;
};
export type apiTracesSpanAttributeKeysListResponseError = (apiTracesSpanAttributeKeysListResponse400 | apiTracesSpanAttributeKeysListResponse404 | apiTracesSpanAttributeKeysListResponse500 | apiTracesSpanAttributeKeysListResponse503) & {
  headers: Headers;
};

export type apiTracesSpanAttributeKeysListResponse = (apiTracesSpanAttributeKeysListResponseSuccess | apiTracesSpanAttributeKeysListResponseError)

export const getApiTracesSpanAttributeKeysListUrl = (params: ApiTracesSpanAttributeKeysListParams,) => {
  const normalizedParams = new URLSearchParams();

  Object.entries(params || {}).forEach(([key, value]) => {

    if (value !== undefined) {
      normalizedParams.append(key, value === null ? 'null' : value.toString())
    }
  });

  const stringifiedParams = normalizedParams.toString();

  return stringifiedParams.length > 0 ? `/api/traces/span-attribute-keys/?${stringifiedParams}` : `/api/traces/span-attribute-keys/`
}

/**
 * Returns every distinct key across the string, number, and boolean attribute
maps together with its inferred type and occurrence count.

GET /api/traces/span-attribute-keys/?project_id=<uuid>
 * @summary Discover all span attribute keys for a project.
 */
export const apiTracesSpanAttributeKeysList = async (params: ApiTracesSpanAttributeKeysListParams, options?: RequestInit): Promise<apiTracesSpanAttributeKeysListResponse> => {

  return apiMutator<apiTracesSpanAttributeKeysListResponse>(getApiTracesSpanAttributeKeysListUrl(params),
  {
    ...options,
    method: 'GET'


  }
);}



export type apiTracesSpanAttributeValuesListResponse200 = {
  data: SpanAttributeValuesResponseApi
  status: 200
}

export type apiTracesSpanAttributeValuesListResponse400 = {
  data: ApiErrorResponseApi
  status: 400
}

export type apiTracesSpanAttributeValuesListResponse404 = {
  data: ApiErrorResponseApi
  status: 404
}

export type apiTracesSpanAttributeValuesListResponse500 = {
  data: ApiErrorResponseApi
  status: 500
}

export type apiTracesSpanAttributeValuesListResponse503 = {
  data: ApiErrorResponseApi
  status: 503
}

export type apiTracesSpanAttributeValuesListResponseSuccess = (apiTracesSpanAttributeValuesListResponse200) & {
  headers: Headers;
};
export type apiTracesSpanAttributeValuesListResponseError = (apiTracesSpanAttributeValuesListResponse400 | apiTracesSpanAttributeValuesListResponse404 | apiTracesSpanAttributeValuesListResponse500 | apiTracesSpanAttributeValuesListResponse503) & {
  headers: Headers;
};

export type apiTracesSpanAttributeValuesListResponse = (apiTracesSpanAttributeValuesListResponseSuccess | apiTracesSpanAttributeValuesListResponseError)

export const getApiTracesSpanAttributeValuesListUrl = (params: ApiTracesSpanAttributeValuesListParams,) => {
  const normalizedParams = new URLSearchParams();

  Object.entries(params || {}).forEach(([key, value]) => {

    if (value !== undefined) {
      normalizedParams.append(key, value === null ? 'null' : value.toString())
    }
  });

  const stringifiedParams = normalizedParams.toString();

  return stringifiedParams.length > 0 ? `/api/traces/span-attribute-values/?${stringifiedParams}` : `/api/traces/span-attribute-values/`
}

/**
 * Returns the most frequent values for the given string attribute key,
with optional prefix search filtering.

GET /api/traces/span-attribute-values/?project_id=<uuid>&key=<attr_key>[&q=<search>][&limit=50]
 * @summary Get top values for a specific span attribute key.
 */
export const apiTracesSpanAttributeValuesList = async (params: ApiTracesSpanAttributeValuesListParams, options?: RequestInit): Promise<apiTracesSpanAttributeValuesListResponse> => {

  return apiMutator<apiTracesSpanAttributeValuesListResponse>(getApiTracesSpanAttributeValuesListUrl(params),
  {
    ...options,
    method: 'GET'


  }
);}



export type modelHubAiFilterCreateResponse200 = {
  data: AIFilterResponseApi
  status: 200
}

export type modelHubAiFilterCreateResponse400 = {
  data: ApiErrorResponseApi
  status: 400
}

export type modelHubAiFilterCreateResponse500 = {
  data: ApiErrorResponseApi
  status: 500
}

export type modelHubAiFilterCreateResponseSuccess = (modelHubAiFilterCreateResponse200) & {
  headers: Headers;
};
export type modelHubAiFilterCreateResponseError = (modelHubAiFilterCreateResponse400 | modelHubAiFilterCreateResponse500) & {
  headers: Headers;
};

export type modelHubAiFilterCreateResponse = (modelHubAiFilterCreateResponseSuccess | modelHubAiFilterCreateResponseError)

export const getModelHubAiFilterCreateUrl = () => {




  return `/model-hub/ai-filter/`
}

/**
 * Request body:
{
    "query": "show me LLM evals that are pass/fail",
    "schema": [
        {
            "field": "eval_type",
            "label": "Eval Type",
            "type": "enum",
            "operators": ["is", "is_not"],
            "choices": ["llm", "code", "agent"]
        },
        ...
    ]
}
 * @summary POST /model-hub/ai-filter/
 */
export const modelHubAiFilterCreate = async (aIFilterRequestApi: AIFilterRequestApi, options?: RequestInit): Promise<modelHubAiFilterCreateResponse> => {

  return apiMutator<modelHubAiFilterCreateResponse>(getModelHubAiFilterCreateUrl(),
  {
    ...options,
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...options?.headers },
    body: JSON.stringify(
      aIFilterRequestApi,)
  }
);}



export type modelHubAnnotationQueuesListResponse200 = {
  data: ModelHubAnnotationQueuesList200
  status: 200
}

export type modelHubAnnotationQueuesListResponseSuccess = (modelHubAnnotationQueuesListResponse200) & {
  headers: Headers;
};
;

export type modelHubAnnotationQueuesListResponse = (modelHubAnnotationQueuesListResponseSuccess)

export const getModelHubAnnotationQueuesListUrl = (params?: ModelHubAnnotationQueuesListParams,) => {
  const normalizedParams = new URLSearchParams();

  Object.entries(params || {}).forEach(([key, value]) => {

    if (value !== undefined) {
      normalizedParams.append(key, value === null ? 'null' : value.toString())
    }
  });

  const stringifiedParams = normalizedParams.toString();

  return stringifiedParams.length > 0 ? `/model-hub/annotation-queues/?${stringifiedParams}` : `/model-hub/annotation-queues/`
}

export const modelHubAnnotationQueuesList = async (params?: ModelHubAnnotationQueuesListParams, options?: RequestInit): Promise<modelHubAnnotationQueuesListResponse> => {

  return apiMutator<modelHubAnnotationQueuesListResponse>(getModelHubAnnotationQueuesListUrl(params),
  {
    ...options,
    method: 'GET'


  }
);}



export type modelHubAnnotationQueuesCreateResponse201 = {
  data: AnnotationQueueApi
  status: 201
}

export type modelHubAnnotationQueuesCreateResponseSuccess = (modelHubAnnotationQueuesCreateResponse201) & {
  headers: Headers;
};
;

export type modelHubAnnotationQueuesCreateResponse = (modelHubAnnotationQueuesCreateResponseSuccess)

export const getModelHubAnnotationQueuesCreateUrl = () => {




  return `/model-hub/annotation-queues/`
}

export const modelHubAnnotationQueuesCreate = async (annotationQueueApi: NonReadonly<AnnotationQueueApi>, options?: RequestInit): Promise<modelHubAnnotationQueuesCreateResponse> => {

  return apiMutator<modelHubAnnotationQueuesCreateResponse>(getModelHubAnnotationQueuesCreateUrl(),
  {
    ...options,
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...options?.headers },
    body: JSON.stringify(
      annotationQueueApi,)
  }
);}



export type modelHubAnnotationQueuesForSourceResponse200 = {
  data: QueueJsonResponseApi
  status: 200
}

export type modelHubAnnotationQueuesForSourceResponse400 = {
  data: ApiErrorResponseApi
  status: 400
}

export type modelHubAnnotationQueuesForSourceResponse403 = {
  data: ApiErrorResponseApi
  status: 403
}

export type modelHubAnnotationQueuesForSourceResponse404 = {
  data: ApiErrorResponseApi
  status: 404
}

export type modelHubAnnotationQueuesForSourceResponse409 = {
  data: ApiErrorResponseApi
  status: 409
}

export type modelHubAnnotationQueuesForSourceResponse500 = {
  data: ApiErrorResponseApi
  status: 500
}

export type modelHubAnnotationQueuesForSourceResponseSuccess = (modelHubAnnotationQueuesForSourceResponse200) & {
  headers: Headers;
};
export type modelHubAnnotationQueuesForSourceResponseError = (modelHubAnnotationQueuesForSourceResponse400 | modelHubAnnotationQueuesForSourceResponse403 | modelHubAnnotationQueuesForSourceResponse404 | modelHubAnnotationQueuesForSourceResponse409 | modelHubAnnotationQueuesForSourceResponse500) & {
  headers: Headers;
};

export type modelHubAnnotationQueuesForSourceResponse = (modelHubAnnotationQueuesForSourceResponseSuccess | modelHubAnnotationQueuesForSourceResponseError)

export const getModelHubAnnotationQueuesForSourceUrl = (params?: ModelHubAnnotationQueuesForSourceParams,) => {
  const normalizedParams = new URLSearchParams();

  Object.entries(params || {}).forEach(([key, value]) => {

    if (value !== undefined) {
      normalizedParams.append(key, value === null ? 'null' : value.toString())
    }
  });

  const stringifiedParams = normalizedParams.toString();

  return stringifiedParams.length > 0 ? `/model-hub/annotation-queues/for-source/?${stringifiedParams}` : `/model-hub/annotation-queues/for-source/`
}

/**
 * Find annotation queues for a given source that the current user can annotate.
Includes queues where:
- The source is a queue item AND the user is an annotator in that queue
  (regardless of whether the item is explicitly assigned to them)

Query params:
  - source_type, source_id  (single source)
  - OR sources (JSON array of {source_type, source_id} objects for multi-source lookup)
 */
export const modelHubAnnotationQueuesForSource = async (params?: ModelHubAnnotationQueuesForSourceParams, options?: RequestInit): Promise<modelHubAnnotationQueuesForSourceResponse> => {

  return apiMutator<modelHubAnnotationQueuesForSourceResponse>(getModelHubAnnotationQueuesForSourceUrl(params),
  {
    ...options,
    method: 'GET'


  }
);}



export type modelHubAnnotationQueuesGetOrCreateDefaultResponse200 = {
  data: QueueDefaultResponseApi
  status: 200
}

export type modelHubAnnotationQueuesGetOrCreateDefaultResponse400 = {
  data: ApiErrorResponseApi
  status: 400
}

export type modelHubAnnotationQueuesGetOrCreateDefaultResponse403 = {
  data: ApiErrorResponseApi
  status: 403
}

export type modelHubAnnotationQueuesGetOrCreateDefaultResponse404 = {
  data: ApiErrorResponseApi
  status: 404
}

export type modelHubAnnotationQueuesGetOrCreateDefaultResponse409 = {
  data: ApiErrorResponseApi
  status: 409
}

export type modelHubAnnotationQueuesGetOrCreateDefaultResponse500 = {
  data: ApiErrorResponseApi
  status: 500
}

export type modelHubAnnotationQueuesGetOrCreateDefaultResponseSuccess = (modelHubAnnotationQueuesGetOrCreateDefaultResponse200) & {
  headers: Headers;
};
export type modelHubAnnotationQueuesGetOrCreateDefaultResponseError = (modelHubAnnotationQueuesGetOrCreateDefaultResponse400 | modelHubAnnotationQueuesGetOrCreateDefaultResponse403 | modelHubAnnotationQueuesGetOrCreateDefaultResponse404 | modelHubAnnotationQueuesGetOrCreateDefaultResponse409 | modelHubAnnotationQueuesGetOrCreateDefaultResponse500) & {
  headers: Headers;
};

export type modelHubAnnotationQueuesGetOrCreateDefaultResponse = (modelHubAnnotationQueuesGetOrCreateDefaultResponseSuccess | modelHubAnnotationQueuesGetOrCreateDefaultResponseError)

export const getModelHubAnnotationQueuesGetOrCreateDefaultUrl = () => {




  return `/model-hub/annotation-queues/get-or-create-default/`
}

/**
 * Get or create the default annotation queue for a project, dataset, or agent definition.
Default queues are open to all org members (no annotator restriction).

Body params (one of):
  - project_id
  - dataset_id
  - agent_definition_id
 */
export const modelHubAnnotationQueuesGetOrCreateDefault = async (queueDefaultRequestApi: QueueDefaultRequestApi, options?: RequestInit): Promise<modelHubAnnotationQueuesGetOrCreateDefaultResponse> => {

  return apiMutator<modelHubAnnotationQueuesGetOrCreateDefaultResponse>(getModelHubAnnotationQueuesGetOrCreateDefaultUrl(),
  {
    ...options,
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...options?.headers },
    body: JSON.stringify(
      queueDefaultRequestApi,)
  }
);}



export type modelHubAnnotationQueuesReadResponse200 = {
  data: AnnotationQueueApi
  status: 200
}

export type modelHubAnnotationQueuesReadResponseSuccess = (modelHubAnnotationQueuesReadResponse200) & {
  headers: Headers;
};
;

export type modelHubAnnotationQueuesReadResponse = (modelHubAnnotationQueuesReadResponseSuccess)

export const getModelHubAnnotationQueuesReadUrl = (id: string,) => {




  return `/model-hub/annotation-queues/${id}/`
}

export const modelHubAnnotationQueuesRead = async (id: string, options?: RequestInit): Promise<modelHubAnnotationQueuesReadResponse> => {

  return apiMutator<modelHubAnnotationQueuesReadResponse>(getModelHubAnnotationQueuesReadUrl(id),
  {
    ...options,
    method: 'GET'


  }
);}



export type modelHubAnnotationQueuesUpdateResponse200 = {
  data: AnnotationQueueApi
  status: 200
}

export type modelHubAnnotationQueuesUpdateResponseSuccess = (modelHubAnnotationQueuesUpdateResponse200) & {
  headers: Headers;
};
;

export type modelHubAnnotationQueuesUpdateResponse = (modelHubAnnotationQueuesUpdateResponseSuccess)

export const getModelHubAnnotationQueuesUpdateUrl = (id: string,) => {




  return `/model-hub/annotation-queues/${id}/`
}

/**
 * Only managers of the queue may update queue settings.
 */
export const modelHubAnnotationQueuesUpdate = async (id: string,
    annotationQueueApi: NonReadonly<AnnotationQueueApi>, options?: RequestInit): Promise<modelHubAnnotationQueuesUpdateResponse> => {

  return apiMutator<modelHubAnnotationQueuesUpdateResponse>(getModelHubAnnotationQueuesUpdateUrl(id),
  {
    ...options,
    method: 'PUT',
    headers: { 'Content-Type': 'application/json', ...options?.headers },
    body: JSON.stringify(
      annotationQueueApi,)
  }
);}



export type modelHubAnnotationQueuesPartialUpdateResponse200 = {
  data: AnnotationQueueApi
  status: 200
}

export type modelHubAnnotationQueuesPartialUpdateResponseSuccess = (modelHubAnnotationQueuesPartialUpdateResponse200) & {
  headers: Headers;
};
;

export type modelHubAnnotationQueuesPartialUpdateResponse = (modelHubAnnotationQueuesPartialUpdateResponseSuccess)

export const getModelHubAnnotationQueuesPartialUpdateUrl = (id: string,) => {




  return `/model-hub/annotation-queues/${id}/`
}

export const modelHubAnnotationQueuesPartialUpdate = async (id: string,
    annotationQueueApi: NonReadonly<AnnotationQueueApi>, options?: RequestInit): Promise<modelHubAnnotationQueuesPartialUpdateResponse> => {

  return apiMutator<modelHubAnnotationQueuesPartialUpdateResponse>(getModelHubAnnotationQueuesPartialUpdateUrl(id),
  {
    ...options,
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json', ...options?.headers },
    body: JSON.stringify(
      annotationQueueApi,)
  }
);}



export type modelHubAnnotationQueuesDeleteResponse204 = {
  data: void
  status: 204
}

export type modelHubAnnotationQueuesDeleteResponseSuccess = (modelHubAnnotationQueuesDeleteResponse204) & {
  headers: Headers;
};
;

export type modelHubAnnotationQueuesDeleteResponse = (modelHubAnnotationQueuesDeleteResponseSuccess)

export const getModelHubAnnotationQueuesDeleteUrl = (id: string,) => {




  return `/model-hub/annotation-queues/${id}/`
}

/**
 * ``BaseModel.delete()`` flips ``deleted=True`` instead of removing
the row. Attached automation rules go dormant (the scheduler
filters ``queue__deleted=False``), items stay invisible but
recoverable, label bindings preserved.

For truly destructive removal, use the ``hard-delete`` action
below.
 * @summary Archive a queue (soft delete).
 */
export const modelHubAnnotationQueuesDelete = async (id: string, options?: RequestInit): Promise<modelHubAnnotationQueuesDeleteResponse> => {

  return apiMutator<modelHubAnnotationQueuesDeleteResponse>(getModelHubAnnotationQueuesDeleteUrl(id),
  {
    ...options,
    method: 'DELETE'


  }
);}



export type modelHubAnnotationQueuesAddLabelResponse200 = {
  data: QueueAddLabelResponseApi
  status: 200
}

export type modelHubAnnotationQueuesAddLabelResponse400 = {
  data: ApiErrorResponseApi
  status: 400
}

export type modelHubAnnotationQueuesAddLabelResponse403 = {
  data: ApiErrorResponseApi
  status: 403
}

export type modelHubAnnotationQueuesAddLabelResponse404 = {
  data: ApiErrorResponseApi
  status: 404
}

export type modelHubAnnotationQueuesAddLabelResponse409 = {
  data: ApiErrorResponseApi
  status: 409
}

export type modelHubAnnotationQueuesAddLabelResponse500 = {
  data: ApiErrorResponseApi
  status: 500
}

export type modelHubAnnotationQueuesAddLabelResponseSuccess = (modelHubAnnotationQueuesAddLabelResponse200) & {
  headers: Headers;
};
export type modelHubAnnotationQueuesAddLabelResponseError = (modelHubAnnotationQueuesAddLabelResponse400 | modelHubAnnotationQueuesAddLabelResponse403 | modelHubAnnotationQueuesAddLabelResponse404 | modelHubAnnotationQueuesAddLabelResponse409 | modelHubAnnotationQueuesAddLabelResponse500) & {
  headers: Headers;
};

export type modelHubAnnotationQueuesAddLabelResponse = (modelHubAnnotationQueuesAddLabelResponseSuccess | modelHubAnnotationQueuesAddLabelResponseError)

export const getModelHubAnnotationQueuesAddLabelUrl = (id: string,) => {




  return `/model-hub/annotation-queues/${id}/add-label/`
}

/**
 * Add a label to an annotation queue.
Labels apply to all sources in the queue's project (for default queues).
Queue items are created lazily when someone actually annotates.
 */
export const modelHubAnnotationQueuesAddLabel = async (id: string,
    queueLabelRequestApi: QueueLabelRequestApi, options?: RequestInit): Promise<modelHubAnnotationQueuesAddLabelResponse> => {

  return apiMutator<modelHubAnnotationQueuesAddLabelResponse>(getModelHubAnnotationQueuesAddLabelUrl(id),
  {
    ...options,
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...options?.headers },
    body: JSON.stringify(
      queueLabelRequestApi,)
  }
);}



export type modelHubAnnotationQueuesAgreementResponse200 = {
  data: QueueJsonResponseApi
  status: 200
}

export type modelHubAnnotationQueuesAgreementResponse400 = {
  data: ApiErrorResponseApi
  status: 400
}

export type modelHubAnnotationQueuesAgreementResponse403 = {
  data: ApiErrorResponseApi
  status: 403
}

export type modelHubAnnotationQueuesAgreementResponse404 = {
  data: ApiErrorResponseApi
  status: 404
}

export type modelHubAnnotationQueuesAgreementResponse409 = {
  data: ApiErrorResponseApi
  status: 409
}

export type modelHubAnnotationQueuesAgreementResponse500 = {
  data: ApiErrorResponseApi
  status: 500
}

export type modelHubAnnotationQueuesAgreementResponseSuccess = (modelHubAnnotationQueuesAgreementResponse200) & {
  headers: Headers;
};
export type modelHubAnnotationQueuesAgreementResponseError = (modelHubAnnotationQueuesAgreementResponse400 | modelHubAnnotationQueuesAgreementResponse403 | modelHubAnnotationQueuesAgreementResponse404 | modelHubAnnotationQueuesAgreementResponse409 | modelHubAnnotationQueuesAgreementResponse500) & {
  headers: Headers;
};

export type modelHubAnnotationQueuesAgreementResponse = (modelHubAnnotationQueuesAgreementResponseSuccess | modelHubAnnotationQueuesAgreementResponseError)

export const getModelHubAnnotationQueuesAgreementUrl = (id: string,) => {




  return `/model-hub/annotation-queues/${id}/agreement/`
}

/**
 * Calculate inter-annotator agreement metrics.
 */
export const modelHubAnnotationQueuesAgreement = async (id: string, options?: RequestInit): Promise<modelHubAnnotationQueuesAgreementResponse> => {

  return apiMutator<modelHubAnnotationQueuesAgreementResponse>(getModelHubAnnotationQueuesAgreementUrl(id),
  {
    ...options,
    method: 'GET'


  }
);}



export type modelHubAnnotationQueuesAnalyticsResponse200 = {
  data: QueueJsonResponseApi
  status: 200
}

export type modelHubAnnotationQueuesAnalyticsResponse400 = {
  data: ApiErrorResponseApi
  status: 400
}

export type modelHubAnnotationQueuesAnalyticsResponse403 = {
  data: ApiErrorResponseApi
  status: 403
}

export type modelHubAnnotationQueuesAnalyticsResponse404 = {
  data: ApiErrorResponseApi
  status: 404
}

export type modelHubAnnotationQueuesAnalyticsResponse409 = {
  data: ApiErrorResponseApi
  status: 409
}

export type modelHubAnnotationQueuesAnalyticsResponse500 = {
  data: ApiErrorResponseApi
  status: 500
}

export type modelHubAnnotationQueuesAnalyticsResponseSuccess = (modelHubAnnotationQueuesAnalyticsResponse200) & {
  headers: Headers;
};
export type modelHubAnnotationQueuesAnalyticsResponseError = (modelHubAnnotationQueuesAnalyticsResponse400 | modelHubAnnotationQueuesAnalyticsResponse403 | modelHubAnnotationQueuesAnalyticsResponse404 | modelHubAnnotationQueuesAnalyticsResponse409 | modelHubAnnotationQueuesAnalyticsResponse500) & {
  headers: Headers;
};

export type modelHubAnnotationQueuesAnalyticsResponse = (modelHubAnnotationQueuesAnalyticsResponseSuccess | modelHubAnnotationQueuesAnalyticsResponseError)

export const getModelHubAnnotationQueuesAnalyticsUrl = (id: string,) => {




  return `/model-hub/annotation-queues/${id}/analytics/`
}

/**
 * Queue analytics: throughput, annotator performance, label distribution.
 */
export const modelHubAnnotationQueuesAnalytics = async (id: string, options?: RequestInit): Promise<modelHubAnnotationQueuesAnalyticsResponse> => {

  return apiMutator<modelHubAnnotationQueuesAnalyticsResponse>(getModelHubAnnotationQueuesAnalyticsUrl(id),
  {
    ...options,
    method: 'GET'


  }
);}



export type modelHubAnnotationQueuesExportFieldsResponse200 = {
  data: QueueJsonResponseApi
  status: 200
}

export type modelHubAnnotationQueuesExportFieldsResponse400 = {
  data: ApiErrorResponseApi
  status: 400
}

export type modelHubAnnotationQueuesExportFieldsResponse403 = {
  data: ApiErrorResponseApi
  status: 403
}

export type modelHubAnnotationQueuesExportFieldsResponse404 = {
  data: ApiErrorResponseApi
  status: 404
}

export type modelHubAnnotationQueuesExportFieldsResponse409 = {
  data: ApiErrorResponseApi
  status: 409
}

export type modelHubAnnotationQueuesExportFieldsResponse500 = {
  data: ApiErrorResponseApi
  status: 500
}

export type modelHubAnnotationQueuesExportFieldsResponseSuccess = (modelHubAnnotationQueuesExportFieldsResponse200) & {
  headers: Headers;
};
export type modelHubAnnotationQueuesExportFieldsResponseError = (modelHubAnnotationQueuesExportFieldsResponse400 | modelHubAnnotationQueuesExportFieldsResponse403 | modelHubAnnotationQueuesExportFieldsResponse404 | modelHubAnnotationQueuesExportFieldsResponse409 | modelHubAnnotationQueuesExportFieldsResponse500) & {
  headers: Headers;
};

export type modelHubAnnotationQueuesExportFieldsResponse = (modelHubAnnotationQueuesExportFieldsResponseSuccess | modelHubAnnotationQueuesExportFieldsResponseError)

export const getModelHubAnnotationQueuesExportFieldsUrl = (id: string,) => {




  return `/model-hub/annotation-queues/${id}/export-fields/`
}

/**
 * Return source/label/attribute fields available for dataset export.
 */
export const modelHubAnnotationQueuesExportFields = async (id: string, options?: RequestInit): Promise<modelHubAnnotationQueuesExportFieldsResponse> => {

  return apiMutator<modelHubAnnotationQueuesExportFieldsResponse>(getModelHubAnnotationQueuesExportFieldsUrl(id),
  {
    ...options,
    method: 'GET'


  }
);}



export type modelHubAnnotationQueuesExportToDatasetResponse200 = {
  data: QueueExportToDatasetResponseApi
  status: 200
}

export type modelHubAnnotationQueuesExportToDatasetResponse400 = {
  data: ApiErrorResponseApi
  status: 400
}

export type modelHubAnnotationQueuesExportToDatasetResponse403 = {
  data: ApiErrorResponseApi
  status: 403
}

export type modelHubAnnotationQueuesExportToDatasetResponse404 = {
  data: ApiErrorResponseApi
  status: 404
}

export type modelHubAnnotationQueuesExportToDatasetResponse409 = {
  data: ApiErrorResponseApi
  status: 409
}

export type modelHubAnnotationQueuesExportToDatasetResponse500 = {
  data: ApiErrorResponseApi
  status: 500
}

export type modelHubAnnotationQueuesExportToDatasetResponseSuccess = (modelHubAnnotationQueuesExportToDatasetResponse200) & {
  headers: Headers;
};
export type modelHubAnnotationQueuesExportToDatasetResponseError = (modelHubAnnotationQueuesExportToDatasetResponse400 | modelHubAnnotationQueuesExportToDatasetResponse403 | modelHubAnnotationQueuesExportToDatasetResponse404 | modelHubAnnotationQueuesExportToDatasetResponse409 | modelHubAnnotationQueuesExportToDatasetResponse500) & {
  headers: Headers;
};

export type modelHubAnnotationQueuesExportToDatasetResponse = (modelHubAnnotationQueuesExportToDatasetResponseSuccess | modelHubAnnotationQueuesExportToDatasetResponseError)

export const getModelHubAnnotationQueuesExportToDatasetUrl = (id: string,) => {




  return `/model-hub/annotation-queues/${id}/export-to-dataset/`
}

/**
 * Export queue items to a dataset using a user-editable column mapping.
 */
export const modelHubAnnotationQueuesExportToDataset = async (id: string,
    queueExportToDatasetRequestApi: QueueExportToDatasetRequestApi, options?: RequestInit): Promise<modelHubAnnotationQueuesExportToDatasetResponse> => {

  return apiMutator<modelHubAnnotationQueuesExportToDatasetResponse>(getModelHubAnnotationQueuesExportToDatasetUrl(id),
  {
    ...options,
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...options?.headers },
    body: JSON.stringify(
      queueExportToDatasetRequestApi,)
  }
);}



export type modelHubAnnotationQueuesExportAnnotationsResponse200 = {
  data: QueueExportAnnotationsResponseApi
  status: 200
}

export type modelHubAnnotationQueuesExportAnnotationsResponse400 = {
  data: ApiErrorResponseApi
  status: 400
}

export type modelHubAnnotationQueuesExportAnnotationsResponse403 = {
  data: ApiErrorResponseApi
  status: 403
}

export type modelHubAnnotationQueuesExportAnnotationsResponse404 = {
  data: ApiErrorResponseApi
  status: 404
}

export type modelHubAnnotationQueuesExportAnnotationsResponse409 = {
  data: ApiErrorResponseApi
  status: 409
}

export type modelHubAnnotationQueuesExportAnnotationsResponse500 = {
  data: ApiErrorResponseApi
  status: 500
}

export type modelHubAnnotationQueuesExportAnnotationsResponseSuccess = (modelHubAnnotationQueuesExportAnnotationsResponse200) & {
  headers: Headers;
};
export type modelHubAnnotationQueuesExportAnnotationsResponseError = (modelHubAnnotationQueuesExportAnnotationsResponse400 | modelHubAnnotationQueuesExportAnnotationsResponse403 | modelHubAnnotationQueuesExportAnnotationsResponse404 | modelHubAnnotationQueuesExportAnnotationsResponse409 | modelHubAnnotationQueuesExportAnnotationsResponse500) & {
  headers: Headers;
};

export type modelHubAnnotationQueuesExportAnnotationsResponse = (modelHubAnnotationQueuesExportAnnotationsResponseSuccess | modelHubAnnotationQueuesExportAnnotationsResponseError)

export const getModelHubAnnotationQueuesExportAnnotationsUrl = (id: string,
    params?: ModelHubAnnotationQueuesExportAnnotationsParams,) => {
  const normalizedParams = new URLSearchParams();

  Object.entries(params || {}).forEach(([key, value]) => {

    if (value !== undefined) {
      normalizedParams.append(key, value === null ? 'null' : value.toString())
    }
  });

  const stringifiedParams = normalizedParams.toString();

  return stringifiedParams.length > 0 ? `/model-hub/annotation-queues/${id}/export/?${stringifiedParams}` : `/model-hub/annotation-queues/${id}/export/`
}

/**
 * Export all items with their annotations.
 */
export const modelHubAnnotationQueuesExportAnnotations = async (id: string,
    params?: ModelHubAnnotationQueuesExportAnnotationsParams, options?: RequestInit): Promise<modelHubAnnotationQueuesExportAnnotationsResponse> => {

  return apiMutator<modelHubAnnotationQueuesExportAnnotationsResponse>(getModelHubAnnotationQueuesExportAnnotationsUrl(id,params),
  {
    ...options,
    method: 'GET'


  }
);}



export type modelHubAnnotationQueuesHardDeleteResponse200 = {
  data: QueueHardDeleteResponseApi
  status: 200
}

export type modelHubAnnotationQueuesHardDeleteResponse400 = {
  data: ApiErrorResponseApi
  status: 400
}

export type modelHubAnnotationQueuesHardDeleteResponse403 = {
  data: ApiErrorResponseApi
  status: 403
}

export type modelHubAnnotationQueuesHardDeleteResponse404 = {
  data: ApiErrorResponseApi
  status: 404
}

export type modelHubAnnotationQueuesHardDeleteResponse409 = {
  data: ApiErrorResponseApi
  status: 409
}

export type modelHubAnnotationQueuesHardDeleteResponse500 = {
  data: ApiErrorResponseApi
  status: 500
}

export type modelHubAnnotationQueuesHardDeleteResponseSuccess = (modelHubAnnotationQueuesHardDeleteResponse200) & {
  headers: Headers;
};
export type modelHubAnnotationQueuesHardDeleteResponseError = (modelHubAnnotationQueuesHardDeleteResponse400 | modelHubAnnotationQueuesHardDeleteResponse403 | modelHubAnnotationQueuesHardDeleteResponse404 | modelHubAnnotationQueuesHardDeleteResponse409 | modelHubAnnotationQueuesHardDeleteResponse500) & {
  headers: Headers;
};

export type modelHubAnnotationQueuesHardDeleteResponse = (modelHubAnnotationQueuesHardDeleteResponseSuccess | modelHubAnnotationQueuesHardDeleteResponseError)

export const getModelHubAnnotationQueuesHardDeleteUrl = (id: string,) => {




  return `/model-hub/annotation-queues/${id}/hard-delete/`
}

/**
 * Hard delete cascades through the FK graph (rules, items,
assignments, scores) via ``on_delete=CASCADE``. There is no
recovery — callers must pass ``force=true`` AND the queue's
exact name as ``confirm_name`` so the action can't fire from
a typo'd request.
 * @summary Permanently remove a queue + everything attached.
 */
export const modelHubAnnotationQueuesHardDelete = async (id: string,
    queueHardDeleteRequestApi: QueueHardDeleteRequestApi, options?: RequestInit): Promise<modelHubAnnotationQueuesHardDeleteResponse> => {

  return apiMutator<modelHubAnnotationQueuesHardDeleteResponse>(getModelHubAnnotationQueuesHardDeleteUrl(id),
  {
    ...options,
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...options?.headers },
    body: JSON.stringify(
      queueHardDeleteRequestApi,)
  }
);}



export type modelHubAnnotationQueuesProgressResponse200 = {
  data: QueueProgressResponseApi
  status: 200
}

export type modelHubAnnotationQueuesProgressResponse400 = {
  data: ApiErrorResponseApi
  status: 400
}

export type modelHubAnnotationQueuesProgressResponse403 = {
  data: ApiErrorResponseApi
  status: 403
}

export type modelHubAnnotationQueuesProgressResponse404 = {
  data: ApiErrorResponseApi
  status: 404
}

export type modelHubAnnotationQueuesProgressResponse409 = {
  data: ApiErrorResponseApi
  status: 409
}

export type modelHubAnnotationQueuesProgressResponse500 = {
  data: ApiErrorResponseApi
  status: 500
}

export type modelHubAnnotationQueuesProgressResponseSuccess = (modelHubAnnotationQueuesProgressResponse200) & {
  headers: Headers;
};
export type modelHubAnnotationQueuesProgressResponseError = (modelHubAnnotationQueuesProgressResponse400 | modelHubAnnotationQueuesProgressResponse403 | modelHubAnnotationQueuesProgressResponse404 | modelHubAnnotationQueuesProgressResponse409 | modelHubAnnotationQueuesProgressResponse500) & {
  headers: Headers;
};

export type modelHubAnnotationQueuesProgressResponse = (modelHubAnnotationQueuesProgressResponseSuccess | modelHubAnnotationQueuesProgressResponseError)

export const getModelHubAnnotationQueuesProgressUrl = (id: string,) => {




  return `/model-hub/annotation-queues/${id}/progress/`
}

export const modelHubAnnotationQueuesProgress = async (id: string, options?: RequestInit): Promise<modelHubAnnotationQueuesProgressResponse> => {

  return apiMutator<modelHubAnnotationQueuesProgressResponse>(getModelHubAnnotationQueuesProgressUrl(id),
  {
    ...options,
    method: 'GET'


  }
);}



export type modelHubAnnotationQueuesRemoveLabelResponse200 = {
  data: QueueRemoveLabelResponseApi
  status: 200
}

export type modelHubAnnotationQueuesRemoveLabelResponse400 = {
  data: ApiErrorResponseApi
  status: 400
}

export type modelHubAnnotationQueuesRemoveLabelResponse403 = {
  data: ApiErrorResponseApi
  status: 403
}

export type modelHubAnnotationQueuesRemoveLabelResponse404 = {
  data: ApiErrorResponseApi
  status: 404
}

export type modelHubAnnotationQueuesRemoveLabelResponse409 = {
  data: ApiErrorResponseApi
  status: 409
}

export type modelHubAnnotationQueuesRemoveLabelResponse500 = {
  data: ApiErrorResponseApi
  status: 500
}

export type modelHubAnnotationQueuesRemoveLabelResponseSuccess = (modelHubAnnotationQueuesRemoveLabelResponse200) & {
  headers: Headers;
};
export type modelHubAnnotationQueuesRemoveLabelResponseError = (modelHubAnnotationQueuesRemoveLabelResponse400 | modelHubAnnotationQueuesRemoveLabelResponse403 | modelHubAnnotationQueuesRemoveLabelResponse404 | modelHubAnnotationQueuesRemoveLabelResponse409 | modelHubAnnotationQueuesRemoveLabelResponse500) & {
  headers: Headers;
};

export type modelHubAnnotationQueuesRemoveLabelResponse = (modelHubAnnotationQueuesRemoveLabelResponseSuccess | modelHubAnnotationQueuesRemoveLabelResponseError)

export const getModelHubAnnotationQueuesRemoveLabelUrl = (id: string,) => {




  return `/model-hub/annotation-queues/${id}/remove-label/`
}

/**
 * Remove a label from an annotation queue.
 */
export const modelHubAnnotationQueuesRemoveLabel = async (id: string,
    queueLabelRequestApi: QueueLabelRequestApi, options?: RequestInit): Promise<modelHubAnnotationQueuesRemoveLabelResponse> => {

  return apiMutator<modelHubAnnotationQueuesRemoveLabelResponse>(getModelHubAnnotationQueuesRemoveLabelUrl(id),
  {
    ...options,
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...options?.headers },
    body: JSON.stringify(
      queueLabelRequestApi,)
  }
);}



export type modelHubAnnotationQueuesRestoreResponse200 = {
  data: QueueStatusResponseApi
  status: 200
}

export type modelHubAnnotationQueuesRestoreResponse400 = {
  data: ApiErrorResponseApi
  status: 400
}

export type modelHubAnnotationQueuesRestoreResponse403 = {
  data: ApiErrorResponseApi
  status: 403
}

export type modelHubAnnotationQueuesRestoreResponse404 = {
  data: ApiErrorResponseApi
  status: 404
}

export type modelHubAnnotationQueuesRestoreResponse409 = {
  data: ApiErrorResponseApi
  status: 409
}

export type modelHubAnnotationQueuesRestoreResponse500 = {
  data: ApiErrorResponseApi
  status: 500
}

export type modelHubAnnotationQueuesRestoreResponseSuccess = (modelHubAnnotationQueuesRestoreResponse200) & {
  headers: Headers;
};
export type modelHubAnnotationQueuesRestoreResponseError = (modelHubAnnotationQueuesRestoreResponse400 | modelHubAnnotationQueuesRestoreResponse403 | modelHubAnnotationQueuesRestoreResponse404 | modelHubAnnotationQueuesRestoreResponse409 | modelHubAnnotationQueuesRestoreResponse500) & {
  headers: Headers;
};

export type modelHubAnnotationQueuesRestoreResponse = (modelHubAnnotationQueuesRestoreResponseSuccess | modelHubAnnotationQueuesRestoreResponseError)

export const getModelHubAnnotationQueuesRestoreUrl = (id: string,) => {




  return `/model-hub/annotation-queues/${id}/restore/`
}

export const modelHubAnnotationQueuesRestore = async (id: string, options?: RequestInit): Promise<modelHubAnnotationQueuesRestoreResponse> => {

  return apiMutator<modelHubAnnotationQueuesRestoreResponse>(getModelHubAnnotationQueuesRestoreUrl(id),
  {
    ...options,
    method: 'POST'


  }
);}



export type modelHubAnnotationQueuesUpdateStatusResponse200 = {
  data: QueueStatusResponseApi
  status: 200
}

export type modelHubAnnotationQueuesUpdateStatusResponse400 = {
  data: ApiErrorResponseApi
  status: 400
}

export type modelHubAnnotationQueuesUpdateStatusResponse403 = {
  data: ApiErrorResponseApi
  status: 403
}

export type modelHubAnnotationQueuesUpdateStatusResponse404 = {
  data: ApiErrorResponseApi
  status: 404
}

export type modelHubAnnotationQueuesUpdateStatusResponse409 = {
  data: ApiErrorResponseApi
  status: 409
}

export type modelHubAnnotationQueuesUpdateStatusResponse500 = {
  data: ApiErrorResponseApi
  status: 500
}

export type modelHubAnnotationQueuesUpdateStatusResponseSuccess = (modelHubAnnotationQueuesUpdateStatusResponse200) & {
  headers: Headers;
};
export type modelHubAnnotationQueuesUpdateStatusResponseError = (modelHubAnnotationQueuesUpdateStatusResponse400 | modelHubAnnotationQueuesUpdateStatusResponse403 | modelHubAnnotationQueuesUpdateStatusResponse404 | modelHubAnnotationQueuesUpdateStatusResponse409 | modelHubAnnotationQueuesUpdateStatusResponse500) & {
  headers: Headers;
};

export type modelHubAnnotationQueuesUpdateStatusResponse = (modelHubAnnotationQueuesUpdateStatusResponseSuccess | modelHubAnnotationQueuesUpdateStatusResponseError)

export const getModelHubAnnotationQueuesUpdateStatusUrl = (id: string,) => {




  return `/model-hub/annotation-queues/${id}/update-status/`
}

export const modelHubAnnotationQueuesUpdateStatus = async (id: string,
    queueStatusRequestApi: QueueStatusRequestApi, options?: RequestInit): Promise<modelHubAnnotationQueuesUpdateStatusResponse> => {

  return apiMutator<modelHubAnnotationQueuesUpdateStatusResponse>(getModelHubAnnotationQueuesUpdateStatusUrl(id),
  {
    ...options,
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...options?.headers },
    body: JSON.stringify(
      queueStatusRequestApi,)
  }
);}



export type modelHubAnnotationQueuesAutomationRulesListResponse200 = {
  data: ModelHubAnnotationQueuesAutomationRulesList200
  status: 200
}

export type modelHubAnnotationQueuesAutomationRulesListResponseSuccess = (modelHubAnnotationQueuesAutomationRulesListResponse200) & {
  headers: Headers;
};
;

export type modelHubAnnotationQueuesAutomationRulesListResponse = (modelHubAnnotationQueuesAutomationRulesListResponseSuccess)

export const getModelHubAnnotationQueuesAutomationRulesListUrl = (queueId: string,
    params?: ModelHubAnnotationQueuesAutomationRulesListParams,) => {
  const normalizedParams = new URLSearchParams();

  Object.entries(params || {}).forEach(([key, value]) => {

    if (value !== undefined) {
      normalizedParams.append(key, value === null ? 'null' : value.toString())
    }
  });

  const stringifiedParams = normalizedParams.toString();

  return stringifiedParams.length > 0 ? `/model-hub/annotation-queues/${queueId}/automation-rules/?${stringifiedParams}` : `/model-hub/annotation-queues/${queueId}/automation-rules/`
}

export const modelHubAnnotationQueuesAutomationRulesList = async (queueId: string,
    params?: ModelHubAnnotationQueuesAutomationRulesListParams, options?: RequestInit): Promise<modelHubAnnotationQueuesAutomationRulesListResponse> => {

  return apiMutator<modelHubAnnotationQueuesAutomationRulesListResponse>(getModelHubAnnotationQueuesAutomationRulesListUrl(queueId,params),
  {
    ...options,
    method: 'GET'


  }
);}



export type modelHubAnnotationQueuesAutomationRulesCreateResponse201 = {
  data: AutomationRuleApi
  status: 201
}

export type modelHubAnnotationQueuesAutomationRulesCreateResponseSuccess = (modelHubAnnotationQueuesAutomationRulesCreateResponse201) & {
  headers: Headers;
};
;

export type modelHubAnnotationQueuesAutomationRulesCreateResponse = (modelHubAnnotationQueuesAutomationRulesCreateResponseSuccess)

export const getModelHubAnnotationQueuesAutomationRulesCreateUrl = (queueId: string,) => {




  return `/model-hub/annotation-queues/${queueId}/automation-rules/`
}

export const modelHubAnnotationQueuesAutomationRulesCreate = async (queueId: string,
    automationRuleApi: NonReadonly<AutomationRuleApi>, options?: RequestInit): Promise<modelHubAnnotationQueuesAutomationRulesCreateResponse> => {

  return apiMutator<modelHubAnnotationQueuesAutomationRulesCreateResponse>(getModelHubAnnotationQueuesAutomationRulesCreateUrl(queueId),
  {
    ...options,
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...options?.headers },
    body: JSON.stringify(
      automationRuleApi,)
  }
);}



export type modelHubAnnotationQueuesAutomationRulesReadResponse200 = {
  data: AutomationRuleApi
  status: 200
}

export type modelHubAnnotationQueuesAutomationRulesReadResponseSuccess = (modelHubAnnotationQueuesAutomationRulesReadResponse200) & {
  headers: Headers;
};
;

export type modelHubAnnotationQueuesAutomationRulesReadResponse = (modelHubAnnotationQueuesAutomationRulesReadResponseSuccess)

export const getModelHubAnnotationQueuesAutomationRulesReadUrl = (queueId: string,
    id: string,) => {




  return `/model-hub/annotation-queues/${queueId}/automation-rules/${id}/`
}

export const modelHubAnnotationQueuesAutomationRulesRead = async (queueId: string,
    id: string, options?: RequestInit): Promise<modelHubAnnotationQueuesAutomationRulesReadResponse> => {

  return apiMutator<modelHubAnnotationQueuesAutomationRulesReadResponse>(getModelHubAnnotationQueuesAutomationRulesReadUrl(queueId,id),
  {
    ...options,
    method: 'GET'


  }
);}



export type modelHubAnnotationQueuesAutomationRulesUpdateResponse200 = {
  data: AutomationRuleApi
  status: 200
}

export type modelHubAnnotationQueuesAutomationRulesUpdateResponseSuccess = (modelHubAnnotationQueuesAutomationRulesUpdateResponse200) & {
  headers: Headers;
};
;

export type modelHubAnnotationQueuesAutomationRulesUpdateResponse = (modelHubAnnotationQueuesAutomationRulesUpdateResponseSuccess)

export const getModelHubAnnotationQueuesAutomationRulesUpdateUrl = (queueId: string,
    id: string,) => {




  return `/model-hub/annotation-queues/${queueId}/automation-rules/${id}/`
}

export const modelHubAnnotationQueuesAutomationRulesUpdate = async (queueId: string,
    id: string,
    automationRuleApi: NonReadonly<AutomationRuleApi>, options?: RequestInit): Promise<modelHubAnnotationQueuesAutomationRulesUpdateResponse> => {

  return apiMutator<modelHubAnnotationQueuesAutomationRulesUpdateResponse>(getModelHubAnnotationQueuesAutomationRulesUpdateUrl(queueId,id),
  {
    ...options,
    method: 'PUT',
    headers: { 'Content-Type': 'application/json', ...options?.headers },
    body: JSON.stringify(
      automationRuleApi,)
  }
);}



export type modelHubAnnotationQueuesAutomationRulesPartialUpdateResponse200 = {
  data: AutomationRuleApi
  status: 200
}

export type modelHubAnnotationQueuesAutomationRulesPartialUpdateResponseSuccess = (modelHubAnnotationQueuesAutomationRulesPartialUpdateResponse200) & {
  headers: Headers;
};
;

export type modelHubAnnotationQueuesAutomationRulesPartialUpdateResponse = (modelHubAnnotationQueuesAutomationRulesPartialUpdateResponseSuccess)

export const getModelHubAnnotationQueuesAutomationRulesPartialUpdateUrl = (queueId: string,
    id: string,) => {




  return `/model-hub/annotation-queues/${queueId}/automation-rules/${id}/`
}

export const modelHubAnnotationQueuesAutomationRulesPartialUpdate = async (queueId: string,
    id: string,
    automationRuleApi: NonReadonly<AutomationRuleApi>, options?: RequestInit): Promise<modelHubAnnotationQueuesAutomationRulesPartialUpdateResponse> => {

  return apiMutator<modelHubAnnotationQueuesAutomationRulesPartialUpdateResponse>(getModelHubAnnotationQueuesAutomationRulesPartialUpdateUrl(queueId,id),
  {
    ...options,
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json', ...options?.headers },
    body: JSON.stringify(
      automationRuleApi,)
  }
);}



export type modelHubAnnotationQueuesAutomationRulesDeleteResponse204 = {
  data: void
  status: 204
}

export type modelHubAnnotationQueuesAutomationRulesDeleteResponseSuccess = (modelHubAnnotationQueuesAutomationRulesDeleteResponse204) & {
  headers: Headers;
};
;

export type modelHubAnnotationQueuesAutomationRulesDeleteResponse = (modelHubAnnotationQueuesAutomationRulesDeleteResponseSuccess)

export const getModelHubAnnotationQueuesAutomationRulesDeleteUrl = (queueId: string,
    id: string,) => {




  return `/model-hub/annotation-queues/${queueId}/automation-rules/${id}/`
}

export const modelHubAnnotationQueuesAutomationRulesDelete = async (queueId: string,
    id: string, options?: RequestInit): Promise<modelHubAnnotationQueuesAutomationRulesDeleteResponse> => {

  return apiMutator<modelHubAnnotationQueuesAutomationRulesDeleteResponse>(getModelHubAnnotationQueuesAutomationRulesDeleteUrl(queueId,id),
  {
    ...options,
    method: 'DELETE'


  }
);}



export type modelHubAnnotationQueuesAutomationRulesEvaluateResponse200 = {
  data: QueueJsonResponseApi
  status: 200
}

export type modelHubAnnotationQueuesAutomationRulesEvaluateResponse202 = {
  data: AutomationRuleEvaluateAcceptedResponseApi
  status: 202
}

export type modelHubAnnotationQueuesAutomationRulesEvaluateResponse400 = {
  data: ApiErrorResponseApi
  status: 400
}

export type modelHubAnnotationQueuesAutomationRulesEvaluateResponse403 = {
  data: ApiErrorResponseApi
  status: 403
}

export type modelHubAnnotationQueuesAutomationRulesEvaluateResponse404 = {
  data: ApiErrorResponseApi
  status: 404
}

export type modelHubAnnotationQueuesAutomationRulesEvaluateResponse409 = {
  data: ApiErrorResponseApi
  status: 409
}

export type modelHubAnnotationQueuesAutomationRulesEvaluateResponse500 = {
  data: ApiErrorResponseApi
  status: 500
}

export type modelHubAnnotationQueuesAutomationRulesEvaluateResponseSuccess = (modelHubAnnotationQueuesAutomationRulesEvaluateResponse200 | modelHubAnnotationQueuesAutomationRulesEvaluateResponse202) & {
  headers: Headers;
};
export type modelHubAnnotationQueuesAutomationRulesEvaluateResponseError = (modelHubAnnotationQueuesAutomationRulesEvaluateResponse400 | modelHubAnnotationQueuesAutomationRulesEvaluateResponse403 | modelHubAnnotationQueuesAutomationRulesEvaluateResponse404 | modelHubAnnotationQueuesAutomationRulesEvaluateResponse409 | modelHubAnnotationQueuesAutomationRulesEvaluateResponse500) & {
  headers: Headers;
};

export type modelHubAnnotationQueuesAutomationRulesEvaluateResponse = (modelHubAnnotationQueuesAutomationRulesEvaluateResponseSuccess | modelHubAnnotationQueuesAutomationRulesEvaluateResponseError)

export const getModelHubAnnotationQueuesAutomationRulesEvaluateUrl = (queueId: string,
    id: string,) => {




  return `/model-hub/annotation-queues/${queueId}/automation-rules/${id}/evaluate/`
}

/**
 * Small runs (filter resolves to ≤ ``RULE_RUN_SYNC_THRESHOLD``) finish
in the HTTP request and return 200 with the result — fast feedback
for the common case. Large runs (mostly first-ever runs on backlogs
or rules with wide filters) hand the work to a Temporal activity and
return 202 immediately. The activity emails creator + queue managers
on completion.

The peek is a cheap dry-run (``[:cap+1]`` LIMIT, no COUNT(*)) — sub-
100ms even on 10M+ row trace tables — so this branch costs little
even when it ends up taking the sync path.
 * @summary Trigger a manual rule run with a sync-or-async branch.
 */
export const modelHubAnnotationQueuesAutomationRulesEvaluate = async (queueId: string,
    id: string, options?: RequestInit): Promise<modelHubAnnotationQueuesAutomationRulesEvaluateResponse> => {

  return apiMutator<modelHubAnnotationQueuesAutomationRulesEvaluateResponse>(getModelHubAnnotationQueuesAutomationRulesEvaluateUrl(queueId,id),
  {
    ...options,
    method: 'POST'


  }
);}



export type modelHubAnnotationQueuesAutomationRulesPreviewResponse200 = {
  data: QueueJsonResponseApi
  status: 200
}

export type modelHubAnnotationQueuesAutomationRulesPreviewResponse400 = {
  data: ApiErrorResponseApi
  status: 400
}

export type modelHubAnnotationQueuesAutomationRulesPreviewResponse403 = {
  data: ApiErrorResponseApi
  status: 403
}

export type modelHubAnnotationQueuesAutomationRulesPreviewResponse404 = {
  data: ApiErrorResponseApi
  status: 404
}

export type modelHubAnnotationQueuesAutomationRulesPreviewResponse409 = {
  data: ApiErrorResponseApi
  status: 409
}

export type modelHubAnnotationQueuesAutomationRulesPreviewResponse500 = {
  data: ApiErrorResponseApi
  status: 500
}

export type modelHubAnnotationQueuesAutomationRulesPreviewResponseSuccess = (modelHubAnnotationQueuesAutomationRulesPreviewResponse200) & {
  headers: Headers;
};
export type modelHubAnnotationQueuesAutomationRulesPreviewResponseError = (modelHubAnnotationQueuesAutomationRulesPreviewResponse400 | modelHubAnnotationQueuesAutomationRulesPreviewResponse403 | modelHubAnnotationQueuesAutomationRulesPreviewResponse404 | modelHubAnnotationQueuesAutomationRulesPreviewResponse409 | modelHubAnnotationQueuesAutomationRulesPreviewResponse500) & {
  headers: Headers;
};

export type modelHubAnnotationQueuesAutomationRulesPreviewResponse = (modelHubAnnotationQueuesAutomationRulesPreviewResponseSuccess | modelHubAnnotationQueuesAutomationRulesPreviewResponseError)

export const getModelHubAnnotationQueuesAutomationRulesPreviewUrl = (queueId: string,
    id: string,) => {




  return `/model-hub/annotation-queues/${queueId}/automation-rules/${id}/preview/`
}

/**
 * Preview how many items match a rule (dry run).
 */
export const modelHubAnnotationQueuesAutomationRulesPreview = async (queueId: string,
    id: string, options?: RequestInit): Promise<modelHubAnnotationQueuesAutomationRulesPreviewResponse> => {

  return apiMutator<modelHubAnnotationQueuesAutomationRulesPreviewResponse>(getModelHubAnnotationQueuesAutomationRulesPreviewUrl(queueId,id),
  {
    ...options,
    method: 'GET'


  }
);}



export type modelHubAnnotationQueuesItemsListResponse200 = {
  data: ModelHubAnnotationQueuesItemsList200
  status: 200
}

export type modelHubAnnotationQueuesItemsListResponseSuccess = (modelHubAnnotationQueuesItemsListResponse200) & {
  headers: Headers;
};
;

export type modelHubAnnotationQueuesItemsListResponse = (modelHubAnnotationQueuesItemsListResponseSuccess)

export const getModelHubAnnotationQueuesItemsListUrl = (queueId: string,
    params?: ModelHubAnnotationQueuesItemsListParams,) => {
  const normalizedParams = new URLSearchParams();

  Object.entries(params || {}).forEach(([key, value]) => {

    if (value !== undefined) {
      normalizedParams.append(key, value === null ? 'null' : value.toString())
    }
  });

  const stringifiedParams = normalizedParams.toString();

  return stringifiedParams.length > 0 ? `/model-hub/annotation-queues/${queueId}/items/?${stringifiedParams}` : `/model-hub/annotation-queues/${queueId}/items/`
}

export const modelHubAnnotationQueuesItemsList = async (queueId: string,
    params?: ModelHubAnnotationQueuesItemsListParams, options?: RequestInit): Promise<modelHubAnnotationQueuesItemsListResponse> => {

  return apiMutator<modelHubAnnotationQueuesItemsListResponse>(getModelHubAnnotationQueuesItemsListUrl(queueId,params),
  {
    ...options,
    method: 'GET'


  }
);}



export type modelHubAnnotationQueuesItemsCreateResponse201 = {
  data: QueueItemApi
  status: 201
}

export type modelHubAnnotationQueuesItemsCreateResponseSuccess = (modelHubAnnotationQueuesItemsCreateResponse201) & {
  headers: Headers;
};
;

export type modelHubAnnotationQueuesItemsCreateResponse = (modelHubAnnotationQueuesItemsCreateResponseSuccess)

export const getModelHubAnnotationQueuesItemsCreateUrl = (queueId: string,) => {




  return `/model-hub/annotation-queues/${queueId}/items/`
}

export const modelHubAnnotationQueuesItemsCreate = async (queueId: string,
    queueItemApi: NonReadonly<QueueItemApi>, options?: RequestInit): Promise<modelHubAnnotationQueuesItemsCreateResponse> => {

  return apiMutator<modelHubAnnotationQueuesItemsCreateResponse>(getModelHubAnnotationQueuesItemsCreateUrl(queueId),
  {
    ...options,
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...options?.headers },
    body: JSON.stringify(
      queueItemApi,)
  }
);}



export type modelHubAnnotationQueuesItemsAddItemsResponse200 = {
  data: QueueAddItemsResponseApi
  status: 200
}

export type modelHubAnnotationQueuesItemsAddItemsResponse400 = {
  data: ApiSelectionTooLargeErrorApi
  status: 400
}

export type modelHubAnnotationQueuesItemsAddItemsResponse403 = {
  data: ApiErrorResponseApi
  status: 403
}

export type modelHubAnnotationQueuesItemsAddItemsResponse404 = {
  data: ApiErrorResponseApi
  status: 404
}

export type modelHubAnnotationQueuesItemsAddItemsResponseSuccess = (modelHubAnnotationQueuesItemsAddItemsResponse200) & {
  headers: Headers;
};
export type modelHubAnnotationQueuesItemsAddItemsResponseError = (modelHubAnnotationQueuesItemsAddItemsResponse400 | modelHubAnnotationQueuesItemsAddItemsResponse403 | modelHubAnnotationQueuesItemsAddItemsResponse404) & {
  headers: Headers;
};

export type modelHubAnnotationQueuesItemsAddItemsResponse = (modelHubAnnotationQueuesItemsAddItemsResponseSuccess | modelHubAnnotationQueuesItemsAddItemsResponseError)

export const getModelHubAnnotationQueuesItemsAddItemsUrl = (queueId: string,) => {




  return `/model-hub/annotation-queues/${queueId}/items/add-items/`
}

export const modelHubAnnotationQueuesItemsAddItems = async (queueId: string,
    addItemsApi: AddItemsApi, options?: RequestInit): Promise<modelHubAnnotationQueuesItemsAddItemsResponse> => {

  return apiMutator<modelHubAnnotationQueuesItemsAddItemsResponse>(getModelHubAnnotationQueuesItemsAddItemsUrl(queueId),
  {
    ...options,
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...options?.headers },
    body: JSON.stringify(
      addItemsApi,)
  }
);}



export type modelHubAnnotationQueuesItemsAssignItemsResponse200 = {
  data: QueueAssignItemsResponseApi
  status: 200
}

export type modelHubAnnotationQueuesItemsAssignItemsResponse400 = {
  data: ApiErrorResponseApi
  status: 400
}

export type modelHubAnnotationQueuesItemsAssignItemsResponse403 = {
  data: ApiErrorResponseApi
  status: 403
}

export type modelHubAnnotationQueuesItemsAssignItemsResponse404 = {
  data: ApiErrorResponseApi
  status: 404
}

export type modelHubAnnotationQueuesItemsAssignItemsResponse409 = {
  data: ApiErrorResponseApi
  status: 409
}

export type modelHubAnnotationQueuesItemsAssignItemsResponse500 = {
  data: ApiErrorResponseApi
  status: 500
}

export type modelHubAnnotationQueuesItemsAssignItemsResponseSuccess = (modelHubAnnotationQueuesItemsAssignItemsResponse200) & {
  headers: Headers;
};
export type modelHubAnnotationQueuesItemsAssignItemsResponseError = (modelHubAnnotationQueuesItemsAssignItemsResponse400 | modelHubAnnotationQueuesItemsAssignItemsResponse403 | modelHubAnnotationQueuesItemsAssignItemsResponse404 | modelHubAnnotationQueuesItemsAssignItemsResponse409 | modelHubAnnotationQueuesItemsAssignItemsResponse500) & {
  headers: Headers;
};

export type modelHubAnnotationQueuesItemsAssignItemsResponse = (modelHubAnnotationQueuesItemsAssignItemsResponseSuccess | modelHubAnnotationQueuesItemsAssignItemsResponseError)

export const getModelHubAnnotationQueuesItemsAssignItemsUrl = (queueId: string,) => {




  return `/model-hub/annotation-queues/${queueId}/items/assign/`
}

/**
 * Assign items to one or more annotators.
 */
export const modelHubAnnotationQueuesItemsAssignItems = async (queueId: string,
    assignItemsApi: AssignItemsApi, options?: RequestInit): Promise<modelHubAnnotationQueuesItemsAssignItemsResponse> => {

  return apiMutator<modelHubAnnotationQueuesItemsAssignItemsResponse>(getModelHubAnnotationQueuesItemsAssignItemsUrl(queueId),
  {
    ...options,
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...options?.headers },
    body: JSON.stringify(
      assignItemsApi,)
  }
);}



export type modelHubAnnotationQueuesItemsBulkRemoveResponse200 = {
  data: QueueBulkRemoveItemsResponseApi
  status: 200
}

export type modelHubAnnotationQueuesItemsBulkRemoveResponse400 = {
  data: ApiErrorResponseApi
  status: 400
}

export type modelHubAnnotationQueuesItemsBulkRemoveResponse403 = {
  data: ApiErrorResponseApi
  status: 403
}

export type modelHubAnnotationQueuesItemsBulkRemoveResponse404 = {
  data: ApiErrorResponseApi
  status: 404
}

export type modelHubAnnotationQueuesItemsBulkRemoveResponse409 = {
  data: ApiErrorResponseApi
  status: 409
}

export type modelHubAnnotationQueuesItemsBulkRemoveResponse500 = {
  data: ApiErrorResponseApi
  status: 500
}

export type modelHubAnnotationQueuesItemsBulkRemoveResponseSuccess = (modelHubAnnotationQueuesItemsBulkRemoveResponse200) & {
  headers: Headers;
};
export type modelHubAnnotationQueuesItemsBulkRemoveResponseError = (modelHubAnnotationQueuesItemsBulkRemoveResponse400 | modelHubAnnotationQueuesItemsBulkRemoveResponse403 | modelHubAnnotationQueuesItemsBulkRemoveResponse404 | modelHubAnnotationQueuesItemsBulkRemoveResponse409 | modelHubAnnotationQueuesItemsBulkRemoveResponse500) & {
  headers: Headers;
};

export type modelHubAnnotationQueuesItemsBulkRemoveResponse = (modelHubAnnotationQueuesItemsBulkRemoveResponseSuccess | modelHubAnnotationQueuesItemsBulkRemoveResponseError)

export const getModelHubAnnotationQueuesItemsBulkRemoveUrl = (queueId: string,) => {




  return `/model-hub/annotation-queues/${queueId}/items/bulk-remove/`
}

export const modelHubAnnotationQueuesItemsBulkRemove = async (queueId: string,
    bulkRemoveItemsApi: BulkRemoveItemsApi, options?: RequestInit): Promise<modelHubAnnotationQueuesItemsBulkRemoveResponse> => {

  return apiMutator<modelHubAnnotationQueuesItemsBulkRemoveResponse>(getModelHubAnnotationQueuesItemsBulkRemoveUrl(queueId),
  {
    ...options,
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...options?.headers },
    body: JSON.stringify(
      bulkRemoveItemsApi,)
  }
);}



export type modelHubAnnotationQueuesItemsNextItemResponse200 = {
  data: QueueNextItemResponseApi
  status: 200
}

export type modelHubAnnotationQueuesItemsNextItemResponse400 = {
  data: ApiErrorResponseApi
  status: 400
}

export type modelHubAnnotationQueuesItemsNextItemResponse403 = {
  data: ApiErrorResponseApi
  status: 403
}

export type modelHubAnnotationQueuesItemsNextItemResponse404 = {
  data: ApiErrorResponseApi
  status: 404
}

export type modelHubAnnotationQueuesItemsNextItemResponse409 = {
  data: ApiErrorResponseApi
  status: 409
}

export type modelHubAnnotationQueuesItemsNextItemResponse500 = {
  data: ApiErrorResponseApi
  status: 500
}

export type modelHubAnnotationQueuesItemsNextItemResponseSuccess = (modelHubAnnotationQueuesItemsNextItemResponse200) & {
  headers: Headers;
};
export type modelHubAnnotationQueuesItemsNextItemResponseError = (modelHubAnnotationQueuesItemsNextItemResponse400 | modelHubAnnotationQueuesItemsNextItemResponse403 | modelHubAnnotationQueuesItemsNextItemResponse404 | modelHubAnnotationQueuesItemsNextItemResponse409 | modelHubAnnotationQueuesItemsNextItemResponse500) & {
  headers: Headers;
};

export type modelHubAnnotationQueuesItemsNextItemResponse = (modelHubAnnotationQueuesItemsNextItemResponseSuccess | modelHubAnnotationQueuesItemsNextItemResponseError)

export const getModelHubAnnotationQueuesItemsNextItemUrl = (queueId: string,
    params?: ModelHubAnnotationQueuesItemsNextItemParams,) => {
  const normalizedParams = new URLSearchParams();

  Object.entries(params || {}).forEach(([key, value]) => {

    if (value !== undefined) {
      normalizedParams.append(key, value === null ? 'null' : value.toString())
    }
  });

  const stringifiedParams = normalizedParams.toString();

  return stringifiedParams.length > 0 ? `/model-hub/annotation-queues/${queueId}/items/next-item/?${stringifiedParams}` : `/model-hub/annotation-queues/${queueId}/items/next-item/`
}

/**
 * Query params:
  exclude: comma-separated item IDs to skip
  before:  item ID — returns the item immediately before this one in order
  review_status: optional review status filter (for reviewer queues)
  exclude_review_status: optional review status to omit (for annotator queues)
  include_completed: when true, navigation can visit completed items too
 * @summary Get the next or previous item in the queue.
 */
export const modelHubAnnotationQueuesItemsNextItem = async (queueId: string,
    params?: ModelHubAnnotationQueuesItemsNextItemParams, options?: RequestInit): Promise<modelHubAnnotationQueuesItemsNextItemResponse> => {

  return apiMutator<modelHubAnnotationQueuesItemsNextItemResponse>(getModelHubAnnotationQueuesItemsNextItemUrl(queueId,params),
  {
    ...options,
    method: 'GET'


  }
);}



export type modelHubAnnotationQueuesItemsReadResponse200 = {
  data: QueueItemApi
  status: 200
}

export type modelHubAnnotationQueuesItemsReadResponseSuccess = (modelHubAnnotationQueuesItemsReadResponse200) & {
  headers: Headers;
};
;

export type modelHubAnnotationQueuesItemsReadResponse = (modelHubAnnotationQueuesItemsReadResponseSuccess)

export const getModelHubAnnotationQueuesItemsReadUrl = (queueId: string,
    id: string,) => {




  return `/model-hub/annotation-queues/${queueId}/items/${id}/`
}

export const modelHubAnnotationQueuesItemsRead = async (queueId: string,
    id: string, options?: RequestInit): Promise<modelHubAnnotationQueuesItemsReadResponse> => {

  return apiMutator<modelHubAnnotationQueuesItemsReadResponse>(getModelHubAnnotationQueuesItemsReadUrl(queueId,id),
  {
    ...options,
    method: 'GET'


  }
);}



export type modelHubAnnotationQueuesItemsUpdateResponse200 = {
  data: QueueItemApi
  status: 200
}

export type modelHubAnnotationQueuesItemsUpdateResponseSuccess = (modelHubAnnotationQueuesItemsUpdateResponse200) & {
  headers: Headers;
};
;

export type modelHubAnnotationQueuesItemsUpdateResponse = (modelHubAnnotationQueuesItemsUpdateResponseSuccess)

export const getModelHubAnnotationQueuesItemsUpdateUrl = (queueId: string,
    id: string,) => {




  return `/model-hub/annotation-queues/${queueId}/items/${id}/`
}

export const modelHubAnnotationQueuesItemsUpdate = async (queueId: string,
    id: string,
    queueItemApi: NonReadonly<QueueItemApi>, options?: RequestInit): Promise<modelHubAnnotationQueuesItemsUpdateResponse> => {

  return apiMutator<modelHubAnnotationQueuesItemsUpdateResponse>(getModelHubAnnotationQueuesItemsUpdateUrl(queueId,id),
  {
    ...options,
    method: 'PUT',
    headers: { 'Content-Type': 'application/json', ...options?.headers },
    body: JSON.stringify(
      queueItemApi,)
  }
);}



export type modelHubAnnotationQueuesItemsPartialUpdateResponse200 = {
  data: QueueItemApi
  status: 200
}

export type modelHubAnnotationQueuesItemsPartialUpdateResponseSuccess = (modelHubAnnotationQueuesItemsPartialUpdateResponse200) & {
  headers: Headers;
};
;

export type modelHubAnnotationQueuesItemsPartialUpdateResponse = (modelHubAnnotationQueuesItemsPartialUpdateResponseSuccess)

export const getModelHubAnnotationQueuesItemsPartialUpdateUrl = (queueId: string,
    id: string,) => {




  return `/model-hub/annotation-queues/${queueId}/items/${id}/`
}

export const modelHubAnnotationQueuesItemsPartialUpdate = async (queueId: string,
    id: string,
    queueItemApi: NonReadonly<QueueItemApi>, options?: RequestInit): Promise<modelHubAnnotationQueuesItemsPartialUpdateResponse> => {

  return apiMutator<modelHubAnnotationQueuesItemsPartialUpdateResponse>(getModelHubAnnotationQueuesItemsPartialUpdateUrl(queueId,id),
  {
    ...options,
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json', ...options?.headers },
    body: JSON.stringify(
      queueItemApi,)
  }
);}



export type modelHubAnnotationQueuesItemsDeleteResponse204 = {
  data: void
  status: 204
}

export type modelHubAnnotationQueuesItemsDeleteResponseSuccess = (modelHubAnnotationQueuesItemsDeleteResponse204) & {
  headers: Headers;
};
;

export type modelHubAnnotationQueuesItemsDeleteResponse = (modelHubAnnotationQueuesItemsDeleteResponseSuccess)

export const getModelHubAnnotationQueuesItemsDeleteUrl = (queueId: string,
    id: string,) => {




  return `/model-hub/annotation-queues/${queueId}/items/${id}/`
}

export const modelHubAnnotationQueuesItemsDelete = async (queueId: string,
    id: string, options?: RequestInit): Promise<modelHubAnnotationQueuesItemsDeleteResponse> => {

  return apiMutator<modelHubAnnotationQueuesItemsDeleteResponse>(getModelHubAnnotationQueuesItemsDeleteUrl(queueId,id),
  {
    ...options,
    method: 'DELETE'


  }
);}



export type modelHubAnnotationQueuesItemsAnnotateDetailResponse200 = {
  data: QueueAnnotateDetailResponseApi
  status: 200
}

export type modelHubAnnotationQueuesItemsAnnotateDetailResponse400 = {
  data: ApiErrorResponseApi
  status: 400
}

export type modelHubAnnotationQueuesItemsAnnotateDetailResponse403 = {
  data: ApiErrorResponseApi
  status: 403
}

export type modelHubAnnotationQueuesItemsAnnotateDetailResponse404 = {
  data: ApiErrorResponseApi
  status: 404
}

export type modelHubAnnotationQueuesItemsAnnotateDetailResponse409 = {
  data: ApiErrorResponseApi
  status: 409
}

export type modelHubAnnotationQueuesItemsAnnotateDetailResponse500 = {
  data: ApiErrorResponseApi
  status: 500
}

export type modelHubAnnotationQueuesItemsAnnotateDetailResponseSuccess = (modelHubAnnotationQueuesItemsAnnotateDetailResponse200) & {
  headers: Headers;
};
export type modelHubAnnotationQueuesItemsAnnotateDetailResponseError = (modelHubAnnotationQueuesItemsAnnotateDetailResponse400 | modelHubAnnotationQueuesItemsAnnotateDetailResponse403 | modelHubAnnotationQueuesItemsAnnotateDetailResponse404 | modelHubAnnotationQueuesItemsAnnotateDetailResponse409 | modelHubAnnotationQueuesItemsAnnotateDetailResponse500) & {
  headers: Headers;
};

export type modelHubAnnotationQueuesItemsAnnotateDetailResponse = (modelHubAnnotationQueuesItemsAnnotateDetailResponseSuccess | modelHubAnnotationQueuesItemsAnnotateDetailResponseError)

export const getModelHubAnnotationQueuesItemsAnnotateDetailUrl = (queueId: string,
    id: string,
    params?: ModelHubAnnotationQueuesItemsAnnotateDetailParams,) => {
  const normalizedParams = new URLSearchParams();

  Object.entries(params || {}).forEach(([key, value]) => {

    if (value !== undefined) {
      normalizedParams.append(key, value === null ? 'null' : value.toString())
    }
  });

  const stringifiedParams = normalizedParams.toString();

  return stringifiedParams.length > 0 ? `/model-hub/annotation-queues/${queueId}/items/${id}/annotate-detail/?${stringifiedParams}` : `/model-hub/annotation-queues/${queueId}/items/${id}/annotate-detail/`
}

/**
 * Get full annotation workspace data for an item.
 */
export const modelHubAnnotationQueuesItemsAnnotateDetail = async (queueId: string,
    id: string,
    params?: ModelHubAnnotationQueuesItemsAnnotateDetailParams, options?: RequestInit): Promise<modelHubAnnotationQueuesItemsAnnotateDetailResponse> => {

  return apiMutator<modelHubAnnotationQueuesItemsAnnotateDetailResponse>(getModelHubAnnotationQueuesItemsAnnotateDetailUrl(queueId,id,params),
  {
    ...options,
    method: 'GET'


  }
);}



export type modelHubAnnotationQueuesItemsAnnotationsListResponse200 = {
  data: QueueItemAnnotationsResponseApi
  status: 200
}

export type modelHubAnnotationQueuesItemsAnnotationsListResponse400 = {
  data: ApiErrorResponseApi
  status: 400
}

export type modelHubAnnotationQueuesItemsAnnotationsListResponse403 = {
  data: ApiErrorResponseApi
  status: 403
}

export type modelHubAnnotationQueuesItemsAnnotationsListResponse404 = {
  data: ApiErrorResponseApi
  status: 404
}

export type modelHubAnnotationQueuesItemsAnnotationsListResponse409 = {
  data: ApiErrorResponseApi
  status: 409
}

export type modelHubAnnotationQueuesItemsAnnotationsListResponse500 = {
  data: ApiErrorResponseApi
  status: 500
}

export type modelHubAnnotationQueuesItemsAnnotationsListResponseSuccess = (modelHubAnnotationQueuesItemsAnnotationsListResponse200) & {
  headers: Headers;
};
export type modelHubAnnotationQueuesItemsAnnotationsListResponseError = (modelHubAnnotationQueuesItemsAnnotationsListResponse400 | modelHubAnnotationQueuesItemsAnnotationsListResponse403 | modelHubAnnotationQueuesItemsAnnotationsListResponse404 | modelHubAnnotationQueuesItemsAnnotationsListResponse409 | modelHubAnnotationQueuesItemsAnnotationsListResponse500) & {
  headers: Headers;
};

export type modelHubAnnotationQueuesItemsAnnotationsListResponse = (modelHubAnnotationQueuesItemsAnnotationsListResponseSuccess | modelHubAnnotationQueuesItemsAnnotationsListResponseError)

export const getModelHubAnnotationQueuesItemsAnnotationsListUrl = (queueId: string,
    id: string,) => {




  return `/model-hub/annotation-queues/${queueId}/items/${id}/annotations/`
}

/**
 * List all annotations for a queue item (across all annotators).
 */
export const modelHubAnnotationQueuesItemsAnnotationsList = async (queueId: string,
    id: string, options?: RequestInit): Promise<modelHubAnnotationQueuesItemsAnnotationsListResponse> => {

  return apiMutator<modelHubAnnotationQueuesItemsAnnotationsListResponse>(getModelHubAnnotationQueuesItemsAnnotationsListUrl(queueId,id),
  {
    ...options,
    method: 'GET'


  }
);}



export type modelHubAnnotationQueuesItemsAnnotationsImportAnnotationsResponse200 = {
  data: QueueImportAnnotationsResponseApi
  status: 200
}

export type modelHubAnnotationQueuesItemsAnnotationsImportAnnotationsResponse400 = {
  data: ApiErrorResponseApi
  status: 400
}

export type modelHubAnnotationQueuesItemsAnnotationsImportAnnotationsResponse403 = {
  data: ApiErrorResponseApi
  status: 403
}

export type modelHubAnnotationQueuesItemsAnnotationsImportAnnotationsResponse404 = {
  data: ApiErrorResponseApi
  status: 404
}

export type modelHubAnnotationQueuesItemsAnnotationsImportAnnotationsResponse409 = {
  data: ApiErrorResponseApi
  status: 409
}

export type modelHubAnnotationQueuesItemsAnnotationsImportAnnotationsResponse500 = {
  data: ApiErrorResponseApi
  status: 500
}

export type modelHubAnnotationQueuesItemsAnnotationsImportAnnotationsResponseSuccess = (modelHubAnnotationQueuesItemsAnnotationsImportAnnotationsResponse200) & {
  headers: Headers;
};
export type modelHubAnnotationQueuesItemsAnnotationsImportAnnotationsResponseError = (modelHubAnnotationQueuesItemsAnnotationsImportAnnotationsResponse400 | modelHubAnnotationQueuesItemsAnnotationsImportAnnotationsResponse403 | modelHubAnnotationQueuesItemsAnnotationsImportAnnotationsResponse404 | modelHubAnnotationQueuesItemsAnnotationsImportAnnotationsResponse409 | modelHubAnnotationQueuesItemsAnnotationsImportAnnotationsResponse500) & {
  headers: Headers;
};

export type modelHubAnnotationQueuesItemsAnnotationsImportAnnotationsResponse = (modelHubAnnotationQueuesItemsAnnotationsImportAnnotationsResponseSuccess | modelHubAnnotationQueuesItemsAnnotationsImportAnnotationsResponseError)

export const getModelHubAnnotationQueuesItemsAnnotationsImportAnnotationsUrl = (queueId: string,
    id: string,) => {




  return `/model-hub/annotation-queues/${queueId}/items/${id}/annotations/import/`
}

/**
 * Import annotations from external sources.
 */
export const modelHubAnnotationQueuesItemsAnnotationsImportAnnotations = async (queueId: string,
    id: string,
    importAnnotationsApi: ImportAnnotationsApi, options?: RequestInit): Promise<modelHubAnnotationQueuesItemsAnnotationsImportAnnotationsResponse> => {

  return apiMutator<modelHubAnnotationQueuesItemsAnnotationsImportAnnotationsResponse>(getModelHubAnnotationQueuesItemsAnnotationsImportAnnotationsUrl(queueId,id),
  {
    ...options,
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...options?.headers },
    body: JSON.stringify(
      importAnnotationsApi,)
  }
);}



export type modelHubAnnotationQueuesItemsAnnotationsSubmitAnnotationsResponse200 = {
  data: QueueSubmitAnnotationsResponseApi
  status: 200
}

export type modelHubAnnotationQueuesItemsAnnotationsSubmitAnnotationsResponse400 = {
  data: ApiErrorResponseApi
  status: 400
}

export type modelHubAnnotationQueuesItemsAnnotationsSubmitAnnotationsResponse403 = {
  data: ApiErrorResponseApi
  status: 403
}

export type modelHubAnnotationQueuesItemsAnnotationsSubmitAnnotationsResponse404 = {
  data: ApiErrorResponseApi
  status: 404
}

export type modelHubAnnotationQueuesItemsAnnotationsSubmitAnnotationsResponse409 = {
  data: ApiErrorResponseApi
  status: 409
}

export type modelHubAnnotationQueuesItemsAnnotationsSubmitAnnotationsResponse500 = {
  data: ApiErrorResponseApi
  status: 500
}

export type modelHubAnnotationQueuesItemsAnnotationsSubmitAnnotationsResponseSuccess = (modelHubAnnotationQueuesItemsAnnotationsSubmitAnnotationsResponse200) & {
  headers: Headers;
};
export type modelHubAnnotationQueuesItemsAnnotationsSubmitAnnotationsResponseError = (modelHubAnnotationQueuesItemsAnnotationsSubmitAnnotationsResponse400 | modelHubAnnotationQueuesItemsAnnotationsSubmitAnnotationsResponse403 | modelHubAnnotationQueuesItemsAnnotationsSubmitAnnotationsResponse404 | modelHubAnnotationQueuesItemsAnnotationsSubmitAnnotationsResponse409 | modelHubAnnotationQueuesItemsAnnotationsSubmitAnnotationsResponse500) & {
  headers: Headers;
};

export type modelHubAnnotationQueuesItemsAnnotationsSubmitAnnotationsResponse = (modelHubAnnotationQueuesItemsAnnotationsSubmitAnnotationsResponseSuccess | modelHubAnnotationQueuesItemsAnnotationsSubmitAnnotationsResponseError)

export const getModelHubAnnotationQueuesItemsAnnotationsSubmitAnnotationsUrl = (queueId: string,
    id: string,) => {




  return `/model-hub/annotation-queues/${queueId}/items/${id}/annotations/submit/`
}

/**
 * Submit or update annotations for a queue item.
 */
export const modelHubAnnotationQueuesItemsAnnotationsSubmitAnnotations = async (queueId: string,
    id: string,
    submitAnnotationsApi: SubmitAnnotationsApi, options?: RequestInit): Promise<modelHubAnnotationQueuesItemsAnnotationsSubmitAnnotationsResponse> => {

  return apiMutator<modelHubAnnotationQueuesItemsAnnotationsSubmitAnnotationsResponse>(getModelHubAnnotationQueuesItemsAnnotationsSubmitAnnotationsUrl(queueId,id),
  {
    ...options,
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...options?.headers },
    body: JSON.stringify(
      submitAnnotationsApi,)
  }
);}



export type modelHubAnnotationQueuesItemsCompleteItemResponse200 = {
  data: QueueNavigationResponseApi
  status: 200
}

export type modelHubAnnotationQueuesItemsCompleteItemResponse400 = {
  data: ApiErrorResponseApi
  status: 400
}

export type modelHubAnnotationQueuesItemsCompleteItemResponse403 = {
  data: ApiErrorResponseApi
  status: 403
}

export type modelHubAnnotationQueuesItemsCompleteItemResponse404 = {
  data: ApiErrorResponseApi
  status: 404
}

export type modelHubAnnotationQueuesItemsCompleteItemResponse409 = {
  data: ApiErrorResponseApi
  status: 409
}

export type modelHubAnnotationQueuesItemsCompleteItemResponse500 = {
  data: ApiErrorResponseApi
  status: 500
}

export type modelHubAnnotationQueuesItemsCompleteItemResponseSuccess = (modelHubAnnotationQueuesItemsCompleteItemResponse200) & {
  headers: Headers;
};
export type modelHubAnnotationQueuesItemsCompleteItemResponseError = (modelHubAnnotationQueuesItemsCompleteItemResponse400 | modelHubAnnotationQueuesItemsCompleteItemResponse403 | modelHubAnnotationQueuesItemsCompleteItemResponse404 | modelHubAnnotationQueuesItemsCompleteItemResponse409 | modelHubAnnotationQueuesItemsCompleteItemResponse500) & {
  headers: Headers;
};

export type modelHubAnnotationQueuesItemsCompleteItemResponse = (modelHubAnnotationQueuesItemsCompleteItemResponseSuccess | modelHubAnnotationQueuesItemsCompleteItemResponseError)

export const getModelHubAnnotationQueuesItemsCompleteItemUrl = (queueId: string,
    id: string,) => {




  return `/model-hub/annotation-queues/${queueId}/items/${id}/complete/`
}

/**
 * Mark item as completed and return next pending item.
 */
export const modelHubAnnotationQueuesItemsCompleteItem = async (queueId: string,
    id: string,
    queueItemNavigationRequestApi: QueueItemNavigationRequestApi, options?: RequestInit): Promise<modelHubAnnotationQueuesItemsCompleteItemResponse> => {

  return apiMutator<modelHubAnnotationQueuesItemsCompleteItemResponse>(getModelHubAnnotationQueuesItemsCompleteItemUrl(queueId,id),
  {
    ...options,
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...options?.headers },
    body: JSON.stringify(
      queueItemNavigationRequestApi,)
  }
);}



export type modelHubAnnotationQueuesItemsDiscussionReadResponse200 = {
  data: QueueDiscussionResponseApi
  status: 200
}

export type modelHubAnnotationQueuesItemsDiscussionReadResponse400 = {
  data: ApiErrorResponseApi
  status: 400
}

export type modelHubAnnotationQueuesItemsDiscussionReadResponse403 = {
  data: ApiErrorResponseApi
  status: 403
}

export type modelHubAnnotationQueuesItemsDiscussionReadResponse404 = {
  data: ApiErrorResponseApi
  status: 404
}

export type modelHubAnnotationQueuesItemsDiscussionReadResponse409 = {
  data: ApiErrorResponseApi
  status: 409
}

export type modelHubAnnotationQueuesItemsDiscussionReadResponse500 = {
  data: ApiErrorResponseApi
  status: 500
}

export type modelHubAnnotationQueuesItemsDiscussionReadResponseSuccess = (modelHubAnnotationQueuesItemsDiscussionReadResponse200) & {
  headers: Headers;
};
export type modelHubAnnotationQueuesItemsDiscussionReadResponseError = (modelHubAnnotationQueuesItemsDiscussionReadResponse400 | modelHubAnnotationQueuesItemsDiscussionReadResponse403 | modelHubAnnotationQueuesItemsDiscussionReadResponse404 | modelHubAnnotationQueuesItemsDiscussionReadResponse409 | modelHubAnnotationQueuesItemsDiscussionReadResponse500) & {
  headers: Headers;
};

export type modelHubAnnotationQueuesItemsDiscussionReadResponse = (modelHubAnnotationQueuesItemsDiscussionReadResponseSuccess | modelHubAnnotationQueuesItemsDiscussionReadResponseError)

export const getModelHubAnnotationQueuesItemsDiscussionReadUrl = (queueId: string,
    id: string,) => {




  return `/model-hub/annotation-queues/${queueId}/items/${id}/discussion/`
}

/**
 * List or create non-blocking discussion comments for a queue item.
 */
export const modelHubAnnotationQueuesItemsDiscussionRead = async (queueId: string,
    id: string, options?: RequestInit): Promise<modelHubAnnotationQueuesItemsDiscussionReadResponse> => {

  return apiMutator<modelHubAnnotationQueuesItemsDiscussionReadResponse>(getModelHubAnnotationQueuesItemsDiscussionReadUrl(queueId,id),
  {
    ...options,
    method: 'GET'


  }
);}



export type modelHubAnnotationQueuesItemsDiscussionCreateResponse200 = {
  data: QueueDiscussionResponseApi
  status: 200
}

export type modelHubAnnotationQueuesItemsDiscussionCreateResponse400 = {
  data: ApiErrorResponseApi
  status: 400
}

export type modelHubAnnotationQueuesItemsDiscussionCreateResponse403 = {
  data: ApiErrorResponseApi
  status: 403
}

export type modelHubAnnotationQueuesItemsDiscussionCreateResponse404 = {
  data: ApiErrorResponseApi
  status: 404
}

export type modelHubAnnotationQueuesItemsDiscussionCreateResponse409 = {
  data: ApiErrorResponseApi
  status: 409
}

export type modelHubAnnotationQueuesItemsDiscussionCreateResponse500 = {
  data: ApiErrorResponseApi
  status: 500
}

export type modelHubAnnotationQueuesItemsDiscussionCreateResponseSuccess = (modelHubAnnotationQueuesItemsDiscussionCreateResponse200) & {
  headers: Headers;
};
export type modelHubAnnotationQueuesItemsDiscussionCreateResponseError = (modelHubAnnotationQueuesItemsDiscussionCreateResponse400 | modelHubAnnotationQueuesItemsDiscussionCreateResponse403 | modelHubAnnotationQueuesItemsDiscussionCreateResponse404 | modelHubAnnotationQueuesItemsDiscussionCreateResponse409 | modelHubAnnotationQueuesItemsDiscussionCreateResponse500) & {
  headers: Headers;
};

export type modelHubAnnotationQueuesItemsDiscussionCreateResponse = (modelHubAnnotationQueuesItemsDiscussionCreateResponseSuccess | modelHubAnnotationQueuesItemsDiscussionCreateResponseError)

export const getModelHubAnnotationQueuesItemsDiscussionCreateUrl = (queueId: string,
    id: string,) => {




  return `/model-hub/annotation-queues/${queueId}/items/${id}/discussion/`
}

/**
 * List or create non-blocking discussion comments for a queue item.
 */
export const modelHubAnnotationQueuesItemsDiscussionCreate = async (queueId: string,
    id: string,
    discussionCommentRequestApi: DiscussionCommentRequestApi, options?: RequestInit): Promise<modelHubAnnotationQueuesItemsDiscussionCreateResponse> => {

  return apiMutator<modelHubAnnotationQueuesItemsDiscussionCreateResponse>(getModelHubAnnotationQueuesItemsDiscussionCreateUrl(queueId,id),
  {
    ...options,
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...options?.headers },
    body: JSON.stringify(
      discussionCommentRequestApi,)
  }
);}



export type modelHubAnnotationQueuesItemsDiscussionCommentsDiscussionCommentReactionResponse200 = {
  data: QueueDiscussionResponseApi
  status: 200
}

export type modelHubAnnotationQueuesItemsDiscussionCommentsDiscussionCommentReactionResponse400 = {
  data: ApiErrorResponseApi
  status: 400
}

export type modelHubAnnotationQueuesItemsDiscussionCommentsDiscussionCommentReactionResponse403 = {
  data: ApiErrorResponseApi
  status: 403
}

export type modelHubAnnotationQueuesItemsDiscussionCommentsDiscussionCommentReactionResponse404 = {
  data: ApiErrorResponseApi
  status: 404
}

export type modelHubAnnotationQueuesItemsDiscussionCommentsDiscussionCommentReactionResponse409 = {
  data: ApiErrorResponseApi
  status: 409
}

export type modelHubAnnotationQueuesItemsDiscussionCommentsDiscussionCommentReactionResponse500 = {
  data: ApiErrorResponseApi
  status: 500
}

export type modelHubAnnotationQueuesItemsDiscussionCommentsDiscussionCommentReactionResponseSuccess = (modelHubAnnotationQueuesItemsDiscussionCommentsDiscussionCommentReactionResponse200) & {
  headers: Headers;
};
export type modelHubAnnotationQueuesItemsDiscussionCommentsDiscussionCommentReactionResponseError = (modelHubAnnotationQueuesItemsDiscussionCommentsDiscussionCommentReactionResponse400 | modelHubAnnotationQueuesItemsDiscussionCommentsDiscussionCommentReactionResponse403 | modelHubAnnotationQueuesItemsDiscussionCommentsDiscussionCommentReactionResponse404 | modelHubAnnotationQueuesItemsDiscussionCommentsDiscussionCommentReactionResponse409 | modelHubAnnotationQueuesItemsDiscussionCommentsDiscussionCommentReactionResponse500) & {
  headers: Headers;
};

export type modelHubAnnotationQueuesItemsDiscussionCommentsDiscussionCommentReactionResponse = (modelHubAnnotationQueuesItemsDiscussionCommentsDiscussionCommentReactionResponseSuccess | modelHubAnnotationQueuesItemsDiscussionCommentsDiscussionCommentReactionResponseError)

export const getModelHubAnnotationQueuesItemsDiscussionCommentsDiscussionCommentReactionUrl = (queueId: string,
    id: string,
    commentId: string,) => {




  return `/model-hub/annotation-queues/${queueId}/items/${id}/discussion/comments/${commentId}/reaction/`
}

/**
 * Toggle the current user's reaction on a discussion comment.
 */
export const modelHubAnnotationQueuesItemsDiscussionCommentsDiscussionCommentReaction = async (queueId: string,
    id: string,
    commentId: string,
    discussionReactionRequestApi: DiscussionReactionRequestApi, options?: RequestInit): Promise<modelHubAnnotationQueuesItemsDiscussionCommentsDiscussionCommentReactionResponse> => {

  return apiMutator<modelHubAnnotationQueuesItemsDiscussionCommentsDiscussionCommentReactionResponse>(getModelHubAnnotationQueuesItemsDiscussionCommentsDiscussionCommentReactionUrl(queueId,id,commentId),
  {
    ...options,
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...options?.headers },
    body: JSON.stringify(
      discussionReactionRequestApi,)
  }
);}



export type modelHubAnnotationQueuesItemsDiscussionReopenDiscussionThreadResponse200 = {
  data: QueueDiscussionResponseApi
  status: 200
}

export type modelHubAnnotationQueuesItemsDiscussionReopenDiscussionThreadResponse400 = {
  data: ApiErrorResponseApi
  status: 400
}

export type modelHubAnnotationQueuesItemsDiscussionReopenDiscussionThreadResponse403 = {
  data: ApiErrorResponseApi
  status: 403
}

export type modelHubAnnotationQueuesItemsDiscussionReopenDiscussionThreadResponse404 = {
  data: ApiErrorResponseApi
  status: 404
}

export type modelHubAnnotationQueuesItemsDiscussionReopenDiscussionThreadResponse409 = {
  data: ApiErrorResponseApi
  status: 409
}

export type modelHubAnnotationQueuesItemsDiscussionReopenDiscussionThreadResponse500 = {
  data: ApiErrorResponseApi
  status: 500
}

export type modelHubAnnotationQueuesItemsDiscussionReopenDiscussionThreadResponseSuccess = (modelHubAnnotationQueuesItemsDiscussionReopenDiscussionThreadResponse200) & {
  headers: Headers;
};
export type modelHubAnnotationQueuesItemsDiscussionReopenDiscussionThreadResponseError = (modelHubAnnotationQueuesItemsDiscussionReopenDiscussionThreadResponse400 | modelHubAnnotationQueuesItemsDiscussionReopenDiscussionThreadResponse403 | modelHubAnnotationQueuesItemsDiscussionReopenDiscussionThreadResponse404 | modelHubAnnotationQueuesItemsDiscussionReopenDiscussionThreadResponse409 | modelHubAnnotationQueuesItemsDiscussionReopenDiscussionThreadResponse500) & {
  headers: Headers;
};

export type modelHubAnnotationQueuesItemsDiscussionReopenDiscussionThreadResponse = (modelHubAnnotationQueuesItemsDiscussionReopenDiscussionThreadResponseSuccess | modelHubAnnotationQueuesItemsDiscussionReopenDiscussionThreadResponseError)

export const getModelHubAnnotationQueuesItemsDiscussionReopenDiscussionThreadUrl = (queueId: string,
    id: string,
    threadId: string,) => {




  return `/model-hub/annotation-queues/${queueId}/items/${id}/discussion/${threadId}/reopen/`
}

export const modelHubAnnotationQueuesItemsDiscussionReopenDiscussionThread = async (queueId: string,
    id: string,
    threadId: string,
    discussionThreadStatusRequestApi: DiscussionThreadStatusRequestApi, options?: RequestInit): Promise<modelHubAnnotationQueuesItemsDiscussionReopenDiscussionThreadResponse> => {

  return apiMutator<modelHubAnnotationQueuesItemsDiscussionReopenDiscussionThreadResponse>(getModelHubAnnotationQueuesItemsDiscussionReopenDiscussionThreadUrl(queueId,id,threadId),
  {
    ...options,
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...options?.headers },
    body: JSON.stringify(
      discussionThreadStatusRequestApi,)
  }
);}



export type modelHubAnnotationQueuesItemsDiscussionResolveDiscussionThreadResponse200 = {
  data: QueueDiscussionResponseApi
  status: 200
}

export type modelHubAnnotationQueuesItemsDiscussionResolveDiscussionThreadResponse400 = {
  data: ApiErrorResponseApi
  status: 400
}

export type modelHubAnnotationQueuesItemsDiscussionResolveDiscussionThreadResponse403 = {
  data: ApiErrorResponseApi
  status: 403
}

export type modelHubAnnotationQueuesItemsDiscussionResolveDiscussionThreadResponse404 = {
  data: ApiErrorResponseApi
  status: 404
}

export type modelHubAnnotationQueuesItemsDiscussionResolveDiscussionThreadResponse409 = {
  data: ApiErrorResponseApi
  status: 409
}

export type modelHubAnnotationQueuesItemsDiscussionResolveDiscussionThreadResponse500 = {
  data: ApiErrorResponseApi
  status: 500
}

export type modelHubAnnotationQueuesItemsDiscussionResolveDiscussionThreadResponseSuccess = (modelHubAnnotationQueuesItemsDiscussionResolveDiscussionThreadResponse200) & {
  headers: Headers;
};
export type modelHubAnnotationQueuesItemsDiscussionResolveDiscussionThreadResponseError = (modelHubAnnotationQueuesItemsDiscussionResolveDiscussionThreadResponse400 | modelHubAnnotationQueuesItemsDiscussionResolveDiscussionThreadResponse403 | modelHubAnnotationQueuesItemsDiscussionResolveDiscussionThreadResponse404 | modelHubAnnotationQueuesItemsDiscussionResolveDiscussionThreadResponse409 | modelHubAnnotationQueuesItemsDiscussionResolveDiscussionThreadResponse500) & {
  headers: Headers;
};

export type modelHubAnnotationQueuesItemsDiscussionResolveDiscussionThreadResponse = (modelHubAnnotationQueuesItemsDiscussionResolveDiscussionThreadResponseSuccess | modelHubAnnotationQueuesItemsDiscussionResolveDiscussionThreadResponseError)

export const getModelHubAnnotationQueuesItemsDiscussionResolveDiscussionThreadUrl = (queueId: string,
    id: string,
    threadId: string,) => {




  return `/model-hub/annotation-queues/${queueId}/items/${id}/discussion/${threadId}/resolve/`
}

export const modelHubAnnotationQueuesItemsDiscussionResolveDiscussionThread = async (queueId: string,
    id: string,
    threadId: string,
    discussionThreadStatusRequestApi: DiscussionThreadStatusRequestApi, options?: RequestInit): Promise<modelHubAnnotationQueuesItemsDiscussionResolveDiscussionThreadResponse> => {

  return apiMutator<modelHubAnnotationQueuesItemsDiscussionResolveDiscussionThreadResponse>(getModelHubAnnotationQueuesItemsDiscussionResolveDiscussionThreadUrl(queueId,id,threadId),
  {
    ...options,
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...options?.headers },
    body: JSON.stringify(
      discussionThreadStatusRequestApi,)
  }
);}



export type modelHubAnnotationQueuesItemsReleaseReservationResponse200 = {
  data: QueueReleaseReservationResponseApi
  status: 200
}

export type modelHubAnnotationQueuesItemsReleaseReservationResponse400 = {
  data: ApiErrorResponseApi
  status: 400
}

export type modelHubAnnotationQueuesItemsReleaseReservationResponse403 = {
  data: ApiErrorResponseApi
  status: 403
}

export type modelHubAnnotationQueuesItemsReleaseReservationResponse404 = {
  data: ApiErrorResponseApi
  status: 404
}

export type modelHubAnnotationQueuesItemsReleaseReservationResponse409 = {
  data: ApiErrorResponseApi
  status: 409
}

export type modelHubAnnotationQueuesItemsReleaseReservationResponse500 = {
  data: ApiErrorResponseApi
  status: 500
}

export type modelHubAnnotationQueuesItemsReleaseReservationResponseSuccess = (modelHubAnnotationQueuesItemsReleaseReservationResponse200) & {
  headers: Headers;
};
export type modelHubAnnotationQueuesItemsReleaseReservationResponseError = (modelHubAnnotationQueuesItemsReleaseReservationResponse400 | modelHubAnnotationQueuesItemsReleaseReservationResponse403 | modelHubAnnotationQueuesItemsReleaseReservationResponse404 | modelHubAnnotationQueuesItemsReleaseReservationResponse409 | modelHubAnnotationQueuesItemsReleaseReservationResponse500) & {
  headers: Headers;
};

export type modelHubAnnotationQueuesItemsReleaseReservationResponse = (modelHubAnnotationQueuesItemsReleaseReservationResponseSuccess | modelHubAnnotationQueuesItemsReleaseReservationResponseError)

export const getModelHubAnnotationQueuesItemsReleaseReservationUrl = (queueId: string,
    id: string,) => {




  return `/model-hub/annotation-queues/${queueId}/items/${id}/release/`
}

/**
 * Release reservation on an item.
 */
export const modelHubAnnotationQueuesItemsReleaseReservation = async (queueId: string,
    id: string, options?: RequestInit): Promise<modelHubAnnotationQueuesItemsReleaseReservationResponse> => {

  return apiMutator<modelHubAnnotationQueuesItemsReleaseReservationResponse>(getModelHubAnnotationQueuesItemsReleaseReservationUrl(queueId,id),
  {
    ...options,
    method: 'POST'


  }
);}



export type modelHubAnnotationQueuesItemsReviewItemResponse200 = {
  data: QueueReviewItemResponseApi
  status: 200
}

export type modelHubAnnotationQueuesItemsReviewItemResponse400 = {
  data: ApiErrorResponseApi
  status: 400
}

export type modelHubAnnotationQueuesItemsReviewItemResponse403 = {
  data: ApiErrorResponseApi
  status: 403
}

export type modelHubAnnotationQueuesItemsReviewItemResponse404 = {
  data: ApiErrorResponseApi
  status: 404
}

export type modelHubAnnotationQueuesItemsReviewItemResponse409 = {
  data: ApiErrorResponseApi
  status: 409
}

export type modelHubAnnotationQueuesItemsReviewItemResponse500 = {
  data: ApiErrorResponseApi
  status: 500
}

export type modelHubAnnotationQueuesItemsReviewItemResponseSuccess = (modelHubAnnotationQueuesItemsReviewItemResponse200) & {
  headers: Headers;
};
export type modelHubAnnotationQueuesItemsReviewItemResponseError = (modelHubAnnotationQueuesItemsReviewItemResponse400 | modelHubAnnotationQueuesItemsReviewItemResponse403 | modelHubAnnotationQueuesItemsReviewItemResponse404 | modelHubAnnotationQueuesItemsReviewItemResponse409 | modelHubAnnotationQueuesItemsReviewItemResponse500) & {
  headers: Headers;
};

export type modelHubAnnotationQueuesItemsReviewItemResponse = (modelHubAnnotationQueuesItemsReviewItemResponseSuccess | modelHubAnnotationQueuesItemsReviewItemResponseError)

export const getModelHubAnnotationQueuesItemsReviewItemUrl = (queueId: string,
    id: string,) => {




  return `/model-hub/annotation-queues/${queueId}/items/${id}/review/`
}

/**
 * Approve, request changes, or leave reviewer feedback on an item.
 */
export const modelHubAnnotationQueuesItemsReviewItem = async (queueId: string,
    id: string,
    reviewItemRequestApi: ReviewItemRequestApi, options?: RequestInit): Promise<modelHubAnnotationQueuesItemsReviewItemResponse> => {

  return apiMutator<modelHubAnnotationQueuesItemsReviewItemResponse>(getModelHubAnnotationQueuesItemsReviewItemUrl(queueId,id),
  {
    ...options,
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...options?.headers },
    body: JSON.stringify(
      reviewItemRequestApi,)
  }
);}



export type modelHubAnnotationQueuesItemsSkipItemResponse200 = {
  data: QueueNavigationResponseApi
  status: 200
}

export type modelHubAnnotationQueuesItemsSkipItemResponse400 = {
  data: ApiErrorResponseApi
  status: 400
}

export type modelHubAnnotationQueuesItemsSkipItemResponse403 = {
  data: ApiErrorResponseApi
  status: 403
}

export type modelHubAnnotationQueuesItemsSkipItemResponse404 = {
  data: ApiErrorResponseApi
  status: 404
}

export type modelHubAnnotationQueuesItemsSkipItemResponse409 = {
  data: ApiErrorResponseApi
  status: 409
}

export type modelHubAnnotationQueuesItemsSkipItemResponse500 = {
  data: ApiErrorResponseApi
  status: 500
}

export type modelHubAnnotationQueuesItemsSkipItemResponseSuccess = (modelHubAnnotationQueuesItemsSkipItemResponse200) & {
  headers: Headers;
};
export type modelHubAnnotationQueuesItemsSkipItemResponseError = (modelHubAnnotationQueuesItemsSkipItemResponse400 | modelHubAnnotationQueuesItemsSkipItemResponse403 | modelHubAnnotationQueuesItemsSkipItemResponse404 | modelHubAnnotationQueuesItemsSkipItemResponse409 | modelHubAnnotationQueuesItemsSkipItemResponse500) & {
  headers: Headers;
};

export type modelHubAnnotationQueuesItemsSkipItemResponse = (modelHubAnnotationQueuesItemsSkipItemResponseSuccess | modelHubAnnotationQueuesItemsSkipItemResponseError)

export const getModelHubAnnotationQueuesItemsSkipItemUrl = (queueId: string,
    id: string,) => {




  return `/model-hub/annotation-queues/${queueId}/items/${id}/skip/`
}

/**
 * Mark item as skipped and return next pending item.
 */
export const modelHubAnnotationQueuesItemsSkipItem = async (queueId: string,
    id: string,
    queueItemNavigationRequestApi: QueueItemNavigationRequestApi, options?: RequestInit): Promise<modelHubAnnotationQueuesItemsSkipItemResponse> => {

  return apiMutator<modelHubAnnotationQueuesItemsSkipItemResponse>(getModelHubAnnotationQueuesItemsSkipItemUrl(queueId,id),
  {
    ...options,
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...options?.headers },
    body: JSON.stringify(
      queueItemNavigationRequestApi,)
  }
);}



export type modelHubAnnotationTasksListResponse200 = {
  data: ModelHubAnnotationTasksList200
  status: 200
}

export type modelHubAnnotationTasksListResponseSuccess = (modelHubAnnotationTasksListResponse200) & {
  headers: Headers;
};
;

export type modelHubAnnotationTasksListResponse = (modelHubAnnotationTasksListResponseSuccess)

export const getModelHubAnnotationTasksListUrl = (params?: ModelHubAnnotationTasksListParams,) => {
  const normalizedParams = new URLSearchParams();

  Object.entries(params || {}).forEach(([key, value]) => {

    if (value !== undefined) {
      normalizedParams.append(key, value === null ? 'null' : value.toString())
    }
  });

  const stringifiedParams = normalizedParams.toString();

  return stringifiedParams.length > 0 ? `/model-hub/annotation-tasks/?${stringifiedParams}` : `/model-hub/annotation-tasks/`
}

export const modelHubAnnotationTasksList = async (params?: ModelHubAnnotationTasksListParams, options?: RequestInit): Promise<modelHubAnnotationTasksListResponse> => {

  return apiMutator<modelHubAnnotationTasksListResponse>(getModelHubAnnotationTasksListUrl(params),
  {
    ...options,
    method: 'GET'


  }
);}



export type modelHubAnnotationTasksReadResponse200 = {
  data: AnnotationTaskApi
  status: 200
}

export type modelHubAnnotationTasksReadResponseSuccess = (modelHubAnnotationTasksReadResponse200) & {
  headers: Headers;
};
;

export type modelHubAnnotationTasksReadResponse = (modelHubAnnotationTasksReadResponseSuccess)

export const getModelHubAnnotationTasksReadUrl = (id: string,) => {




  return `/model-hub/annotation-tasks/${id}/`
}

export const modelHubAnnotationTasksRead = async (id: string, options?: RequestInit): Promise<modelHubAnnotationTasksReadResponse> => {

  return apiMutator<modelHubAnnotationTasksReadResponse>(getModelHubAnnotationTasksReadUrl(id),
  {
    ...options,
    method: 'GET'


  }
);}



export type modelHubAnnotationsLabelsListResponse200 = {
  data: ModelHubAnnotationsLabelsList200
  status: 200
}

export type modelHubAnnotationsLabelsListResponseSuccess = (modelHubAnnotationsLabelsListResponse200) & {
  headers: Headers;
};
;

export type modelHubAnnotationsLabelsListResponse = (modelHubAnnotationsLabelsListResponseSuccess)

export const getModelHubAnnotationsLabelsListUrl = (params?: ModelHubAnnotationsLabelsListParams,) => {
  const normalizedParams = new URLSearchParams();

  Object.entries(params || {}).forEach(([key, value]) => {

    if (value !== undefined) {
      normalizedParams.append(key, value === null ? 'null' : value.toString())
    }
  });

  const stringifiedParams = normalizedParams.toString();

  return stringifiedParams.length > 0 ? `/model-hub/annotations-labels/?${stringifiedParams}` : `/model-hub/annotations-labels/`
}

export const modelHubAnnotationsLabelsList = async (params?: ModelHubAnnotationsLabelsListParams, options?: RequestInit): Promise<modelHubAnnotationsLabelsListResponse> => {

  return apiMutator<modelHubAnnotationsLabelsListResponse>(getModelHubAnnotationsLabelsListUrl(params),
  {
    ...options,
    method: 'GET'


  }
);}



export type modelHubAnnotationsLabelsCreateResponse201 = {
  data: AnnotationsLabelsApi
  status: 201
}

export type modelHubAnnotationsLabelsCreateResponseSuccess = (modelHubAnnotationsLabelsCreateResponse201) & {
  headers: Headers;
};
;

export type modelHubAnnotationsLabelsCreateResponse = (modelHubAnnotationsLabelsCreateResponseSuccess)

export const getModelHubAnnotationsLabelsCreateUrl = () => {




  return `/model-hub/annotations-labels/`
}

/**
 * Custom create to provide clearer error responses in GM format.
 */
export const modelHubAnnotationsLabelsCreate = async (annotationsLabelsApi: NonReadonly<AnnotationsLabelsApi>, options?: RequestInit): Promise<modelHubAnnotationsLabelsCreateResponse> => {

  return apiMutator<modelHubAnnotationsLabelsCreateResponse>(getModelHubAnnotationsLabelsCreateUrl(),
  {
    ...options,
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...options?.headers },
    body: JSON.stringify(
      annotationsLabelsApi,)
  }
);}



export type modelHubAnnotationsLabelsReadResponse200 = {
  data: AnnotationsLabelsApi
  status: 200
}

export type modelHubAnnotationsLabelsReadResponseSuccess = (modelHubAnnotationsLabelsReadResponse200) & {
  headers: Headers;
};
;

export type modelHubAnnotationsLabelsReadResponse = (modelHubAnnotationsLabelsReadResponseSuccess)

export const getModelHubAnnotationsLabelsReadUrl = (id: string,) => {




  return `/model-hub/annotations-labels/${id}/`
}

export const modelHubAnnotationsLabelsRead = async (id: string, options?: RequestInit): Promise<modelHubAnnotationsLabelsReadResponse> => {

  return apiMutator<modelHubAnnotationsLabelsReadResponse>(getModelHubAnnotationsLabelsReadUrl(id),
  {
    ...options,
    method: 'GET'


  }
);}



export type modelHubAnnotationsLabelsUpdateResponse200 = {
  data: AnnotationsLabelsApi
  status: 200
}

export type modelHubAnnotationsLabelsUpdateResponseSuccess = (modelHubAnnotationsLabelsUpdateResponse200) & {
  headers: Headers;
};
;

export type modelHubAnnotationsLabelsUpdateResponse = (modelHubAnnotationsLabelsUpdateResponseSuccess)

export const getModelHubAnnotationsLabelsUpdateUrl = (id: string,) => {




  return `/model-hub/annotations-labels/${id}/`
}

export const modelHubAnnotationsLabelsUpdate = async (id: string,
    annotationsLabelsApi: NonReadonly<AnnotationsLabelsApi>, options?: RequestInit): Promise<modelHubAnnotationsLabelsUpdateResponse> => {

  return apiMutator<modelHubAnnotationsLabelsUpdateResponse>(getModelHubAnnotationsLabelsUpdateUrl(id),
  {
    ...options,
    method: 'PUT',
    headers: { 'Content-Type': 'application/json', ...options?.headers },
    body: JSON.stringify(
      annotationsLabelsApi,)
  }
);}



export type modelHubAnnotationsLabelsPartialUpdateResponse200 = {
  data: AnnotationsLabelsApi
  status: 200
}

export type modelHubAnnotationsLabelsPartialUpdateResponseSuccess = (modelHubAnnotationsLabelsPartialUpdateResponse200) & {
  headers: Headers;
};
;

export type modelHubAnnotationsLabelsPartialUpdateResponse = (modelHubAnnotationsLabelsPartialUpdateResponseSuccess)

export const getModelHubAnnotationsLabelsPartialUpdateUrl = (id: string,) => {




  return `/model-hub/annotations-labels/${id}/`
}

export const modelHubAnnotationsLabelsPartialUpdate = async (id: string,
    annotationsLabelsApi: NonReadonly<AnnotationsLabelsApi>, options?: RequestInit): Promise<modelHubAnnotationsLabelsPartialUpdateResponse> => {

  return apiMutator<modelHubAnnotationsLabelsPartialUpdateResponse>(getModelHubAnnotationsLabelsPartialUpdateUrl(id),
  {
    ...options,
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json', ...options?.headers },
    body: JSON.stringify(
      annotationsLabelsApi,)
  }
);}



export type modelHubAnnotationsLabelsDeleteResponse204 = {
  data: void
  status: 204
}

export type modelHubAnnotationsLabelsDeleteResponseSuccess = (modelHubAnnotationsLabelsDeleteResponse204) & {
  headers: Headers;
};
;

export type modelHubAnnotationsLabelsDeleteResponse = (modelHubAnnotationsLabelsDeleteResponseSuccess)

export const getModelHubAnnotationsLabelsDeleteUrl = (id: string,) => {




  return `/model-hub/annotations-labels/${id}/`
}

export const modelHubAnnotationsLabelsDelete = async (id: string, options?: RequestInit): Promise<modelHubAnnotationsLabelsDeleteResponse> => {

  return apiMutator<modelHubAnnotationsLabelsDeleteResponse>(getModelHubAnnotationsLabelsDeleteUrl(id),
  {
    ...options,
    method: 'DELETE'


  }
);}



export type modelHubAnnotationsLabelsRestoreResponse201 = {
  data: AnnotationsLabelsApi
  status: 201
}

export type modelHubAnnotationsLabelsRestoreResponseSuccess = (modelHubAnnotationsLabelsRestoreResponse201) & {
  headers: Headers;
};
;

export type modelHubAnnotationsLabelsRestoreResponse = (modelHubAnnotationsLabelsRestoreResponseSuccess)

export const getModelHubAnnotationsLabelsRestoreUrl = (id: string,) => {




  return `/model-hub/annotations-labels/${id}/restore/`
}

/**
 * Restore a soft-deleted (archived) annotation label.
 */
export const modelHubAnnotationsLabelsRestore = async (id: string, options?: RequestInit): Promise<modelHubAnnotationsLabelsRestoreResponse> => {

  return apiMutator<modelHubAnnotationsLabelsRestoreResponse>(getModelHubAnnotationsLabelsRestoreUrl(id),
  {
    ...options,
    method: 'POST'


  }
);}



export type modelHubAnnotationsListResponse200 = {
  data: ModelHubAnnotationsList200
  status: 200
}

export type modelHubAnnotationsListResponseSuccess = (modelHubAnnotationsListResponse200) & {
  headers: Headers;
};
;

export type modelHubAnnotationsListResponse = (modelHubAnnotationsListResponseSuccess)

export const getModelHubAnnotationsListUrl = (params?: ModelHubAnnotationsListParams,) => {
  const normalizedParams = new URLSearchParams();

  Object.entries(params || {}).forEach(([key, value]) => {

    if (value !== undefined) {
      normalizedParams.append(key, value === null ? 'null' : value.toString())
    }
  });

  const stringifiedParams = normalizedParams.toString();

  return stringifiedParams.length > 0 ? `/model-hub/annotations/?${stringifiedParams}` : `/model-hub/annotations/`
}

export const modelHubAnnotationsList = async (params?: ModelHubAnnotationsListParams, options?: RequestInit): Promise<modelHubAnnotationsListResponse> => {

  return apiMutator<modelHubAnnotationsListResponse>(getModelHubAnnotationsListUrl(params),
  {
    ...options,
    method: 'GET'


  }
);}



export type modelHubAnnotationsCreateResponse201 = {
  data: AnnotationsApi
  status: 201
}

export type modelHubAnnotationsCreateResponseSuccess = (modelHubAnnotationsCreateResponse201) & {
  headers: Headers;
};
;

export type modelHubAnnotationsCreateResponse = (modelHubAnnotationsCreateResponseSuccess)

export const getModelHubAnnotationsCreateUrl = () => {




  return `/model-hub/annotations/`
}

export const modelHubAnnotationsCreate = async (annotationsApi: NonReadonly<AnnotationsApi>, options?: RequestInit): Promise<modelHubAnnotationsCreateResponse> => {

  return apiMutator<modelHubAnnotationsCreateResponse>(getModelHubAnnotationsCreateUrl(),
  {
    ...options,
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...options?.headers },
    body: JSON.stringify(
      annotationsApi,)
  }
);}



export type modelHubAnnotationsBulkDestroyResponse201 = {
  data: AnnotationsApi
  status: 201
}

export type modelHubAnnotationsBulkDestroyResponseSuccess = (modelHubAnnotationsBulkDestroyResponse201) & {
  headers: Headers;
};
;

export type modelHubAnnotationsBulkDestroyResponse = (modelHubAnnotationsBulkDestroyResponseSuccess)

export const getModelHubAnnotationsBulkDestroyUrl = () => {




  return `/model-hub/annotations/bulk_destroy/`
}

/**
 * Bulk delete annotations and their associated data
Expected input: {"annotation_ids": ["uuid1", "uuid2", ...]}
 */
export const modelHubAnnotationsBulkDestroy = async (annotationsApi: NonReadonly<AnnotationsApi>, options?: RequestInit): Promise<modelHubAnnotationsBulkDestroyResponse> => {

  return apiMutator<modelHubAnnotationsBulkDestroyResponse>(getModelHubAnnotationsBulkDestroyUrl(),
  {
    ...options,
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...options?.headers },
    body: JSON.stringify(
      annotationsApi,)
  }
);}



export type modelHubAnnotationsPreviewAnnotationsResponse201 = {
  data: AnnotationsApi
  status: 201
}

export type modelHubAnnotationsPreviewAnnotationsResponseSuccess = (modelHubAnnotationsPreviewAnnotationsResponse201) & {
  headers: Headers;
};
;

export type modelHubAnnotationsPreviewAnnotationsResponse = (modelHubAnnotationsPreviewAnnotationsResponseSuccess)

export const getModelHubAnnotationsPreviewAnnotationsUrl = () => {




  return `/model-hub/annotations/preview_annotations/`
}

/**
 * Preview the first row of data for specified columns in a dataset.
 */
export const modelHubAnnotationsPreviewAnnotations = async (annotationsApi: NonReadonly<AnnotationsApi>, options?: RequestInit): Promise<modelHubAnnotationsPreviewAnnotationsResponse> => {

  return apiMutator<modelHubAnnotationsPreviewAnnotationsResponse>(getModelHubAnnotationsPreviewAnnotationsUrl(),
  {
    ...options,
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...options?.headers },
    body: JSON.stringify(
      annotationsApi,)
  }
);}



export type modelHubAnnotationsReadResponse200 = {
  data: AnnotationsApi
  status: 200
}

export type modelHubAnnotationsReadResponseSuccess = (modelHubAnnotationsReadResponse200) & {
  headers: Headers;
};
;

export type modelHubAnnotationsReadResponse = (modelHubAnnotationsReadResponseSuccess)

export const getModelHubAnnotationsReadUrl = (id: string,) => {




  return `/model-hub/annotations/${id}/`
}

export const modelHubAnnotationsRead = async (id: string, options?: RequestInit): Promise<modelHubAnnotationsReadResponse> => {

  return apiMutator<modelHubAnnotationsReadResponse>(getModelHubAnnotationsReadUrl(id),
  {
    ...options,
    method: 'GET'


  }
);}



export type modelHubAnnotationsUpdateResponse200 = {
  data: AnnotationsApi
  status: 200
}

export type modelHubAnnotationsUpdateResponseSuccess = (modelHubAnnotationsUpdateResponse200) & {
  headers: Headers;
};
;

export type modelHubAnnotationsUpdateResponse = (modelHubAnnotationsUpdateResponseSuccess)

export const getModelHubAnnotationsUpdateUrl = (id: string,) => {




  return `/model-hub/annotations/${id}/`
}

export const modelHubAnnotationsUpdate = async (id: string,
    annotationsApi: NonReadonly<AnnotationsApi>, options?: RequestInit): Promise<modelHubAnnotationsUpdateResponse> => {

  return apiMutator<modelHubAnnotationsUpdateResponse>(getModelHubAnnotationsUpdateUrl(id),
  {
    ...options,
    method: 'PUT',
    headers: { 'Content-Type': 'application/json', ...options?.headers },
    body: JSON.stringify(
      annotationsApi,)
  }
);}



export type modelHubAnnotationsPartialUpdateResponse200 = {
  data: AnnotationsApi
  status: 200
}

export type modelHubAnnotationsPartialUpdateResponseSuccess = (modelHubAnnotationsPartialUpdateResponse200) & {
  headers: Headers;
};
;

export type modelHubAnnotationsPartialUpdateResponse = (modelHubAnnotationsPartialUpdateResponseSuccess)

export const getModelHubAnnotationsPartialUpdateUrl = (id: string,) => {




  return `/model-hub/annotations/${id}/`
}

export const modelHubAnnotationsPartialUpdate = async (id: string,
    annotationsApi: NonReadonly<AnnotationsApi>, options?: RequestInit): Promise<modelHubAnnotationsPartialUpdateResponse> => {

  return apiMutator<modelHubAnnotationsPartialUpdateResponse>(getModelHubAnnotationsPartialUpdateUrl(id),
  {
    ...options,
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json', ...options?.headers },
    body: JSON.stringify(
      annotationsApi,)
  }
);}



export type modelHubAnnotationsDeleteResponse204 = {
  data: void
  status: 204
}

export type modelHubAnnotationsDeleteResponseSuccess = (modelHubAnnotationsDeleteResponse204) & {
  headers: Headers;
};
;

export type modelHubAnnotationsDeleteResponse = (modelHubAnnotationsDeleteResponseSuccess)

export const getModelHubAnnotationsDeleteUrl = (id: string,) => {




  return `/model-hub/annotations/${id}/`
}

export const modelHubAnnotationsDelete = async (id: string, options?: RequestInit): Promise<modelHubAnnotationsDeleteResponse> => {

  return apiMutator<modelHubAnnotationsDeleteResponse>(getModelHubAnnotationsDeleteUrl(id),
  {
    ...options,
    method: 'DELETE'


  }
);}



export type modelHubAnnotationsAnnotateRowResponse200 = {
  data: AnnotationsApi
  status: 200
}

export type modelHubAnnotationsAnnotateRowResponseSuccess = (modelHubAnnotationsAnnotateRowResponse200) & {
  headers: Headers;
};
;

export type modelHubAnnotationsAnnotateRowResponse = (modelHubAnnotationsAnnotateRowResponseSuccess)

export const getModelHubAnnotationsAnnotateRowUrl = (id: string,) => {




  return `/model-hub/annotations/${id}/annotate_row/`
}

/**
 * Annotate a specific row with the provided values.
 */
export const modelHubAnnotationsAnnotateRow = async (id: string, options?: RequestInit): Promise<modelHubAnnotationsAnnotateRowResponse> => {

  return apiMutator<modelHubAnnotationsAnnotateRowResponse>(getModelHubAnnotationsAnnotateRowUrl(id),
  {
    ...options,
    method: 'GET'


  }
);}



export type modelHubAnnotationsResetAnnotationsResponse201 = {
  data: AnnotationsApi
  status: 201
}

export type modelHubAnnotationsResetAnnotationsResponseSuccess = (modelHubAnnotationsResetAnnotationsResponse201) & {
  headers: Headers;
};
;

export type modelHubAnnotationsResetAnnotationsResponse = (modelHubAnnotationsResetAnnotationsResponseSuccess)

export const getModelHubAnnotationsResetAnnotationsUrl = (id: string,) => {




  return `/model-hub/annotations/${id}/reset_annotations/`
}

export const modelHubAnnotationsResetAnnotations = async (id: string,
    annotationsApi: NonReadonly<AnnotationsApi>, options?: RequestInit): Promise<modelHubAnnotationsResetAnnotationsResponse> => {

  return apiMutator<modelHubAnnotationsResetAnnotationsResponse>(getModelHubAnnotationsResetAnnotationsUrl(id),
  {
    ...options,
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...options?.headers },
    body: JSON.stringify(
      annotationsApi,)
  }
);}



export type modelHubAnnotationsUpdateCellsResponse201 = {
  data: AnnotationsApi
  status: 201
}

export type modelHubAnnotationsUpdateCellsResponseSuccess = (modelHubAnnotationsUpdateCellsResponse201) & {
  headers: Headers;
};
;

export type modelHubAnnotationsUpdateCellsResponse = (modelHubAnnotationsUpdateCellsResponseSuccess)

export const getModelHubAnnotationsUpdateCellsUrl = (id: string,) => {




  return `/model-hub/annotations/${id}/update_cells/`
}

export const modelHubAnnotationsUpdateCells = async (id: string,
    annotationsApi: NonReadonly<AnnotationsApi>, options?: RequestInit): Promise<modelHubAnnotationsUpdateCellsResponse> => {

  return apiMutator<modelHubAnnotationsUpdateCellsResponse>(getModelHubAnnotationsUpdateCellsUrl(id),
  {
    ...options,
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...options?.headers },
    body: JSON.stringify(
      annotationsApi,)
  }
);}



export type modelHubDatasetAnnotationSummaryListResponse200 = {
  data: AnnotationSummaryResponseApi
  status: 200
}

export type modelHubDatasetAnnotationSummaryListResponse400 = {
  data: ApiErrorResponseApi
  status: 400
}

export type modelHubDatasetAnnotationSummaryListResponse403 = {
  data: ApiErrorResponseApi
  status: 403
}

export type modelHubDatasetAnnotationSummaryListResponse500 = {
  data: ApiErrorResponseApi
  status: 500
}

export type modelHubDatasetAnnotationSummaryListResponseSuccess = (modelHubDatasetAnnotationSummaryListResponse200) & {
  headers: Headers;
};
export type modelHubDatasetAnnotationSummaryListResponseError = (modelHubDatasetAnnotationSummaryListResponse400 | modelHubDatasetAnnotationSummaryListResponse403 | modelHubDatasetAnnotationSummaryListResponse500) & {
  headers: Headers;
};

export type modelHubDatasetAnnotationSummaryListResponse = (modelHubDatasetAnnotationSummaryListResponseSuccess | modelHubDatasetAnnotationSummaryListResponseError)

export const getModelHubDatasetAnnotationSummaryListUrl = (datasetId: string,) => {




  return `/model-hub/dataset/${datasetId}/annotation-summary/`
}

export const modelHubDatasetAnnotationSummaryList = async (datasetId: string, options?: RequestInit): Promise<modelHubDatasetAnnotationSummaryListResponse> => {

  return apiMutator<modelHubDatasetAnnotationSummaryListResponse>(getModelHubDatasetAnnotationSummaryListUrl(datasetId),
  {
    ...options,
    method: 'GET'


  }
);}



export type modelHubScoresListResponse200 = {
  data: ModelHubScoresList200
  status: 200
}

export type modelHubScoresListResponseSuccess = (modelHubScoresListResponse200) & {
  headers: Headers;
};
;

export type modelHubScoresListResponse = (modelHubScoresListResponseSuccess)

export const getModelHubScoresListUrl = (params?: ModelHubScoresListParams,) => {
  const normalizedParams = new URLSearchParams();

  Object.entries(params || {}).forEach(([key, value]) => {

    if (value !== undefined) {
      normalizedParams.append(key, value === null ? 'null' : value.toString())
    }
  });

  const stringifiedParams = normalizedParams.toString();

  return stringifiedParams.length > 0 ? `/model-hub/scores/?${stringifiedParams}` : `/model-hub/scores/`
}

/**
 * GET    /model-hub/scores/?source_type=trace&source_id=<uuid>
POST   /model-hub/scores/                 (single score)
POST   /model-hub/scores/bulk/            (multiple scores on one source)
DELETE /model-hub/scores/<id>/
 * @summary Universal Score CRUD.
 */
export const modelHubScoresList = async (params?: ModelHubScoresListParams, options?: RequestInit): Promise<modelHubScoresListResponse> => {

  return apiMutator<modelHubScoresListResponse>(getModelHubScoresListUrl(params),
  {
    ...options,
    method: 'GET'


  }
);}



export type modelHubScoresCreateResponse200 = {
  data: ScoreResponseApi
  status: 200
}

export type modelHubScoresCreateResponse400 = {
  data: ApiErrorResponseApi
  status: 400
}

export type modelHubScoresCreateResponse403 = {
  data: ApiErrorResponseApi
  status: 403
}

export type modelHubScoresCreateResponse404 = {
  data: ApiErrorResponseApi
  status: 404
}

export type modelHubScoresCreateResponse409 = {
  data: ApiErrorResponseApi
  status: 409
}

export type modelHubScoresCreateResponse500 = {
  data: ApiErrorResponseApi
  status: 500
}

export type modelHubScoresCreateResponseSuccess = (modelHubScoresCreateResponse200) & {
  headers: Headers;
};
export type modelHubScoresCreateResponseError = (modelHubScoresCreateResponse400 | modelHubScoresCreateResponse403 | modelHubScoresCreateResponse404 | modelHubScoresCreateResponse409 | modelHubScoresCreateResponse500) & {
  headers: Headers;
};

export type modelHubScoresCreateResponse = (modelHubScoresCreateResponseSuccess | modelHubScoresCreateResponseError)

export const getModelHubScoresCreateUrl = () => {




  return `/model-hub/scores/`
}

/**
 * Create a single score.
 */
export const modelHubScoresCreate = async (createScoreApi: CreateScoreApi, options?: RequestInit): Promise<modelHubScoresCreateResponse> => {

  return apiMutator<modelHubScoresCreateResponse>(getModelHubScoresCreateUrl(),
  {
    ...options,
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...options?.headers },
    body: JSON.stringify(
      createScoreApi,)
  }
);}



export type modelHubScoresBulkCreateResponse200 = {
  data: BulkCreateScoresResponseApi
  status: 200
}

export type modelHubScoresBulkCreateResponse400 = {
  data: ApiErrorResponseApi
  status: 400
}

export type modelHubScoresBulkCreateResponse403 = {
  data: ApiErrorResponseApi
  status: 403
}

export type modelHubScoresBulkCreateResponse404 = {
  data: ApiErrorResponseApi
  status: 404
}

export type modelHubScoresBulkCreateResponse409 = {
  data: ApiErrorResponseApi
  status: 409
}

export type modelHubScoresBulkCreateResponse500 = {
  data: ApiErrorResponseApi
  status: 500
}

export type modelHubScoresBulkCreateResponseSuccess = (modelHubScoresBulkCreateResponse200) & {
  headers: Headers;
};
export type modelHubScoresBulkCreateResponseError = (modelHubScoresBulkCreateResponse400 | modelHubScoresBulkCreateResponse403 | modelHubScoresBulkCreateResponse404 | modelHubScoresBulkCreateResponse409 | modelHubScoresBulkCreateResponse500) & {
  headers: Headers;
};

export type modelHubScoresBulkCreateResponse = (modelHubScoresBulkCreateResponseSuccess | modelHubScoresBulkCreateResponseError)

export const getModelHubScoresBulkCreateUrl = () => {




  return `/model-hub/scores/bulk/`
}

/**
 * Create multiple scores on a single source (e.g. from inline annotator).
 */
export const modelHubScoresBulkCreate = async (bulkCreateScoresApi: BulkCreateScoresApi, options?: RequestInit): Promise<modelHubScoresBulkCreateResponse> => {

  return apiMutator<modelHubScoresBulkCreateResponse>(getModelHubScoresBulkCreateUrl(),
  {
    ...options,
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...options?.headers },
    body: JSON.stringify(
      bulkCreateScoresApi,)
  }
);}



export type modelHubScoresForSourceResponse200 = {
  data: ScoreForSourceResponseApi
  status: 200
}

export type modelHubScoresForSourceResponse400 = {
  data: ApiErrorResponseApi
  status: 400
}

export type modelHubScoresForSourceResponse403 = {
  data: ApiErrorResponseApi
  status: 403
}

export type modelHubScoresForSourceResponse404 = {
  data: ApiErrorResponseApi
  status: 404
}

export type modelHubScoresForSourceResponse409 = {
  data: ApiErrorResponseApi
  status: 409
}

export type modelHubScoresForSourceResponse500 = {
  data: ApiErrorResponseApi
  status: 500
}

export type modelHubScoresForSourceResponseSuccess = (modelHubScoresForSourceResponse200) & {
  headers: Headers;
};
export type modelHubScoresForSourceResponseError = (modelHubScoresForSourceResponse400 | modelHubScoresForSourceResponse403 | modelHubScoresForSourceResponse404 | modelHubScoresForSourceResponse409 | modelHubScoresForSourceResponse500) & {
  headers: Headers;
};

export type modelHubScoresForSourceResponse = (modelHubScoresForSourceResponseSuccess | modelHubScoresForSourceResponseError)

export const getModelHubScoresForSourceUrl = (params: ModelHubScoresForSourceParams,) => {
  const normalizedParams = new URLSearchParams();

  Object.entries(params || {}).forEach(([key, value]) => {

    if (value !== undefined) {
      normalizedParams.append(key, value === null ? 'null' : value.toString())
    }
  });

  const stringifiedParams = normalizedParams.toString();

  return stringifiedParams.length > 0 ? `/model-hub/scores/for-source/?${stringifiedParams}` : `/model-hub/scores/for-source/`
}

/**
 * Get all scores for a specific source.
GET /model-hub/scores/for-source/?source_type=trace&source_id=<uuid>
 */
export const modelHubScoresForSource = async (params: ModelHubScoresForSourceParams, options?: RequestInit): Promise<modelHubScoresForSourceResponse> => {

  return apiMutator<modelHubScoresForSourceResponse>(getModelHubScoresForSourceUrl(params),
  {
    ...options,
    method: 'GET'


  }
);}



export type modelHubScoresReadResponse200 = {
  data: ScoreApi
  status: 200
}

export type modelHubScoresReadResponseSuccess = (modelHubScoresReadResponse200) & {
  headers: Headers;
};
;

export type modelHubScoresReadResponse = (modelHubScoresReadResponseSuccess)

export const getModelHubScoresReadUrl = (id: string,) => {




  return `/model-hub/scores/${id}/`
}

/**
 * GET    /model-hub/scores/?source_type=trace&source_id=<uuid>
POST   /model-hub/scores/                 (single score)
POST   /model-hub/scores/bulk/            (multiple scores on one source)
DELETE /model-hub/scores/<id>/
 * @summary Universal Score CRUD.
 */
export const modelHubScoresRead = async (id: string, options?: RequestInit): Promise<modelHubScoresReadResponse> => {

  return apiMutator<modelHubScoresReadResponse>(getModelHubScoresReadUrl(id),
  {
    ...options,
    method: 'GET'


  }
);}



export type modelHubScoresUpdateResponse200 = {
  data: ScoreApi
  status: 200
}

export type modelHubScoresUpdateResponseSuccess = (modelHubScoresUpdateResponse200) & {
  headers: Headers;
};
;

export type modelHubScoresUpdateResponse = (modelHubScoresUpdateResponseSuccess)

export const getModelHubScoresUpdateUrl = (id: string,) => {




  return `/model-hub/scores/${id}/`
}

/**
 * GET    /model-hub/scores/?source_type=trace&source_id=<uuid>
POST   /model-hub/scores/                 (single score)
POST   /model-hub/scores/bulk/            (multiple scores on one source)
DELETE /model-hub/scores/<id>/
 * @summary Universal Score CRUD.
 */
export const modelHubScoresUpdate = async (id: string,
    scoreApi: NonReadonly<ScoreApi>, options?: RequestInit): Promise<modelHubScoresUpdateResponse> => {

  return apiMutator<modelHubScoresUpdateResponse>(getModelHubScoresUpdateUrl(id),
  {
    ...options,
    method: 'PUT',
    headers: { 'Content-Type': 'application/json', ...options?.headers },
    body: JSON.stringify(
      scoreApi,)
  }
);}



export type modelHubScoresPartialUpdateResponse200 = {
  data: ScoreApi
  status: 200
}

export type modelHubScoresPartialUpdateResponseSuccess = (modelHubScoresPartialUpdateResponse200) & {
  headers: Headers;
};
;

export type modelHubScoresPartialUpdateResponse = (modelHubScoresPartialUpdateResponseSuccess)

export const getModelHubScoresPartialUpdateUrl = (id: string,) => {




  return `/model-hub/scores/${id}/`
}

/**
 * GET    /model-hub/scores/?source_type=trace&source_id=<uuid>
POST   /model-hub/scores/                 (single score)
POST   /model-hub/scores/bulk/            (multiple scores on one source)
DELETE /model-hub/scores/<id>/
 * @summary Universal Score CRUD.
 */
export const modelHubScoresPartialUpdate = async (id: string,
    scoreApi: NonReadonly<ScoreApi>, options?: RequestInit): Promise<modelHubScoresPartialUpdateResponse> => {

  return apiMutator<modelHubScoresPartialUpdateResponse>(getModelHubScoresPartialUpdateUrl(id),
  {
    ...options,
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json', ...options?.headers },
    body: JSON.stringify(
      scoreApi,)
  }
);}



export type modelHubScoresDeleteResponse200 = {
  data: ScoreDeleteResponseApi
  status: 200
}

export type modelHubScoresDeleteResponse400 = {
  data: ApiErrorResponseApi
  status: 400
}

export type modelHubScoresDeleteResponse403 = {
  data: ApiErrorResponseApi
  status: 403
}

export type modelHubScoresDeleteResponse404 = {
  data: ApiErrorResponseApi
  status: 404
}

export type modelHubScoresDeleteResponse409 = {
  data: ApiErrorResponseApi
  status: 409
}

export type modelHubScoresDeleteResponse500 = {
  data: ApiErrorResponseApi
  status: 500
}

export type modelHubScoresDeleteResponseSuccess = (modelHubScoresDeleteResponse200) & {
  headers: Headers;
};
export type modelHubScoresDeleteResponseError = (modelHubScoresDeleteResponse400 | modelHubScoresDeleteResponse403 | modelHubScoresDeleteResponse404 | modelHubScoresDeleteResponse409 | modelHubScoresDeleteResponse500) & {
  headers: Headers;
};

export type modelHubScoresDeleteResponse = (modelHubScoresDeleteResponseSuccess | modelHubScoresDeleteResponseError)

export const getModelHubScoresDeleteUrl = (id: string,) => {




  return `/model-hub/scores/${id}/`
}

/**
 * Only the annotator who created the score or an org Owner/Admin may
delete it.
 * @summary Soft-delete a score.
 */
export const modelHubScoresDelete = async (id: string, options?: RequestInit): Promise<modelHubScoresDeleteResponse> => {

  return apiMutator<modelHubScoresDeleteResponse>(getModelHubScoresDeleteUrl(id),
  {
    ...options,
    method: 'DELETE'


  }
);}



export type tracerBulkAnnotationCreateResponse200 = {
  data: BulkAnnotationResponseApi
  status: 200
}

export type tracerBulkAnnotationCreateResponse400 = {
  data: ApiErrorResponseApi
  status: 400
}

export type tracerBulkAnnotationCreateResponse500 = {
  data: ApiErrorResponseApi
  status: 500
}

export type tracerBulkAnnotationCreateResponseSuccess = (tracerBulkAnnotationCreateResponse200) & {
  headers: Headers;
};
export type tracerBulkAnnotationCreateResponseError = (tracerBulkAnnotationCreateResponse400 | tracerBulkAnnotationCreateResponse500) & {
  headers: Headers;
};

export type tracerBulkAnnotationCreateResponse = (tracerBulkAnnotationCreateResponseSuccess | tracerBulkAnnotationCreateResponseError)

export const getTracerBulkAnnotationCreateUrl = () => {




  return `/tracer/bulk-annotation/`
}

export const tracerBulkAnnotationCreate = async (bulkAnnotationRequestApi: BulkAnnotationRequestApi, options?: RequestInit): Promise<tracerBulkAnnotationCreateResponse> => {

  return apiMutator<tracerBulkAnnotationCreateResponse>(getTracerBulkAnnotationCreateUrl(),
  {
    ...options,
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...options?.headers },
    body: JSON.stringify(
      bulkAnnotationRequestApi,)
  }
);}



export type tracerDashboardListResponse200 = {
  data: TracerDashboardList200
  status: 200
}

export type tracerDashboardListResponseSuccess = (tracerDashboardListResponse200) & {
  headers: Headers;
};
;

export type tracerDashboardListResponse = (tracerDashboardListResponseSuccess)

export const getTracerDashboardListUrl = (params?: TracerDashboardListParams,) => {
  const normalizedParams = new URLSearchParams();

  Object.entries(params || {}).forEach(([key, value]) => {

    if (value !== undefined) {
      normalizedParams.append(key, value === null ? 'null' : value.toString())
    }
  });

  const stringifiedParams = normalizedParams.toString();

  return stringifiedParams.length > 0 ? `/tracer/dashboard/?${stringifiedParams}` : `/tracer/dashboard/`
}

export const tracerDashboardList = async (params?: TracerDashboardListParams, options?: RequestInit): Promise<tracerDashboardListResponse> => {

  return apiMutator<tracerDashboardListResponse>(getTracerDashboardListUrl(params),
  {
    ...options,
    method: 'GET'


  }
);}



export type tracerDashboardCreateResponse201 = {
  data: DashboardCreateUpdateApi
  status: 201
}

export type tracerDashboardCreateResponseSuccess = (tracerDashboardCreateResponse201) & {
  headers: Headers;
};
;

export type tracerDashboardCreateResponse = (tracerDashboardCreateResponseSuccess)

export const getTracerDashboardCreateUrl = () => {




  return `/tracer/dashboard/`
}

export const tracerDashboardCreate = async (dashboardCreateUpdateApi: DashboardCreateUpdateApi, options?: RequestInit): Promise<tracerDashboardCreateResponse> => {

  return apiMutator<tracerDashboardCreateResponse>(getTracerDashboardCreateUrl(),
  {
    ...options,
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...options?.headers },
    body: JSON.stringify(
      dashboardCreateUpdateApi,)
  }
);}



export type tracerDashboardFilterValuesResponse200 = {
  data: TracerDashboardFilterValues200
  status: 200
}

export type tracerDashboardFilterValuesResponseSuccess = (tracerDashboardFilterValuesResponse200) & {
  headers: Headers;
};
;

export type tracerDashboardFilterValuesResponse = (tracerDashboardFilterValuesResponseSuccess)

export const getTracerDashboardFilterValuesUrl = (params?: TracerDashboardFilterValuesParams,) => {
  const normalizedParams = new URLSearchParams();

  Object.entries(params || {}).forEach(([key, value]) => {

    if (value !== undefined) {
      normalizedParams.append(key, value === null ? 'null' : value.toString())
    }
  });

  const stringifiedParams = normalizedParams.toString();

  return stringifiedParams.length > 0 ? `/tracer/dashboard/filter_values/?${stringifiedParams}` : `/tracer/dashboard/filter_values/`
}

/**
 * Return distinct values for a given metric/attribute, for filter value picker.
 */
export const tracerDashboardFilterValues = async (params?: TracerDashboardFilterValuesParams, options?: RequestInit): Promise<tracerDashboardFilterValuesResponse> => {

  return apiMutator<tracerDashboardFilterValuesResponse>(getTracerDashboardFilterValuesUrl(params),
  {
    ...options,
    method: 'GET'


  }
);}



export type tracerDashboardMetricsResponse200 = {
  data: TracerDashboardMetrics200
  status: 200
}

export type tracerDashboardMetricsResponseSuccess = (tracerDashboardMetricsResponse200) & {
  headers: Headers;
};
;

export type tracerDashboardMetricsResponse = (tracerDashboardMetricsResponseSuccess)

export const getTracerDashboardMetricsUrl = (params?: TracerDashboardMetricsParams,) => {
  const normalizedParams = new URLSearchParams();

  Object.entries(params || {}).forEach(([key, value]) => {

    if (value !== undefined) {
      normalizedParams.append(key, value === null ? 'null' : value.toString())
    }
  });

  const stringifiedParams = normalizedParams.toString();

  return stringifiedParams.length > 0 ? `/tracer/dashboard/metrics/?${stringifiedParams}` : `/tracer/dashboard/metrics/`
}

/**
 * Backward compat: if ``workflow`` param is provided, return only
that source's metrics in the old grouped format.
 * @summary Return all available metrics across traces and datasets.
 */
export const tracerDashboardMetrics = async (params?: TracerDashboardMetricsParams, options?: RequestInit): Promise<tracerDashboardMetricsResponse> => {

  return apiMutator<tracerDashboardMetricsResponse>(getTracerDashboardMetricsUrl(params),
  {
    ...options,
    method: 'GET'


  }
);}



export type tracerDashboardQueryResponse201 = {
  data: DashboardApi
  status: 201
}

export type tracerDashboardQueryResponseSuccess = (tracerDashboardQueryResponse201) & {
  headers: Headers;
};
;

export type tracerDashboardQueryResponse = (tracerDashboardQueryResponseSuccess)

export const getTracerDashboardQueryUrl = () => {




  return `/tracer/dashboard/query/`
}

/**
 * Each metric carries a ``source`` field ("traces" or "datasets").
Metrics are partitioned by source and dispatched to the appropriate
query builder.  Results are merged into a single response.

Backward compat: if ``workflow`` is present and metrics lack
``source``, infer source from workflow.
 * @summary Execute a widget query and return chart data.
 */
export const tracerDashboardQuery = async (dashboardApi: NonReadonly<DashboardApi>, options?: RequestInit): Promise<tracerDashboardQueryResponse> => {

  return apiMutator<tracerDashboardQueryResponse>(getTracerDashboardQueryUrl(),
  {
    ...options,
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...options?.headers },
    body: JSON.stringify(
      dashboardApi,)
  }
);}



export type tracerDashboardSimulationAgentsResponse200 = {
  data: TracerDashboardSimulationAgents200
  status: 200
}

export type tracerDashboardSimulationAgentsResponseSuccess = (tracerDashboardSimulationAgentsResponse200) & {
  headers: Headers;
};
;

export type tracerDashboardSimulationAgentsResponse = (tracerDashboardSimulationAgentsResponseSuccess)

export const getTracerDashboardSimulationAgentsUrl = (params?: TracerDashboardSimulationAgentsParams,) => {
  const normalizedParams = new URLSearchParams();

  Object.entries(params || {}).forEach(([key, value]) => {

    if (value !== undefined) {
      normalizedParams.append(key, value === null ? 'null' : value.toString())
    }
  });

  const stringifiedParams = normalizedParams.toString();

  return stringifiedParams.length > 0 ? `/tracer/dashboard/simulation-agents/?${stringifiedParams}` : `/tracer/dashboard/simulation-agents/`
}

/**
 * Return simulation agents with their observability project links.
 */
export const tracerDashboardSimulationAgents = async (params?: TracerDashboardSimulationAgentsParams, options?: RequestInit): Promise<tracerDashboardSimulationAgentsResponse> => {

  return apiMutator<tracerDashboardSimulationAgentsResponse>(getTracerDashboardSimulationAgentsUrl(params),
  {
    ...options,
    method: 'GET'


  }
);}



export type tracerDashboardWidgetsListResponse200 = {
  data: TracerDashboardWidgetsList200
  status: 200
}

export type tracerDashboardWidgetsListResponseSuccess = (tracerDashboardWidgetsListResponse200) & {
  headers: Headers;
};
;

export type tracerDashboardWidgetsListResponse = (tracerDashboardWidgetsListResponseSuccess)

export const getTracerDashboardWidgetsListUrl = (dashboardPk: string,
    params?: TracerDashboardWidgetsListParams,) => {
  const normalizedParams = new URLSearchParams();

  Object.entries(params || {}).forEach(([key, value]) => {

    if (value !== undefined) {
      normalizedParams.append(key, value === null ? 'null' : value.toString())
    }
  });

  const stringifiedParams = normalizedParams.toString();

  return stringifiedParams.length > 0 ? `/tracer/dashboard/${dashboardPk}/widgets/?${stringifiedParams}` : `/tracer/dashboard/${dashboardPk}/widgets/`
}

export const tracerDashboardWidgetsList = async (dashboardPk: string,
    params?: TracerDashboardWidgetsListParams, options?: RequestInit): Promise<tracerDashboardWidgetsListResponse> => {

  return apiMutator<tracerDashboardWidgetsListResponse>(getTracerDashboardWidgetsListUrl(dashboardPk,params),
  {
    ...options,
    method: 'GET'


  }
);}



export type tracerDashboardWidgetsCreateResponse201 = {
  data: DashboardWidgetApi
  status: 201
}

export type tracerDashboardWidgetsCreateResponseSuccess = (tracerDashboardWidgetsCreateResponse201) & {
  headers: Headers;
};
;

export type tracerDashboardWidgetsCreateResponse = (tracerDashboardWidgetsCreateResponseSuccess)

export const getTracerDashboardWidgetsCreateUrl = (dashboardPk: string,) => {




  return `/tracer/dashboard/${dashboardPk}/widgets/`
}

export const tracerDashboardWidgetsCreate = async (dashboardPk: string,
    dashboardWidgetApi: NonReadonly<DashboardWidgetApi>, options?: RequestInit): Promise<tracerDashboardWidgetsCreateResponse> => {

  return apiMutator<tracerDashboardWidgetsCreateResponse>(getTracerDashboardWidgetsCreateUrl(dashboardPk),
  {
    ...options,
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...options?.headers },
    body: JSON.stringify(
      dashboardWidgetApi,)
  }
);}



export type tracerDashboardWidgetsPreviewQueryResponse201 = {
  data: DashboardWidgetApi
  status: 201
}

export type tracerDashboardWidgetsPreviewQueryResponseSuccess = (tracerDashboardWidgetsPreviewQueryResponse201) & {
  headers: Headers;
};
;

export type tracerDashboardWidgetsPreviewQueryResponse = (tracerDashboardWidgetsPreviewQueryResponseSuccess)

export const getTracerDashboardWidgetsPreviewQueryUrl = (dashboardPk: string,) => {




  return `/tracer/dashboard/${dashboardPk}/widgets/preview/`
}

/**
 * Execute an ad-hoc query_config without saving, for live preview.
 */
export const tracerDashboardWidgetsPreviewQuery = async (dashboardPk: string,
    dashboardWidgetApi: NonReadonly<DashboardWidgetApi>, options?: RequestInit): Promise<tracerDashboardWidgetsPreviewQueryResponse> => {

  return apiMutator<tracerDashboardWidgetsPreviewQueryResponse>(getTracerDashboardWidgetsPreviewQueryUrl(dashboardPk),
  {
    ...options,
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...options?.headers },
    body: JSON.stringify(
      dashboardWidgetApi,)
  }
);}



export type tracerDashboardWidgetsReorderResponse201 = {
  data: DashboardWidgetApi
  status: 201
}

export type tracerDashboardWidgetsReorderResponseSuccess = (tracerDashboardWidgetsReorderResponse201) & {
  headers: Headers;
};
;

export type tracerDashboardWidgetsReorderResponse = (tracerDashboardWidgetsReorderResponseSuccess)

export const getTracerDashboardWidgetsReorderUrl = (dashboardPk: string,) => {




  return `/tracer/dashboard/${dashboardPk}/widgets/reorder/`
}

/**
 * Batch update widget positions.
 */
export const tracerDashboardWidgetsReorder = async (dashboardPk: string,
    dashboardWidgetApi: NonReadonly<DashboardWidgetApi>, options?: RequestInit): Promise<tracerDashboardWidgetsReorderResponse> => {

  return apiMutator<tracerDashboardWidgetsReorderResponse>(getTracerDashboardWidgetsReorderUrl(dashboardPk),
  {
    ...options,
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...options?.headers },
    body: JSON.stringify(
      dashboardWidgetApi,)
  }
);}



export type tracerDashboardWidgetsReadResponse200 = {
  data: DashboardWidgetApi
  status: 200
}

export type tracerDashboardWidgetsReadResponseSuccess = (tracerDashboardWidgetsReadResponse200) & {
  headers: Headers;
};
;

export type tracerDashboardWidgetsReadResponse = (tracerDashboardWidgetsReadResponseSuccess)

export const getTracerDashboardWidgetsReadUrl = (dashboardPk: string,
    id: string,) => {




  return `/tracer/dashboard/${dashboardPk}/widgets/${id}/`
}

export const tracerDashboardWidgetsRead = async (dashboardPk: string,
    id: string, options?: RequestInit): Promise<tracerDashboardWidgetsReadResponse> => {

  return apiMutator<tracerDashboardWidgetsReadResponse>(getTracerDashboardWidgetsReadUrl(dashboardPk,id),
  {
    ...options,
    method: 'GET'


  }
);}



export type tracerDashboardWidgetsUpdateResponse200 = {
  data: DashboardWidgetApi
  status: 200
}

export type tracerDashboardWidgetsUpdateResponseSuccess = (tracerDashboardWidgetsUpdateResponse200) & {
  headers: Headers;
};
;

export type tracerDashboardWidgetsUpdateResponse = (tracerDashboardWidgetsUpdateResponseSuccess)

export const getTracerDashboardWidgetsUpdateUrl = (dashboardPk: string,
    id: string,) => {




  return `/tracer/dashboard/${dashboardPk}/widgets/${id}/`
}

export const tracerDashboardWidgetsUpdate = async (dashboardPk: string,
    id: string,
    dashboardWidgetApi: NonReadonly<DashboardWidgetApi>, options?: RequestInit): Promise<tracerDashboardWidgetsUpdateResponse> => {

  return apiMutator<tracerDashboardWidgetsUpdateResponse>(getTracerDashboardWidgetsUpdateUrl(dashboardPk,id),
  {
    ...options,
    method: 'PUT',
    headers: { 'Content-Type': 'application/json', ...options?.headers },
    body: JSON.stringify(
      dashboardWidgetApi,)
  }
);}



export type tracerDashboardWidgetsPartialUpdateResponse200 = {
  data: DashboardWidgetApi
  status: 200
}

export type tracerDashboardWidgetsPartialUpdateResponseSuccess = (tracerDashboardWidgetsPartialUpdateResponse200) & {
  headers: Headers;
};
;

export type tracerDashboardWidgetsPartialUpdateResponse = (tracerDashboardWidgetsPartialUpdateResponseSuccess)

export const getTracerDashboardWidgetsPartialUpdateUrl = (dashboardPk: string,
    id: string,) => {




  return `/tracer/dashboard/${dashboardPk}/widgets/${id}/`
}

export const tracerDashboardWidgetsPartialUpdate = async (dashboardPk: string,
    id: string,
    dashboardWidgetApi: NonReadonly<DashboardWidgetApi>, options?: RequestInit): Promise<tracerDashboardWidgetsPartialUpdateResponse> => {

  return apiMutator<tracerDashboardWidgetsPartialUpdateResponse>(getTracerDashboardWidgetsPartialUpdateUrl(dashboardPk,id),
  {
    ...options,
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json', ...options?.headers },
    body: JSON.stringify(
      dashboardWidgetApi,)
  }
);}



export type tracerDashboardWidgetsDeleteResponse204 = {
  data: void
  status: 204
}

export type tracerDashboardWidgetsDeleteResponseSuccess = (tracerDashboardWidgetsDeleteResponse204) & {
  headers: Headers;
};
;

export type tracerDashboardWidgetsDeleteResponse = (tracerDashboardWidgetsDeleteResponseSuccess)

export const getTracerDashboardWidgetsDeleteUrl = (dashboardPk: string,
    id: string,) => {




  return `/tracer/dashboard/${dashboardPk}/widgets/${id}/`
}

export const tracerDashboardWidgetsDelete = async (dashboardPk: string,
    id: string, options?: RequestInit): Promise<tracerDashboardWidgetsDeleteResponse> => {

  return apiMutator<tracerDashboardWidgetsDeleteResponse>(getTracerDashboardWidgetsDeleteUrl(dashboardPk,id),
  {
    ...options,
    method: 'DELETE'


  }
);}



export type tracerDashboardWidgetsDuplicateWidgetResponse201 = {
  data: DashboardWidgetApi
  status: 201
}

export type tracerDashboardWidgetsDuplicateWidgetResponseSuccess = (tracerDashboardWidgetsDuplicateWidgetResponse201) & {
  headers: Headers;
};
;

export type tracerDashboardWidgetsDuplicateWidgetResponse = (tracerDashboardWidgetsDuplicateWidgetResponseSuccess)

export const getTracerDashboardWidgetsDuplicateWidgetUrl = (dashboardPk: string,
    id: string,) => {




  return `/tracer/dashboard/${dashboardPk}/widgets/${id}/duplicate/`
}

/**
 * Duplicate a widget.
 */
export const tracerDashboardWidgetsDuplicateWidget = async (dashboardPk: string,
    id: string,
    dashboardWidgetApi: NonReadonly<DashboardWidgetApi>, options?: RequestInit): Promise<tracerDashboardWidgetsDuplicateWidgetResponse> => {

  return apiMutator<tracerDashboardWidgetsDuplicateWidgetResponse>(getTracerDashboardWidgetsDuplicateWidgetUrl(dashboardPk,id),
  {
    ...options,
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...options?.headers },
    body: JSON.stringify(
      dashboardWidgetApi,)
  }
);}



export type tracerDashboardWidgetsExecuteQueryResponse201 = {
  data: DashboardWidgetApi
  status: 201
}

export type tracerDashboardWidgetsExecuteQueryResponseSuccess = (tracerDashboardWidgetsExecuteQueryResponse201) & {
  headers: Headers;
};
;

export type tracerDashboardWidgetsExecuteQueryResponse = (tracerDashboardWidgetsExecuteQueryResponseSuccess)

export const getTracerDashboardWidgetsExecuteQueryUrl = (dashboardPk: string,
    id: string,) => {




  return `/tracer/dashboard/${dashboardPk}/widgets/${id}/query/`
}

/**
 * Execute the widget's query_config against ClickHouse and return results.
 */
export const tracerDashboardWidgetsExecuteQuery = async (dashboardPk: string,
    id: string,
    dashboardWidgetApi: NonReadonly<DashboardWidgetApi>, options?: RequestInit): Promise<tracerDashboardWidgetsExecuteQueryResponse> => {

  return apiMutator<tracerDashboardWidgetsExecuteQueryResponse>(getTracerDashboardWidgetsExecuteQueryUrl(dashboardPk,id),
  {
    ...options,
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...options?.headers },
    body: JSON.stringify(
      dashboardWidgetApi,)
  }
);}



export type tracerDashboardReadResponse200 = {
  data: DashboardDetailApi
  status: 200
}

export type tracerDashboardReadResponseSuccess = (tracerDashboardReadResponse200) & {
  headers: Headers;
};
;

export type tracerDashboardReadResponse = (tracerDashboardReadResponseSuccess)

export const getTracerDashboardReadUrl = (id: string,) => {




  return `/tracer/dashboard/${id}/`
}

export const tracerDashboardRead = async (id: string, options?: RequestInit): Promise<tracerDashboardReadResponse> => {

  return apiMutator<tracerDashboardReadResponse>(getTracerDashboardReadUrl(id),
  {
    ...options,
    method: 'GET'


  }
);}



export type tracerDashboardUpdateResponse200 = {
  data: DashboardCreateUpdateApi
  status: 200
}

export type tracerDashboardUpdateResponseSuccess = (tracerDashboardUpdateResponse200) & {
  headers: Headers;
};
;

export type tracerDashboardUpdateResponse = (tracerDashboardUpdateResponseSuccess)

export const getTracerDashboardUpdateUrl = (id: string,) => {




  return `/tracer/dashboard/${id}/`
}

export const tracerDashboardUpdate = async (id: string,
    dashboardCreateUpdateApi: DashboardCreateUpdateApi, options?: RequestInit): Promise<tracerDashboardUpdateResponse> => {

  return apiMutator<tracerDashboardUpdateResponse>(getTracerDashboardUpdateUrl(id),
  {
    ...options,
    method: 'PUT',
    headers: { 'Content-Type': 'application/json', ...options?.headers },
    body: JSON.stringify(
      dashboardCreateUpdateApi,)
  }
);}



export type tracerDashboardPartialUpdateResponse200 = {
  data: DashboardCreateUpdateApi
  status: 200
}

export type tracerDashboardPartialUpdateResponseSuccess = (tracerDashboardPartialUpdateResponse200) & {
  headers: Headers;
};
;

export type tracerDashboardPartialUpdateResponse = (tracerDashboardPartialUpdateResponseSuccess)

export const getTracerDashboardPartialUpdateUrl = (id: string,) => {




  return `/tracer/dashboard/${id}/`
}

export const tracerDashboardPartialUpdate = async (id: string,
    dashboardCreateUpdateApi: DashboardCreateUpdateApi, options?: RequestInit): Promise<tracerDashboardPartialUpdateResponse> => {

  return apiMutator<tracerDashboardPartialUpdateResponse>(getTracerDashboardPartialUpdateUrl(id),
  {
    ...options,
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json', ...options?.headers },
    body: JSON.stringify(
      dashboardCreateUpdateApi,)
  }
);}



export type tracerDashboardDeleteResponse204 = {
  data: void
  status: 204
}

export type tracerDashboardDeleteResponseSuccess = (tracerDashboardDeleteResponse204) & {
  headers: Headers;
};
;

export type tracerDashboardDeleteResponse = (tracerDashboardDeleteResponseSuccess)

export const getTracerDashboardDeleteUrl = (id: string,) => {




  return `/tracer/dashboard/${id}/`
}

export const tracerDashboardDelete = async (id: string, options?: RequestInit): Promise<tracerDashboardDeleteResponse> => {

  return apiMutator<tracerDashboardDeleteResponse>(getTracerDashboardDeleteUrl(id),
  {
    ...options,
    method: 'DELETE'


  }
);}



export type tracerGetAnnotationLabelsListResponse200 = {
  data: GetAnnotationLabelsResponseApi
  status: 200
}

export type tracerGetAnnotationLabelsListResponse400 = {
  data: ApiErrorResponseApi
  status: 400
}

export type tracerGetAnnotationLabelsListResponse500 = {
  data: ApiErrorResponseApi
  status: 500
}

export type tracerGetAnnotationLabelsListResponseSuccess = (tracerGetAnnotationLabelsListResponse200) & {
  headers: Headers;
};
export type tracerGetAnnotationLabelsListResponseError = (tracerGetAnnotationLabelsListResponse400 | tracerGetAnnotationLabelsListResponse500) & {
  headers: Headers;
};

export type tracerGetAnnotationLabelsListResponse = (tracerGetAnnotationLabelsListResponseSuccess | tracerGetAnnotationLabelsListResponseError)

export const getTracerGetAnnotationLabelsListUrl = () => {




  return `/tracer/get-annotation-labels/`
}

export const tracerGetAnnotationLabelsList = async ( options?: RequestInit): Promise<tracerGetAnnotationLabelsListResponse> => {

  return apiMutator<tracerGetAnnotationLabelsListResponse>(getTracerGetAnnotationLabelsListUrl(),
  {
    ...options,
    method: 'GET'


  }
);}



export type tracerObservationSpanListResponse200 = {
  data: TracerObservationSpanList200
  status: 200
}

export type tracerObservationSpanListResponseSuccess = (tracerObservationSpanListResponse200) & {
  headers: Headers;
};
;

export type tracerObservationSpanListResponse = (tracerObservationSpanListResponseSuccess)

export const getTracerObservationSpanListUrl = (params?: TracerObservationSpanListParams,) => {
  const normalizedParams = new URLSearchParams();

  Object.entries(params || {}).forEach(([key, value]) => {

    if (value !== undefined) {
      normalizedParams.append(key, value === null ? 'null' : value.toString())
    }
  });

  const stringifiedParams = normalizedParams.toString();

  return stringifiedParams.length > 0 ? `/tracer/observation-span/?${stringifiedParams}` : `/tracer/observation-span/`
}

export const tracerObservationSpanList = async (params?: TracerObservationSpanListParams, options?: RequestInit): Promise<tracerObservationSpanListResponse> => {

  return apiMutator<tracerObservationSpanListResponse>(getTracerObservationSpanListUrl(params),
  {
    ...options,
    method: 'GET'


  }
);}



export type tracerObservationSpanCreateResponse201 = {
  data: ObservationSpanApi
  status: 201
}

export type tracerObservationSpanCreateResponseSuccess = (tracerObservationSpanCreateResponse201) & {
  headers: Headers;
};
;

export type tracerObservationSpanCreateResponse = (tracerObservationSpanCreateResponseSuccess)

export const getTracerObservationSpanCreateUrl = () => {




  return `/tracer/observation-span/`
}

export const tracerObservationSpanCreate = async (observationSpanApi: NonReadonly<ObservationSpanApi>, options?: RequestInit): Promise<tracerObservationSpanCreateResponse> => {

  return apiMutator<tracerObservationSpanCreateResponse>(getTracerObservationSpanCreateUrl(),
  {
    ...options,
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...options?.headers },
    body: JSON.stringify(
      observationSpanApi,)
  }
);}



export type tracerObservationSpanAddAnnotationsResponse201 = {
  data: AddObservationSpanAnnotationsApi
  status: 201
}

export type tracerObservationSpanAddAnnotationsResponseSuccess = (tracerObservationSpanAddAnnotationsResponse201) & {
  headers: Headers;
};
;

export type tracerObservationSpanAddAnnotationsResponse = (tracerObservationSpanAddAnnotationsResponseSuccess)

export const getTracerObservationSpanAddAnnotationsUrl = () => {




  return `/tracer/observation-span/add_annotations/`
}

export const tracerObservationSpanAddAnnotations = async (addObservationSpanAnnotationsApi: AddObservationSpanAnnotationsApi, options?: RequestInit): Promise<tracerObservationSpanAddAnnotationsResponse> => {

  return apiMutator<tracerObservationSpanAddAnnotationsResponse>(getTracerObservationSpanAddAnnotationsUrl(),
  {
    ...options,
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...options?.headers },
    body: JSON.stringify(
      addObservationSpanAnnotationsApi,)
  }
);}



export type tracerObservationSpanBulkCreateResponse201 = {
  data: ObservationSpanApi
  status: 201
}

export type tracerObservationSpanBulkCreateResponseSuccess = (tracerObservationSpanBulkCreateResponse201) & {
  headers: Headers;
};
;

export type tracerObservationSpanBulkCreateResponse = (tracerObservationSpanBulkCreateResponseSuccess)

export const getTracerObservationSpanBulkCreateUrl = () => {




  return `/tracer/observation-span/bulk_create/`
}

export const tracerObservationSpanBulkCreate = async (observationSpanApi: NonReadonly<ObservationSpanApi>, options?: RequestInit): Promise<tracerObservationSpanBulkCreateResponse> => {

  return apiMutator<tracerObservationSpanBulkCreateResponse>(getTracerObservationSpanBulkCreateUrl(),
  {
    ...options,
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...options?.headers },
    body: JSON.stringify(
      observationSpanApi,)
  }
);}



export type tracerObservationSpanCreateOtelSpanResponse201 = {
  data: ObservationSpanApi
  status: 201
}

export type tracerObservationSpanCreateOtelSpanResponseSuccess = (tracerObservationSpanCreateOtelSpanResponse201) & {
  headers: Headers;
};
;

export type tracerObservationSpanCreateOtelSpanResponse = (tracerObservationSpanCreateOtelSpanResponseSuccess)

export const getTracerObservationSpanCreateOtelSpanUrl = () => {




  return `/tracer/observation-span/create_otel_span/`
}

export const tracerObservationSpanCreateOtelSpan = async (observationSpanApi: NonReadonly<ObservationSpanApi>, options?: RequestInit): Promise<tracerObservationSpanCreateOtelSpanResponse> => {

  return apiMutator<tracerObservationSpanCreateOtelSpanResponse>(getTracerObservationSpanCreateOtelSpanUrl(),
  {
    ...options,
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...options?.headers },
    body: JSON.stringify(
      observationSpanApi,)
  }
);}



export type tracerObservationSpanDeleteAnnotationLabelResponse204 = {
  data: void
  status: 204
}

export type tracerObservationSpanDeleteAnnotationLabelResponseSuccess = (tracerObservationSpanDeleteAnnotationLabelResponse204) & {
  headers: Headers;
};
;

export type tracerObservationSpanDeleteAnnotationLabelResponse = (tracerObservationSpanDeleteAnnotationLabelResponseSuccess)

export const getTracerObservationSpanDeleteAnnotationLabelUrl = () => {




  return `/tracer/observation-span/delete_annotation_label/`
}

export const tracerObservationSpanDeleteAnnotationLabel = async ( options?: RequestInit): Promise<tracerObservationSpanDeleteAnnotationLabelResponse> => {

  return apiMutator<tracerObservationSpanDeleteAnnotationLabelResponse>(getTracerObservationSpanDeleteAnnotationLabelUrl(),
  {
    ...options,
    method: 'DELETE'


  }
);}



export type tracerObservationSpanGetEvalAttributesListResponse200 = {
  data: TracerObservationSpanGetEvalAttributesList200
  status: 200
}

export type tracerObservationSpanGetEvalAttributesListResponseSuccess = (tracerObservationSpanGetEvalAttributesListResponse200) & {
  headers: Headers;
};
;

export type tracerObservationSpanGetEvalAttributesListResponse = (tracerObservationSpanGetEvalAttributesListResponseSuccess)

export const getTracerObservationSpanGetEvalAttributesListUrl = (params?: TracerObservationSpanGetEvalAttributesListParams,) => {
  const normalizedParams = new URLSearchParams();

  Object.entries(params || {}).forEach(([key, value]) => {

    if (value !== undefined) {
      normalizedParams.append(key, value === null ? 'null' : value.toString())
    }
  });

  const stringifiedParams = normalizedParams.toString();

  return stringifiedParams.length > 0 ? `/tracer/observation-span/get_eval_attributes_list/?${stringifiedParams}` : `/tracer/observation-span/get_eval_attributes_list/`
}

/**
 * Query params:
    filters: JSON {"project_id": "<uuid>"} (required)
    row_type: spans | traces | sessions (default spans;
              voiceCalls aliases to spans)
 * @summary Attribute paths the EvalPicker exposes per row_type.
 */
export const tracerObservationSpanGetEvalAttributesList = async (params?: TracerObservationSpanGetEvalAttributesListParams, options?: RequestInit): Promise<tracerObservationSpanGetEvalAttributesListResponse> => {

  return apiMutator<tracerObservationSpanGetEvalAttributesListResponse>(getTracerObservationSpanGetEvalAttributesListUrl(params),
  {
    ...options,
    method: 'GET'


  }
);}



export type tracerObservationSpanGetEvaluationDetailsResponse200 = {
  data: TracerObservationSpanGetEvaluationDetails200
  status: 200
}

export type tracerObservationSpanGetEvaluationDetailsResponseSuccess = (tracerObservationSpanGetEvaluationDetailsResponse200) & {
  headers: Headers;
};
;

export type tracerObservationSpanGetEvaluationDetailsResponse = (tracerObservationSpanGetEvaluationDetailsResponseSuccess)

export const getTracerObservationSpanGetEvaluationDetailsUrl = (params?: TracerObservationSpanGetEvaluationDetailsParams,) => {
  const normalizedParams = new URLSearchParams();

  Object.entries(params || {}).forEach(([key, value]) => {

    if (value !== undefined) {
      normalizedParams.append(key, value === null ? 'null' : value.toString())
    }
  });

  const stringifiedParams = normalizedParams.toString();

  return stringifiedParams.length > 0 ? `/tracer/observation-span/get_evaluation_details/?${stringifiedParams}` : `/tracer/observation-span/get_evaluation_details/`
}

export const tracerObservationSpanGetEvaluationDetails = async (params?: TracerObservationSpanGetEvaluationDetailsParams, options?: RequestInit): Promise<tracerObservationSpanGetEvaluationDetailsResponse> => {

  return apiMutator<tracerObservationSpanGetEvaluationDetailsResponse>(getTracerObservationSpanGetEvaluationDetailsUrl(params),
  {
    ...options,
    method: 'GET'


  }
);}



export type tracerObservationSpanGetGraphMethodsResponse201 = {
  data: ObservationSpanApi
  status: 201
}

export type tracerObservationSpanGetGraphMethodsResponseSuccess = (tracerObservationSpanGetGraphMethodsResponse201) & {
  headers: Headers;
};
;

export type tracerObservationSpanGetGraphMethodsResponse = (tracerObservationSpanGetGraphMethodsResponseSuccess)

export const getTracerObservationSpanGetGraphMethodsUrl = () => {




  return `/tracer/observation-span/get_graph_methods/`
}

/**
 * Fetch data for the observe graph with optimized queries
 */
export const tracerObservationSpanGetGraphMethods = async (observationSpanApi: NonReadonly<ObservationSpanApi>, options?: RequestInit): Promise<tracerObservationSpanGetGraphMethodsResponse> => {

  return apiMutator<tracerObservationSpanGetGraphMethodsResponse>(getTracerObservationSpanGetGraphMethodsUrl(),
  {
    ...options,
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...options?.headers },
    body: JSON.stringify(
      observationSpanApi,)
  }
);}



export type tracerObservationSpanGetObservationSpanFieldsResponse200 = {
  data: TracerObservationSpanGetObservationSpanFields200
  status: 200
}

export type tracerObservationSpanGetObservationSpanFieldsResponseSuccess = (tracerObservationSpanGetObservationSpanFieldsResponse200) & {
  headers: Headers;
};
;

export type tracerObservationSpanGetObservationSpanFieldsResponse = (tracerObservationSpanGetObservationSpanFieldsResponseSuccess)

export const getTracerObservationSpanGetObservationSpanFieldsUrl = (params?: TracerObservationSpanGetObservationSpanFieldsParams,) => {
  const normalizedParams = new URLSearchParams();

  Object.entries(params || {}).forEach(([key, value]) => {

    if (value !== undefined) {
      normalizedParams.append(key, value === null ? 'null' : value.toString())
    }
  });

  const stringifiedParams = normalizedParams.toString();

  return stringifiedParams.length > 0 ? `/tracer/observation-span/get_observation_span_fields/?${stringifiedParams}` : `/tracer/observation-span/get_observation_span_fields/`
}

export const tracerObservationSpanGetObservationSpanFields = async (params?: TracerObservationSpanGetObservationSpanFieldsParams, options?: RequestInit): Promise<tracerObservationSpanGetObservationSpanFieldsResponse> => {

  return apiMutator<tracerObservationSpanGetObservationSpanFieldsResponse>(getTracerObservationSpanGetObservationSpanFieldsUrl(params),
  {
    ...options,
    method: 'GET'


  }
);}



export type tracerObservationSpanGetSpanAttributesListResponse200 = {
  data: TracerObservationSpanGetSpanAttributesList200
  status: 200
}

export type tracerObservationSpanGetSpanAttributesListResponseSuccess = (tracerObservationSpanGetSpanAttributesListResponse200) & {
  headers: Headers;
};
;

export type tracerObservationSpanGetSpanAttributesListResponse = (tracerObservationSpanGetSpanAttributesListResponseSuccess)

export const getTracerObservationSpanGetSpanAttributesListUrl = (params?: TracerObservationSpanGetSpanAttributesListParams,) => {
  const normalizedParams = new URLSearchParams();

  Object.entries(params || {}).forEach(([key, value]) => {

    if (value !== undefined) {
      normalizedParams.append(key, value === null ? 'null' : value.toString())
    }
  });

  const stringifiedParams = normalizedParams.toString();

  return stringifiedParams.length > 0 ? `/tracer/observation-span/get_span_attributes_list/?${stringifiedParams}` : `/tracer/observation-span/get_span_attributes_list/`
}

/**
 * Query params:
    filters: JSON {"project_id": "<uuid>"} (required)
 * @summary Distinct span_attributes keys for a project (spans surface).
 */
export const tracerObservationSpanGetSpanAttributesList = async (params?: TracerObservationSpanGetSpanAttributesListParams, options?: RequestInit): Promise<tracerObservationSpanGetSpanAttributesListResponse> => {

  return apiMutator<tracerObservationSpanGetSpanAttributesListResponse>(getTracerObservationSpanGetSpanAttributesListUrl(params),
  {
    ...options,
    method: 'GET'


  }
);}



export type tracerObservationSpanGetSpansExportDataResponse200 = {
  data: TracerObservationSpanGetSpansExportData200
  status: 200
}

export type tracerObservationSpanGetSpansExportDataResponseSuccess = (tracerObservationSpanGetSpansExportDataResponse200) & {
  headers: Headers;
};
;

export type tracerObservationSpanGetSpansExportDataResponse = (tracerObservationSpanGetSpansExportDataResponseSuccess)

export const getTracerObservationSpanGetSpansExportDataUrl = (params?: TracerObservationSpanGetSpansExportDataParams,) => {
  const normalizedParams = new URLSearchParams();

  Object.entries(params || {}).forEach(([key, value]) => {

    if (value !== undefined) {
      normalizedParams.append(key, value === null ? 'null' : value.toString())
    }
  });

  const stringifiedParams = normalizedParams.toString();

  return stringifiedParams.length > 0 ? `/tracer/observation-span/get_spans_export_data/?${stringifiedParams}` : `/tracer/observation-span/get_spans_export_data/`
}

export const tracerObservationSpanGetSpansExportData = async (params?: TracerObservationSpanGetSpansExportDataParams, options?: RequestInit): Promise<tracerObservationSpanGetSpansExportDataResponse> => {

  return apiMutator<tracerObservationSpanGetSpansExportDataResponse>(getTracerObservationSpanGetSpansExportDataUrl(params),
  {
    ...options,
    method: 'GET'


  }
);}



export type tracerObservationSpanGetTraceIdByIndexSpansAsBaseResponse200 = {
  data: TracerObservationSpanGetTraceIdByIndexSpansAsBase200
  status: 200
}

export type tracerObservationSpanGetTraceIdByIndexSpansAsBaseResponseSuccess = (tracerObservationSpanGetTraceIdByIndexSpansAsBaseResponse200) & {
  headers: Headers;
};
;

export type tracerObservationSpanGetTraceIdByIndexSpansAsBaseResponse = (tracerObservationSpanGetTraceIdByIndexSpansAsBaseResponseSuccess)

export const getTracerObservationSpanGetTraceIdByIndexSpansAsBaseUrl = (params?: TracerObservationSpanGetTraceIdByIndexSpansAsBaseParams,) => {
  const normalizedParams = new URLSearchParams();

  Object.entries(params || {}).forEach(([key, value]) => {

    if (value !== undefined) {
      normalizedParams.append(key, value === null ? 'null' : value.toString())
    }
  });

  const stringifiedParams = normalizedParams.toString();

  return stringifiedParams.length > 0 ? `/tracer/observation-span/get_trace_id_by_index_spans_as_base/?${stringifiedParams}` : `/tracer/observation-span/get_trace_id_by_index_spans_as_base/`
}

/**
 * Get the previous and next span id by index for non-observe projects.
Mirrors the query/filter logic of list_spans.
 */
export const tracerObservationSpanGetTraceIdByIndexSpansAsBase = async (params?: TracerObservationSpanGetTraceIdByIndexSpansAsBaseParams, options?: RequestInit): Promise<tracerObservationSpanGetTraceIdByIndexSpansAsBaseResponse> => {

  return apiMutator<tracerObservationSpanGetTraceIdByIndexSpansAsBaseResponse>(getTracerObservationSpanGetTraceIdByIndexSpansAsBaseUrl(params),
  {
    ...options,
    method: 'GET'


  }
);}



export type tracerObservationSpanGetTraceIdByIndexSpansAsObserveResponse200 = {
  data: TracerObservationSpanGetTraceIdByIndexSpansAsObserve200
  status: 200
}

export type tracerObservationSpanGetTraceIdByIndexSpansAsObserveResponseSuccess = (tracerObservationSpanGetTraceIdByIndexSpansAsObserveResponse200) & {
  headers: Headers;
};
;

export type tracerObservationSpanGetTraceIdByIndexSpansAsObserveResponse = (tracerObservationSpanGetTraceIdByIndexSpansAsObserveResponseSuccess)

export const getTracerObservationSpanGetTraceIdByIndexSpansAsObserveUrl = (params?: TracerObservationSpanGetTraceIdByIndexSpansAsObserveParams,) => {
  const normalizedParams = new URLSearchParams();

  Object.entries(params || {}).forEach(([key, value]) => {

    if (value !== undefined) {
      normalizedParams.append(key, value === null ? 'null' : value.toString())
    }
  });

  const stringifiedParams = normalizedParams.toString();

  return stringifiedParams.length > 0 ? `/tracer/observation-span/get_trace_id_by_index_spans_as_observe/?${stringifiedParams}` : `/tracer/observation-span/get_trace_id_by_index_spans_as_observe/`
}

/**
 * Get the previous and next trace id by index for observe projects.
Mirrors the query/filter logic of list_spans_as_observe.
 */
export const tracerObservationSpanGetTraceIdByIndexSpansAsObserve = async (params?: TracerObservationSpanGetTraceIdByIndexSpansAsObserveParams, options?: RequestInit): Promise<tracerObservationSpanGetTraceIdByIndexSpansAsObserveResponse> => {

  return apiMutator<tracerObservationSpanGetTraceIdByIndexSpansAsObserveResponse>(getTracerObservationSpanGetTraceIdByIndexSpansAsObserveUrl(params),
  {
    ...options,
    method: 'GET'


  }
);}



export type tracerObservationSpanListSpansResponse200 = {
  data: TracerObservationSpanListSpans200
  status: 200
}

export type tracerObservationSpanListSpansResponseSuccess = (tracerObservationSpanListSpansResponse200) & {
  headers: Headers;
};
;

export type tracerObservationSpanListSpansResponse = (tracerObservationSpanListSpansResponseSuccess)

export const getTracerObservationSpanListSpansUrl = (params?: TracerObservationSpanListSpansParams,) => {
  const normalizedParams = new URLSearchParams();

  Object.entries(params || {}).forEach(([key, value]) => {

    if (value !== undefined) {
      normalizedParams.append(key, value === null ? 'null' : value.toString())
    }
  });

  const stringifiedParams = normalizedParams.toString();

  return stringifiedParams.length > 0 ? `/tracer/observation-span/list_spans/?${stringifiedParams}` : `/tracer/observation-span/list_spans/`
}

/**
 * List spans filtered by project ID and project version ID with optimized queries.
 */
export const tracerObservationSpanListSpans = async (params?: TracerObservationSpanListSpansParams, options?: RequestInit): Promise<tracerObservationSpanListSpansResponse> => {

  return apiMutator<tracerObservationSpanListSpansResponse>(getTracerObservationSpanListSpansUrl(params),
  {
    ...options,
    method: 'GET'


  }
);}



export type tracerObservationSpanListSpansObserveResponse200 = {
  data: TracerObservationSpanListSpansObserve200
  status: 200
}

export type tracerObservationSpanListSpansObserveResponseSuccess = (tracerObservationSpanListSpansObserveResponse200) & {
  headers: Headers;
};
;

export type tracerObservationSpanListSpansObserveResponse = (tracerObservationSpanListSpansObserveResponseSuccess)

export const getTracerObservationSpanListSpansObserveUrl = (params?: TracerObservationSpanListSpansObserveParams,) => {
  const normalizedParams = new URLSearchParams();

  Object.entries(params || {}).forEach(([key, value]) => {

    if (value !== undefined) {
      normalizedParams.append(key, value === null ? 'null' : value.toString())
    }
  });

  const stringifiedParams = normalizedParams.toString();

  return stringifiedParams.length > 0 ? `/tracer/observation-span/list_spans_observe/?${stringifiedParams}` : `/tracer/observation-span/list_spans_observe/`
}

export const tracerObservationSpanListSpansObserve = async (params?: TracerObservationSpanListSpansObserveParams, options?: RequestInit): Promise<tracerObservationSpanListSpansObserveResponse> => {

  return apiMutator<tracerObservationSpanListSpansObserveResponse>(getTracerObservationSpanListSpansObserveUrl(params),
  {
    ...options,
    method: 'GET'


  }
);}



export type tracerObservationSpanRetrieveLoadingResponse200 = {
  data: TracerObservationSpanRetrieveLoading200
  status: 200
}

export type tracerObservationSpanRetrieveLoadingResponseSuccess = (tracerObservationSpanRetrieveLoadingResponse200) & {
  headers: Headers;
};
;

export type tracerObservationSpanRetrieveLoadingResponse = (tracerObservationSpanRetrieveLoadingResponseSuccess)

export const getTracerObservationSpanRetrieveLoadingUrl = (params?: TracerObservationSpanRetrieveLoadingParams,) => {
  const normalizedParams = new URLSearchParams();

  Object.entries(params || {}).forEach(([key, value]) => {

    if (value !== undefined) {
      normalizedParams.append(key, value === null ? 'null' : value.toString())
    }
  });

  const stringifiedParams = normalizedParams.toString();

  return stringifiedParams.length > 0 ? `/tracer/observation-span/retrieve_loading/?${stringifiedParams}` : `/tracer/observation-span/retrieve_loading/`
}

export const tracerObservationSpanRetrieveLoading = async (params?: TracerObservationSpanRetrieveLoadingParams, options?: RequestInit): Promise<tracerObservationSpanRetrieveLoadingResponse> => {

  return apiMutator<tracerObservationSpanRetrieveLoadingResponse>(getTracerObservationSpanRetrieveLoadingUrl(params),
  {
    ...options,
    method: 'GET'


  }
);}



export type tracerObservationSpanRootSpansResponse200 = {
  data: TracerObservationSpanRootSpans200
  status: 200
}

export type tracerObservationSpanRootSpansResponseSuccess = (tracerObservationSpanRootSpansResponse200) & {
  headers: Headers;
};
;

export type tracerObservationSpanRootSpansResponse = (tracerObservationSpanRootSpansResponseSuccess)

export const getTracerObservationSpanRootSpansUrl = (params?: TracerObservationSpanRootSpansParams,) => {
  const normalizedParams = new URLSearchParams();

  Object.entries(params || {}).forEach(([key, value]) => {

    if (value !== undefined) {
      normalizedParams.append(key, value === null ? 'null' : value.toString())
    }
  });

  const stringifiedParams = normalizedParams.toString();

  return stringifiedParams.length > 0 ? `/tracer/observation-span/root-spans/?${stringifiedParams}` : `/tracer/observation-span/root-spans/`
}

/**
 * Given a list of trace_ids, return the root span ID for each trace.
Root span = the span where parent_span_id IS NULL for that trace.

Query param: trace_ids (repeated, e.g. ?trace_ids=<id>&trace_ids=<id>)
 */
export const tracerObservationSpanRootSpans = async (params?: TracerObservationSpanRootSpansParams, options?: RequestInit): Promise<tracerObservationSpanRootSpansResponse> => {

  return apiMutator<tracerObservationSpanRootSpansResponse>(getTracerObservationSpanRootSpansUrl(params),
  {
    ...options,
    method: 'GET'


  }
);}



export type tracerObservationSpanSubmitFeedbackResponse201 = {
  data: ObservationSpanApi
  status: 201
}

export type tracerObservationSpanSubmitFeedbackResponseSuccess = (tracerObservationSpanSubmitFeedbackResponse201) & {
  headers: Headers;
};
;

export type tracerObservationSpanSubmitFeedbackResponse = (tracerObservationSpanSubmitFeedbackResponseSuccess)

export const getTracerObservationSpanSubmitFeedbackUrl = () => {




  return `/tracer/observation-span/submit_feedback/`
}

export const tracerObservationSpanSubmitFeedback = async (observationSpanApi: NonReadonly<ObservationSpanApi>, options?: RequestInit): Promise<tracerObservationSpanSubmitFeedbackResponse> => {

  return apiMutator<tracerObservationSpanSubmitFeedbackResponse>(getTracerObservationSpanSubmitFeedbackUrl(),
  {
    ...options,
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...options?.headers },
    body: JSON.stringify(
      observationSpanApi,)
  }
);}



export type tracerObservationSpanSubmitFeedbackActionTypeResponse201 = {
  data: ObservationSpanApi
  status: 201
}

export type tracerObservationSpanSubmitFeedbackActionTypeResponseSuccess = (tracerObservationSpanSubmitFeedbackActionTypeResponse201) & {
  headers: Headers;
};
;

export type tracerObservationSpanSubmitFeedbackActionTypeResponse = (tracerObservationSpanSubmitFeedbackActionTypeResponseSuccess)

export const getTracerObservationSpanSubmitFeedbackActionTypeUrl = () => {




  return `/tracer/observation-span/submit_feedback_action_type/`
}

export const tracerObservationSpanSubmitFeedbackActionType = async (observationSpanApi: NonReadonly<ObservationSpanApi>, options?: RequestInit): Promise<tracerObservationSpanSubmitFeedbackActionTypeResponse> => {

  return apiMutator<tracerObservationSpanSubmitFeedbackActionTypeResponse>(getTracerObservationSpanSubmitFeedbackActionTypeUrl(),
  {
    ...options,
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...options?.headers },
    body: JSON.stringify(
      observationSpanApi,)
  }
);}



export type tracerObservationSpanUpdateTagsResponse201 = {
  data: ObservationSpanApi
  status: 201
}

export type tracerObservationSpanUpdateTagsResponseSuccess = (tracerObservationSpanUpdateTagsResponse201) & {
  headers: Headers;
};
;

export type tracerObservationSpanUpdateTagsResponse = (tracerObservationSpanUpdateTagsResponseSuccess)

export const getTracerObservationSpanUpdateTagsUrl = () => {




  return `/tracer/observation-span/update-tags/`
}

/**
 * Update tags for an observation span.
 */
export const tracerObservationSpanUpdateTags = async (observationSpanApi: NonReadonly<ObservationSpanApi>, options?: RequestInit): Promise<tracerObservationSpanUpdateTagsResponse> => {

  return apiMutator<tracerObservationSpanUpdateTagsResponse>(getTracerObservationSpanUpdateTagsUrl(),
  {
    ...options,
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...options?.headers },
    body: JSON.stringify(
      observationSpanApi,)
  }
);}



export type tracerObservationSpanReadResponse200 = {
  data: ObservationSpanApi
  status: 200
}

export type tracerObservationSpanReadResponseSuccess = (tracerObservationSpanReadResponse200) & {
  headers: Headers;
};
;

export type tracerObservationSpanReadResponse = (tracerObservationSpanReadResponseSuccess)

export const getTracerObservationSpanReadUrl = (id: string,) => {




  return `/tracer/observation-span/${id}/`
}

export const tracerObservationSpanRead = async (id: string, options?: RequestInit): Promise<tracerObservationSpanReadResponse> => {

  return apiMutator<tracerObservationSpanReadResponse>(getTracerObservationSpanReadUrl(id),
  {
    ...options,
    method: 'GET'


  }
);}



export type tracerObservationSpanUpdateResponse200 = {
  data: ObservationSpanApi
  status: 200
}

export type tracerObservationSpanUpdateResponseSuccess = (tracerObservationSpanUpdateResponse200) & {
  headers: Headers;
};
;

export type tracerObservationSpanUpdateResponse = (tracerObservationSpanUpdateResponseSuccess)

export const getTracerObservationSpanUpdateUrl = (id: string,) => {




  return `/tracer/observation-span/${id}/`
}

export const tracerObservationSpanUpdate = async (id: string,
    observationSpanApi: NonReadonly<ObservationSpanApi>, options?: RequestInit): Promise<tracerObservationSpanUpdateResponse> => {

  return apiMutator<tracerObservationSpanUpdateResponse>(getTracerObservationSpanUpdateUrl(id),
  {
    ...options,
    method: 'PUT',
    headers: { 'Content-Type': 'application/json', ...options?.headers },
    body: JSON.stringify(
      observationSpanApi,)
  }
);}



export type tracerObservationSpanPartialUpdateResponse200 = {
  data: ObservationSpanApi
  status: 200
}

export type tracerObservationSpanPartialUpdateResponseSuccess = (tracerObservationSpanPartialUpdateResponse200) & {
  headers: Headers;
};
;

export type tracerObservationSpanPartialUpdateResponse = (tracerObservationSpanPartialUpdateResponseSuccess)

export const getTracerObservationSpanPartialUpdateUrl = (id: string,) => {




  return `/tracer/observation-span/${id}/`
}

export const tracerObservationSpanPartialUpdate = async (id: string,
    observationSpanApi: NonReadonly<ObservationSpanApi>, options?: RequestInit): Promise<tracerObservationSpanPartialUpdateResponse> => {

  return apiMutator<tracerObservationSpanPartialUpdateResponse>(getTracerObservationSpanPartialUpdateUrl(id),
  {
    ...options,
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json', ...options?.headers },
    body: JSON.stringify(
      observationSpanApi,)
  }
);}



export type tracerObservationSpanDeleteResponse204 = {
  data: void
  status: 204
}

export type tracerObservationSpanDeleteResponseSuccess = (tracerObservationSpanDeleteResponse204) & {
  headers: Headers;
};
;

export type tracerObservationSpanDeleteResponse = (tracerObservationSpanDeleteResponseSuccess)

export const getTracerObservationSpanDeleteUrl = (id: string,) => {




  return `/tracer/observation-span/${id}/`
}

export const tracerObservationSpanDelete = async (id: string, options?: RequestInit): Promise<tracerObservationSpanDeleteResponse> => {

  return apiMutator<tracerObservationSpanDeleteResponse>(getTracerObservationSpanDeleteUrl(id),
  {
    ...options,
    method: 'DELETE'


  }
);}



export type tracerProjectVersionAddAnnotationsResponse201 = {
  data: ProjectVersionApi
  status: 201
}

export type tracerProjectVersionAddAnnotationsResponseSuccess = (tracerProjectVersionAddAnnotationsResponse201) & {
  headers: Headers;
};
;

export type tracerProjectVersionAddAnnotationsResponse = (tracerProjectVersionAddAnnotationsResponseSuccess)

export const getTracerProjectVersionAddAnnotationsUrl = () => {




  return `/tracer/project-version/add_annotations/`
}

export const tracerProjectVersionAddAnnotations = async (projectVersionApi: NonReadonly<ProjectVersionApi>, options?: RequestInit): Promise<tracerProjectVersionAddAnnotationsResponse> => {

  return apiMutator<tracerProjectVersionAddAnnotationsResponse>(getTracerProjectVersionAddAnnotationsUrl(),
  {
    ...options,
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...options?.headers },
    body: JSON.stringify(
      projectVersionApi,)
  }
);}



export type tracerProjectListResponse200 = {
  data: TracerProjectList200
  status: 200
}

export type tracerProjectListResponseSuccess = (tracerProjectListResponse200) & {
  headers: Headers;
};
;

export type tracerProjectListResponse = (tracerProjectListResponseSuccess)

export const getTracerProjectListUrl = (params?: TracerProjectListParams,) => {
  const normalizedParams = new URLSearchParams();

  Object.entries(params || {}).forEach(([key, value]) => {

    if (value !== undefined) {
      normalizedParams.append(key, value === null ? 'null' : value.toString())
    }
  });

  const stringifiedParams = normalizedParams.toString();

  return stringifiedParams.length > 0 ? `/tracer/project/?${stringifiedParams}` : `/tracer/project/`
}

/**
 * Get a paginated list of all projects for the organization.
 */
export const tracerProjectList = async (params?: TracerProjectListParams, options?: RequestInit): Promise<tracerProjectListResponse> => {

  return apiMutator<tracerProjectListResponse>(getTracerProjectListUrl(params),
  {
    ...options,
    method: 'GET'


  }
);}



export type tracerProjectCreateResponse201 = {
  data: ProjectApi
  status: 201
}

export type tracerProjectCreateResponseSuccess = (tracerProjectCreateResponse201) & {
  headers: Headers;
};
;

export type tracerProjectCreateResponse = (tracerProjectCreateResponseSuccess)

export const getTracerProjectCreateUrl = () => {




  return `/tracer/project/`
}

/**
 * Create a new project.
 */
export const tracerProjectCreate = async (projectApi: NonReadonly<ProjectApi>, options?: RequestInit): Promise<tracerProjectCreateResponse> => {

  return apiMutator<tracerProjectCreateResponse>(getTracerProjectCreateUrl(),
  {
    ...options,
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...options?.headers },
    body: JSON.stringify(
      projectApi,)
  }
);}



export type tracerProjectFetchSystemMetricsResponse200 = {
  data: TracerProjectFetchSystemMetrics200
  status: 200
}

export type tracerProjectFetchSystemMetricsResponseSuccess = (tracerProjectFetchSystemMetricsResponse200) & {
  headers: Headers;
};
;

export type tracerProjectFetchSystemMetricsResponse = (tracerProjectFetchSystemMetricsResponseSuccess)

export const getTracerProjectFetchSystemMetricsUrl = (params?: TracerProjectFetchSystemMetricsParams,) => {
  const normalizedParams = new URLSearchParams();

  Object.entries(params || {}).forEach(([key, value]) => {

    if (value !== undefined) {
      normalizedParams.append(key, value === null ? 'null' : value.toString())
    }
  });

  const stringifiedParams = normalizedParams.toString();

  return stringifiedParams.length > 0 ? `/tracer/project/fetch_system_metrics/?${stringifiedParams}` : `/tracer/project/fetch_system_metrics/`
}

export const tracerProjectFetchSystemMetrics = async (params?: TracerProjectFetchSystemMetricsParams, options?: RequestInit): Promise<tracerProjectFetchSystemMetricsResponse> => {

  return apiMutator<tracerProjectFetchSystemMetricsResponse>(getTracerProjectFetchSystemMetricsUrl(params),
  {
    ...options,
    method: 'GET'


  }
);}



export type tracerProjectGetGraphDataResponse200 = {
  data: TracerProjectGetGraphData200
  status: 200
}

export type tracerProjectGetGraphDataResponseSuccess = (tracerProjectGetGraphDataResponse200) & {
  headers: Headers;
};
;

export type tracerProjectGetGraphDataResponse = (tracerProjectGetGraphDataResponseSuccess)

export const getTracerProjectGetGraphDataUrl = (params?: TracerProjectGetGraphDataParams,) => {
  const normalizedParams = new URLSearchParams();

  Object.entries(params || {}).forEach(([key, value]) => {

    if (value !== undefined) {
      normalizedParams.append(key, value === null ? 'null' : value.toString())
    }
  });

  const stringifiedParams = normalizedParams.toString();

  return stringifiedParams.length > 0 ? `/tracer/project/get_graph_data/?${stringifiedParams}` : `/tracer/project/get_graph_data/`
}

export const tracerProjectGetGraphData = async (params?: TracerProjectGetGraphDataParams, options?: RequestInit): Promise<tracerProjectGetGraphDataResponse> => {

  return apiMutator<tracerProjectGetGraphDataResponse>(getTracerProjectGetGraphDataUrl(params),
  {
    ...options,
    method: 'GET'


  }
);}



export type tracerProjectGetUserGraphDataResponse201 = {
  data: ProjectApi
  status: 201
}

export type tracerProjectGetUserGraphDataResponseSuccess = (tracerProjectGetUserGraphDataResponse201) & {
  headers: Headers;
};
;

export type tracerProjectGetUserGraphDataResponse = (tracerProjectGetUserGraphDataResponseSuccess)

export const getTracerProjectGetUserGraphDataUrl = () => {




  return `/tracer/project/get_user_graph_data/`
}

export const tracerProjectGetUserGraphData = async (projectApi: NonReadonly<ProjectApi>, options?: RequestInit): Promise<tracerProjectGetUserGraphDataResponse> => {

  return apiMutator<tracerProjectGetUserGraphDataResponse>(getTracerProjectGetUserGraphDataUrl(),
  {
    ...options,
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...options?.headers },
    body: JSON.stringify(
      projectApi,)
  }
);}



export type tracerProjectGetUserMetricsResponse201 = {
  data: ProjectApi
  status: 201
}

export type tracerProjectGetUserMetricsResponseSuccess = (tracerProjectGetUserMetricsResponse201) & {
  headers: Headers;
};
;

export type tracerProjectGetUserMetricsResponse = (tracerProjectGetUserMetricsResponseSuccess)

export const getTracerProjectGetUserMetricsUrl = () => {




  return `/tracer/project/get_user_metrics/`
}

export const tracerProjectGetUserMetrics = async (projectApi: NonReadonly<ProjectApi>, options?: RequestInit): Promise<tracerProjectGetUserMetricsResponse> => {

  return apiMutator<tracerProjectGetUserMetricsResponse>(getTracerProjectGetUserMetricsUrl(),
  {
    ...options,
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...options?.headers },
    body: JSON.stringify(
      projectApi,)
  }
);}



export type tracerProjectGetUsersAggregateGraphDataResponse201 = {
  data: ProjectApi
  status: 201
}

export type tracerProjectGetUsersAggregateGraphDataResponseSuccess = (tracerProjectGetUsersAggregateGraphDataResponse201) & {
  headers: Headers;
};
;

export type tracerProjectGetUsersAggregateGraphDataResponse = (tracerProjectGetUsersAggregateGraphDataResponseSuccess)

export const getTracerProjectGetUsersAggregateGraphDataUrl = () => {




  return `/tracer/project/get_users_aggregate_graph_data/`
}

/**
 * Supports SYSTEM_METRIC, EVAL, and ANNOTATION types.
All metrics are aggregated at the user level.
 * @summary Fetch time-series aggregate user metrics for the observe graph.
 */
export const tracerProjectGetUsersAggregateGraphData = async (projectApi: NonReadonly<ProjectApi>, options?: RequestInit): Promise<tracerProjectGetUsersAggregateGraphDataResponse> => {

  return apiMutator<tracerProjectGetUsersAggregateGraphDataResponse>(getTracerProjectGetUsersAggregateGraphDataUrl(),
  {
    ...options,
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...options?.headers },
    body: JSON.stringify(
      projectApi,)
  }
);}



export type tracerProjectListProjectIdsResponse200 = {
  data: TracerProjectListProjectIds200
  status: 200
}

export type tracerProjectListProjectIdsResponseSuccess = (tracerProjectListProjectIdsResponse200) & {
  headers: Headers;
};
;

export type tracerProjectListProjectIdsResponse = (tracerProjectListProjectIdsResponseSuccess)

export const getTracerProjectListProjectIdsUrl = (params?: TracerProjectListProjectIdsParams,) => {
  const normalizedParams = new URLSearchParams();

  Object.entries(params || {}).forEach(([key, value]) => {

    if (value !== undefined) {
      normalizedParams.append(key, value === null ? 'null' : value.toString())
    }
  });

  const stringifiedParams = normalizedParams.toString();

  return stringifiedParams.length > 0 ? `/tracer/project/list_project_ids/?${stringifiedParams}` : `/tracer/project/list_project_ids/`
}

/**
 * List project ids for a given project.
 */
export const tracerProjectListProjectIds = async (params?: TracerProjectListProjectIdsParams, options?: RequestInit): Promise<tracerProjectListProjectIdsResponse> => {

  return apiMutator<tracerProjectListProjectIdsResponse>(getTracerProjectListProjectIdsUrl(params),
  {
    ...options,
    method: 'GET'


  }
);}



export type tracerProjectListProjectsResponse200 = {
  data: TracerProjectListProjects200
  status: 200
}

export type tracerProjectListProjectsResponseSuccess = (tracerProjectListProjectsResponse200) & {
  headers: Headers;
};
;

export type tracerProjectListProjectsResponse = (tracerProjectListProjectsResponseSuccess)

export const getTracerProjectListProjectsUrl = (params?: TracerProjectListProjectsParams,) => {
  const normalizedParams = new URLSearchParams();

  Object.entries(params || {}).forEach(([key, value]) => {

    if (value !== undefined) {
      normalizedParams.append(key, value === null ? 'null' : value.toString())
    }
  });

  const stringifiedParams = normalizedParams.toString();

  return stringifiedParams.length > 0 ? `/tracer/project/list_projects/?${stringifiedParams}` : `/tracer/project/list_projects/`
}

/**
 * Volume counts come from ClickHouse (fast) instead of a PG
JOIN on observation_spans (was 12+ seconds).
 * @summary List projects filtered by organization ID.
 */
export const tracerProjectListProjects = async (params?: TracerProjectListProjectsParams, options?: RequestInit): Promise<tracerProjectListProjectsResponse> => {

  return apiMutator<tracerProjectListProjectsResponse>(getTracerProjectListProjectsUrl(params),
  {
    ...options,
    method: 'GET'


  }
);}



export type tracerProjectProjectSdkCodeResponse200 = {
  data: TracerProjectProjectSdkCode200
  status: 200
}

export type tracerProjectProjectSdkCodeResponseSuccess = (tracerProjectProjectSdkCodeResponse200) & {
  headers: Headers;
};
;

export type tracerProjectProjectSdkCodeResponse = (tracerProjectProjectSdkCodeResponseSuccess)

export const getTracerProjectProjectSdkCodeUrl = (params?: TracerProjectProjectSdkCodeParams,) => {
  const normalizedParams = new URLSearchParams();

  Object.entries(params || {}).forEach(([key, value]) => {

    if (value !== undefined) {
      normalizedParams.append(key, value === null ? 'null' : value.toString())
    }
  });

  const stringifiedParams = normalizedParams.toString();

  return stringifiedParams.length > 0 ? `/tracer/project/project_sdk_code/?${stringifiedParams}` : `/tracer/project/project_sdk_code/`
}

export const tracerProjectProjectSdkCode = async (params?: TracerProjectProjectSdkCodeParams, options?: RequestInit): Promise<tracerProjectProjectSdkCodeResponse> => {

  return apiMutator<tracerProjectProjectSdkCodeResponse>(getTracerProjectProjectSdkCodeUrl(params),
  {
    ...options,
    method: 'GET'


  }
);}



export type tracerProjectUpdateProjectConfigResponse201 = {
  data: ProjectApi
  status: 201
}

export type tracerProjectUpdateProjectConfigResponseSuccess = (tracerProjectUpdateProjectConfigResponse201) & {
  headers: Headers;
};
;

export type tracerProjectUpdateProjectConfigResponse = (tracerProjectUpdateProjectConfigResponseSuccess)

export const getTracerProjectUpdateProjectConfigUrl = () => {




  return `/tracer/project/update_project_config/`
}

export const tracerProjectUpdateProjectConfig = async (projectApi: NonReadonly<ProjectApi>, options?: RequestInit): Promise<tracerProjectUpdateProjectConfigResponse> => {

  return apiMutator<tracerProjectUpdateProjectConfigResponse>(getTracerProjectUpdateProjectConfigUrl(),
  {
    ...options,
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...options?.headers },
    body: JSON.stringify(
      projectApi,)
  }
);}



export type tracerProjectUpdateProjectNameResponse201 = {
  data: ProjectApi
  status: 201
}

export type tracerProjectUpdateProjectNameResponseSuccess = (tracerProjectUpdateProjectNameResponse201) & {
  headers: Headers;
};
;

export type tracerProjectUpdateProjectNameResponse = (tracerProjectUpdateProjectNameResponseSuccess)

export const getTracerProjectUpdateProjectNameUrl = () => {




  return `/tracer/project/update_project_name/`
}

export const tracerProjectUpdateProjectName = async (projectApi: NonReadonly<ProjectApi>, options?: RequestInit): Promise<tracerProjectUpdateProjectNameResponse> => {

  return apiMutator<tracerProjectUpdateProjectNameResponse>(getTracerProjectUpdateProjectNameUrl(),
  {
    ...options,
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...options?.headers },
    body: JSON.stringify(
      projectApi,)
  }
);}



export type tracerProjectUpdateProjectSessionConfigResponse201 = {
  data: ProjectApi
  status: 201
}

export type tracerProjectUpdateProjectSessionConfigResponseSuccess = (tracerProjectUpdateProjectSessionConfigResponse201) & {
  headers: Headers;
};
;

export type tracerProjectUpdateProjectSessionConfigResponse = (tracerProjectUpdateProjectSessionConfigResponseSuccess)

export const getTracerProjectUpdateProjectSessionConfigUrl = () => {




  return `/tracer/project/update_project_session_config/`
}

export const tracerProjectUpdateProjectSessionConfig = async (projectApi: NonReadonly<ProjectApi>, options?: RequestInit): Promise<tracerProjectUpdateProjectSessionConfigResponse> => {

  return apiMutator<tracerProjectUpdateProjectSessionConfigResponse>(getTracerProjectUpdateProjectSessionConfigUrl(),
  {
    ...options,
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...options?.headers },
    body: JSON.stringify(
      projectApi,)
  }
);}



export type tracerProjectReadResponse200 = {
  data: ProjectApi
  status: 200
}

export type tracerProjectReadResponseSuccess = (tracerProjectReadResponse200) & {
  headers: Headers;
};
;

export type tracerProjectReadResponse = (tracerProjectReadResponseSuccess)

export const getTracerProjectReadUrl = (id: string,) => {




  return `/tracer/project/${id}/`
}

/**
 * Get a single project by ID with sampling rate.
 */
export const tracerProjectRead = async (id: string, options?: RequestInit): Promise<tracerProjectReadResponse> => {

  return apiMutator<tracerProjectReadResponse>(getTracerProjectReadUrl(id),
  {
    ...options,
    method: 'GET'


  }
);}



export type tracerProjectUpdateResponse200 = {
  data: ProjectApi
  status: 200
}

export type tracerProjectUpdateResponseSuccess = (tracerProjectUpdateResponse200) & {
  headers: Headers;
};
;

export type tracerProjectUpdateResponse = (tracerProjectUpdateResponseSuccess)

export const getTracerProjectUpdateUrl = (id: string,) => {




  return `/tracer/project/${id}/`
}

export const tracerProjectUpdate = async (id: string,
    projectApi: NonReadonly<ProjectApi>, options?: RequestInit): Promise<tracerProjectUpdateResponse> => {

  return apiMutator<tracerProjectUpdateResponse>(getTracerProjectUpdateUrl(id),
  {
    ...options,
    method: 'PUT',
    headers: { 'Content-Type': 'application/json', ...options?.headers },
    body: JSON.stringify(
      projectApi,)
  }
);}



export type tracerProjectPartialUpdateResponse200 = {
  data: ProjectApi
  status: 200
}

export type tracerProjectPartialUpdateResponseSuccess = (tracerProjectPartialUpdateResponse200) & {
  headers: Headers;
};
;

export type tracerProjectPartialUpdateResponse = (tracerProjectPartialUpdateResponseSuccess)

export const getTracerProjectPartialUpdateUrl = (id: string,) => {




  return `/tracer/project/${id}/`
}

export const tracerProjectPartialUpdate = async (id: string,
    projectApi: NonReadonly<ProjectApi>, options?: RequestInit): Promise<tracerProjectPartialUpdateResponse> => {

  return apiMutator<tracerProjectPartialUpdateResponse>(getTracerProjectPartialUpdateUrl(id),
  {
    ...options,
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json', ...options?.headers },
    body: JSON.stringify(
      projectApi,)
  }
);}



export type tracerProjectDeleteResponse204 = {
  data: void
  status: 204
}

export type tracerProjectDeleteResponseSuccess = (tracerProjectDeleteResponse204) & {
  headers: Headers;
};
;

export type tracerProjectDeleteResponse = (tracerProjectDeleteResponseSuccess)

export const getTracerProjectDeleteUrl = (id: string,) => {




  return `/tracer/project/${id}/`
}

export const tracerProjectDelete = async (id: string, options?: RequestInit): Promise<tracerProjectDeleteResponse> => {

  return apiMutator<tracerProjectDeleteResponse>(getTracerProjectDeleteUrl(id),
  {
    ...options,
    method: 'DELETE'


  }
);}



export type tracerProjectUpdateTagsResponse200 = {
  data: ProjectApi
  status: 200
}

export type tracerProjectUpdateTagsResponseSuccess = (tracerProjectUpdateTagsResponse200) & {
  headers: Headers;
};
;

export type tracerProjectUpdateTagsResponse = (tracerProjectUpdateTagsResponseSuccess)

export const getTracerProjectUpdateTagsUrl = (id: string,) => {




  return `/tracer/project/${id}/tags/`
}

/**
 * Update tags for a project.
 */
export const tracerProjectUpdateTags = async (id: string,
    projectApi: NonReadonly<ProjectApi>, options?: RequestInit): Promise<tracerProjectUpdateTagsResponse> => {

  return apiMutator<tracerProjectUpdateTagsResponse>(getTracerProjectUpdateTagsUrl(id),
  {
    ...options,
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json', ...options?.headers },
    body: JSON.stringify(
      projectApi,)
  }
);}



export type tracerTraceAnnotationListResponse200 = {
  data: TracerTraceAnnotationList200
  status: 200
}

export type tracerTraceAnnotationListResponseSuccess = (tracerTraceAnnotationListResponse200) & {
  headers: Headers;
};
;

export type tracerTraceAnnotationListResponse = (tracerTraceAnnotationListResponseSuccess)

export const getTracerTraceAnnotationListUrl = (params?: TracerTraceAnnotationListParams,) => {
  const normalizedParams = new URLSearchParams();

  Object.entries(params || {}).forEach(([key, value]) => {

    if (value !== undefined) {
      normalizedParams.append(key, value === null ? 'null' : value.toString())
    }
  });

  const stringifiedParams = normalizedParams.toString();

  return stringifiedParams.length > 0 ? `/tracer/trace-annotation/?${stringifiedParams}` : `/tracer/trace-annotation/`
}

export const tracerTraceAnnotationList = async (params?: TracerTraceAnnotationListParams, options?: RequestInit): Promise<tracerTraceAnnotationListResponse> => {

  return apiMutator<tracerTraceAnnotationListResponse>(getTracerTraceAnnotationListUrl(params),
  {
    ...options,
    method: 'GET'


  }
);}



export type tracerTraceAnnotationCreateResponse201 = {
  data: GetTraceAnnotationApi
  status: 201
}

export type tracerTraceAnnotationCreateResponseSuccess = (tracerTraceAnnotationCreateResponse201) & {
  headers: Headers;
};
;

export type tracerTraceAnnotationCreateResponse = (tracerTraceAnnotationCreateResponseSuccess)

export const getTracerTraceAnnotationCreateUrl = () => {




  return `/tracer/trace-annotation/`
}

export const tracerTraceAnnotationCreate = async (getTraceAnnotationApi: GetTraceAnnotationApi, options?: RequestInit): Promise<tracerTraceAnnotationCreateResponse> => {

  return apiMutator<tracerTraceAnnotationCreateResponse>(getTracerTraceAnnotationCreateUrl(),
  {
    ...options,
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...options?.headers },
    body: JSON.stringify(
      getTraceAnnotationApi,)
  }
);}



export type tracerTraceAnnotationGetAnnotationValuesResponse200 = {
  data: TracerTraceAnnotationGetAnnotationValues200
  status: 200
}

export type tracerTraceAnnotationGetAnnotationValuesResponseSuccess = (tracerTraceAnnotationGetAnnotationValuesResponse200) & {
  headers: Headers;
};
;

export type tracerTraceAnnotationGetAnnotationValuesResponse = (tracerTraceAnnotationGetAnnotationValuesResponseSuccess)

export const getTracerTraceAnnotationGetAnnotationValuesUrl = (params?: TracerTraceAnnotationGetAnnotationValuesParams,) => {
  const normalizedParams = new URLSearchParams();

  Object.entries(params || {}).forEach(([key, value]) => {

    if (value !== undefined) {
      normalizedParams.append(key, value === null ? 'null' : value.toString())
    }
  });

  const stringifiedParams = normalizedParams.toString();

  return stringifiedParams.length > 0 ? `/tracer/trace-annotation/get_annotation_values/?${stringifiedParams}` : `/tracer/trace-annotation/get_annotation_values/`
}

export const tracerTraceAnnotationGetAnnotationValues = async (params?: TracerTraceAnnotationGetAnnotationValuesParams, options?: RequestInit): Promise<tracerTraceAnnotationGetAnnotationValuesResponse> => {

  return apiMutator<tracerTraceAnnotationGetAnnotationValuesResponse>(getTracerTraceAnnotationGetAnnotationValuesUrl(params),
  {
    ...options,
    method: 'GET'


  }
);}



export type tracerTraceAnnotationReadResponse200 = {
  data: GetTraceAnnotationApi
  status: 200
}

export type tracerTraceAnnotationReadResponseSuccess = (tracerTraceAnnotationReadResponse200) & {
  headers: Headers;
};
;

export type tracerTraceAnnotationReadResponse = (tracerTraceAnnotationReadResponseSuccess)

export const getTracerTraceAnnotationReadUrl = (id: string,) => {




  return `/tracer/trace-annotation/${id}/`
}

export const tracerTraceAnnotationRead = async (id: string, options?: RequestInit): Promise<tracerTraceAnnotationReadResponse> => {

  return apiMutator<tracerTraceAnnotationReadResponse>(getTracerTraceAnnotationReadUrl(id),
  {
    ...options,
    method: 'GET'


  }
);}



export type tracerTraceAnnotationUpdateResponse200 = {
  data: GetTraceAnnotationApi
  status: 200
}

export type tracerTraceAnnotationUpdateResponseSuccess = (tracerTraceAnnotationUpdateResponse200) & {
  headers: Headers;
};
;

export type tracerTraceAnnotationUpdateResponse = (tracerTraceAnnotationUpdateResponseSuccess)

export const getTracerTraceAnnotationUpdateUrl = (id: string,) => {




  return `/tracer/trace-annotation/${id}/`
}

export const tracerTraceAnnotationUpdate = async (id: string,
    getTraceAnnotationApi: GetTraceAnnotationApi, options?: RequestInit): Promise<tracerTraceAnnotationUpdateResponse> => {

  return apiMutator<tracerTraceAnnotationUpdateResponse>(getTracerTraceAnnotationUpdateUrl(id),
  {
    ...options,
    method: 'PUT',
    headers: { 'Content-Type': 'application/json', ...options?.headers },
    body: JSON.stringify(
      getTraceAnnotationApi,)
  }
);}



export type tracerTraceAnnotationPartialUpdateResponse200 = {
  data: GetTraceAnnotationApi
  status: 200
}

export type tracerTraceAnnotationPartialUpdateResponseSuccess = (tracerTraceAnnotationPartialUpdateResponse200) & {
  headers: Headers;
};
;

export type tracerTraceAnnotationPartialUpdateResponse = (tracerTraceAnnotationPartialUpdateResponseSuccess)

export const getTracerTraceAnnotationPartialUpdateUrl = (id: string,) => {




  return `/tracer/trace-annotation/${id}/`
}

export const tracerTraceAnnotationPartialUpdate = async (id: string,
    getTraceAnnotationApi: GetTraceAnnotationApi, options?: RequestInit): Promise<tracerTraceAnnotationPartialUpdateResponse> => {

  return apiMutator<tracerTraceAnnotationPartialUpdateResponse>(getTracerTraceAnnotationPartialUpdateUrl(id),
  {
    ...options,
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json', ...options?.headers },
    body: JSON.stringify(
      getTraceAnnotationApi,)
  }
);}



export type tracerTraceAnnotationDeleteResponse204 = {
  data: void
  status: 204
}

export type tracerTraceAnnotationDeleteResponseSuccess = (tracerTraceAnnotationDeleteResponse204) & {
  headers: Headers;
};
;

export type tracerTraceAnnotationDeleteResponse = (tracerTraceAnnotationDeleteResponseSuccess)

export const getTracerTraceAnnotationDeleteUrl = (id: string,) => {




  return `/tracer/trace-annotation/${id}/`
}

export const tracerTraceAnnotationDelete = async (id: string, options?: RequestInit): Promise<tracerTraceAnnotationDeleteResponse> => {

  return apiMutator<tracerTraceAnnotationDeleteResponse>(getTracerTraceAnnotationDeleteUrl(id),
  {
    ...options,
    method: 'DELETE'


  }
);}



export type tracerTraceSessionListResponse200 = {
  data: TracerTraceSessionList200
  status: 200
}

export type tracerTraceSessionListResponseSuccess = (tracerTraceSessionListResponse200) & {
  headers: Headers;
};
;

export type tracerTraceSessionListResponse = (tracerTraceSessionListResponseSuccess)

export const getTracerTraceSessionListUrl = (params?: TracerTraceSessionListParams,) => {
  const normalizedParams = new URLSearchParams();

  Object.entries(params || {}).forEach(([key, value]) => {

    if (value !== undefined) {
      normalizedParams.append(key, value === null ? 'null' : value.toString())
    }
  });

  const stringifiedParams = normalizedParams.toString();

  return stringifiedParams.length > 0 ? `/tracer/trace-session/?${stringifiedParams}` : `/tracer/trace-session/`
}

export const tracerTraceSessionList = async (params?: TracerTraceSessionListParams, options?: RequestInit): Promise<tracerTraceSessionListResponse> => {

  return apiMutator<tracerTraceSessionListResponse>(getTracerTraceSessionListUrl(params),
  {
    ...options,
    method: 'GET'


  }
);}



export type tracerTraceSessionCreateResponse201 = {
  data: TraceSessionApi
  status: 201
}

export type tracerTraceSessionCreateResponseSuccess = (tracerTraceSessionCreateResponse201) & {
  headers: Headers;
};
;

export type tracerTraceSessionCreateResponse = (tracerTraceSessionCreateResponseSuccess)

export const getTracerTraceSessionCreateUrl = () => {




  return `/tracer/trace-session/`
}

export const tracerTraceSessionCreate = async (traceSessionApi: NonReadonly<TraceSessionApi>, options?: RequestInit): Promise<tracerTraceSessionCreateResponse> => {

  return apiMutator<tracerTraceSessionCreateResponse>(getTracerTraceSessionCreateUrl(),
  {
    ...options,
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...options?.headers },
    body: JSON.stringify(
      traceSessionApi,)
  }
);}



export type tracerTraceSessionGetSessionFilterValuesResponse200 = {
  data: TracerTraceSessionGetSessionFilterValues200
  status: 200
}

export type tracerTraceSessionGetSessionFilterValuesResponseSuccess = (tracerTraceSessionGetSessionFilterValuesResponse200) & {
  headers: Headers;
};
;

export type tracerTraceSessionGetSessionFilterValuesResponse = (tracerTraceSessionGetSessionFilterValuesResponseSuccess)

export const getTracerTraceSessionGetSessionFilterValuesUrl = (params?: TracerTraceSessionGetSessionFilterValuesParams,) => {
  const normalizedParams = new URLSearchParams();

  Object.entries(params || {}).forEach(([key, value]) => {

    if (value !== undefined) {
      normalizedParams.append(key, value === null ? 'null' : value.toString())
    }
  });

  const stringifiedParams = normalizedParams.toString();

  return stringifiedParams.length > 0 ? `/tracer/trace-session/get_session_filter_values/?${stringifiedParams}` : `/tracer/trace-session/get_session_filter_values/`
}

/**
 * Return distinct values for a session-level column.
Used by the filter panel's value picker for session-specific fields
(session_id, user_id, first_message, etc.).

Query params:
    project_id: required
    column: the session column name (camelCase, e.g. "sessionId")
    search: optional search substring
    page: page number (0-based), default 0
    page_size: default 50
 */
export const tracerTraceSessionGetSessionFilterValues = async (params?: TracerTraceSessionGetSessionFilterValuesParams, options?: RequestInit): Promise<tracerTraceSessionGetSessionFilterValuesResponse> => {

  return apiMutator<tracerTraceSessionGetSessionFilterValuesResponse>(getTracerTraceSessionGetSessionFilterValuesUrl(params),
  {
    ...options,
    method: 'GET'


  }
);}



export type tracerTraceSessionGetSessionGraphDataResponse201 = {
  data: TraceSessionApi
  status: 201
}

export type tracerTraceSessionGetSessionGraphDataResponseSuccess = (tracerTraceSessionGetSessionGraphDataResponse201) & {
  headers: Headers;
};
;

export type tracerTraceSessionGetSessionGraphDataResponse = (tracerTraceSessionGetSessionGraphDataResponseSuccess)

export const getTracerTraceSessionGetSessionGraphDataUrl = () => {




  return `/tracer/trace-session/get_session_graph_data/`
}

/**
 * Supports the same metric types as the trace graph endpoint:
- SYSTEM_METRIC: latency, tokens, cost, error_rate, session_count,
  avg_duration, avg_traces_per_session — all aggregated at session level
- EVAL: eval scores averaged across sessions
- ANNOTATION: annotation scores averaged across sessions

Response shape matches trace graph: {metric_name, data: [{timestamp, value, primary_traffic}]}
 * @summary Fetch time-series session metrics for the observe graph.
 */
export const tracerTraceSessionGetSessionGraphData = async (traceSessionApi: NonReadonly<TraceSessionApi>, options?: RequestInit): Promise<tracerTraceSessionGetSessionGraphDataResponse> => {

  return apiMutator<tracerTraceSessionGetSessionGraphDataResponse>(getTracerTraceSessionGetSessionGraphDataUrl(),
  {
    ...options,
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...options?.headers },
    body: JSON.stringify(
      traceSessionApi,)
  }
);}



export type tracerTraceSessionGetTraceSessionExportDataResponse200 = {
  data: TracerTraceSessionGetTraceSessionExportData200
  status: 200
}

export type tracerTraceSessionGetTraceSessionExportDataResponseSuccess = (tracerTraceSessionGetTraceSessionExportDataResponse200) & {
  headers: Headers;
};
;

export type tracerTraceSessionGetTraceSessionExportDataResponse = (tracerTraceSessionGetTraceSessionExportDataResponseSuccess)

export const getTracerTraceSessionGetTraceSessionExportDataUrl = (params?: TracerTraceSessionGetTraceSessionExportDataParams,) => {
  const normalizedParams = new URLSearchParams();

  Object.entries(params || {}).forEach(([key, value]) => {

    if (value !== undefined) {
      normalizedParams.append(key, value === null ? 'null' : value.toString())
    }
  });

  const stringifiedParams = normalizedParams.toString();

  return stringifiedParams.length > 0 ? `/tracer/trace-session/get_trace_session_export_data/?${stringifiedParams}` : `/tracer/trace-session/get_trace_session_export_data/`
}

/**
 * Export traces filtered by project ID and project version ID with optimized queries.
 */
export const tracerTraceSessionGetTraceSessionExportData = async (params?: TracerTraceSessionGetTraceSessionExportDataParams, options?: RequestInit): Promise<tracerTraceSessionGetTraceSessionExportDataResponse> => {

  return apiMutator<tracerTraceSessionGetTraceSessionExportDataResponse>(getTracerTraceSessionGetTraceSessionExportDataUrl(params),
  {
    ...options,
    method: 'GET'


  }
);}



export type tracerTraceSessionListSessionsResponse200 = {
  data: TracerTraceSessionListSessions200
  status: 200
}

export type tracerTraceSessionListSessionsResponseSuccess = (tracerTraceSessionListSessionsResponse200) & {
  headers: Headers;
};
;

export type tracerTraceSessionListSessionsResponse = (tracerTraceSessionListSessionsResponseSuccess)

export const getTracerTraceSessionListSessionsUrl = (params?: TracerTraceSessionListSessionsParams,) => {
  const normalizedParams = new URLSearchParams();

  Object.entries(params || {}).forEach(([key, value]) => {

    if (value !== undefined) {
      normalizedParams.append(key, value === null ? 'null' : value.toString())
    }
  });

  const stringifiedParams = normalizedParams.toString();

  return stringifiedParams.length > 0 ? `/tracer/trace-session/list_sessions/?${stringifiedParams}` : `/tracer/trace-session/list_sessions/`
}

/**
 * List traces filtered by project ID and project version ID with optimized queries.
 */
export const tracerTraceSessionListSessions = async (params?: TracerTraceSessionListSessionsParams, options?: RequestInit): Promise<tracerTraceSessionListSessionsResponse> => {

  return apiMutator<tracerTraceSessionListSessionsResponse>(getTracerTraceSessionListSessionsUrl(params),
  {
    ...options,
    method: 'GET'


  }
);}



export type tracerTraceSessionReadResponse200 = {
  data: TraceSessionApi
  status: 200
}

export type tracerTraceSessionReadResponseSuccess = (tracerTraceSessionReadResponse200) & {
  headers: Headers;
};
;

export type tracerTraceSessionReadResponse = (tracerTraceSessionReadResponseSuccess)

export const getTracerTraceSessionReadUrl = (id: string,) => {




  return `/tracer/trace-session/${id}/`
}

export const tracerTraceSessionRead = async (id: string, options?: RequestInit): Promise<tracerTraceSessionReadResponse> => {

  return apiMutator<tracerTraceSessionReadResponse>(getTracerTraceSessionReadUrl(id),
  {
    ...options,
    method: 'GET'


  }
);}



export type tracerTraceSessionUpdateResponse200 = {
  data: TraceSessionApi
  status: 200
}

export type tracerTraceSessionUpdateResponseSuccess = (tracerTraceSessionUpdateResponse200) & {
  headers: Headers;
};
;

export type tracerTraceSessionUpdateResponse = (tracerTraceSessionUpdateResponseSuccess)

export const getTracerTraceSessionUpdateUrl = (id: string,) => {




  return `/tracer/trace-session/${id}/`
}

export const tracerTraceSessionUpdate = async (id: string,
    traceSessionApi: NonReadonly<TraceSessionApi>, options?: RequestInit): Promise<tracerTraceSessionUpdateResponse> => {

  return apiMutator<tracerTraceSessionUpdateResponse>(getTracerTraceSessionUpdateUrl(id),
  {
    ...options,
    method: 'PUT',
    headers: { 'Content-Type': 'application/json', ...options?.headers },
    body: JSON.stringify(
      traceSessionApi,)
  }
);}



export type tracerTraceSessionPartialUpdateResponse200 = {
  data: TraceSessionApi
  status: 200
}

export type tracerTraceSessionPartialUpdateResponseSuccess = (tracerTraceSessionPartialUpdateResponse200) & {
  headers: Headers;
};
;

export type tracerTraceSessionPartialUpdateResponse = (tracerTraceSessionPartialUpdateResponseSuccess)

export const getTracerTraceSessionPartialUpdateUrl = (id: string,) => {




  return `/tracer/trace-session/${id}/`
}

export const tracerTraceSessionPartialUpdate = async (id: string,
    traceSessionApi: NonReadonly<TraceSessionApi>, options?: RequestInit): Promise<tracerTraceSessionPartialUpdateResponse> => {

  return apiMutator<tracerTraceSessionPartialUpdateResponse>(getTracerTraceSessionPartialUpdateUrl(id),
  {
    ...options,
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json', ...options?.headers },
    body: JSON.stringify(
      traceSessionApi,)
  }
);}



export type tracerTraceSessionDeleteResponse204 = {
  data: void
  status: 204
}

export type tracerTraceSessionDeleteResponseSuccess = (tracerTraceSessionDeleteResponse204) & {
  headers: Headers;
};
;

export type tracerTraceSessionDeleteResponse = (tracerTraceSessionDeleteResponseSuccess)

export const getTracerTraceSessionDeleteUrl = (id: string,) => {




  return `/tracer/trace-session/${id}/`
}

export const tracerTraceSessionDelete = async (id: string, options?: RequestInit): Promise<tracerTraceSessionDeleteResponse> => {

  return apiMutator<tracerTraceSessionDeleteResponse>(getTracerTraceSessionDeleteUrl(id),
  {
    ...options,
    method: 'DELETE'


  }
);}



export type tracerTraceSessionEvalLogsResponse200 = {
  data: TraceSessionApi
  status: 200
}

export type tracerTraceSessionEvalLogsResponseSuccess = (tracerTraceSessionEvalLogsResponse200) & {
  headers: Headers;
};
;

export type tracerTraceSessionEvalLogsResponse = (tracerTraceSessionEvalLogsResponseSuccess)

export const getTracerTraceSessionEvalLogsUrl = (id: string,) => {




  return `/tracer/trace-session/${id}/eval_logs/`
}

/**
 * Session-level eval results are walled off from span/trace surfaces
by ``target_type='session'`` — this endpoint is the only place
they appear.

Query params:
    page (int, 1-indexed, default 1)
    page_size (int, default 25, max 100)
 * @summary Session-scoped eval log feed for TracesDrawer's "Evals" tab.
 */
export const tracerTraceSessionEvalLogs = async (id: string, options?: RequestInit): Promise<tracerTraceSessionEvalLogsResponse> => {

  return apiMutator<tracerTraceSessionEvalLogsResponse>(getTracerTraceSessionEvalLogsUrl(id),
  {
    ...options,
    method: 'GET'


  }
);}



export type tracerTraceListResponse200 = {
  data: TracerTraceList200
  status: 200
}

export type tracerTraceListResponseSuccess = (tracerTraceListResponse200) & {
  headers: Headers;
};
;

export type tracerTraceListResponse = (tracerTraceListResponseSuccess)

export const getTracerTraceListUrl = (params?: TracerTraceListParams,) => {
  const normalizedParams = new URLSearchParams();

  Object.entries(params || {}).forEach(([key, value]) => {

    if (value !== undefined) {
      normalizedParams.append(key, value === null ? 'null' : value.toString())
    }
  });

  const stringifiedParams = normalizedParams.toString();

  return stringifiedParams.length > 0 ? `/tracer/trace/?${stringifiedParams}` : `/tracer/trace/`
}

export const tracerTraceList = async (params?: TracerTraceListParams, options?: RequestInit): Promise<tracerTraceListResponse> => {

  return apiMutator<tracerTraceListResponse>(getTracerTraceListUrl(params),
  {
    ...options,
    method: 'GET'


  }
);}



export type tracerTraceCreateResponse201 = {
  data: TraceApi
  status: 201
}

export type tracerTraceCreateResponseSuccess = (tracerTraceCreateResponse201) & {
  headers: Headers;
};
;

export type tracerTraceCreateResponse = (tracerTraceCreateResponseSuccess)

export const getTracerTraceCreateUrl = () => {




  return `/tracer/trace/`
}

export const tracerTraceCreate = async (traceApi: NonReadonly<TraceApi>, options?: RequestInit): Promise<tracerTraceCreateResponse> => {

  return apiMutator<tracerTraceCreateResponse>(getTracerTraceCreateUrl(),
  {
    ...options,
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...options?.headers },
    body: JSON.stringify(
      traceApi,)
  }
);}



export type tracerTraceAgentGraphResponse200 = {
  data: TracerTraceAgentGraph200
  status: 200
}

export type tracerTraceAgentGraphResponseSuccess = (tracerTraceAgentGraphResponse200) & {
  headers: Headers;
};
;

export type tracerTraceAgentGraphResponse = (tracerTraceAgentGraphResponseSuccess)

export const getTracerTraceAgentGraphUrl = (params?: TracerTraceAgentGraphParams,) => {
  const normalizedParams = new URLSearchParams();

  Object.entries(params || {}).forEach(([key, value]) => {

    if (value !== undefined) {
      normalizedParams.append(key, value === null ? 'null' : value.toString())
    }
  });

  const stringifiedParams = normalizedParams.toString();

  return stringifiedParams.length > 0 ? `/tracer/trace/agent_graph/?${stringifiedParams}` : `/tracer/trace/agent_graph/`
}

/**
 * Computes nodes (distinct span types/names) and edges (parent→child
transitions) across all traces in the given time window.
 * @summary Return the aggregate agent graph for a project.
 */
export const tracerTraceAgentGraph = async (params?: TracerTraceAgentGraphParams, options?: RequestInit): Promise<tracerTraceAgentGraphResponse> => {

  return apiMutator<tracerTraceAgentGraphResponse>(getTracerTraceAgentGraphUrl(params),
  {
    ...options,
    method: 'GET'


  }
);}



export type tracerTraceBulkCreateResponse201 = {
  data: TraceApi
  status: 201
}

export type tracerTraceBulkCreateResponseSuccess = (tracerTraceBulkCreateResponse201) & {
  headers: Headers;
};
;

export type tracerTraceBulkCreateResponse = (tracerTraceBulkCreateResponseSuccess)

export const getTracerTraceBulkCreateUrl = () => {




  return `/tracer/trace/bulk_create/`
}

export const tracerTraceBulkCreate = async (traceApi: NonReadonly<TraceApi>, options?: RequestInit): Promise<tracerTraceBulkCreateResponse> => {

  return apiMutator<tracerTraceBulkCreateResponse>(getTracerTraceBulkCreateUrl(),
  {
    ...options,
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...options?.headers },
    body: JSON.stringify(
      traceApi,)
  }
);}



export type tracerTraceCompareTracesResponse201 = {
  data: TraceApi
  status: 201
}

export type tracerTraceCompareTracesResponseSuccess = (tracerTraceCompareTracesResponse201) & {
  headers: Headers;
};
;

export type tracerTraceCompareTracesResponse = (tracerTraceCompareTracesResponseSuccess)

export const getTracerTraceCompareTracesUrl = () => {




  return `/tracer/trace/compare_traces/`
}

/**
 * Compare traces across project versions with optimized queries.
 */
export const tracerTraceCompareTraces = async (traceApi: NonReadonly<TraceApi>, options?: RequestInit): Promise<tracerTraceCompareTracesResponse> => {

  return apiMutator<tracerTraceCompareTracesResponse>(getTracerTraceCompareTracesUrl(),
  {
    ...options,
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...options?.headers },
    body: JSON.stringify(
      traceApi,)
  }
);}



export type tracerTraceGetEvalNamesResponse200 = {
  data: TracerTraceGetEvalNames200
  status: 200
}

export type tracerTraceGetEvalNamesResponseSuccess = (tracerTraceGetEvalNamesResponse200) & {
  headers: Headers;
};
;

export type tracerTraceGetEvalNamesResponse = (tracerTraceGetEvalNamesResponseSuccess)

export const getTracerTraceGetEvalNamesUrl = (params?: TracerTraceGetEvalNamesParams,) => {
  const normalizedParams = new URLSearchParams();

  Object.entries(params || {}).forEach(([key, value]) => {

    if (value !== undefined) {
      normalizedParams.append(key, value === null ? 'null' : value.toString())
    }
  });

  const stringifiedParams = normalizedParams.toString();

  return stringifiedParams.length > 0 ? `/tracer/trace/get_eval_names/?${stringifiedParams}` : `/tracer/trace/get_eval_names/`
}

/**
 * Fetch all evaluation template names.
 */
export const tracerTraceGetEvalNames = async (params?: TracerTraceGetEvalNamesParams, options?: RequestInit): Promise<tracerTraceGetEvalNamesResponse> => {

  return apiMutator<tracerTraceGetEvalNamesResponse>(getTracerTraceGetEvalNamesUrl(params),
  {
    ...options,
    method: 'GET'


  }
);}



export type tracerTraceGetGraphMethodsResponse201 = {
  data: TraceApi
  status: 201
}

export type tracerTraceGetGraphMethodsResponseSuccess = (tracerTraceGetGraphMethodsResponse201) & {
  headers: Headers;
};
;

export type tracerTraceGetGraphMethodsResponse = (tracerTraceGetGraphMethodsResponseSuccess)

export const getTracerTraceGetGraphMethodsUrl = () => {




  return `/tracer/trace/get_graph_methods/`
}

/**
 * Fetch data for the observe graph with optimized queries
 */
export const tracerTraceGetGraphMethods = async (traceApi: NonReadonly<TraceApi>, options?: RequestInit): Promise<tracerTraceGetGraphMethodsResponse> => {

  return apiMutator<tracerTraceGetGraphMethodsResponse>(getTracerTraceGetGraphMethodsUrl(),
  {
    ...options,
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...options?.headers },
    body: JSON.stringify(
      traceApi,)
  }
);}



export type tracerTraceGetPropertiesResponse200 = {
  data: TracerTraceGetProperties200
  status: 200
}

export type tracerTraceGetPropertiesResponseSuccess = (tracerTraceGetPropertiesResponse200) & {
  headers: Headers;
};
;

export type tracerTraceGetPropertiesResponse = (tracerTraceGetPropertiesResponseSuccess)

export const getTracerTraceGetPropertiesUrl = (params?: TracerTraceGetPropertiesParams,) => {
  const normalizedParams = new URLSearchParams();

  Object.entries(params || {}).forEach(([key, value]) => {

    if (value !== undefined) {
      normalizedParams.append(key, value === null ? 'null' : value.toString())
    }
  });

  const stringifiedParams = normalizedParams.toString();

  return stringifiedParams.length > 0 ? `/tracer/trace/get_properties/?${stringifiedParams}` : `/tracer/trace/get_properties/`
}

/**
 * Fetch all properties for graphing.
 */
export const tracerTraceGetProperties = async (params?: TracerTraceGetPropertiesParams, options?: RequestInit): Promise<tracerTraceGetPropertiesResponse> => {

  return apiMutator<tracerTraceGetPropertiesResponse>(getTracerTraceGetPropertiesUrl(params),
  {
    ...options,
    method: 'GET'


  }
);}



export type tracerTraceGetTraceExportDataResponse200 = {
  data: TracerTraceGetTraceExportData200
  status: 200
}

export type tracerTraceGetTraceExportDataResponseSuccess = (tracerTraceGetTraceExportDataResponse200) & {
  headers: Headers;
};
;

export type tracerTraceGetTraceExportDataResponse = (tracerTraceGetTraceExportDataResponseSuccess)

export const getTracerTraceGetTraceExportDataUrl = (params?: TracerTraceGetTraceExportDataParams,) => {
  const normalizedParams = new URLSearchParams();

  Object.entries(params || {}).forEach(([key, value]) => {

    if (value !== undefined) {
      normalizedParams.append(key, value === null ? 'null' : value.toString())
    }
  });

  const stringifiedParams = normalizedParams.toString();

  return stringifiedParams.length > 0 ? `/tracer/trace/get_trace_export_data/?${stringifiedParams}` : `/tracer/trace/get_trace_export_data/`
}

/**
 * Export traces filtered by project ID with optimized queries.
Auto-detects voice/conversation projects and exports voice-specific fields.
 */
export const tracerTraceGetTraceExportData = async (params?: TracerTraceGetTraceExportDataParams, options?: RequestInit): Promise<tracerTraceGetTraceExportDataResponse> => {

  return apiMutator<tracerTraceGetTraceExportDataResponse>(getTracerTraceGetTraceExportDataUrl(params),
  {
    ...options,
    method: 'GET'


  }
);}



export type tracerTraceGetTraceIdByIndexResponse200 = {
  data: TracerTraceGetTraceIdByIndex200
  status: 200
}

export type tracerTraceGetTraceIdByIndexResponseSuccess = (tracerTraceGetTraceIdByIndexResponse200) & {
  headers: Headers;
};
;

export type tracerTraceGetTraceIdByIndexResponse = (tracerTraceGetTraceIdByIndexResponseSuccess)

export const getTracerTraceGetTraceIdByIndexUrl = (params?: TracerTraceGetTraceIdByIndexParams,) => {
  const normalizedParams = new URLSearchParams();

  Object.entries(params || {}).forEach(([key, value]) => {

    if (value !== undefined) {
      normalizedParams.append(key, value === null ? 'null' : value.toString())
    }
  });

  const stringifiedParams = normalizedParams.toString();

  return stringifiedParams.length > 0 ? `/tracer/trace/get_trace_id_by_index/?${stringifiedParams}` : `/tracer/trace/get_trace_id_by_index/`
}

/**
 * Get the previous and next trace id by index using efficient database queries.
 */
export const tracerTraceGetTraceIdByIndex = async (params?: TracerTraceGetTraceIdByIndexParams, options?: RequestInit): Promise<tracerTraceGetTraceIdByIndexResponse> => {

  return apiMutator<tracerTraceGetTraceIdByIndexResponse>(getTracerTraceGetTraceIdByIndexUrl(params),
  {
    ...options,
    method: 'GET'


  }
);}



export type tracerTraceGetTraceIdByIndexObserveResponse200 = {
  data: TracerTraceGetTraceIdByIndexObserve200
  status: 200
}

export type tracerTraceGetTraceIdByIndexObserveResponseSuccess = (tracerTraceGetTraceIdByIndexObserveResponse200) & {
  headers: Headers;
};
;

export type tracerTraceGetTraceIdByIndexObserveResponse = (tracerTraceGetTraceIdByIndexObserveResponseSuccess)

export const getTracerTraceGetTraceIdByIndexObserveUrl = (params?: TracerTraceGetTraceIdByIndexObserveParams,) => {
  const normalizedParams = new URLSearchParams();

  Object.entries(params || {}).forEach(([key, value]) => {

    if (value !== undefined) {
      normalizedParams.append(key, value === null ? 'null' : value.toString())
    }
  });

  const stringifiedParams = normalizedParams.toString();

  return stringifiedParams.length > 0 ? `/tracer/trace/get_trace_id_by_index_observe/?${stringifiedParams}` : `/tracer/trace/get_trace_id_by_index_observe/`
}

/**
 * Get the previous and next trace id by index.
 */
export const tracerTraceGetTraceIdByIndexObserve = async (params?: TracerTraceGetTraceIdByIndexObserveParams, options?: RequestInit): Promise<tracerTraceGetTraceIdByIndexObserveResponse> => {

  return apiMutator<tracerTraceGetTraceIdByIndexObserveResponse>(getTracerTraceGetTraceIdByIndexObserveUrl(params),
  {
    ...options,
    method: 'GET'


  }
);}



export type tracerTraceListTracesResponse200 = {
  data: TracerTraceListTraces200
  status: 200
}

export type tracerTraceListTracesResponseSuccess = (tracerTraceListTracesResponse200) & {
  headers: Headers;
};
;

export type tracerTraceListTracesResponse = (tracerTraceListTracesResponseSuccess)

export const getTracerTraceListTracesUrl = (params?: TracerTraceListTracesParams,) => {
  const normalizedParams = new URLSearchParams();

  Object.entries(params || {}).forEach(([key, value]) => {

    if (value !== undefined) {
      normalizedParams.append(key, value === null ? 'null' : value.toString())
    }
  });

  const stringifiedParams = normalizedParams.toString();

  return stringifiedParams.length > 0 ? `/tracer/trace/list_traces/?${stringifiedParams}` : `/tracer/trace/list_traces/`
}

/**
 * List traces filtered by project ID and project version ID with optimized queries.
 */
export const tracerTraceListTraces = async (params?: TracerTraceListTracesParams, options?: RequestInit): Promise<tracerTraceListTracesResponse> => {

  return apiMutator<tracerTraceListTracesResponse>(getTracerTraceListTracesUrl(params),
  {
    ...options,
    method: 'GET'


  }
);}



export type tracerTraceListTracesOfSessionResponse200 = {
  data: TracerTraceListTracesOfSession200
  status: 200
}

export type tracerTraceListTracesOfSessionResponseSuccess = (tracerTraceListTracesOfSessionResponse200) & {
  headers: Headers;
};
;

export type tracerTraceListTracesOfSessionResponse = (tracerTraceListTracesOfSessionResponseSuccess)

export const getTracerTraceListTracesOfSessionUrl = (params?: TracerTraceListTracesOfSessionParams,) => {
  const normalizedParams = new URLSearchParams();

  Object.entries(params || {}).forEach(([key, value]) => {

    if (value !== undefined) {
      normalizedParams.append(key, value === null ? 'null' : value.toString())
    }
  });

  const stringifiedParams = normalizedParams.toString();

  return stringifiedParams.length > 0 ? `/tracer/trace/list_traces_of_session/?${stringifiedParams}` : `/tracer/trace/list_traces_of_session/`
}

/**
 * List traces filtered by project ID with optimized queries.
 */
export const tracerTraceListTracesOfSession = async (params?: TracerTraceListTracesOfSessionParams, options?: RequestInit): Promise<tracerTraceListTracesOfSessionResponse> => {

  return apiMutator<tracerTraceListTracesOfSessionResponse>(getTracerTraceListTracesOfSessionUrl(params),
  {
    ...options,
    method: 'GET'


  }
);}



export type tracerTraceListVoiceCallsResponse200 = {
  data: TracerTraceListVoiceCalls200
  status: 200
}

export type tracerTraceListVoiceCallsResponseSuccess = (tracerTraceListVoiceCallsResponse200) & {
  headers: Headers;
};
;

export type tracerTraceListVoiceCallsResponse = (tracerTraceListVoiceCallsResponseSuccess)

export const getTracerTraceListVoiceCallsUrl = (params?: TracerTraceListVoiceCallsParams,) => {
  const normalizedParams = new URLSearchParams();

  Object.entries(params || {}).forEach(([key, value]) => {

    if (value !== undefined) {
      normalizedParams.append(key, value === null ? 'null' : value.toString())
    }
  });

  const stringifiedParams = normalizedParams.toString();

  return stringifiedParams.length > 0 ? `/tracer/trace/list_voice_calls/?${stringifiedParams}` : `/tracer/trace/list_voice_calls/`
}

/**
 * List voice/conversation traces for a project in an optimized way and
return a response similar to the provided call object schema.

Query params:
- project_id (required)
- page (1-based, optional, default 1)
- page_size (optional, default 30)
 */
export const tracerTraceListVoiceCalls = async (params?: TracerTraceListVoiceCallsParams, options?: RequestInit): Promise<tracerTraceListVoiceCallsResponse> => {

  return apiMutator<tracerTraceListVoiceCallsResponse>(getTracerTraceListVoiceCallsUrl(params),
  {
    ...options,
    method: 'GET'


  }
);}



export type tracerTraceVoiceCallDetailResponse200 = {
  data: TracerTraceVoiceCallDetail200
  status: 200
}

export type tracerTraceVoiceCallDetailResponseSuccess = (tracerTraceVoiceCallDetailResponse200) & {
  headers: Headers;
};
;

export type tracerTraceVoiceCallDetailResponse = (tracerTraceVoiceCallDetailResponseSuccess)

export const getTracerTraceVoiceCallDetailUrl = (params?: TracerTraceVoiceCallDetailParams,) => {
  const normalizedParams = new URLSearchParams();

  Object.entries(params || {}).forEach(([key, value]) => {

    if (value !== undefined) {
      normalizedParams.append(key, value === null ? 'null' : value.toString())
    }
  });

  const stringifiedParams = normalizedParams.toString();

  return stringifiedParams.length > 0 ? `/tracer/trace/voice_call_detail/?${stringifiedParams}` : `/tracer/trace/voice_call_detail/`
}

/**
 * Query params:
- trace_id (required) — UUID of the voice call trace.
 * @summary Return the heavy / detail-only fields for a single voice call.
 */
export const tracerTraceVoiceCallDetail = async (params?: TracerTraceVoiceCallDetailParams, options?: RequestInit): Promise<tracerTraceVoiceCallDetailResponse> => {

  return apiMutator<tracerTraceVoiceCallDetailResponse>(getTracerTraceVoiceCallDetailUrl(params),
  {
    ...options,
    method: 'GET'


  }
);}



export type tracerTraceReadResponse200 = {
  data: TraceApi
  status: 200
}

export type tracerTraceReadResponseSuccess = (tracerTraceReadResponse200) & {
  headers: Headers;
};
;

export type tracerTraceReadResponse = (tracerTraceReadResponseSuccess)

export const getTracerTraceReadUrl = (id: string,) => {




  return `/tracer/trace/${id}/`
}

/**
 * Retrieve a trace by its ID.
 */
export const tracerTraceRead = async (id: string, options?: RequestInit): Promise<tracerTraceReadResponse> => {

  return apiMutator<tracerTraceReadResponse>(getTracerTraceReadUrl(id),
  {
    ...options,
    method: 'GET'


  }
);}



export type tracerTraceUpdateResponse200 = {
  data: TraceApi
  status: 200
}

export type tracerTraceUpdateResponseSuccess = (tracerTraceUpdateResponse200) & {
  headers: Headers;
};
;

export type tracerTraceUpdateResponse = (tracerTraceUpdateResponseSuccess)

export const getTracerTraceUpdateUrl = (id: string,) => {




  return `/tracer/trace/${id}/`
}

export const tracerTraceUpdate = async (id: string,
    traceApi: NonReadonly<TraceApi>, options?: RequestInit): Promise<tracerTraceUpdateResponse> => {

  return apiMutator<tracerTraceUpdateResponse>(getTracerTraceUpdateUrl(id),
  {
    ...options,
    method: 'PUT',
    headers: { 'Content-Type': 'application/json', ...options?.headers },
    body: JSON.stringify(
      traceApi,)
  }
);}



export type tracerTracePartialUpdateResponse200 = {
  data: TraceApi
  status: 200
}

export type tracerTracePartialUpdateResponseSuccess = (tracerTracePartialUpdateResponse200) & {
  headers: Headers;
};
;

export type tracerTracePartialUpdateResponse = (tracerTracePartialUpdateResponseSuccess)

export const getTracerTracePartialUpdateUrl = (id: string,) => {




  return `/tracer/trace/${id}/`
}

export const tracerTracePartialUpdate = async (id: string,
    traceApi: NonReadonly<TraceApi>, options?: RequestInit): Promise<tracerTracePartialUpdateResponse> => {

  return apiMutator<tracerTracePartialUpdateResponse>(getTracerTracePartialUpdateUrl(id),
  {
    ...options,
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json', ...options?.headers },
    body: JSON.stringify(
      traceApi,)
  }
);}



export type tracerTraceDeleteResponse204 = {
  data: void
  status: 204
}

export type tracerTraceDeleteResponseSuccess = (tracerTraceDeleteResponse204) & {
  headers: Headers;
};
;

export type tracerTraceDeleteResponse = (tracerTraceDeleteResponseSuccess)

export const getTracerTraceDeleteUrl = (id: string,) => {




  return `/tracer/trace/${id}/`
}

export const tracerTraceDelete = async (id: string, options?: RequestInit): Promise<tracerTraceDeleteResponse> => {

  return apiMutator<tracerTraceDeleteResponse>(getTracerTraceDeleteUrl(id),
  {
    ...options,
    method: 'DELETE'


  }
);}



export type tracerTraceUpdateTagsResponse200 = {
  data: TraceTagsUpdateApi
  status: 200
}

export type tracerTraceUpdateTagsResponseSuccess = (tracerTraceUpdateTagsResponse200) & {
  headers: Headers;
};
;

export type tracerTraceUpdateTagsResponse = (tracerTraceUpdateTagsResponseSuccess)

export const getTracerTraceUpdateTagsUrl = (id: string,) => {




  return `/tracer/trace/${id}/tags/`
}

/**
 * Update tags for a trace.
 */
export const tracerTraceUpdateTags = async (id: string,
    traceTagsUpdateApi: TraceTagsUpdateApi, options?: RequestInit): Promise<tracerTraceUpdateTagsResponse> => {

  return apiMutator<tracerTraceUpdateTagsResponse>(getTracerTraceUpdateTagsUrl(id),
  {
    ...options,
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json', ...options?.headers },
    body: JSON.stringify(
      traceTagsUpdateApi,)
  }
);}



export type tracerUsersListResponse200 = {
  data: UsersResponseApi
  status: 200
}

export type tracerUsersListResponse400 = {
  data: ApiErrorResponseApi
  status: 400
}

export type tracerUsersListResponse500 = {
  data: ApiErrorResponseApi
  status: 500
}

export type tracerUsersListResponseSuccess = (tracerUsersListResponse200) & {
  headers: Headers;
};
export type tracerUsersListResponseError = (tracerUsersListResponse400 | tracerUsersListResponse500) & {
  headers: Headers;
};

export type tracerUsersListResponse = (tracerUsersListResponseSuccess | tracerUsersListResponseError)

export const getTracerUsersListUrl = (params?: TracerUsersListParams,) => {
  const normalizedParams = new URLSearchParams();

  Object.entries(params || {}).forEach(([key, value]) => {

    if (value !== undefined) {
      normalizedParams.append(key, value === null ? 'null' : value.toString())
    }
  });

  const stringifiedParams = normalizedParams.toString();

  return stringifiedParams.length > 0 ? `/tracer/users/?${stringifiedParams}` : `/tracer/users/`
}

/**
 * List traces filtered by project ID with optimized queries.
 */
export const tracerUsersList = async (params?: TracerUsersListParams, options?: RequestInit): Promise<tracerUsersListResponse> => {

  return apiMutator<tracerUsersListResponse>(getTracerUsersListUrl(params),
  {
    ...options,
    method: 'GET'


  }
);}



export type tracerUsersGetCodeExampleListResponse200 = {
  data: UserCodeExampleResponseApi
  status: 200
}

export type tracerUsersGetCodeExampleListResponse400 = {
  data: ApiErrorResponseApi
  status: 400
}

export type tracerUsersGetCodeExampleListResponse500 = {
  data: ApiErrorResponseApi
  status: 500
}

export type tracerUsersGetCodeExampleListResponseSuccess = (tracerUsersGetCodeExampleListResponse200) & {
  headers: Headers;
};
export type tracerUsersGetCodeExampleListResponseError = (tracerUsersGetCodeExampleListResponse400 | tracerUsersGetCodeExampleListResponse500) & {
  headers: Headers;
};

export type tracerUsersGetCodeExampleListResponse = (tracerUsersGetCodeExampleListResponseSuccess | tracerUsersGetCodeExampleListResponseError)

export const getTracerUsersGetCodeExampleListUrl = () => {




  return `/tracer/users/get_code_example/`
}

export const tracerUsersGetCodeExampleList = async ( options?: RequestInit): Promise<tracerUsersGetCodeExampleListResponse> => {

  return apiMutator<tracerUsersGetCodeExampleListResponse>(getTracerUsersGetCodeExampleListUrl(),
  {
    ...options,
    method: 'GET'


  }
);}
