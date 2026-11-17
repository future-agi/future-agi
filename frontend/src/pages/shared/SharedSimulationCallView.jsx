import React, { useMemo } from "react";
import PropTypes from "prop-types";
import { Alert, Box, Stack } from "@mui/material";
import {
  callTranscript,
  mapCallDetail,
} from "src/api/simulate-environments/runDetail";
import ChatTranscriptPane from "src/sections/simulate/environments/workspace/runs/detail/ChatTranscriptPane";
import { Meta } from "src/sections/simulate/environments/workspace/runs/detail/chatDrawerCells";
import SharedVoiceView from "./SharedVoiceView";
import { simulationCallKind } from "./sharedViewHelpers";

const secs = (s) => (s == null ? null : `${Number(s).toFixed(1)}s`);

/**
 * Read-only view of a shared simulation call. `call` is the v3
 * call-execution detail payload; voice reuses the shared voice view (with
 * the same simulate-shaped data the environment voice drawer builds), chat
 * renders the environment chat transcript.
 */
const SharedSimulationCallView = ({ call }) => {
  const kind = simulationCallKind(call);

  const voiceData = useMemo(
    () =>
      call && kind === "voice"
        ? {
            ...call,
            transcript: callTranscript(call),
            module: "simulate",
            origin: "simulate",
          }
        : null,
    [call, kind],
  );
  const chatDetail = useMemo(
    () => (call && kind === "chat" ? mapCallDetail(call) : null),
    [call, kind],
  );

  if (!call) {
    return (
      <Box sx={{ p: 3 }}>
        <Alert severity="warning">This shared call has no data.</Alert>
      </Box>
    );
  }

  const meta = [
    { label: "Scenario", value: call.scenario },
    ...(kind === "chat"
      ? [
          { label: "Status", value: call.status },
          {
            label: "Turns",
            value: chatDetail?.stats.turnCount ?? chatDetail?.turns.length,
          },
          { label: "Duration", value: secs(chatDetail?.durationS) },
        ]
      : []),
  ].filter((m) => m.value != null && m.value !== "");

  return (
    <Box
      sx={{
        display: "flex",
        flexDirection: "column",
        flex: 1,
        minHeight: 0,
        overflow: "hidden",
      }}
    >
      {meta.length > 0 && (
        <Stack
          direction="row"
          flexWrap="wrap"
          gap={0.75}
          sx={{
            px: 2,
            py: 1.25,
            borderBottom: "1px solid",
            borderColor: "divider",
            flexShrink: 0,
          }}
        >
          {meta.map((m) => (
            <Meta key={m.label} label={m.label} value={m.value} />
          ))}
        </Stack>
      )}
      {kind === "voice" ? (
        <SharedVoiceView voiceData={voiceData} />
      ) : (
        <Box sx={{ flex: 1, minHeight: 0, overflow: "auto" }}>
          <ChatTranscriptPane turns={chatDetail?.turns || []} />
        </Box>
      )}
    </Box>
  );
};

SharedSimulationCallView.propTypes = {
  call: PropTypes.object,
};

export default SharedSimulationCallView;
