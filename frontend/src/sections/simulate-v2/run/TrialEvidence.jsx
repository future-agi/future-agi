import PropTypes from "prop-types";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { Box, Button, Stack, TextField, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { paths } from "src/routes/paths";
import { TextLink } from "../agents/connect/connectBits";
import {
  addDisputePatch,
  checkSourceOf,
  disputesOf,
} from "../_mock/trialEvidence";

const head = {
  typography: "s3",
  fontWeight: 600,
  color: "text.tertiary",
  letterSpacing: 0.4,
};
const box = { border: "1px solid", borderColor: "divider", borderRadius: 1 };
const rowSx = {
  px: 1.5,
  py: 0.875,
  "& + &": { borderTop: "1px solid", borderColor: "divider" },
};
const day = (iso) =>
  new Date(iso).toLocaleDateString("en-US", { month: "short", day: "numeric" });

/**
 * The trial's artifacts: each one the adapter provided, opening the tab
 * that shows it, and each it didn't — labelled, with why.
 */
export function TrialArtifacts({ items, onOpen, hideOpen = false }) {
  return (
    <Stack sx={{ p: 2 }} spacing={1}>
      <Typography sx={head}>
        {`${items.filter((x) => x.recorded).length} of ${items.length} recorded`}
      </Typography>
      <Box sx={box}>
        {items.map((x) => (
          <Stack
            key={x.id}
            direction="row"
            alignItems="center"
            spacing={1.25}
            sx={rowSx}
          >
            <Iconify
              icon={
                x.recorded
                  ? "solar:check-circle-linear"
                  : "solar:minus-circle-linear"
              }
              width={14}
              sx={{
                color: x.recorded ? "text.secondary" : "text.disabled",
                flexShrink: 0,
              }}
            />
            <Box flex={1} minWidth={0}>
              <Typography sx={{ typography: "s2" }}>{x.label}</Typography>
              <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
                {x.note}
              </Typography>
            </Box>
            {x.recorded && x.pane && !hideOpen ? (
              <TextLink onClick={() => onOpen(x.pane)}>Open</TextLink>
            ) : (
              !x.recorded && (
                <Typography
                  sx={{
                    typography: "s3",
                    color: "text.disabled",
                    flexShrink: 0,
                  }}
                >
                  Not recorded
                </Typography>
              )
            )}
          </Stack>
        ))}
      </Box>
    </Stack>
  );
}
TrialArtifacts.propTypes = {
  items: PropTypes.array.isRequired,
  onOpen: PropTypes.func.isRequired,
  /* In a drawer whose tabs aren't ours to switch. */
  hideOpen: PropTypes.bool,
};

/** Where a failed check comes from: its rule, opened on Contract, and the scenario version it ran. */
export function CheckSource({ task, env, envState, run }) {
  const navigate = useNavigate();
  const { rule, version } = checkSourceOf(task, env, envState, run);
  if (!env?.id) return null;
  const go = (step, q = "") =>
    navigate(`${paths.dashboard.simulate.environmentStep(env.id, step)}${q}`);
  return (
    <Box sx={box}>
      {rule && (
        <Stack
          direction="row"
          alignItems="flex-start"
          spacing={1.25}
          sx={rowSx}
        >
          <Typography
            sx={{
              typography: "s3",
              color: "text.tertiary",
              width: 64,
              flexShrink: 0,
              pt: "2px",
            }}
          >
            {`Rule ${rule.id}`}
          </Typography>
          <Typography sx={{ typography: "s2", flex: 1, minWidth: 0 }}>
            {rule.text}
          </Typography>
          <TextLink onClick={() => go("contract", `?rule=${rule.id}`)}>
            Open rule
          </TextLink>
        </Stack>
      )}
      <Stack direction="row" alignItems="center" spacing={1.25} sx={rowSx}>
        <Typography
          sx={{
            typography: "s3",
            color: "text.tertiary",
            width: 64,
            flexShrink: 0,
          }}
        >
          Scenario
        </Typography>
        <Typography
          sx={{
            typography: "s2",
            flex: 1,
            minWidth: 0,
            color: "text.secondary",
          }}
        >
          {`Version ${version} — the version this trial ran`}
        </Typography>
        <TextLink onClick={() => go("scenarios")}>Open scenarios</TextLink>
      </Stack>
    </Box>
  );
}
CheckSource.propTypes = {
  task: PropTypes.object.isRequired,
  env: PropTypes.object,
  envState: PropTypes.object,
  run: PropTypes.object,
};

/**
 * Dispute a result: a note beside it, kept with who and when. The recorded
 * score is never rewritten — a re-grade or a re-run changes results.
 */
export function DisputeNotes({ envState, patch, runId, task, score }) {
  const [open, setOpen] = useState(false);
  const [note, setNote] = useState("");
  const notes = disputesOf(envState, runId, task.id);
  const save = () => {
    patch(addDisputePatch(envState, runId, task.id, note.trim(), score));
    setNote("");
    setOpen(false);
  };
  return (
    <Box>
      {notes.length > 0 && (
        <Box sx={{ ...box, mb: 1 }}>
          {notes.map((n) => (
            <Box key={n.at} sx={rowSx}>
              <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
                {`Disputed by ${n.by} · ${day(n.at)} · score kept as recorded${n.score != null ? ` (${n.score})` : ""}`}
              </Typography>
              <Typography sx={{ typography: "s2" }}>{n.note}</Typography>
            </Box>
          ))}
        </Box>
      )}
      {open ? (
        <Stack spacing={1}>
          <TextField
            size="small"
            fullWidth
            multiline
            minRows={2}
            autoFocus
            value={note}
            onChange={(e) => setNote(e.target.value)}
            placeholder="Why this result looks wrong — what the evidence shows"
            inputProps={{ "aria-label": "Dispute note" }}
            sx={{ "& .MuiInputBase-input": { typography: "s2" } }}
          />
          <Stack direction="row" spacing={1} justifyContent="flex-end">
            <Button
              size="small"
              onClick={() => setOpen(false)}
              sx={{ color: "text.secondary" }}
            >
              Cancel
            </Button>
            <Button
              size="small"
              variant="contained"
              disabled={!note.trim() || !patch}
              onClick={save}
            >
              Add note
            </Button>
          </Stack>
          <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
            The score stays as graded. Re-grade or re-run to change results.
          </Typography>
        </Stack>
      ) : (
        patch && (
          <TextLink onClick={() => setOpen(true)}>
            {notes.length ? "Add another note" : "Dispute this result"}
          </TextLink>
        )
      )}
    </Box>
  );
}
DisputeNotes.propTypes = {
  envState: PropTypes.object,
  patch: PropTypes.func,
  runId: PropTypes.string,
  task: PropTypes.object.isRequired,
  score: PropTypes.oneOfType([PropTypes.number, PropTypes.string]),
};
