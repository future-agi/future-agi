import { Box, Stack } from "@mui/material";
import React, { useMemo, useState } from "react";
import CustomAgentTabs from "src/sections/agents/CustomAgentTabs";
import { ShowComponent } from "../show";
import CallLogMessages from "./CallLogMessages";
import PropTypes from "prop-types";
import UnifiedCallTranscript from "src/sections/test-detail/TestDetailDrawer/UnifiedTranscripts";
import BottomAttributesTab from "../traceDetailDrawer/bottom-attributes-tab";
import CallDetailLogs from "./CallDetailLogs/CallDetailLogs";
import LoadingStateComponent from "./LoadingStateComponent";
import { getLoadingStateWithRespectiveStatus } from "src/sections/test-detail/common";
import { LEFT_SECTION_TABS } from "./utils";
import { AGENT_TYPES } from "src/sections/agents/constants";
import TestDetailTraceSection from "src/sections/test-detail/TestDetailDrawer/TestDetailTraceSection";
import { getSpanAttributes } from "../traceDetailDrawer/DrawerRightRenderer/getSpanData";

const LeftSectionBottom = ({ data }) => {
  const isSimulateModule = data?.module === "simulate";

  const TABS = useMemo(() => {
    if (isSimulateModule && data?.simulationCallType === AGENT_TYPES.VOICE) {
      return [
        {
          label: "Transcript",
          value: LEFT_SECTION_TABS.TRANSCRIPT,
          disabled: false,
        },
        {
          label: "Logs",
          value: LEFT_SECTION_TABS.LOGS,
          disabled: false,
        },
      ];
    }
    if (isSimulateModule && data?.simulationCallType === AGENT_TYPES.CHAT) {
      return [
        {
          label: "Transcript",
          value: LEFT_SECTION_TABS.TRANSCRIPT,
          disabled: false,
        },
        // {
        //   label: "Attributes",
        //   value: LEFT_SECTION_TABS.ATTRIBUTES,
        //   disabled: false,
        // },
        // {
        //   label: "Traces",
        //   value: LEFT_SECTION_TABS.TRACES,
        //   disabled: false,
        // },
      ];
    }

    return [
      {
        label: "Transcript",
        value: LEFT_SECTION_TABS.TRANSCRIPT,
        disabled: false,
      },
      {
        label: "Messages",
        value: LEFT_SECTION_TABS.MESSAGES,
        disabled: false,
      },
      {
        label: "Attributes",
        value: LEFT_SECTION_TABS.ATTRIBUTES,
        disabled: false,
      },
      {
        label: "Logs",
        value: LEFT_SECTION_TABS.LOGS,
        disabled: false,
      },
    ];
  }, [isSimulateModule, data?.simulationCallType]);

  const [currentLeftTab, setCurrentLeftTab] = useState(
    LEFT_SECTION_TABS.TRANSCRIPT,
  );

  const handleTabChange = (_, value) => {
    setCurrentLeftTab(value);
  };

  const filteredTranscript = useMemo(() => {
    const transcript = data?.transcript;
    return transcript?.filter((item) => item.speaker_role !== "system");
  }, [data]);
  const observationSpan = data?.observation_span;
  const { isCallInProgress, message: loadingMessage } =
    getLoadingStateWithRespectiveStatus(data?.status, data?.simulationCallType);
  return (
    <Stack width={"100%"} spacing={2} minHeight={300}>
      <CustomAgentTabs
        value={currentLeftTab}
        onChange={handleTabChange}
        tabs={TABS}
      />
      <ShowComponent condition={isCallInProgress}>
        <LoadingStateComponent message={loadingMessage} />
      </ShowComponent>

      <ShowComponent condition={!isCallInProgress}>
        <ShowComponent
          condition={currentLeftTab === LEFT_SECTION_TABS.TRANSCRIPT}
        >
          <UnifiedCallTranscript
            transcript={filteredTranscript}
            simulatorName={
              data?.module === "simulate"
                ? data?.simulatorName
                : data?.customer_name
            }
            agentName={
              data?.module === "simulate"
                ? data?.agentName
                : "User/FAGI Simulator"
            }
            endReason={data?.endedReason}
            callType={data?.call_type}
            simulationCallType={data?.simulationCallType}
          />
        </ShowComponent>
        <ShowComponent
          condition={currentLeftTab === LEFT_SECTION_TABS.MESSAGES}
        >
          <CallLogMessages data={data} />
        </ShowComponent>
        <ShowComponent condition={currentLeftTab === LEFT_SECTION_TABS.TRACES}>
          <TestDetailTraceSection data={data} />
        </ShowComponent>
        <ShowComponent
          condition={currentLeftTab === LEFT_SECTION_TABS.ATTRIBUTES}
        >
          <Box sx={{ width: "100%" }}>
            <BottomAttributesTab
              observationSpan={
                isSimulateModule &&
                data?.simulationCallType === AGENT_TYPES.CHAT
                  ? data?.trace_details?.attributes ??
                    data?.traceDetails?.attributes
                  : observationSpan
              }
            />
          </Box>
        </ShowComponent>
        <ShowComponent condition={currentLeftTab === LEFT_SECTION_TABS.LOGS}>
          <CallDetailLogs
            module={data?.module}
            callLogId={data?.id}
            vapiId={getSpanAttributes(observationSpan?.[0])?.rawLog?.id}
            callLogs={getSpanAttributes(observationSpan?.[0])?.callLogs}
          />
        </ShowComponent>
      </ShowComponent>
    </Stack>
  );
};

LeftSectionBottom.propTypes = {
  data: PropTypes.object.isRequired,
};

export default LeftSectionBottom;
