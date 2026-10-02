import React from "react";
import { Box, Typography } from "@mui/material";
import PropTypes from "prop-types";
import CallStatus from "../../test/CallLogs/CallStatus";
// Used only by the disabled live-call "Listen" button below.
// import { CallStatus as CallStatusValues } from "../../test/CallLogs/common";
import { formatDuration } from "src/utils/format-time";
import _ from "lodash";
import { ShowComponent } from "../../../components/show";
import { AGENT_TYPES } from "src/sections/agents/constants";
import { formatStartTimeByRequiredFormat } from "src/utils/utils";
// Used only by the disabled live-call "Listen" button below.
// import Iconify from "src/components/iconify";

const CallDetailCellRenderer = (props) => {
  const data = props?.value;
  // Used only by the disabled live-call "Listen" button below.
  // const rowData = props?.data; // Full row data from AG Grid
  // const filteredTranscript = useMemo(() => {
  //   return data?.transcript?.filter((item) => item.speakerRole !== "system");
  // }, [data]);
  const simulationCallType = data?.simulation_call_type;
  const agentType =
    simulationCallType === AGENT_TYPES.CHAT
      ? AGENT_TYPES.CHAT
      : AGENT_TYPES.VOICE;
  const customerName = data?.customer_name;
  // const callId = data?.call_id; // used only by the disabled Listen button
  const turnCount = data?.turn_count;

  const formattedStartTime = formatStartTimeByRequiredFormat(
    data?.start_time,
    "dd-MM-yyyy HH:mm:ss",
  );

  return (
    <Box
      sx={{
        height: "100%",
        display: "flex",
        flexDirection: "column",
        gap: 1,
        width: "100%",
      }}
    >
      <Box sx={{ display: "flex", gap: 0.5, alignItems: "center" }}>
        <ShowComponent condition={agentType === AGENT_TYPES.VOICE}>
          <Box>
            <Typography fontWeight="fontWeightMedium" variant="s2">
              {customerName}
            </Typography>
          </Box>
        </ShowComponent>

        <CallStatus value={data.status} />
        {/*
          Live-call "Listen" button disabled. It opens LiveCallMonitor, which
          joins the call's LiveKit room via the listener-token endpoint
          (provider_call_data.livekit.room_name). Hosted-runner sims don't
          register that room during ONGOING, so the monitor would join an empty
          room. Re-enable (and add a joinable-room gate) once hosted sims expose
          one.

        {agentType === AGENT_TYPES.VOICE &&
          data.status === CallStatusValues.ONGOING &&
          (rowData?.id || callId) &&
          !(data?.phone_number ?? rowData?.phone_number) && (
            <Box
              component="button"
              data-listen-btn="true"
              onClick={(e) => {
                e.stopPropagation();
                e.preventDefault();
                window.dispatchEvent(
                  new CustomEvent("open-live-call-monitor", {
                    detail: { callId: rowData?.id || callId },
                  }),
                );
              }}
              sx={{
                display: "flex",
                alignItems: "center",
                gap: 0.5,
                px: 1,
                py: 0.5,
                border: "none",
                borderRadius: 0.5,
                bgcolor: "blue.o10",
                cursor: "pointer",
                color: "blue.600",
                fontSize: 12,
                fontWeight: 600,
                "&:hover": { bgcolor: "blue.o10", opacity: 0.8 },
              }}
            >
              <Iconify icon="mdi:headphones" width={14} />
              Listen
            </Box>
          )}
        */}
      </Box>
      <ShowComponent
        condition={data?.ended_reason && agentType === AGENT_TYPES.VOICE}
      >
        <Box>
          <Typography typography="s3">
            End Reason : {data.ended_reason}
          </Typography>
        </Box>
      </ShowComponent>
      <ShowComponent condition={agentType === AGENT_TYPES.VOICE}>
        <Box>
          <Typography typography="s3">
            Duration : {formatDuration(data.duration)}
          </Typography>
        </Box>
      </ShowComponent>
      <ShowComponent
        condition={agentType === AGENT_TYPES.CHAT && !!formattedStartTime}
      >
        Start time: {formattedStartTime}
      </ShowComponent>

      {/* <ShowComponent
        condition={filteredTranscript?.[0] && agentType === AGENT_TYPES.VOICE}
      >
        <Box
          sx={{
            whiteSpace: "nowrap",
            overflow: "hidden",
            textOverflow: "ellipsis",
          }}
        >
          <Typography typography="s2" color="text.disabled">
            {formatRole(
              filteredTranscript?.[0]?.speakerRole,
              data?.agentDefinitionUsedName,
              data?.simulator_agent_name,
              data?.call_type,
            )}{" "}
            : {filteredTranscript?.[0]?.content}
          </Typography>
        </Box>
      </ShowComponent> */}
      <ShowComponent condition={agentType === AGENT_TYPES.CHAT && turnCount}>
        <Box>
          <Typography typography="s2" color="text.primary">
            No of Turns: {turnCount}
          </Typography>
        </Box>
      </ShowComponent>
    </Box>
  );
};

CallDetailCellRenderer.propTypes = {
  value: PropTypes.object,
  data: PropTypes.object,
};

export default CallDetailCellRenderer;
