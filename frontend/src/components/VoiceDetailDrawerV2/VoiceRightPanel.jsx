import React, { useMemo, useState } from "react";
import PropTypes from "prop-types";
import { Box, Button, Stack } from "@mui/material";
import CompactTabs from "./CompactTabs";
import Iconify from "src/components/iconify";
import { ShowComponent } from "src/components/show";
import {
  getCompareBaselineTooltipTitle,
  getLoadingStateWithRespectiveStatus,
  TestRunExecutionStatus,
} from "src/sections/test-detail/common";
import CustomTooltip from "src/components/tooltip/CustomTooltip";
import CallAnalyticsView from "./CallAnalyticsView";
import { isLiveKitProvider } from "src/sections/agents/constants";
import ScoresListSection from "src/components/ScoresListSection/ScoresListSection";
import EvalsTabView from "src/components/traceDetail/EvalsTabView";
import { openFixWithFalcon } from "src/sections/falcon-ai/helpers/openFixWithFalcon";
import VoiceLogsView from "./VoiceLogsView";
import LoadingStateComponent from "src/components/CallLogsDetailDrawer/LoadingStateComponent";
import {
  extractCostBreakdown,
  extractLatencies,
} from "src/components/CallLogsDetailDrawer/utils";
import { getSpanAttributes } from "src/components/traceDetailDrawer/DrawerRightRenderer/getSpanData";
import AttributesTable from "./AttributesTable";
import MessagesView from "./MessagesView";
import CallDetailsBar from "./CallDetailsBar";
import ScenarioView from "./ScenarioView";

const TABS = {
  ANALYTICS: "analytics",
  EVALUATIONS: "evaluations",
  MESSAGES: "messages",
  LOGS: "logs",
  ATTRIBUTES: "attributes",
  ANNOTATIONS: "annotations",
  SCENARIO: "scenario",
};

const VoiceRightPanel = ({ data, onCompareBaseline, onAction, hideAnnotationTab }) => {
  const [currentTab, setCurrentTab] = useState(TABS.ANALYTICS);
  const isSimulate = data?.module === "simulate";
  // Prefer the conversation root span (where voice-call attributes/raw_log
  // live). `trace.observation_spans.all()` is returned without a guaranteed
  // order, so [0] can be a child span with an empty span_attributes object.
  const observationSpan = useMemo(() => {
    const spans = data?.observation_span;
    if (!Array.isArray(spans) || spans.length === 0) return undefined;
    return (
      spans.find(
        (s) => !s?.parent_span_id && s?.observation_type === "conversation",
      ) ||
      spans.find((s) => !s?.parent_span_id) ||
      spans[0]
    );
  }, [data?.observation_span]);
  const canCompare =
    isSimulate &&
    !!onCompareBaseline &&
    !!data?.session_id;

  const { isCallInProgress, message: loadingMessage } =
    getLoadingStateWithRespectiveStatus(
      data?.status,
      data?.simulation_call_type,
    );

  const messagesList = useMemo(() => {
    if (Array.isArray(data?.messages)) return data.messages;
    if (Array.isArray(data?.transcript)) {
      return data.transcript.map((t) => ({
        role: t.speakerRole || t.role,
        content: t.message || t.content || t.text,
        ...t,
      }));
    }
    return [];
  }, [data]);

  const vapiId = getSpanAttributes(observationSpan)?.rawLog?.id;
  const callLogs = getSpanAttributes(observationSpan)?.callLogs;
  const hasLogs = !!vapiId || !!callLogs || !!data?.id;

  const hasScenarioData =
    isSimulate &&
    !!data?.scenario_columns &&
    Object.keys(data.scenario_columns).length > 0;

  const tabs = useMemo(() => {
    // Icons match the trace drawer's SpanDetailPane TAB_CONFIG where they
    // exist ("Evals", "Log View", "Annotations") so both drawers feel like
    // the same product.
    const t = [
      {
        label: "Call Analytics",
        value: TABS.ANALYTICS,
        icon: "mdi:chart-line",
      },
      {
        label: "Evals",
        value: TABS.EVALUATIONS,
        icon: "mdi:checkbox-marked-circle-outline",
      },
      {
        label: "Messages",
        value: TABS.MESSAGES,
        icon: "mdi:message-text-outline",
      },
    ];
    if (hasLogs) {
      t.push({
        label: "Logs",
        value: TABS.LOGS,
        icon: "mdi:format-list-bulleted",
      });
    }
    t.push({
      label: "Attributes",
      value: TABS.ATTRIBUTES,
      icon: "mdi:code-json",
    });
    if (!hideAnnotationTab) {
      t.push({
        label: "Annotations",
        value: TABS.ANNOTATIONS,
        icon: "mdi:pencil-outline",
      });
    }

    if (hasScenarioData) {
      t.push({
        label: "Scenario",
        value: TABS.SCENARIO,
        icon: "mdi:script-text-outline",
      });
    }
    return t;
  }, [hasLogs, hasScenarioData]);

  const analyticsProps = useMemo(() => {
    // API-provided per-call metrics (prefer over client-computed values)
    const apiMetrics = {
      turnCount: data?.turn_count,
      talkRatio: data?.talk_ratio,
      agentTalkPercentage: data?.agent_talk_percentage,
      avgAgentLatencyMs:
        data?.avg_agent_latency_ms ?? data?.avg_agent_latency,
      userWpm: data?.user_wpm,
      botWpm: data?.bot_wpm,
      userInterruptionCount: data?.user_interruption_count,
      aiInterruptionCount: data?.ai_interruption_count,
    };

    if (isSimulate) {
      return {
        transcript: data?.transcript,
        latencies: data?.customer_latency_metrics?.system_metrics,
        analysisSummary: data?.call_summary,
        costBreakdown: data?.customer_cost_breakdown,
        isLiveKit: isLiveKitProvider(data?.provider),
        apiMetrics,
      };
    }
    const rawLog = getSpanAttributes(observationSpan)?.rawLog;
    // Prefer the customer-agent metrics the backend now surfaces
    // (voice_call_detail looks them up from the matching CallExecution).
    // Fall back to the provider's raw `artifact.performanceMetrics` /
    // `costBreakdown` for pure-observe traffic where no CallExecution is
    // linked.
    const customerLatencies = data?.customer_latency_metrics?.system_metrics;
    const customerCost = data?.customer_cost_breakdown;
    return {
      transcript: data?.transcript,
      latencies:
        customerLatencies || extractLatencies(rawLog?.artifact?.performanceMetrics),
      analysisSummary: rawLog?.summary || data?.call_summary,
      costBreakdown: customerCost || extractCostBreakdown(rawLog?.costBreakdown),
      isLiveKit: isLiveKitProvider(data?.provider),
      apiMetrics,
    };
  }, [isSimulate, data, observationSpan]);

  const evalRows = useMemo(() => {
    if (isSimulate) {
      return data?.eval_metrics || data?.eval_outputs || null;
    }
    return (
      data?.eval_outputs || observationSpan?.evals_metrics || null
    );
  }, [isSimulate, data, observationSpan]);

  // Normalize voice eval payloads into the shape EvalsTabView expects.
  // Voice evals come in different shapes depending on module:
  //  - observe: observationSpan.evals_metrics → { id: { name, output, reason, outputType } }
  //  - simulate: data.eval_metrics → similar per-id map OR { id: { name, score, reason } }
  //  - arrays of { name, score, reason, ... }
  // The shared EvalsTabView uses a canonical 0-100 score and eval_name
  // field so the trace drawer's traffic-light bucketing kicks in.
  const normalizedEvals = useMemo(() => {
    if (!evalRows) return [];
    const rows = Array.isArray(evalRows)
      ? evalRows.map((e, i) => [e?.id || `eval-${i}`, e])
      : Object.entries(evalRows);

    return rows.map(([id, e], i) => {
      // Categorical evals (post-migration) emit output as a list like ["never"].
      // Surface the first bucket name so it shows in the badge.
      let rawValue = e?.score ?? e?.output ?? e?.value;
      if (Array.isArray(rawValue)) {
        rawValue = rawValue.length === 1 ? rawValue[0] : rawValue.join(", ");
      }
      let score = null;
      let scoreLabel;

      if (typeof rawValue === "number") {
        // Numbers in [0, 1] → percent. Numbers already in [0, 100] → as-is.
        score =
          rawValue <= 1 ? Math.round(rawValue * 100) : Math.round(rawValue);
      } else if (typeof rawValue === "boolean") {
        score = rawValue ? 100 : 0;
        scoreLabel = rawValue ? "Pass" : "Fail";
      } else if (typeof rawValue === "string") {
        const lower = rawValue.toLowerCase();
        if (lower.includes("pass") || lower === "true") {
          score = 100;
          scoreLabel = "Pass";
        } else if (lower.includes("fail") || lower === "false") {
          score = 0;
          scoreLabel = "Fail";
        } else {
          // Surface the string verbatim; leave score null so the badge goes gray
          scoreLabel =
            rawValue.length > 24 ? `${rawValue.slice(0, 24)}…` : rawValue;
        }
      }

      return {
        id: `eval-${id}-${i}`,
        eval_name: e?.name || e?.metric || String(id),
        score,
        score_label: scoreLabel,
        explanation: e?.reason || e?.explanation,
        // Error localization fields — pulled from whatever key the
        // backend used. Makes the shared EvalsTabView render the
        // dropdown / "Run" UX for failed voice evals.
        cell_id: e?.cell_id || e?.cellId,
        error_analysis:
          e?.error_analysis || e?.errorAnalysis || e?.error_details,
        error_localizer_status:
          e?.error_localizer_status || e?.errorLocalizerStatus,
        selected_input_key: e?.selected_input_key || e?.selectedInputKey,
        datapoint: e?.datapoint || {
          selectedInputKey: e?.selected_input_key || e?.selectedInputKey,
          selected_input_key: e?.selected_input_key || e?.selectedInputKey,
          inputData: e?.input_data || e?.inputData,
          input_data: e?.input_data || e?.inputData,
          inputTypes: e?.input_types || e?.inputTypes,
          input_types: e?.input_types || e?.inputTypes,
        },
      };
    });
  }, [evalRows]);

  const traceId = data?.trace_id || data?.id;
  const annotationSources = useMemo(() => {
    if (isSimulate) {
      return {
        sourceType: "call_execution",
        sourceId: data?.id,
      };
    }
    // observationSpan is already computed above: finds root by parent_span_id === null
    // and observation_type === "conversation", so use it directly instead of [0].
    const span = observationSpan;
    return {
      sourceType: span?.id ? "observation_span" : "trace",
      sourceId: span?.id || traceId,
      secondarySourceType: span?.id ? "trace" : undefined,
      secondarySourceId: span?.id ? traceId : undefined,
    };
  }, [isSimulate, data, traceId, observationSpan]);

  const attributesObj = useMemo(() => {
    // Source-of-truth chain, in order of richness:
    //  1. Root observation span's attributes — observability-on path.
    //  2. `data.attributes` — simulate detail endpoint's flattened dict
    //     built from `provider_call_data` via the same extractor the
    //     ingest pipeline uses, so the observability-off drawer mirrors
    //     the observe shape (~40 flat keys vs. a single raw_log tree).
    //  3. Legacy `trace_details.attributes` for older cached payloads.
    //  4. The span object itself as a last resort.
    return (
      observationSpan?.span_attributes ||
      data?.attributes ||
      data?.trace_details?.attributes ||
      observationSpan ||
      null
    );
  }, [data, observationSpan]);

  return (
    <Stack
      sx={{
        minHeight: 300,
        height: "100%",
        containerType: "inline-size",
        display: "flex",
        flexDirection: "column",
        overflow: "hidden",
      }}
    >
      {/* Call details — chips + tags + Actions button live at the top of
          the right panel, matching the trace drawer's span-detail-pane
          layout. */}
      <CallDetailsBar data={data} onAction={onAction} />

      <Stack
        direction="row"
        alignItems="center"
        justifyContent="space-between"
        gap={1}
        sx={{
          flexShrink: 0,
          px: 1.25,
          minWidth: 0,
        }}
      >
        <Box sx={{ flex: 1, minWidth: 0 }}>
          <CompactTabs
            value={currentTab}
            onChange={(_, value) => setCurrentTab(value)}
            tabs={tabs}
          />
        </Box>
        {canCompare && (
          <CustomTooltip
            show={data?.status !== TestRunExecutionStatus.COMPLETED}
            title={getCompareBaselineTooltipTitle(data?.status)}
            type="black"
            arrow
            placement="bottom"
            size="small"
          >
            <span>
              <Button
                variant="outlined"
                color="primary"
                size="small"
                disabled={data?.status !== TestRunExecutionStatus.COMPLETED}
                startIcon={<Iconify icon="mdi:swap-horizontal" width={14} />}
                onClick={() => onCompareBaseline(true)}
                sx={{
                  whiteSpace: "nowrap",
                  fontSize: 11,
                  height: 26,
                  textTransform: "none",
                }}
              >
                Compare with baseline
              </Button>
            </span>
          </CustomTooltip>
        )}
      </Stack>

      <ShowComponent condition={isCallInProgress}>
        <LoadingStateComponent message={loadingMessage} />
      </ShowComponent>

      <ShowComponent condition={!isCallInProgress}>
        <Box
          sx={{
            flex: 1,
            minHeight: 0,
            width: "100%",
            overflow: "auto",
            px: 1.25,
            py: 1,
          }}
        >
          <ShowComponent condition={currentTab === TABS.ANALYTICS}>
            <CallAnalyticsView {...analyticsProps} />
          </ShowComponent>

          <ShowComponent condition={currentTab === TABS.EVALUATIONS}>
            <EvalsTabView
              evals={normalizedEvals}
              emptyMessage="No evaluations for this call"
              showSpanColumn={false}
              onFixWithFalcon={({ level, ev, failingEvals, allEvals }) => {
                const projectId = data?.project_id;
                const callId = data?.id;
                if (level === "eval" && ev) {
                  openFixWithFalcon({
                    level: "eval",
                    context: {
                      trace_id: traceId,
                      call_id: callId,
                      span_id:
                        ev.spanId || ev.observation_span_id,
                      eval_log_id:
                        ev.eval_log_id || ev.cell_id || ev.log_id,
                      custom_eval_config_id:
                        ev.custom_eval_config_id || ev.eval_config_id,
                      eval_name: ev.eval_name,
                      score: ev.score,
                      explanation: ev.explanation || ev.eval_explanation,
                      project_id: projectId,
                      module: data?.module,
                    },
                  });
                  return;
                }
                const total = (allEvals || []).length;
                const passCount = (allEvals || []).filter(
                  (e) => e.score != null && e.score >= 50,
                ).length;
                openFixWithFalcon({
                  level: "voice",
                  context: {
                    trace_id: traceId,
                    call_id: callId,
                    project_id: projectId,
                    module: data?.module,
                    evals_summary: `${passCount}/${total} passed`,
                    failing_evals: (failingEvals || []).map((e) => ({
                      name: e.eval_name,
                      score: e.score,
                    })),
                  },
                });
              }}
            />
          </ShowComponent>

          <ShowComponent condition={currentTab === TABS.MESSAGES}>
            <MessagesView messages={messagesList} />
          </ShowComponent>

          <ShowComponent condition={currentTab === TABS.LOGS && hasLogs}>
            <VoiceLogsView
              module={data?.module}
              callLogId={data?.id}
              vapiId={vapiId}
              callLogs={callLogs}
            />
          </ShowComponent>

          <ShowComponent condition={currentTab === TABS.ATTRIBUTES}>
            <AttributesTable attributes={attributesObj} />
          </ShowComponent>

          <ShowComponent condition={currentTab === TABS.ANNOTATIONS}>
            <ScoresListSection
              sourceType={annotationSources.sourceType}
              sourceId={annotationSources.sourceId}
              secondarySourceType={annotationSources.secondarySourceType}
              secondarySourceId={annotationSources.secondarySourceId}
              title=""
              renderActions={
                onAction ? (
                  <Button
                    size="small"
                    variant="outlined"
                    startIcon={<Iconify icon="mingcute:add-line" width={14} />}
                    onClick={() => onAction("annotate")}
                    sx={{
                      textTransform: "none",
                      fontSize: 12,
                      fontWeight: 500,
                      borderColor: "divider",
                      color: "text.primary",
                      borderRadius: "4px",
                      px: 1.5,
                      py: 0.25,
                    }}
                  >
                    Add Label
                  </Button>
                ) : null
              }
            />
          </ShowComponent>

          <ShowComponent
            condition={currentTab === TABS.SCENARIO && hasScenarioData}
          >
            <ScenarioView data={data} />
          </ShowComponent>
        </Box>
      </ShowComponent>
    </Stack>
  );
};

VoiceRightPanel.propTypes = {
  data: PropTypes.object.isRequired,
  onCompareBaseline: PropTypes.func,
  onAction: PropTypes.func,
  hideAnnotationTab: PropTypes.bool
};

export default VoiceRightPanel;
