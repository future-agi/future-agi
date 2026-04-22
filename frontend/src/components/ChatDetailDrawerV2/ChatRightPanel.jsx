import React, { useMemo, useState } from "react";
import PropTypes from "prop-types";
import { Box, Button, Stack } from "@mui/material";
import CompactTabs from "src/components/VoiceDetailDrawerV2/CompactTabs";
import AttributesTable from "src/components/VoiceDetailDrawerV2/AttributesTable";
import MessagesView from "src/components/VoiceDetailDrawerV2/MessagesView";
import ScenarioView from "src/components/VoiceDetailDrawerV2/ScenarioView";
import Iconify from "src/components/iconify";
import { ShowComponent } from "src/components/show";
import {
  getCompareBaselineTooltipTitle,
  getLoadingStateWithRespectiveStatus,
  TestRunExecutionStatus,
} from "src/sections/test-detail/common";
import CustomTooltip from "src/components/tooltip/CustomTooltip";
import ScoresListSection from "src/components/ScoresListSection/ScoresListSection";
import EvalsTabView from "src/components/traceDetail/EvalsTabView";
import { openFixWithFalcon } from "src/sections/falcon-ai/helpers/openFixWithFalcon";
import LoadingStateComponent from "src/components/CallLogsDetailDrawer/LoadingStateComponent";
import { getSpanAttributes } from "src/components/traceDetailDrawer/DrawerRightRenderer/getSpanData";
import ChatAnalyticsView from "./ChatAnalyticsView";
import ChatDetailsBar from "./ChatDetailsBar";

const TABS = {
  ANALYTICS: "analytics",
  EVALUATIONS: "evaluations",
  MESSAGES: "messages",
  ATTRIBUTES: "attributes",
  ANNOTATIONS: "annotations",
  SCENARIO: "scenario",
};

/**
 * Chat drawer right panel. Mirrors `VoiceRightPanel` structure so the two
 * drawers feel like the same product — same `CallDetailsBar` chip strip,
 * same `CompactTabs` nav, same Evals/Messages/Attributes/Annotations/
 * Scenario panes (all data-agnostic components).
 *
 * Differences from voice:
 *   - "Call Analytics" uses `ChatAnalyticsView` (chat KPIs: turns,
 *     tokens, cost, avg latency) instead of voice's Latency pipeline /
 *     voice KPI strip.
 *   - No "Logs" tab — chat doesn't have a vapi/callLogs equivalent yet.
 *   - Eval normalization logic mirrors `VoiceRightPanel.normalizedEvals`.
 */
const ChatRightPanel = ({ data, onCompareBaseline, onAction }) => {
  const [currentTab, setCurrentTab] = useState(TABS.ANALYTICS);
  const isSimulate = data?.module === "simulate";

  // Prefer the conversation root span (same logic as VoiceRightPanel).
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
    isSimulate && !!onCompareBaseline && !!data?.session_id;

  const { isCallInProgress, message: loadingMessage } =
    getLoadingStateWithRespectiveStatus(
      data?.status,
      data?.simulation_call_type || data?.simulationCallType,
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

  const hasScenarioData =
    isSimulate &&
    !!data?.scenario_columns &&
    Object.keys(data.scenario_columns).length > 0;

  const tabs = useMemo(() => {
    const t = [
      {
        label: "Chat Analytics",
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
      {
        label: "Attributes",
        value: TABS.ATTRIBUTES,
        icon: "mdi:code-json",
      },
      {
        label: "Annotations",
        value: TABS.ANNOTATIONS,
        icon: "mdi:pencil-outline",
      },
    ];
    if (hasScenarioData) {
      t.push({
        label: "Scenario",
        value: TABS.SCENARIO,
        icon: "mdi:script-text-outline",
      });
    }
    return t;
  }, [hasScenarioData]);

  // Eval normalization — mirrors VoiceRightPanel.normalizedEvals byte-for-byte
  // so the Evals pane renders consistently across both drawers. If this logic
  // ever changes, update both (or lift into a shared helper).
  const evalRows = useMemo(() => {
    if (isSimulate) {
      return data?.eval_metrics || data?.eval_outputs || null;
    }
    return data?.eval_outputs || observationSpan?.evals_metrics || null;
  }, [isSimulate, data, observationSpan]);

  const normalizedEvals = useMemo(() => {
    if (!evalRows) return [];
    const rows = Array.isArray(evalRows)
      ? evalRows.map((e, i) => [e?.id || `eval-${i}`, e])
      : Object.entries(evalRows);

    return rows.map(([id, e], i) => {
      const rawValue = e?.score ?? e?.output ?? e?.value;
      let score = null;
      let scoreLabel;

      if (typeof rawValue === "number") {
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
    const span = data?.observation_span?.[0];
    return {
      sourceType: span?.id ? "observation_span" : "trace",
      sourceId: span?.id || traceId,
      secondarySourceType: span?.id ? "trace" : undefined,
      secondarySourceId: span?.id ? traceId : undefined,
    };
  }, [isSimulate, data, traceId]);

  const attributesObj = useMemo(() => {
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
      <ChatDetailsBar data={data} onAction={onAction} />

      <Stack
        direction="row"
        alignItems="center"
        justifyContent="space-between"
        gap={1}
        sx={{ flexShrink: 0, px: 1.25, minWidth: 0 }}
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
            <ChatAnalyticsView data={data} />
          </ShowComponent>

          <ShowComponent condition={currentTab === TABS.EVALUATIONS}>
            <EvalsTabView
              evals={normalizedEvals}
              emptyMessage="No evaluations for this chat"
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
                      span_id: ev.spanId || ev.observation_span_id,
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
                  level: "chat",
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
                    startIcon={
                      <Iconify icon="mingcute:add-line" width={14} />
                    }
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

ChatRightPanel.propTypes = {
  data: PropTypes.object.isRequired,
  onCompareBaseline: PropTypes.func,
  onAction: PropTypes.func,
};

export default ChatRightPanel;
