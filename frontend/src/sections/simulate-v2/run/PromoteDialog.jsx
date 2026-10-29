import PropTypes from "prop-types";
import { useEffect, useState } from "react";
import {
  Box,
  Button,
  Checkbox,
  FormControlLabel,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import { ConfirmDialog } from "src/components/custom-dialog";

const box = { border: "1px solid", borderColor: "divider", borderRadius: 1 };
const rowSx = {
  px: 1.5,
  py: 0.75,
  "& + &": { borderTop: "1px solid", borderColor: "divider" },
};

/* A label and its value, one line of the review. */
function Line({ k, children }) {
  return (
    <Stack direction="row" spacing={1.5} sx={rowSx}>
      <Typography
        sx={{
          typography: "s2",
          color: "text.tertiary",
          width: 96,
          flexShrink: 0,
        }}
      >
        {k}
      </Typography>
      <Typography sx={{ typography: "s2", flex: 1, minWidth: 0 }}>
        {children}
      </Typography>
    </Stack>
  );
}
Line.propTypes = { k: PropTypes.string, children: PropTypes.node };

/**
 * Promote after review: the exact candidate version, what it's promoted
 * over, the verdict, and every must-pass regression — which has to be
 * acknowledged, recorded with the promotion. The baseline stays available.
 */
export default function PromoteDialog({
  open,
  candidate,
  baseline,
  verdict,
  regressions,
  onClose,
  onPromote,
}) {
  const [ack, setAck] = useState(false);
  const [note, setNote] = useState("");
  useEffect(() => {
    if (!open) return;
    setAck(false);
    setNote("");
  }, [open]);
  if (!open) return null;
  const needsAck = regressions.length > 0;
  return (
    <ConfirmDialog
      open
      onClose={onClose}
      PaperProps={{ sx: { maxWidth: 560 } }}
      title={`Promote agent ${candidate}`}
      content={
        <Stack spacing={1.5}>
          <Box sx={box}>
            <Line k="Candidate">{`Agent ${candidate}`}</Line>
            <Line k="Over">{`Agent ${baseline} — stays available; Roll back returns to it`}</Line>
            <Line k="Verdict">{verdict}</Line>
          </Box>
          {needsAck && (
            <Box>
              <Typography
                sx={{
                  typography: "s2",
                  fontWeight: 600,
                  color: "status.danger.fg",
                  mb: 0.5,
                }}
              >
                {`${regressions.length} must-pass regression${regressions.length === 1 ? "" : "s"}`}
              </Typography>
              <Box sx={box}>
                {regressions.map((r) => (
                  <Typography key={r.id} sx={{ ...rowSx, typography: "s2" }}>
                    {`${r.title} — passed on ${baseline}, fails on ${candidate}`}
                  </Typography>
                ))}
              </Box>
              <FormControlLabel
                sx={{
                  mt: 0.5,
                  alignItems: "flex-start",
                  "& .MuiFormControlLabel-label": {
                    typography: "s2",
                    pt: "2px",
                  },
                }}
                control={
                  <Checkbox
                    size="small"
                    checked={ack}
                    onChange={(e) => setAck(e.target.checked)}
                  />
                }
                label="I reviewed these regressions and still want to promote. This review is recorded with the promotion."
              />
            </Box>
          )}
          <TextField
            size="small"
            fullWidth
            value={note}
            onChange={(e) => setNote(e.target.value)}
            placeholder={
              needsAck
                ? "Why it's still the right call (recorded)"
                : "Note for the release (optional)"
            }
            inputProps={{ "aria-label": "Promotion note" }}
            sx={{ "& .MuiInputBase-input": { typography: "s2" } }}
          />
          <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
            Promoting records a release of this version. Environments following
            production move to it; nothing else changes the agent.
          </Typography>
        </Stack>
      }
      action={
        <Button
          size="small"
          variant="contained"
          color="primary"
          disabled={needsAck && (!ack || !note.trim())}
          onClick={() =>
            onPromote({
              note: note.trim(),
              reviewed: regressions.map((r) => r.id),
            })
          }
        >
          {`Promote ${candidate}`}
        </Button>
      }
    />
  );
}
PromoteDialog.propTypes = {
  open: PropTypes.bool,
  candidate: PropTypes.string,
  baseline: PropTypes.string,
  verdict: PropTypes.string,
  regressions: PropTypes.array.isRequired,
  onClose: PropTypes.func.isRequired,
  onPromote: PropTypes.func.isRequired,
};
