import { Box, Button, Typography, useTheme } from "@mui/material";
import React, { lazy, Suspense, useMemo, useState } from "react";
import PropTypes from "prop-types";
import CallLogTitle from "./CallLogTitle";
import { formatDuration } from "src/utils/format-time";
import { format } from "date-fns";
import TranscriptPreview from "./TranscriptPreview";
import { Icon } from "@iconify/react";
import { ShowComponent } from "../../../components/show";
import CallStatus from "./CallStatus";
import { useParams } from "react-router";
import { Events, PropertyName, trackEvent } from "src/utils/Mixpanel";
import AudioDownloadButton from "src/sections/test-detail/AudioDownloadButton";
import CustomTooltip from "src/components/tooltip";
import TestAudioPlayer from "src/components/custom-audio/TestAudioPlayer";
import { AGENT_TYPES } from "src/sections/agents/constants";
import { formatDurationSafe } from "src/components/CallLogsDrawer/CustomCallLogHeader";
import { normalizeRecordings } from "src/utils/utils";
const ViewFullTranscript = lazy(() => import("./ViewFullTranscript"));

const CallLogsCard = ({ log }) => {
  const theme = useTheme();
  const { testId } = useParams();
  const [open, setOpen] = useState(false);
  const simulationCallType =
    log?.simulation_call_type;
  const callType = log?.call_type;
  const agentDefinitionUsedName =
    log?.agent_definition_used_name;
  const simulatorAgentName =
    log?.simulator_agent_name;
  const overallScore = log?.overall_score ;
  const audioUrl = log?.audio_url;
  const filteredTranscript = useMemo(() => {
    const originalTranscript = log?.transcript;
    return originalTranscript?.filter(
      (item) => item.speaker_role !== "system",
    );
  }, [log]);

  const audioData = useMemo(() => ({ url: audioUrl }), [audioUrl]);
  const audioUrls = useMemo(
    () => normalizeRecordings(log?.recordings),
    [log?.recordings],
  );
  const audioFilename = `recording-${log?.id || "audio"}.wav`;

  return (
    <Box
      sx={{
        display: "flex",
        flexDirection: "column",
        gap: 2,
        padding: 2,
        border: "1px solid",
        borderColor: "divider",
        borderRadius: 1,
      }}
    >
      <Box
        sx={{
          display: "flex",
          gap: 1,
          justifyContent: "space-between",
          alignItems: "center",
        }}
      >
        <Box sx={{ display: "flex", gap: 1, alignItems: "center" }}>
          <CallLogTitle
            simulationCallType={simulationCallType}
            callType={callType}
          />
          <CallStatus value={log?.status} />
          {/* <CallSentiment sentiment={log.sentiment} /> */}
        </Box>
        <Box sx={{ display: "flex", gap: 1, alignItems: "center" }}>
          <Typography
            typography="s2"
            fontWeight="fontWeightMedium"
            color="text.disabled"
          >
            {format(new Date(log?.timestamp), "yyyy-MM-dd HH:mm")}
          </Typography>
          <ShowComponent
            condition={overallScore !== null && overallScore !== undefined}
          >
            <CustomTooltip
              show={true}
              arrow={true}
              title={"Customer Satisfaction Score"}
            >
              <Typography
                typography="s2"
                fontWeight="fontWeightMedium"
                color="text.disabled"
                sx={{ cursor: "pointer" }}
              >
                CSAT :{" "}
                {formatDuration(log?.duration) === "0s"
                  ? "N/A"
                  : `${overallScore} / 10`}
              </Typography>
            </CustomTooltip>
          </ShowComponent>
        </Box>
      </Box>
      <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
        <Typography
          typography="s2"
          sx={{
            border: "1px solid",
            borderColor: "divider",
            display: "inline",
            borderRadius: 1,
            padding: "4px 8px",
          }}
        >
          {log?.scenario}
        </Typography>
        <ShowComponent
          condition={log?.duration !== null || log?.duration !== undefined}
        >
          <Typography typography="s2" color="text.disabled">
            Duration : {formatDurationSafe(log?.duration)}
          </Typography>
        </ShowComponent>
      </Box>
      <TranscriptPreview
        transcript={filteredTranscript}
        agentName={agentDefinitionUsedName}
        simulatorName={simulatorAgentName}
        callType={callType}
        simulationCallType={simulationCallType}
      />
      <Box sx={{ display: "flex", gap: 1, alignItems: "center" }}>
        <ShowComponent
          condition={audioUrl && simulationCallType === AGENT_TYPES.CHAT}
        >
          <Box
            className="audio-control-btn"
            onClick={(e) => e.stopPropagation()}
            sx={{
              maxHeight: "60px",
              width: "300px",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
            }}
          >
            <TestAudioPlayer audioData={audioData} showFileName={false} />
          </Box>
        </ShowComponent>
        <ShowComponent condition={simulationCallType === AGENT_TYPES.VOICE}>
          <AudioDownloadButton
            size="small"
            disabled={formatDuration(log?.duration) === "0s"}
            audioUrls={audioUrls}
            filename={audioFilename}
          />
        </ShowComponent>

        <Button
          variant="outlined"
          size="small"
          disabled={
            formatDuration(log?.duration) === "0s" ||
            filteredTranscript?.length === 0
          }
          onClick={() => {
            setOpen(true);
            trackEvent(Events.runTestViewTranscriptClicked, {
              [PropertyName.id]: log?.id,
              [PropertyName.propId]: testId,
            });
          }}
          startIcon={
            <Box
              component={Icon}
              icon="solar:eye-outline"
              width={16}
              height={16}
              sx={{ color: theme.palette.text.primary }}
            />
          }
        >
          View Full Transcript
        </Button>
        {/* <Button
          variant="outlined"
          size="small"
          onClick={() => {}}
          startIcon={
            <Box
              component={Icon}
              icon="ri:share-line"
              width={16}
              height={16}
              sx={{ color: theme.palette.text.primary }}
            />
          }
        >
          Share
        </Button> */}
      </Box>
      <Suspense fallback={null}>
        <ViewFullTranscript
          open={open}
          onClose={() => setOpen(false)}
          transcript={filteredTranscript}
          agentName={agentDefinitionUsedName}
          simulatorName={simulatorAgentName}
          simulationCallType={simulationCallType}
        />
      </Suspense>
    </Box>
  );
};

CallLogsCard.propTypes = {
  log: PropTypes.object,
};

export default React.memo(CallLogsCard);
