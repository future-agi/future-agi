import PropTypes from "prop-types";
import { useState } from "react";
import {
  IconButton,
  ListItemIcon,
  ListItemText,
  Menu,
  MenuItem,
} from "@mui/material";
import { enqueueSnackbar } from "notistack";

import Iconify from "src/components/iconify";
import { copyToClipboard } from "src/utils/copyToClipboard";
import {
  fetchCandidatePatch,
  patchFileName,
} from "src/api/simulate-environments/fixRuns";

import { useCodingAgentAttemptsEnabled } from "src/api/simulate-environments/codingAgent";

import { downloadText } from "./downloadText";
import { fixName, winnerOf } from "./fixRunView";
import useCopyForCodingAgent from "./useCopyForCodingAgent";

const exportFileName = (fix) =>
  `${fixName(fix).toLowerCase().replace(/\s+/g, "-")}.json`;

// The design's ⋯ on the epoch page (EpochMoreMenu): everything that is not the
// page's one primary. An action that does not apply is disabled, not hidden.
export default function EpochMoreMenu({ fix }) {
  const [anchor, setAnchor] = useState(null);
  const winner = fix.state === "ready" ? winnerOf(fix) : null;
  const close = () => setAnchor(null);
  const codingAgent = useCodingAgentAttemptsEnabled();
  const brief = useCopyForCodingAgent(fix.source_run_id);

  const copyId = async () => {
    close();
    const copied = await copyToClipboard(fix.id);
    enqueueSnackbar(
      copied ? "Epoch ID copied" : "Could not copy the epoch ID",
      {
        variant: copied ? "success" : "error",
      },
    );
  };
  const downloadPatch = async () => {
    close();
    try {
      const patch = await fetchCandidatePatch(fix.id, winner.id);
      downloadText(patchFileName(fix.id, winner.attempt), patch);
    } catch {
      enqueueSnackbar("Could not download the patch. Try again.", {
        variant: "error",
      });
    }
  };
  const exportEpoch = () => {
    close();
    downloadText(
      exportFileName(fix),
      JSON.stringify(fix, null, 2),
      "application/json",
    );
  };

  const copyForCodingAgent = () => {
    close();
    brief.copy();
  };

  const items = [
    ...(codingAgent
      ? [
          {
            label: "Copy for coding agent",
            hint: fix.source_run_id
              ? "A prompt for Claude Code, Cursor or Codex to fix this run"
              : "Only an epoch started from a run has a brief to copy",
            icon: "solar:clipboard-text-linear",
            onClick: copyForCodingAgent,
            disabled: !fix.source_run_id,
          },
        ]
      : []),
    {
      label: "Copy epoch ID",
      hint: "To quote it to support or in the API",
      icon: "solar:copy-linear",
      onClick: copyId,
    },
    {
      label: "Download patch",
      hint: winner
        ? `Candidate ${winner.attempt}'s change, to apply by hand`
        : "Only an epoch with a winner has a change to download",
      icon: "solar:download-minimalistic-linear",
      onClick: downloadPatch,
      disabled: !winner,
    },
    {
      label: "Export epoch",
      hint: "Everything this epoch recorded, as a JSON file",
      icon: "solar:export-linear",
      onClick: exportEpoch,
    },
  ];

  return (
    <>
      <IconButton
        size="small"
        aria-label="More actions"
        onClick={(e) => setAnchor(e.currentTarget)}
      >
        <Iconify icon="solar:menu-dots-bold" width={16} />
      </IconButton>
      <Menu
        anchorEl={anchor}
        open={!!anchor}
        onClose={close}
        anchorOrigin={{ vertical: "bottom", horizontal: "right" }}
        transformOrigin={{ vertical: "top", horizontal: "right" }}
      >
        {items.map((it) => (
          <MenuItem key={it.label} onClick={it.onClick} disabled={it.disabled}>
            <ListItemIcon>
              <Iconify icon={it.icon} width={16} />
            </ListItemIcon>
            <ListItemText primary={it.label} secondary={it.hint} />
          </MenuItem>
        ))}
      </Menu>
    </>
  );
}

EpochMoreMenu.propTypes = { fix: PropTypes.object.isRequired };
