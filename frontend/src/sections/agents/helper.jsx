import React from "react";
import SvgColor from "src/components/svg-color";
import { z } from "zod";
import CallLogsCellRenderer from "./CallLogs/CallLogsCellRenderer";
import VoiceCostCell from "./CallLogs/VoiceCostCell";
import VoiceLatencyCell from "./CallLogs/VoiceLatencyCell";
import VoiceTokenCell from "./CallLogs/VoiceTokenCell";
import TalkRatioCell from "./CallLogs/TalkRatioCell";
import EvalCellRenderer from "../test-detail/CellRenderers/EvalCellRenderer";
import CallLogsHeaderCellRenderer from "./CallLogs/CallLogsHeaderCellRenderer";
import { useQuery } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { Box, Skeleton } from "@mui/material";
import { AGENT_TYPES, isLiveKitProvider } from "./constants";
import AnnotationHeaderCellRenderer from "./CallLogs/AnnotationHeaderCellRenderer";
import headerComponentLabels from "./headerComponetLabels";
import NewAnnotationCellRenderer from "./NewAnnotationCellRenderer";

export const agentDefinitionSections = [
  {
    id: "basic-info",
    title: "Basic Info",
  },
  {
    id: "configuration ",
    title: "Configuration",
  },
  {
    id: "behavior-config",
    title: "Behaviour",
  },
];

export const stepFields = [
  ["agentType", "agentName", "languages"],
  [
    "provider",
    "assistantId",
    "apiEndpoint",
    "authenticationMethod",
    "apiKey",
    "countryCode",
    "contactNumber",
    "observabilityEnabled",
    "model",
  ],
  ["description", "knowledgeBase", "inbound", "commitMessage"],
];

export const emptyAgentSteps = [
  {
    id: "agent-definition",
    title: "Agent Definition",
    subtitle:
      "Set up and manage AI agent configuration for testing and communication",
    icon: "/assets/icons/navbar/ic_project.svg",
  },
  {
    id: "agent-scenarios",
    title: "Agent Scenarios",
    subtitle: "Create and customize test scenarios for your AI agents",
    icon: "/assets/icons/navbar/ic_sessions.svg",
  },
  {
    id: "tests-and-observability",
    title: "Tests and Observability",
    subtitle: "Monitor, test, and analyze your AI agent's performance",
    icon: "/assets/icons/navbar/ic_run.svg",
  },
];

// New schema including all fields in the accordions
export const createAgentDefinitionSchema = (options) => {
  const keysRequired = options?.keysRequired || false;
  return z
    .object({
      // Basic Information
      agentType: z.string().min(1, "Agent type is required"),
      agentName: z.string().min(1, "Agent name is required"),
      languages: z
        .array(z.string())
        .min(1, "At least one language is required"),

      // Configuration
      provider: z.string().optional(),
      assistantId: keysRequired
        ? z.string().min(1, "Assistant ID is required")
        : z.string().optional(),
      // apiEndpoint: z.string().optional(),
      authenticationMethod: z.string().optional(),
      apiKey: keysRequired
        ? z.string().min(1, "API key is required")
        : z.string().optional(),
      observabilityEnabled: z.boolean().default(false),
      username: z.string().optional(),
      password: z.string().optional(),
      token: z.string().optional(),
      headers: z
        .array(
          z.object({
            key: z.string(),
            value: z.string(),
          }),
        )
        .optional(),

      // Behaviour
      description: z.string().min(1, "Description is required"),
      knowledgeBase: z.string().optional(),
      countryCode: z.string().optional(),
      contactNumber: z.string().optional(),
      inbound: z.boolean(),
      commitMessage: z.string().min(1, "Commit message is required"),
      model: z.string().optional(),
      modelDetails: z.any().optional().nullable(),

      // LiveKit fields
      livekitUrl: z.string().optional(),
      livekitApiKey: z.string().optional(),
      livekitApiSecret: z.string().optional(),
      livekitAgentName: z.string().optional(),
      livekitConfigJson: z.any().optional().nullable(),
      // NOTE: max is not enforced here — the backend caps via
      // DEFAULT_ORG_LIMIT exposed on /accounts/user-info/. The UI reads the
      // value from `useAuthContext().orgLimit` and sets `inputProps.max` on
      // the TextField. The server-side IntegerField validator is the
      // authoritative cap; zod only guards the lower bound.
      livekitMaxConcurrency: z.coerce
        .number()
        .min(1, "Must be at least 1")
        .optional()
        .nullable(),
    })
    .superRefine(async (data, ctx) => {
      if (
        data.agentType === AGENT_TYPES.VOICE &&
        !isLiveKitProvider(data.provider)
      ) {
        // Phone number is optional when API key + assistant ID are provided (web bridge)
        // const hasWebBridgeCreds =
        //   data.apiKey?.trim() && data.assistantId?.trim();
        const hasCountryCode = !!data.countryCode?.trim();
        const hasContactNumber = !!data.contactNumber?.trim();
        // if (!hasWebBridgeCreds) {
          if (!hasCountryCode) {
            ctx.addIssue({
              path: ["countryCode"],
              message: "Country code is required",
              code: z.ZodIssueCode.custom,
            });
          }
          if (!hasContactNumber) {
            ctx.addIssue({
              path: ["contactNumber"],
              message: "Contact number is required",
              code: z.ZodIssueCode.custom,
            });
          }
        // } else {
          // Both are optional, but if one is provided the other is required
          if (hasContactNumber && !hasCountryCode) {
            ctx.addIssue({
              path: ["countryCode"],
              message: "Country code is required when contact number is provided",
              code: z.ZodIssueCode.custom,
            });
          }
          if (hasCountryCode && !hasContactNumber) {
            ctx.addIssue({
              path: ["contactNumber"],
              message: "Contact number is required when country code is provided",
              code: z.ZodIssueCode.custom,
            });
          }
        // }
        if (hasContactNumber) {
          // Validate contact number format only if it's provided
          const trimmedNumber = data.contactNumber.trim();
          if (!/^\d+$/.test(trimmedNumber)) {
            ctx.addIssue({
              path: ["contactNumber"],
              message: "Contact number must contain only digits",
              code: z.ZodIssueCode.custom,
            });
          } else if (trimmedNumber.length < 10) {
            ctx.addIssue({
              path: ["contactNumber"],
              message: "Contact number must be at least 10 digits",
              code: z.ZodIssueCode.custom,
            });
          } else if (trimmedNumber.length > 12) {
            ctx.addIssue({
              path: ["contactNumber"],
              message: "Contact number cannot exceed 12 digits",
              code: z.ZodIssueCode.custom,
            });
          }
        }
      }

      if (isLiveKitProvider(data.provider)) {
        if (!data.livekitUrl || data.livekitUrl.trim() === "") {
          ctx.addIssue({
            path: ["livekitUrl"],
            message: "LiveKit Server URL is required",
            code: z.ZodIssueCode.custom,
          });
        }
        if (!data.livekitApiKey || data.livekitApiKey.trim() === "") {
          ctx.addIssue({
            path: ["livekitApiKey"],
            message: "LiveKit API Key is required",
            code: z.ZodIssueCode.custom,
          });
        }
        if (!data.livekitApiSecret || data.livekitApiSecret.trim() === "") {
          ctx.addIssue({
            path: ["livekitApiSecret"],
            message: "LiveKit API Secret is required",
            code: z.ZodIssueCode.custom,
          });
        }
        if (!data.livekitAgentName || data.livekitAgentName.trim() === "") {
          ctx.addIssue({
            path: ["livekitAgentName"],
            message: "Agent Name is required",
            code: z.ZodIssueCode.custom,
          });
        }
      } else if (data.provider === "others") {
        // if (data.authenticationMethod === "api_key") {
        //   if (!data.username || data.username.trim() === "") {
        //     ctx.addIssue({
        //       path: ["username"],
        //       message: "Username is required",
        //       code: z.ZodIssueCode.custom,
        //     });
        //   }
        //   if (!data.password || data.password.trim() === "") {
        //     ctx.addIssue({
        //       path: ["password"],
        //       message: "Password is required",
        //       code: z.ZodIssueCode.custom,
        //     });
        //   }
        // } else if (data.authenticationMethod === "bearer_token") {
        //   if (!data.token || data.token.trim() === "") {
        //     ctx.addIssue({
        //       path: ["token"],
        //       message: "Token is required",
        //       code: z.ZodIssueCode.custom,
        //     });
        //   }
        // }
      } else {
        if (
          data.agentType === AGENT_TYPES.VOICE &&
          (data.observabilityEnabled || keysRequired || !data.inbound)
        ) {
          if (!data?.authenticationMethod) {
            ctx.addIssue({
              path: ["authenticationMethod"],
              message: "Authentication method is required",
              code: z.ZodIssueCode.custom,
            });
          }
          if (!data.provider || data.provider.trim() === "") {
            ctx.addIssue({
              path: ["provider"],
              message: "Please select a provider",
              code: z.ZodIssueCode.custom,
            });
          }
          if (!data.apiKey) {
            ctx.addIssue({
              path: ["apiKey"],
              message: "API key is required",
              code: z.ZodIssueCode.custom,
            });
          } else {
            if (!data.provider) {
              ctx.addIssue({
                path: ["provider"],
                message: "Please select a provider",
                code: z.ZodIssueCode.custom,
              });
            }
            try {
              await axios.post(endpoints.agentDefinitions.verifyApiKey, {
                provider: data.provider,
                api_key: data.apiKey,
              });
            } catch (error) {
              ctx.addIssue({
                path: ["apiKey"],
                message: "Invalid API key",
                code: z.ZodIssueCode.custom,
              });
            }
          }
          if (!data.assistantId) {
            ctx.addIssue({
              path: ["assistantId"],
              message: "Assistant ID is required",
              code: z.ZodIssueCode.custom,
            });
          } else {
            if (!data.provider) {
              ctx.addIssue({
                path: ["provider"],
                message: "Please select a provider",
                code: z.ZodIssueCode.custom,
              });
            } else if (!data.apiKey) {
              ctx.addIssue({
                path: ["apiKey"],
                message: "Please enter a valid API key",
                code: z.ZodIssueCode.custom,
              });
            } else {
              try {
                await axios.post(endpoints.agentDefinitions.verifyAssistantId, {
                  provider: data.provider,
                  api_key: data.apiKey,
                  assistant_id: data.assistantId,
                });
              } catch (error) {
                ctx.addIssue({
                  path: ["assistantId"],
                  message: "Invalid assistant ID",
                  code: z.ZodIssueCode.custom,
                });
              }
            }
          }
        }
      }
    });
};

export const defaultAgentDefinitionValues = {
  agentType: "",
  agentName: "",
  provider: "",
  apiKey: "",
  assistantId: "",
  description: "",
  languages: ["en"],
  knowledgeBase: "",
  countryCode: "",
  contactNumber: "",
  inbound: true,
  commitMessage: "",
  observabilityEnabled: false,
  token: "",
  authenticationMethod: "",
  username: "",
  password: "",
  livekitUrl: "",
  livekitApiKey: "",
  livekitApiSecret: "",
  livekitAgentName: "",
  livekitConfigJson: "",
  livekitMaxConcurrency: 2,
  _livekitCredentialsValid: false,
};

export const icon = (name) => (
  <SvgColor
    src={`/assets/icons/agent/${name}.svg`}
    sx={{ width: 20, height: 20 }}
  />
);

export const generateEvalColumnsFromConfig = (items = []) => {
  if (!items.length) return [];

  // Return flat columns (not grouped). AG Grid column groups in this
  // project were being silently dropped — flattening sidesteps that and
  // matches how the `list_spans_observe` trace list renders eval columns.
  return items.map((item) => {
    const evalId = item.id;
    const displayName = item.name?.replace(/_/g, " ") || evalId;
    const isReason = item.source_field === "reason";
    const dataKey = isReason ? item.parent_eval_id : evalId;
    return {
      headerName: displayName,
      field: `eval_outputs.${evalId}`,
      flex: 1,
      minWidth: isReason ? 240 : 140,
      hide: item.is_visible === false,
      headerComponent: CallLogsHeaderCellRenderer,
      headerComponentParams: { displayName },
      valueGetter: (params) => params.data?.eval_outputs?.[dataKey] || {},
      cellRenderer: (params) => {
        const evalData = params?.data?.eval_outputs?.[dataKey] || {};
        if (isReason) {
          const reason = evalData?.reason;
          return (
            <Box
              sx={{
                display: "flex",
                alignItems: "center",
                height: "100%",
                width: "100%",
                padding: "4px 8px",
                color: "text.primary",
              }}
            >
              {reason || "-"}
            </Box>
          );
        }
        return (
          <EvalCellRenderer
            value={{
              ...evalData,
              type: evalData?.output_type,
              value: evalData.output,
            }}
          />
        );
      },
    };
  });
};

const LoadingSkeleton = () => {
  return (
    <Skeleton
      variant="rectangular"
      width="80%"
      sx={{
        mx: 1,
        borderRadius: 0.5,
      }}
      height={15}
    />
  );
};
const generateAnnotationColumnsFromConfig = (
  items = [],
  expandedMetrics = [],
) => {
  if (!items.length) {
    return [];
  }

  const grouping = {};
  for (const eachCol of items) {
    if (!grouping[eachCol?.group_by]) {
      grouping[eachCol?.group_by] = [eachCol];
    } else {
      grouping[eachCol?.group_by].push(eachCol);
    }
  }

  return Object.entries(grouping).map(([groupName, metrics]) => ({
    headerName: groupName,
    children: metrics.map((metric) => {
      const metricId = metric?.id;
      const displayName = metric?.name?.replace(/_/g, " ") || metricId;
      const outputType = metric?.annotation_label_type;
      const settings = metric?.settings || {};
      const isExpanded =
        outputType === "text" || expandedMetrics.includes(metricId);

      if (!isExpanded) {
        // Collapsed: flat column under group → 2 header rows
        return {
          headerName: displayName,
          field: `annotation_outputs.${metricId}`,
          flex: 1,
          minWidth: 200,
          headerComponent: AnnotationHeaderCellRenderer,
          headerComponentParams: {
            displayName: displayName,
            metricId,
            isTextType: outputType === "text",
          },
          valueGetter: (params) => {
            const metricData = params?.data?.annotation_outputs?.[metricId];
            if (!metricData) return null;
            if (metricData.score !== undefined) return metricData.score;
            const { annotators: _, ...aggregates } = metricData;
            return Object.keys(aggregates)?.length > 0 ? aggregates : null;
          },
          cellRenderer: NewAnnotationCellRenderer,
          cellRendererParams: {
            annotationType: outputType,
            isAverage: true,
            settings,
          },
        };
      }

      // Expanded: nested group → 3 header rows with annotator columns
      const metricAnnotators = Object.values(metric?.annotators || {});

      const avgColumn = {
        headerName: "Avg",
        field: `annotation_outputs.${metricId}.score`,
        flex: 1,
        minWidth: 200,
        headerComponent: headerComponentLabels,
        headerComponentParams: {
          displayName: "Avg",
          isAverage: true,
        },
        valueGetter: (params) => {
          const metricData = params?.data?.annotation_outputs?.[metricId];
          if (!metricData) return null;
          if (metricData?.score !== undefined) return metricData?.score;
          const { annotators: _, ...aggregates } = metricData;
          return Object.keys(aggregates)?.length > 0 ? aggregates : null;
        },
        cellRenderer: NewAnnotationCellRenderer,
        cellRendererParams: {
          annotationType: outputType,
          isAverage: true,
          settings,
        },
      };

      const annotatorColumns = metricAnnotators.map((annotator) => ({
        headerName: annotator?.user_name,
        field: `annotation_outputs.${metricId}.annotators.${annotator?.user_id}`,
        flex: 1,
        minWidth: 200,
        ...(outputType === "text" ? { wrapText: true, autoHeight: true } : {}),
        headerComponent: headerComponentLabels,
        headerComponentParams: {
          displayName: annotator?.user_name,
          isAverage: false,
        },
        valueGetter: (params) => {
          const annotatorData =
            params?.data?.annotation_outputs?.[metricId]?.annotators?.[
              annotator.user_id
            ];
          if (!annotatorData) return null;
          if (annotatorData?.score !== undefined) return annotatorData?.score;

          return annotatorData.value ?? null;
        },
        cellRenderer: NewAnnotationCellRenderer,
        cellRendererParams: {
          annotationType: outputType,
          isAverage: false,
          settings,
        },
      }));

      return {
        headerName: displayName,
        headerGroupComponent: AnnotationHeaderCellRenderer,
        headerGroupComponentParams: {
          displayName,
          metricId,
          isTextType: outputType === "text",
        },
        children: [
          ...(outputType !== "text" ? [avgColumn] : []),
          ...annotatorColumns,
        ],
      };
    }),
  }));
};

// Generate AG Grid columns from evalOutputs
export const getCallLogsColumnDefs = (
  _rows = [],
  isLoading = false,
  agentType,
  module = null,
  config = null,
  expandedMetrics = [],
) => {
  if (isLoading) {
    return [
      {
        headerName:
          agentType === AGENT_TYPES.CHAT ? "Chat Details" : "Call Details",
        field: "call_summary",
        cellRenderer: LoadingSkeleton,
      },
      {
        headerName: "Participant",
        field: "customer_name",
        cellRenderer: LoadingSkeleton,
      },
      {
        headerName: "Duration",
        field: "duration_seconds",
        cellRenderer: LoadingSkeleton,
      },
      {
        headerName: "Status",
        field: "status",
        cellRenderer: LoadingSkeleton,
      },
    ];
  }

  const evalItems = [];
  const annotationItems = [];
  (config || []).forEach((item) => {
    if (item.annotation_label_type === null) evalItems.push(item);
    else annotationItems.push(item);
  });

  const evalColumns = generateEvalColumnsFromConfig(evalItems);
  const annotationColumns =
    module !== "simulate"
      ? generateAnnotationColumnsFromConfig(annotationItems, expandedMetrics)
      : [];
  const baseColumns = [
    // ── Identity ──────────────────────────────────────────────────────
    {
      headerName: "Call Details",
      field: "call_summary",
      flex: 2,
      minWidth: 200,
      cellRenderer: CallLogsCellRenderer,
    },
    {
      headerName: "Status",
      field: "status",
      flex: 0,
      minWidth: 100,
      cellRenderer: CallLogsCellRenderer,
    },
    {
      headerName: "Duration",
      field: "duration_seconds",
      flex: 0,
      minWidth: 90,
      cellRenderer: CallLogsCellRenderer,
    },

    // ── Performance ───────────────────────────────────────────────────
    {
      headerName: "Avg Latency",
      field: "avg_agent_latency_ms",
      flex: 0,
      minWidth: 140,
      cellRenderer: VoiceLatencyCell,
    },
    {
      headerName: "Turn Count",
      field: "turn_count",
      flex: 0,
      minWidth: 110,
      cellRenderer: CallLogsCellRenderer,
    },
    {
      headerName: "Talk Ratio",
      field: "talk_ratio",
      flex: 0,
      minWidth: 120,
      cellRenderer: TalkRatioCell,
      hide: true,
    },

    // ── Resources ─────────────────────────────────────────────────────
    {
      headerName: "Tokens",
      field: "gen_ai.usage.total_tokens",
      flex: 0,
      minWidth: 220,
      cellRenderer: VoiceTokenCell,
    },
    {
      headerName: "Cost",
      field: "cost_cents",
      flex: 0,
      minWidth: 120,
      cellRenderer: VoiceCostCell,
    },

    // ── Conversation quality ──────────────────────────────────────────
    {
      headerName: "User Interrupts",
      field: "user_interruption_count",
      flex: 0,
      minWidth: 140,
      cellRenderer: CallLogsCellRenderer,
    },
    {
      headerName: "Agent Interrupts",
      field: "ai_interruption_count",
      flex: 0,
      minWidth: 140,
      cellRenderer: CallLogsCellRenderer,
    },
    {
      headerName: "Ended Reason",
      field: "ended_reason",
      flex: 1,
      minWidth: 120,
      cellRenderer: CallLogsCellRenderer,
    },

    // ── Secondary (visible, further right) ────────────────────────────
    {
      headerName: "Participant",
      field: "customer_name",
      flex: 1,
      minWidth: 120,
      cellRenderer: CallLogsCellRenderer,
    },
    {
      headerName: "Type",
      field: "call_type",
      flex: 0,
      minWidth: 90,
      cellRenderer: CallLogsCellRenderer,
    },
    {
      headerName: "User WPM",
      field: "user_wpm",
      flex: 0,
      minWidth: 110,
      cellRenderer: CallLogsCellRenderer,
    },
    {
      headerName: "Agent WPM",
      field: "bot_wpm",
      flex: 0,
      minWidth: 110,
      cellRenderer: CallLogsCellRenderer,
    },
    {
      headerName: "Agent Talk (%)",
      field: "agent_talk_percentage",
      flex: 0,
      minWidth: 130,
      valueGetter: (params) => {
        const direct = params.data?.agent_talk_percentage;
        if (direct != null) return direct;
        const ratio = params.data?.talk_ratio;
        if (ratio && typeof ratio === "object") return ratio.bot_pct;
        return null;
      },
      cellRenderer: CallLogsCellRenderer,
    },

    // ── Technical (hidden by default, togglable via display panel) ────
    {
      headerName: "Customer Phone",
      field: "phone_number",
      flex: 1,
      minWidth: 120,
      hide: true,
      cellRenderer: CallLogsCellRenderer,
    },
    {
      headerName: "Call ID",
      field: "call_id",
      flex: 1,
      minWidth: 120,
      hide: true,
      cellRenderer: CallLogsCellRenderer,
    },
    {
      headerName: "Response Time",
      field: "response_time_ms",
      flex: 0,
      minWidth: 110,
      hide: true,
      cellRenderer: CallLogsCellRenderer,
    },
  ];

  return [...baseColumns, ...evalColumns, ...annotationColumns];
};

export const useAgentsList = () => {
  const { data, isLoading, error } = useQuery({
    queryKey: ["agents"],
    queryFn: async () => {
      let allAgents = [];
      let page = 1;
      let totalPages = null;

      while (totalPages === null || page <= totalPages) {
        const res = await axios.get(
          `${endpoints.agentDefinitions.list}?page=${page}`,
        );

        allAgents = allAgents.concat(res.data.results);

        if (totalPages === null) {
          totalPages = res.data.total_pages;
        }

        if (page >= totalPages) {
          break;
        }

        page += 1;
      }

      return allAgents;
    },
  });
  return { agents: data || [], isLoading, error };
};

export const useCallLogs = ({
  module,
  id,
  version,
  page,
  pageLimit,
  params,
  enabled = true,
}) => {
  let endpoint = endpoints.agentDefinitions.getCallLogs(id, version);
  let condition = !!id && !!version;
  let queryKey = ["callLogs", module, id, version, pageLimit, params, page];
  if (module === "project") {
    endpoint = endpoints.project.getCallLogs;
    condition = !!id;
    queryKey = ["callLogs", module, id, pageLimit, params, page];
  }
  const { data, isLoading, error } = useQuery({
    queryKey: queryKey,
    queryFn: () =>
      axios.get(endpoint, {
        params: { page, page_size: pageLimit, ...params },
      }),
    enabled: condition && enabled,
    select: (data) => data?.data,
  });
  return { queryKey, data, isLoading, error };
};

export const prefetchCallLogs = (
  queryClient,
  { module, id, version, page, pageLimit, params },
) => {
  let endpoint = endpoints.agentDefinitions.getCallLogs(id, version);
  let queryKey = ["callLogs", module, id, version, pageLimit, params, page];
  if (module === "project") {
    endpoint = endpoints.project.getCallLogs;
    queryKey = ["callLogs", module, id, pageLimit, params, page];
  }
  queryClient.prefetchQuery({
    queryKey,
    queryFn: () =>
      axios.get(endpoint, {
        params: { page, page_size: pageLimit, ...params },
      }),
  });
};

export const useCallExecutionDetail = (callExecutionId, enabled = false) => {
  return useQuery({
    queryKey: ["callExecutionDetail", callExecutionId],
    queryFn: () =>
      axios.get(endpoints.runTests.callExecutionDetail(callExecutionId)),
    enabled: !!callExecutionId && enabled,
    select: (data) => data?.data,
    staleTime: 5 * 60 * 1000,
    meta: { errorHandled: true },
  });
};

export const useVoiceCallDetail = (traceId, enabled = false) => {
  return useQuery({
    queryKey: ["voiceCallDetail", traceId],
    queryFn: () =>
      axios.get(endpoints.project.getVoiceCallDetail, {
        params: { trace_id: traceId },
      }),
    enabled: !!traceId && enabled,
    select: (data) => data?.data?.result,
    staleTime: 5 * 60 * 1000,
    meta: { errorHandled: true },
  });
};
