import PropTypes from "prop-types";
import { useState } from "react";
import {
  Box,
  Button,
  DialogActions,
  DialogContent,
  DialogTitle,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import ClosableDialog from "../../components/ClosableDialog";

/**
 * An improvement that ended on "Needs clarification": the goal is ambiguous
 * or the targets conflict with a rule. It names the question; there is
 * nothing to ship until it's answered and the improvement restarted.
 */
export default function ClarifyBody({ outcome, onRestart }) {
  const [open, setOpen] = useState(false);
  const [answer, setAnswer] = useState("");
  const restart = () => {
    onRestart(answer.trim());
    setOpen(false);
  };
  return (
    <Box sx={{ flex: 1, overflowY: "auto", px: 2.5, py: 2.5 }}>
      <Typography sx={{ typography: "s1", fontWeight: 600 }}>
        {outcome.ending.label}
      </Typography>
      <Typography
        sx={{
          typography: "s2",
          color: "text.secondary",
          mt: 0.5,
          maxWidth: 640,
        }}
      >
        {outcome.headline}
      </Typography>
      <Typography sx={{ typography: "s3", color: "text.tertiary", mt: 0.5 }}>
        Nothing to ship.
      </Typography>
      <Stack direction="row" spacing={1} sx={{ mt: 2 }}>
        <Button
          size="small"
          variant="contained"
          color="primary"
          onClick={() => setOpen(true)}
        >
          Answer and restart
        </Button>
      </Stack>
      <ClosableDialog
        open={open}
        onClose={() => setOpen(false)}
        maxWidth="xs"
        fullWidth
      >
        <DialogTitle sx={{ typography: "s1", fontWeight: 600 }}>
          Answer and restart
        </DialogTitle>
        <DialogContent>
          <Typography
            sx={{ typography: "s2", color: "text.secondary", mb: 1.5 }}
          >
            {outcome.headline}
          </Typography>
          <TextField
            size="small"
            fullWidth
            multiline
            minRows={2}
            autoFocus
            label="Your answer"
            value={answer}
            onChange={(e) => setAnswer(e.target.value)}
            InputProps={{ sx: { typography: "s2" } }}
          />
        </DialogContent>
        <DialogActions>
          <Button size="small" onClick={() => setOpen(false)}>
            Cancel
          </Button>
          <Button
            size="small"
            variant="contained"
            color="primary"
            disabled={!answer.trim()}
            onClick={restart}
          >
            Restart
          </Button>
        </DialogActions>
      </ClosableDialog>
    </Box>
  );
}
ClarifyBody.propTypes = {
  outcome: PropTypes.object.isRequired,
  onRestart: PropTypes.func.isRequired,
};
