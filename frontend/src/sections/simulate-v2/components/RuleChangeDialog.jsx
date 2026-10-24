import PropTypes from "prop-types";
import { useMemo } from "react";
import { Box, Dialog, IconButton, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { useSimStore } from "../store";
import { openQuestionsIn } from "../_mock/specChange";
import NudgeCard from "../workspace/NudgeCard";

/**
 * The fix-or-policy question (PRD v7 §4.10.6), opened from a screen that
 * needs it answered — Compare, for one. It isn't a separate flow: it shows the
 * same rule nudge cards the environment's "Needs input" inbox holds,
 * answered the same way (Policy change / Not intended) and never by
 * minting an environment version. Closing it leaves them open there.
 */
export default function RuleChangeDialog({
  open,
  onClose,
  envs,
  version,
  origin,
}) {
  const { state, dispatch } = useSimStore();
  const queue = useMemo(
    () => (open ? openQuestionsIn(state, envs, version) : []),
    [open, state, envs, version],
  );
  const multiEnv = (envs || []).length > 1;

  return (
    <Dialog
      open={!!open && queue.length > 0}
      onClose={onClose}
      PaperProps={{
        sx: {
          borderRadius: 2,
          maxWidth: 720,
          width: "100%",
          bgcolor: "background.paper",
          backgroundImage: "none",
          border: "1px solid",
          borderColor: "divider",
        },
      }}
    >
      <Stack
        direction="row"
        alignItems="flex-start"
        spacing={1.25}
        sx={{ px: 2.5, pt: 2.25, pb: 1.5 }}
      >
        <Box
          sx={{
            width: 30,
            height: 30,
            borderRadius: 1,
            flexShrink: 0,
            display: "grid",
            placeItems: "center",
            bgcolor: "action.selected",
            color: "text.secondary",
          }}
        >
          <Iconify icon="solar:scale-linear" width={16} />
        </Box>
        <Box flex={1} minWidth={0}>
          <Typography sx={{ typography: "m3", fontWeight: 600 }}>
            {`${version?.label || "This version"} · ${queue.length} rule question${queue.length === 1 ? "" : "s"}`}
          </Typography>
          <Typography
            sx={{ typography: "s3", color: "text.subtitle", mt: 0.25 }}
          >
            {[
              origin,
              "Answer here or later in each environment's Needs input — nothing changes until you do",
            ]
              .filter(Boolean)
              .join(" · ")}
          </Typography>
        </Box>
        <IconButton size="small" onClick={onClose} sx={{ mt: -0.5, mr: -0.5 }}>
          <Iconify icon="solar:close-linear" width={20} />
        </IconButton>
      </Stack>
      <Box
        sx={{
          borderTop: "1px solid",
          borderColor: "divider",
          maxHeight: "70vh",
          overflowY: "auto",
        }}
      >
        {queue.map(({ env, envState, impact }, i) => (
          <Box
            key={impact.nudge.id}
            sx={{ borderTop: i ? "1px solid" : "none", borderColor: "divider" }}
          >
            {multiEnv && (
              <Typography
                sx={{
                  px: 2.5,
                  pt: 1,
                  typography: "s3",
                  fontWeight: 600,
                  color: "text.subtitle",
                }}
              >
                {env.name}
              </Typography>
            )}
            <NudgeCard
              nudge={impact.nudge}
              env={env}
              envState={envState}
              patch={(p) =>
                dispatch({ type: "patchEnvState", envId: env.id, patch: p })
              }
              onNavigate={onClose}
              defaultOpen={i === 0}
            />
          </Box>
        ))}
      </Box>
    </Dialog>
  );
}

RuleChangeDialog.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  /* The environments to ask in — each has its own sub-goals. */
  envs: PropTypes.array,
  version: PropTypes.object,
  /* Where the version came from, e.g. "Someone edited the assistant on Vapi". */
  origin: PropTypes.string,
};
