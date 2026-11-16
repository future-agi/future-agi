import PropTypes from "prop-types";
import { useState } from "react";
import {
  Box,
  Button,
  InputBase,
  MenuItem,
  TextField,
  Typography,
} from "@mui/material";
import { enqueueSnackbar } from "notistack";

import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import {
  fetchCandidatePatch,
  fixErrorOf,
  patchFileName,
  useShipCandidate,
} from "src/api/simulate-environments/fixRuns";

import { downloadText } from "./downloadText";
import ShipConfirmDialog, { Row } from "./ShipConfirmDialog";
import { isShipped, overridableOf, winnerOf } from "./fixRunView";

const REASON_MAX = 2000;

// Ship refusals in the product's words; the server's are written for logs.
const SHIP_ERROR_COPY = {
  fix_override_reason_required: "Give a reason to ship this trial anyway.",
  fix_override_not_needed:
    "This trial passed every check. Ship it without a reason.",
  fix_candidate_not_proven:
    "This trial can't ship: it failed more than a must-pass check.",
  fix_run_active: "Wait for the improvement to finish, then ship.",
};

const shipHint = (fix, candidate, anyway) => {
  if (anyway) {
    return `Ships Trial ${candidate.attempt} although it broke must-pass checks, with your reason`;
  }
  return fix.agent?.kind === "github"
    ? `Opens a pull request with Trial ${candidate.attempt}'s change on the agent's repository`
    : `Makes Trial ${candidate.attempt}'s version the environment's default`;
};

const trialLabel = (c) =>
  `Trial ${c.attempt} · ${c.outcome === "winner" ? "Winner" : "Broke must-pass checks"}`;

// Which trial ships, when more than one can: the winner first, then each trial
// only its must-pass check refused.
function TrialPicker({ trials, picked, onPick }) {
  return (
    <Row label="Trial">
      {/* The house picker, as RunDialog's version fields. */}
      <TextField
        select
        size="small"
        fullWidth
        value={picked.id}
        onChange={(e) => onPick(e.target.value)}
        inputProps={{ "aria-label": "Trial to ship" }}
      >
        {trials.map((c) => (
          <MenuItem key={c.id} value={c.id}>
            {trialLabel(c)}
          </MenuItem>
        ))}
      </TextField>
    </Row>
  );
}
TrialPicker.propTypes = {
  trials: PropTypes.array.isRequired,
  picked: PropTypes.object.isRequired,
  onPick: PropTypes.func.isRequired,
};

// A trial shipped anyway: the must-pass checks it broke, and the reason, which
// is required.
function AnywayFields({ picked, reason, onReason }) {
  return (
    <>
      <Row label="Broke">
        {picked.override.regressions.map((r) => (
          <Typography key={r} sx={{ typography: "s2" }}>
            {`Must-pass ${r}`}
          </Typography>
        ))}
      </Row>
      <Row label="Reason">
        <InputBase
          multiline
          minRows={3}
          fullWidth
          value={reason}
          onChange={(e) => onReason(e.target.value)}
          placeholder="Why ship it anyway? Required, and kept with your name."
          inputProps={{
            maxLength: REASON_MAX,
            "aria-label": "Why ship it anyway?",
          }}
          sx={{
            typography: "s2",
            border: "1px solid",
            borderColor: "divider",
            borderRadius: "6px",
            px: 1.25,
            py: 1,
          }}
        />
        <Typography sx={{ typography: "s3", color: "text.subtitle", mt: 0.5 }}>
          {`${reason.length}/${REASON_MAX}`}
        </Typography>
      </Row>
    </>
  );
}
AnywayFields.propTypes = {
  picked: PropTypes.object.isRequired,
  reason: PropTypes.string.isRequired,
  onReason: PropTypes.func.isRequired,
};

// One Ship for every trial that can ship: the winner once the fix is ready, and
// any trial only its must-pass check refused, on the user's reason. With no
// winner it reads Ship anyway. It acts only after the user confirms. An
// uploaded agent's trial becomes the environment's default; a GitHub agent's
// opens a pull request, with the patch as the way out when the repository
// cannot take one.
export default function ShipButton({ fix }) {
  const winner = fix.state === "ready" ? winnerOf(fix) : null;
  const trials = [winner, ...overridableOf(fix)].filter(Boolean);
  const ship = useShipCandidate(fix);
  const [offerPatch, setOfferPatch] = useState(false);
  const [busy, setBusy] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [pickedId, setPickedId] = useState(null);
  const [reason, setReason] = useState("");
  const shipped = trials.find((c) => c.pull_request_url || isShipped(fix, c));
  const candidate = trials.find((c) => c.id === pickedId) || trials[0];
  if (!candidate) return null;
  const anyway = candidate.outcome !== "winner";

  if (shipped?.pull_request_url) {
    return (
      <Button
        size="small"
        variant="contained"
        href={shipped.pull_request_url}
        target="_blank"
        rel="noopener noreferrer"
        endIcon={<Iconify icon="solar:arrow-right-up-linear" width={14} />}
      >
        View pull request
      </Button>
    );
  }
  if (shipped) {
    // Named, since the page leads with the winner and a trial shipped anyway may be another.
    return (
      <Button size="small" variant="contained" disabled>
        {`Trial ${shipped.attempt} is the default`}
      </Button>
    );
  }
  if (offerPatch) {
    return (
      <Button
        size="small"
        variant="outlined"
        startIcon={<Iconify icon="solar:download-linear" width={16} />}
        onClick={async () =>
          downloadText(
            patchFileName(fix.id, candidate.attempt),
            await fetchCandidatePatch(fix.id, candidate.id),
          )
        }
      >
        Download patch
      </Button>
    );
  }
  const shipIt = async () => {
    setBusy(true);
    try {
      await ship.mutateAsync({
        candidateId: candidate.id,
        overrideReason: anyway ? reason.trim() : undefined,
      });
      setConfirming(false);
    } catch (error) {
      const parsed = fixErrorOf(error);
      if (parsed.offerPatch) setOfferPatch(true);
      setConfirming(false);
      const message = SHIP_ERROR_COPY[parsed.code] || parsed.message;
      enqueueSnackbar(message || "Could not ship this change", {
        variant: "error",
      });
    } finally {
      setBusy(false);
    }
  };
  return (
    <>
      <CustomTooltip
        show
        title={shipHint(fix, trials[0], !winner)}
        size="small"
        arrow
      >
        <Box component="span">
          <Button
            size="small"
            variant="contained"
            color="primary"
            disabled={busy}
            startIcon={<Iconify icon="solar:upload-linear" width={16} />}
            onClick={() => setConfirming(true)}
          >
            {winner ? "Ship" : "Ship anyway"}
          </Button>
        </Box>
      </CustomTooltip>
      <ShipConfirmDialog
        fix={fix}
        candidate={candidate}
        open={confirming}
        busy={busy}
        confirmDisabled={anyway && !reason.trim()}
        onClose={() => setConfirming(false)}
        onConfirm={shipIt}
        picker={
          trials.length > 1 && (
            <TrialPicker
              trials={trials}
              picked={candidate}
              onPick={setPickedId}
            />
          )
        }
      >
        {anyway && (
          <AnywayFields
            picked={candidate}
            reason={reason}
            onReason={setReason}
          />
        )}
      </ShipConfirmDialog>
    </>
  );
}

ShipButton.propTypes = { fix: PropTypes.object.isRequired };
