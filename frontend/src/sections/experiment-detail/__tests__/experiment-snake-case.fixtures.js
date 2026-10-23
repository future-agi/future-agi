// Snake_case payloads exactly as the backend serializes them (DRF JSONRenderer,
// no camelizing renderer: futureagi/tfc/settings/settings.py:552). Every
// regression test for the experiment snake_case hotfix reads from here, and the
// screenshot harness answers the intercepted API calls with the same objects.
// Sources (paths relative to futureagi/, line numbers as of origin/main 018f24a8f3):
//   detail   GET experiments/v2/{id}/         serializers/experiments.py:681-710, 771-787, 796-879
//   rows     GET experiments/v2/{id}/rows/    views/experiments.py:1187-1250, 1450-1482, 1559-1563, 1585-1603
//   stats    GET experiments/v2/{id}/stats/   views/experiments.py:2404-2413, 2505-2513, 2636-2686
//   names    GET experiments/v2/suggest-name/ views/experiments.py:6324
//            GET experiments/v2/validate-name/ views/experiments.py:6360-6363
//   agents   GET agent-playground/graphs/     agent_playground/serializers/graph.py:29-62
//            GET agent-playground/graphs/{id}/versions/ agent_playground/serializers/graph_version.py:54-73

export const EXPERIMENT_ID = "6f1c2a5e-8d3b-4c71-9a2e-1b5d7c9e0a11";
export const DATASET_ID = "2b7e4f10-3c5d-4e6f-8a9b-0c1d2e3f4a51";
export const SNAPSHOT_DATASET_ID = "9a8b7c6d-5e4f-4a3b-8c2d-1e0f9a8b7c61";
export const BASE_COLUMN_ID = "3c4d5e6f-7a8b-4c9d-8e0f-1a2b3c4d5e71";
export const BASE_COLUMN_SOURCE_ID = "1d2e3f4a-5b6c-4d7e-8f9a-0b1c2d3e4f51";
export const INPUT_COLUMN_ID = "4d5e6f7a-8b9c-4d0e-9f1a-2b3c4d5e6f81";
export const PROMPT_VERSION_ID = "0e1f2a3b-4c5d-4e6f-9a0b-1c2d3e4f5a61";
export const EDT_A_ID = "5e6f7a8b-9c0d-4e1f-8a2b-3c4d5e6f7a91";
export const EDT_B_ID = "6f7a8b9c-0d1e-4f2a-9b3c-4d5e6f7a8ba1";
export const OUTPUT_A_COLUMN_ID = "7a8b9c0d-1e2f-4a3b-8c4d-5e6f7a8b9cb1";
export const OUTPUT_B_COLUMN_ID = "8b9c0d1e-2f3a-4b4c-9d5e-6f7a8b9c0dc1";
export const EVAL_METRIC_ID = "9c0d1e2f-3a4b-4c5d-8e6f-7a8b9c0d1ed1";
export const EVAL_A_COLUMN_ID = "0d1e2f3a-4b5c-4d6e-9f7a-8b9c0d1e2fe1";
export const EVAL_B_COLUMN_ID = "1e2f3a4b-5c6d-4e7f-8a8b-9c0d1e2f3af1";
export const AGENT_EDT_ID = "2f3a4b5c-6d7e-4f8a-9b9c-0d1e2f3a4b02";
export const AGENT_GRAPH_ID = "3a4b5c6d-7e8f-4a9b-8c0d-1e2f3a4b5c12";
export const AGENT_GRAPH_VERSION_ID = "4b5c6d7e-8f9a-4b0c-9d1e-2f3a4b5c6d22";
export const AGENT_NODE_PLAN_COLUMN_ID = "5c6d7e8f-9a0b-4c1d-8e2f-3a4b5c6d7e32";
export const AGENT_NODE_ANSWER_COLUMN_ID =
  "6d7e8f9a-0b1c-4d2e-9f3a-4b5c6d7e8f42";
export const ROW_1_ID = "7e8f9a0b-1c2d-4e3f-8a4b-5c6d7e8f9a52";
export const ROW_2_ID = "8f9a0b1c-2d3e-4f4a-9b5c-6d7e8f9a0b62";

const EDT_A_NAME = "support-reply-gpt-4o";
const EDT_B_NAME = "support-reply-claude-sonnet-4";
const EVAL_NAME = "answer_relevance";

const promptConfig = (overrides) => ({
  // ExperimentPromptConfigOutputSerializer (serializers/experiments.py:681-710)
  id: overrides.id,
  name: overrides.name,
  prompt_id: "a1b2c3d4-e5f6-4a7b-8c9d-0e1f2a3b4c51",
  prompt_name: "support-reply",
  prompt_version: PROMPT_VERSION_ID,
  prompt_version_name: "v3",
  model: overrides.model,
  model_display_name: overrides.model,
  model_config: {},
  model_params: { temperature: 0.2, providers: "openai", logo_url: null },
  configuration: { toolChoice: "auto", tools: [] },
  output_format: overrides.output_format ?? "string",
  messages: null,
  voice_input_column_id: null,
  order: overrides.order,
});

// GET /model-hub/experiments/v2/{id}/ -> result (ExperimentDetailV2Serializer)
export const experimentDetailLlm = {
  id: EXPERIMENT_ID,
  name: "support-replies-oct",
  dataset_id: DATASET_ID,
  column_id: BASE_COLUMN_ID,
  experiment_type: "llm",
  status: "Completed",
  snapshot_dataset_id: SNAPSHOT_DATASET_ID,
  prompt_configs: [
    promptConfig({ id: EDT_A_ID, name: EDT_A_NAME, model: "gpt-4o", order: 0 }),
    promptConfig({
      id: EDT_B_ID,
      name: EDT_B_NAME,
      model: "claude-sonnet-4",
      order: 1,
    }),
  ],
  agent_configs: [],
  user_eval_metrics: [
    {
      id: EVAL_METRIC_ID,
      template_id: "b2c3d4e5-f6a7-4b8c-9d0e-1f2a3b4c5d61",
      template_details: {
        id: "b2c3d4e5-f6a7-4b8c-9d0e-1f2a3b4c5d61",
        name: EVAL_NAME,
        description: "Is the reply relevant to the ticket?",
        config: { output: "score" },
        criteria: "",
        type: "futureagi_built",
      },
      name: EVAL_NAME,
      config: {},
      model: "turing_large",
      error_localizer: false,
      kb_id: null,
    },
  ],
  created_at: "2026-10-14T09:12:00Z",
};

// Same experiment shape with one agent variant (ExperimentAgentConfigOutputSerializer,
// serializers/experiments.py:771-787) and no prompt variants.
export const experimentDetailAgent = {
  ...experimentDetailLlm,
  name: "support-agent-oct",
  prompt_configs: [],
  agent_configs: [
    {
      id: AGENT_EDT_ID,
      name: "support-graph v3",
      agent_id: AGENT_GRAPH_ID,
      agent_name: "support-graph",
      agent_version: AGENT_GRAPH_VERSION_ID,
      agent_version_name: 3,
      order: 0,
    },
  ],
};

// Dataset/snapshot column entry (views/experiments.py:1187-1250)
const datasetColumn = ({ id, name, sourceId, isBase }) => ({
  id,
  name,
  origin_type: "OTHERS",
  data_type: "text",
  status: "Completed",
  group: {
    id: isBase ? id : sourceId,
    name,
    data_type: "text",
    origin: "Dataset",
  },
  average_score: null,
  dataset_id: SNAPSHOT_DATASET_ID,
  choices_map: {},
  is_base_column: Boolean(isBase),
  output_type: null,
  eval_template_id: null,
});

// Experiment output column entry (views/experiments.py:1450-1482)
const outputColumn = ({ id, name, edtId, groupId, groupName, agent }) => ({
  id,
  name,
  origin_type: "experiment",
  data_type: "text",
  status: "Completed",
  group: {
    id: groupId,
    name: groupName,
    data_type: "text",
    origin: "Experiment",
  },
  average_score: null,
  dataset_id: edtId,
  choices_map: {},
  source_id: edtId,
  ...(agent ? { is_agent: true, is_final: agent.isFinal } : {}),
});

// Per-variant eval column entry (views/experiments.py:1378-1404, 1450-1468)
const evalColumn = ({ id, name, edtId }) => ({
  id,
  name,
  origin_type: "evaluation",
  data_type: "float",
  status: "Completed",
  group: {
    id: EVAL_METRIC_ID,
    name: EVAL_NAME,
    data_type: "float",
    origin: "Evaluation",
  },
  average_score: null,
  dataset_id: edtId,
  choices_map: {},
  source_id: edtId,
});

export const llmColumnConfig = [
  datasetColumn({
    id: BASE_COLUMN_ID,
    name: "expected_reply",
    isBase: true,
  }),
  datasetColumn({
    id: INPUT_COLUMN_ID,
    name: "ticket",
    sourceId: BASE_COLUMN_SOURCE_ID,
  }),
  outputColumn({
    id: OUTPUT_A_COLUMN_ID,
    name: EDT_A_NAME,
    edtId: EDT_A_ID,
    groupId: PROMPT_VERSION_ID,
    groupName: "support-reply_v3",
  }),
  outputColumn({
    id: OUTPUT_B_COLUMN_ID,
    name: EDT_B_NAME,
    edtId: EDT_B_ID,
    groupId: PROMPT_VERSION_ID,
    groupName: "support-reply_v3",
  }),
  evalColumn({
    id: EVAL_A_COLUMN_ID,
    name: `${EVAL_NAME}-${EDT_A_NAME}`,
    edtId: EDT_A_ID,
  }),
  evalColumn({
    id: EVAL_B_COLUMN_ID,
    name: `${EVAL_NAME}-${EDT_B_NAME}`,
    edtId: EDT_B_ID,
  }),
];

export const agentColumnConfig = [
  datasetColumn({
    id: BASE_COLUMN_ID,
    name: "expected_reply",
    isBase: true,
  }),
  outputColumn({
    id: AGENT_NODE_PLAN_COLUMN_ID,
    name: "support-graph-v3-plan",
    edtId: AGENT_EDT_ID,
    groupId: AGENT_EDT_ID,
    groupName: "support-graph v3",
    agent: { isFinal: false },
  }),
  outputColumn({
    id: AGENT_NODE_ANSWER_COLUMN_ID,
    name: "support-graph-v3-answer",
    edtId: AGENT_EDT_ID,
    groupId: AGENT_EDT_ID,
    groupName: "support-graph v3",
    agent: { isFinal: true },
  }),
];

// GET rows/?column_config_only=true (views/experiments.py:1559-1563)
export const columnConfigOnlyResponse = (columnConfig, outputFormat) => ({
  status: true,
  result: {
    column_config: columnConfig,
    output_format: outputFormat,
    status: "Completed",
  },
});

const cell = (value, extra = {}) => ({
  cell_value: value,
  status: "pass",
  metadata: {
    response_time_ms: 1234.6,
    token_count: 412,
    cost: { total_cost: 0.0021 },
    cell_metadata: {},
    reason: null,
  },
  ...extra,
});

// GET rows/?current_page_index=0&page_size=10 (views/experiments.py:1585-1603)
export const rowsPageResponse = (columnConfig) => ({
  status: true,
  result: {
    column_config: columnConfig,
    table: [
      {
        row_id: ROW_1_ID,
        [BASE_COLUMN_ID]: cell("Your refund was issued on Oct 2."),
        [INPUT_COLUMN_ID]: cell("Where is my refund?"),
        [OUTPUT_A_COLUMN_ID]: cell("Your refund was issued on October 2."),
        [OUTPUT_B_COLUMN_ID]: cell("We issued the refund on Oct 2."),
        [EVAL_A_COLUMN_ID]: cell(0.92),
        [EVAL_B_COLUMN_ID]: cell(0.81),
      },
      {
        row_id: ROW_2_ID,
        [BASE_COLUMN_ID]: cell("You can change the plan under Billing."),
        [INPUT_COLUMN_ID]: cell("How do I change my plan?"),
        [OUTPUT_A_COLUMN_ID]: cell("Open Billing and pick a new plan."),
        [OUTPUT_B_COLUMN_ID]: cell("Go to Billing to change the plan."),
        [EVAL_A_COLUMN_ID]: cell(0.88),
        [EVAL_B_COLUMN_ID]: cell(0.9),
      },
    ],
    metadata: {
      total_rows: 2,
      dataset: SNAPSHOT_DATASET_ID,
      dataset_name: "support-tickets (snapshot)",
      column: BASE_COLUMN_ID,
      total_pages: 1,
      description: {},
    },
    output_format: "string",
    status: "Completed",
  },
});

// GET /model-hub/experiments/v2/{id}/stats/ (views/experiments.py:2404-2686)
export const statsResponse = ({ winnerChosen = true } = {}) => ({
  status: true,
  result: {
    column_config: [
      {
        status: "Completed",
        name: EVAL_NAME,
        reverse_output: false,
        output_type: "score",
        eval_template_id: "b2c3d4e5-f6a7-4b8c-9d0e-1f2a3b4c5d61",
      },
    ],
    table_data: [
      {
        dataset_id: EDT_A_ID,
        experiment_dataset_name: EDT_A_NAME,
        average_response_time: 1234.56,
        prompt_token: 18420,
        completion_token: 6210,
        total_token: 24630,
        [EVAL_NAME]: 90,
        rank: winnerChosen ? 1 : null,
      },
      {
        dataset_id: EDT_B_ID,
        experiment_dataset_name: EDT_B_NAME,
        average_response_time: 987.25,
        prompt_token: 18420,
        completion_token: 5880,
        total_token: 24300,
        [EVAL_NAME]: 85,
        rank: winnerChosen ? 2 : null,
      },
    ],
    metadata: { is_winner_chosen: winnerChosen },
  },
});

// GET /model-hub/experiments/v2/suggest-name/{dataset_id}/ (views/experiments.py:6324)
export const suggestNameResponse = {
  status: true,
  result: { suggested_name: "support-tickets-2026-10-22-v2" },
};

// GET /model-hub/experiments/v2/validate-name/ (views/experiments.py:6360-6363)
export const validateNameTakenResponse = {
  status: true,
  result: { is_valid: false, message: "Experiment name already exists" },
};

// POST /model-hub/experiments/v2/row-diff/ -> result (views/experiments.py:1874-1905)
export const rowDiffResponse = {
  status: true,
  result: {
    [ROW_1_ID]: {
      [OUTPUT_A_COLUMN_ID]: {
        cell_value: "Your refund was issued on October 2.",
        // model_hub/utils/utils.py:566-606 get_diff: one {text, status} per word
        cell_diff_value: [
          { text: "Your", status: "default" },
          { text: "refund", status: "default" },
          { text: "was", status: "default" },
          { text: "issued", status: "default" },
          { text: "on", status: "default" },
          { text: "Oct", status: "removed" },
          { text: "October", status: "added" },
          { text: "2.", status: "default" },
        ],
        status: "pass",
        value_infos: {},
      },
    },
  },
};

// GET /agent-playground/graphs/ -> result (agent_playground/views/graph.py:165-170).
// Each graph is GraphListSerializer (agent_playground/serializers/graph.py:29-62):
// the latest version id is `active_version_id` (:38-40, annotated by
// agent_playground/utils/graph.py:85-102), never `activeVersionId`.
export const AGENT_GRAPH_PREVIOUS_VERSION_ID =
  "5c6d7e8f-9a0b-4c1d-8e2f-3a4b5c6d7e99";
export const graphListResponse = {
  status: true,
  result: {
    graphs: [
      {
        id: AGENT_GRAPH_ID,
        name: "support-triage-agent",
        description: "Routes tickets and drafts replies",
        is_template: false,
        created_at: "2026-10-01T09:00:00Z",
        updated_at: "2026-10-20T16:30:00Z",
        created_by: {
          id: "0a1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c01",
          name: "Dana Ortiz",
          email: "dana@example.com",
        },
        collaborators: [],
        active_version_id: AGENT_GRAPH_VERSION_ID,
        active_version_number: 3,
        node_count: 2,
      },
    ],
    // common/utils/pagination.py:29-36
    metadata: {
      total_count: 1,
      page_number: 1,
      page_size: 20,
      total_pages: 1,
      next_page: null,
      previous_page: null,
    },
  },
};

// GET /agent-playground/graphs/{id}/versions/ -> result (agent_playground/views/graph.py:384-414),
// ordered by -version_number; GraphVersionListSerializer
// (agent_playground/serializers/graph_version.py:54-73).
export const graphVersionsResponse = {
  status: true,
  result: {
    versions: [
      {
        id: AGENT_GRAPH_VERSION_ID,
        version_number: 3,
        status: "active",
        tags: [],
        commit_message: "Tighten the refund policy branch",
        created_at: "2026-10-20T16:30:00Z",
        global_variables: [],
      },
      {
        id: AGENT_GRAPH_PREVIOUS_VERSION_ID,
        version_number: 2,
        status: "inactive",
        tags: [],
        commit_message: null,
        created_at: "2026-10-10T11:00:00Z",
        global_variables: [],
      },
    ],
    metadata: {
      total_count: 2,
      page_number: 1,
      page_size: 10,
      total_pages: 1,
      next_page: null,
      previous_page: null,
    },
  },
};
