import PropTypes from "prop-types";
import { useState } from "react";
import { Button } from "@mui/material";
import { enqueueSnackbar } from "notistack";

import Iconify from "src/components/iconify";
import {
  fetchCandidatePatch,
  fixErrorOf,
  patchFileName,
  useShipCandidate,
} from "src/api/simulate-environments/fixRuns";

import { downloadText } from "./downloadText";
import { isShipped, winnerOf } from "./fixRunView";

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
  const github = fix.agent?.kind === "github";
  return (
    <Button
      size="small"
      variant="contained"
      disabled={busy}
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
      {github ? "Open pull request" : "Make default"}
    </Button>
  );
}

ShipButton.propTypes = { fix: PropTypes.object.isRequired };
