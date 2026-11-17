import PropTypes from "prop-types";
import { useState } from "react";
import { Box, Button, Stack } from "@mui/material";
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
import ShipConfirmDialog from "./ShipConfirmDialog";
import { isShipped, shipErrorMessage, winnerOf } from "./fixRunView";

const shipHint = (fix, trial) =>
  fix.agent?.kind === "github"
    ? `Opens a pull request with Trial ${trial.attempt}'s change on the agent's repository`
    : `Makes Trial ${trial.attempt}'s version the environment's default`;

// Ship: the fix's winner once the fix is ready, or, given as `candidate`, a
// trial only its must-pass check refused, on the user's reason; either only
// after the user confirms what it will do. An uploaded agent's trial becomes
// the environment's default; a GitHub agent's opens a pull request, with the
// patch as the way out when the repository cannot take one. When the server
// says the same request may work if sent again, Ship stays and the patch sits
// beside it; otherwise the patch takes Ship's place.
export default function ShipButton({ fix, candidate }) {
  const winner = winnerOf(fix);
  const anyway = !!candidate && candidate.outcome !== "winner";
  const trial = candidate || winner;
  const ship = useShipCandidate(fix);
  // null, "beside" Ship, or "instead" of it.
  const [patchOffer, setPatchOffer] = useState(null);
  const [busy, setBusy] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [downloading, setDownloading] = useState(false);
  if (!trial) return null;
  if (anyway ? !trial.override?.allowed : fix.state !== "ready") return null;

  if (trial.pull_request_url) {
    return (
      <Button
        size="small"
        variant="contained"
        href={trial.pull_request_url}
        target="_blank"
        rel="noopener noreferrer"
        endIcon={<Iconify icon="solar:arrow-right-up-linear" width={14} />}
      >
        View pull request
      </Button>
    );
  }
  if (isShipped(fix, trial)) {
    return (
      <Button size="small" variant="contained" disabled>
        Default
      </Button>
    );
  }
  const downloadPatch = async () => {
    setDownloading(true);
    try {
      downloadText(
        patchFileName(fix.id, trial.attempt),
        await fetchCandidatePatch(fix.id, trial.id),
      );
    } catch {
      enqueueSnackbar("Could not download the patch. Try again.", {
        variant: "error",
      });
    } finally {
      setDownloading(false);
    }
  };
  const patchButton = (
    <Button
      size="small"
      variant="outlined"
      disabled={downloading}
      startIcon={<Iconify icon="solar:download-linear" width={16} />}
      onClick={downloadPatch}
    >
      Download patch
    </Button>
  );
  if (patchOffer === "instead") return patchButton;
  const shipTrial = async (overrideReason) => {
    setBusy(true);
    try {
      await ship.mutateAsync({ candidateId: trial.id, overrideReason });
      setConfirming(false);
    } catch (error) {
      const parsed = fixErrorOf(error);
      if (parsed.offerPatch) {
        setPatchOffer(parsed.retryable ? "beside" : "instead");
      }
      setConfirming(false);
      enqueueSnackbar(
        shipErrorMessage(parsed) || "Could not ship this change",
        { variant: "error" },
      );
    } finally {
      setBusy(false);
    }
  };
  return (
    <>
      <Stack direction="row" spacing={1} alignItems="center">
        <CustomTooltip show title={shipHint(fix, trial)} size="small" arrow>
          <Box component="span">
            <Button
              size="small"
              variant={anyway ? "outlined" : "contained"}
              color="primary"
              disabled={busy}
              startIcon={<Iconify icon="solar:upload-linear" width={16} />}
              onClick={() => setConfirming(true)}
            >
              {anyway ? "Ship anyway" : "Ship"}
            </Button>
          </Box>
        </CustomTooltip>
        {patchOffer === "beside" && patchButton}
      </Stack>
      <ShipConfirmDialog
        fix={fix}
        candidate={trial}
        override={anyway ? trial.override : null}
        open={confirming}
        busy={busy}
        onClose={() => setConfirming(false)}
        onConfirm={shipTrial}
      />
    </>
  );
}

ShipButton.propTypes = {
  fix: PropTypes.object.isRequired,
  // A trial only its must-pass check refused, to ship on a reason; else the
  // winner.
  candidate: PropTypes.object,
};
