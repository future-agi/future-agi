import PropTypes from "prop-types";
import { useMemo } from "react";
import {
  Box,
  CircularProgress,
  IconButton,
  Stack,
  Typography,
} from "@mui/material";

import Iconify from "src/components/iconify";
import {
  callTranscript,
  useCallExecutionV3Detail,
} from "src/api/simulate-environments/runDetail";
import VoiceDetailDrawerV2 from "src/components/VoiceDetailDrawerV2";

// The annotate / queue / dataset / tags actions need project + annotation
// context this embedded drawer doesn't wire; hide them rather than leave dead
// menu items (Download still works from the payload alone).
const HIDDEN_VOICE_ACTIONS = ["annotate", "queue", "dataset", "tags"];

// A minimal centred pane for the pre-hydrate / error states. Carries its own
// close (the outer drawer's only other escape is a backdrop click).
function StatePane({ children, onClose }) {
  return (
    <Stack
      sx={{
        width: "60vw",
        maxWidth: "100%",
        height: "100%",
        position: "relative",
      }}
      alignItems="center"
      justifyContent="center"
      spacing={1.5}
    >
      <IconButton
        aria-label="Close"
        size="small"
        onClick={onClose}
        sx={{ position: "absolute", top: 8, right: 8, color: "text.subtitle" }}
      >
        <Iconify icon="mingcute:close-line" width={18} />
      </IconButton>
      {children}
    </Stack>
  );
}
StatePane.propTypes = { children: PropTypes.node, onClose: PropTypes.func };

// The voice branch reuses the REAL product voice drawer unchanged. It reads the
// same v3 `/simulate/v3/call-executions/{id}/` detail the chat branch does, so
// audio/transcript/evals and normalized function calls share one cache, then
// hands the payload straight to
// `VoiceDetailDrawerV2` — a content-only component that renders its own header,
// recording player and analytics. We only tag `module`/`origin` = "simulate"
// (the fields the panels branch on) and hide the annotation actions.
export default function VoiceCallDrawer({ task, onClose }) {
  const { data, isPending, isError } = useCallExecutionV3Detail(
    task.id,
    !!task.id,
  );

  const voiceData = useMemo(
    () =>
      data
        ? {
            ...data,
            transcript: callTranscript(data),
            module: "simulate",
            origin: "simulate",
          }
        : null,
    [data],
  );

  if (isError) {
    return (
      <StatePane onClose={onClose}>
        <Iconify
          icon="solar:danger-triangle-linear"
          width={22}
          sx={{ color: "text.subtitle" }}
        />
        <Typography sx={{ typography: "s2", color: "text.subtitle" }}>
          Couldn&rsquo;t load this call.
        </Typography>
      </StatePane>
    );
  }

  if (!voiceData) {
    return (
      <StatePane onClose={onClose}>
        <CircularProgress size={22} />
      </StatePane>
    );
  }

  return (
    <Box sx={{ height: "100%", display: "flex" }}>
      <VoiceDetailDrawerV2
        data={voiceData}
        onClose={onClose}
        scenarioId={data?.scenario_id}
        isLoading={isPending}
        hiddenActionIds={HIDDEN_VOICE_ACTIONS}
      />
    </Box>
  );
}
VoiceCallDrawer.propTypes = {
  task: PropTypes.shape({ id: PropTypes.string }).isRequired,
  onClose: PropTypes.func,
};
