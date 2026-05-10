import React, { useMemo, useState } from "react";
import CustomAgentTabs from "src/sections/agents/CustomAgentTabs";
import { Stack } from "@mui/material";
import { ShowComponent } from "../show";
import BottomEvalsTab from "../traceDetailDrawer/bottom-evals-tab";
import PropTypes from "prop-types";
import {
  extractCostBreakdown,
  extractLatencies,
  RIGHT_SECTION_TABS,
  transformEvalMetrics,
} from "./utils";
import TestDetailCallAnalytics from "src/sections/test-detail/TestDetailDrawer/TestDetailCallAnalytics";
import LoadingStateComponent from "./LoadingStateComponent";
import { getLoadingStateWithRespectiveStatus } from "src/sections/test-detail/common";
import ScoresListSection from "src/components/ScoresListSection/ScoresListSection";
import { buildVoiceCallScoreSource } from "src/components/voiceAnnotationSources";
import { getSpanAttributes } from "../traceDetailDrawer/DrawerRightRenderer/getSpanData";

const TABS = [
  {
    label: "Call Analytics",
    value: RIGHT_SECTION_TABS.CALL_ANALYTICS,
  },
  {
    label: "Annotations",
    value: RIGHT_SECTION_TABS.ANNOTATIONS,
  },
  {
    label: "Evaluations",
    value: RIGHT_SECTION_TABS.EVALUATIONS,
  },
];

const RightSection = ({ data, hideAnnotations = false }) => {
  const [currentRightTab, setCurrentRightTab] = useState(
    RIGHT_SECTION_TABS.CALL_ANALYTICS,
  );
  const handleTabChange = (_, value) => {
    setCurrentRightTab(value);
  };

  const visibleTabs = useMemo(
    () =>
      hideAnnotations
        ? TABS.filter((t) => t.value !== RIGHT_SECTION_TABS.ANNOTATIONS)
        : TABS,
    [hideAnnotations],
  );

  const evalMetrics = useMemo(() => {
    const transformedEvalMetrics = transformEvalMetrics(data?.evalOutputs);
    return { evalsMetrics: transformedEvalMetrics };
  }, [data]);

  const observationSpan = data?.observation_span?.[0];

  const { isCallInProgress, message: loadingMessage } =
    getLoadingStateWithRespectiveStatus(data?.status, data?.simulationCallType);
  // Determine source type and ID for scores — fetch both levels
  const traceId = data?.trace_id || data?.id;
  const { sourceType, sourceId, secondarySourceType, secondarySourceId } =
    buildVoiceCallScoreSource({
      traceId,
      rootSpanId: observationSpan?.id,
      isSimulate: false,
    });

  return (
    <Stack gap={2} minHeight={300}>
      <CustomAgentTabs
        value={currentRightTab}
        onChange={handleTabChange}
        tabs={visibleTabs}
      />

      <ShowComponent condition={isCallInProgress}>
        <LoadingStateComponent message={loadingMessage} />
      </ShowComponent>
      <ShowComponent condition={!isCallInProgress}>
        <ShowComponent
          condition={currentRightTab === RIGHT_SECTION_TABS.EVALUATIONS}
        >
          <BottomEvalsTab
            observationSpan={evalMetrics}
            isLoading={false}
            showAddFeedback={false}
            showViewDetail={false}
          />
        </ShowComponent>
        <ShowComponent
          condition={currentRightTab === RIGHT_SECTION_TABS.CALL_ANALYTICS}
        >
          <TestDetailCallAnalytics
            latencies={extractLatencies(
              getSpanAttributes(observationSpan)?.rawLog?.artifact
                ?.performanceMetrics,
            )}
            analysisSummary={
              getSpanAttributes(observationSpan)?.rawLog?.summary
            }
            costBreakdown={extractCostBreakdown(
              getSpanAttributes(observationSpan)?.rawLog?.costBreakdown,
            )}
          />
        </ShowComponent>
        <ShowComponent
          condition={currentRightTab === RIGHT_SECTION_TABS.ANNOTATIONS}
        >
          <ScoresListSection
            sourceType={sourceType}
            sourceId={sourceId}
            secondarySourceType={secondarySourceType}
            secondarySourceId={secondarySourceId}
          />
        </ShowComponent>
      </ShowComponent>
    </Stack>
  );
};

export default RightSection;

RightSection.propTypes = {
  data: PropTypes.object.isRequired,
  hideAnnotations: PropTypes.bool,
};
