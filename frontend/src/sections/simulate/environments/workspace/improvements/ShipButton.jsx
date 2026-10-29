import PropTypes from "prop-types";
import { useState } from "react";
import { Box, Button } from "@mui/material";
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
import { isShipped, winnerOf } from "./fixRunView";

const shipHint = (fix, winner) =>
  fix.agent?.kind === "github"
    ? `Opens a pull request with Trial ${winner.attempt}'s change on the agent's repository`
    : `Makes Trial ${winner.attempt}'s version the environment's default`;

// Ship: only the fix's winner, only once the fix is ready. An uploaded agent's
// winner becomes the environment's default; a GitHub agent's opens a pull
// request, with the patch as the way out when the repository cannot take one.
export default function ShipButton({ fix }) {
  const winner = winnerOf(fix);
  const ship = useShipCandidate(fix);
  const [offerPatch, setOfferPatch] = useState(false);
  const [busy, setBusy] = useState(false);
  if (fix.state !== "ready" || !winner) return null;

  if (winner.pull_request_url) {
    return (
      <Button
        size="small"
        variant="contained"
        href={winner.pull_request_url}
        target="_blank"
        rel="noopener noreferrer"
        endIcon={<Iconify icon="solar:arrow-right-up-linear" width={14} />}
      >
        View pull request
      </Button>
    );
  }
  if (isShipped(fix, winner)) {
    return (
      <Button size="small" variant="contained" disabled>
        Default
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
            patchFileName(fix.id, winner.attempt),
            await fetchCandidatePatch(fix.id, winner.id),
          )
        }
      >
        Download patch
      </Button>
    );
  }
  return (
    <CustomTooltip show title={shipHint(fix, winner)} size="small" arrow>
      <Box component="span">
        <Button
          size="small"
          variant="contained"
          color="primary"
          disabled={busy}
          startIcon={<Iconify icon="solar:upload-linear" width={16} />}
          onClick={async () => {
            setBusy(true);
            try {
              await ship.mutateAsync(winner.id);
            } catch (error) {
              const parsed = fixErrorOf(error);
              if (parsed.offerPatch) setOfferPatch(true);
              enqueueSnackbar(parsed.message || "Could not ship this change", {
                variant: "error",
              });
            } finally {
              setBusy(false);
            }
          }}
        >
          Ship
        </Button>
      </Box>
    </CustomTooltip>
  );
}

ShipButton.propTypes = { fix: PropTypes.object.isRequired };
