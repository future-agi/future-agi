import PropTypes from "prop-types";
import { useState } from "react";
import { useSnackbar } from "notistack";
import {
  Box,
  Button,
  ListItemIcon,
  ListItemText,
  Menu,
  MenuItem,
  Typography,
  Tooltip,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { shareTokenOf, sharedUrlOf } from "../_mock/runShare";
import { PrintPortal } from "./SharedReport";
import { downloadJson, trajectoryExportOf } from "../_mock/trajectoryExport";

const itemText = {
  primaryTypographyProps: { typography: "s2", fontWeight: 600 },
  secondaryTypographyProps: {
    typography: "s3",
    color: "text.tertiary",
    sx: { whiteSpace: "normal" },
  },
};

/**
 * Share a finished run (PRD v6 · AC-20.9): a read-only, provenance-stamped
 * link, or the same report as a PDF through the browser's print dialog.
 */
export default function ShareMenu({
  env,
  envState,
  identity,
  report,
  compact = false,
  tasks = null,
}) {
  const { enqueueSnackbar } = useSnackbar();
  const [anchor, setAnchor] = useState(null);
  const [printing, setPrinting] = useState(false);
  if (!identity || !report) return null;

  const token = shareTokenOf(identity, env, envState);
  const url = sharedUrlOf(env.id, identity.id, token);
  const hrefFor = ({ ids, trace }) =>
    sharedUrlOf(
      env.id,
      identity.id,
      token,
      trace ? { trace } : { scenarios: (ids || []).join(",") },
    );

  const copy = async () => {
    setAnchor(null);
    try {
      await navigator.clipboard.writeText(url);
      enqueueSnackbar("Read-only link copied", { variant: "success" });
    } catch {
      enqueueSnackbar(`Couldn't copy — the link is ${url}`, {
        variant: "info",
      });
    }
  };

  return (
    <>
      {compact ? (
        <Tooltip arrow title="Share">
          <Button
            variant="outlined"
            size="small"
            aria-label="Share"
            onClick={(e) => setAnchor(e.currentTarget)}
            sx={{
              minWidth: 0,
              px: 0.75,
              color: "text.primary",
              borderColor: "divider",
            }}
          >
            <Iconify icon="solar:share-linear" width={16} />
          </Button>
        </Tooltip>
      ) : (
        <Button
          variant="outlined"
          size="small"
          onClick={(e) => setAnchor(e.currentTarget)}
          startIcon={<Iconify icon="solar:share-linear" width={16} />}
          endIcon={
            <Iconify
              icon="solar:alt-arrow-down-linear"
              width={12}
              sx={{ color: "text.tertiary" }}
            />
          }
          sx={{ color: "text.primary", borderColor: "divider" }}
        >
          Share
        </Button>
      )}
      <Menu
        anchorEl={anchor}
        open={!!anchor}
        onClose={() => setAnchor(null)}
        anchorOrigin={{ vertical: "bottom", horizontal: "right" }}
        transformOrigin={{ vertical: "top", horizontal: "right" }}
        slotProps={{
          paper: { sx: { mt: 0.75, width: 340, borderRadius: "8px" } },
        }}
      >
        <Box sx={{ px: 2, pt: 1.25, pb: 0.75 }}>
          <Typography
            sx={{
              typography: "s3",
              fontWeight: 600,
              color: "text.tertiary",
              textTransform: "none",
              letterSpacing: 0.4,
            }}
          >
            Share this run&apos;s report
          </Typography>
          <Typography
            sx={{ typography: "s3", color: "text.tertiary", mt: 0.25 }}
          >
            Pass rate and every scenario&apos;s result, each with its trace —
            read-only, stamped with every version this run held fixed.
          </Typography>
        </Box>
        <MenuItem
          onClick={copy}
          sx={{ alignItems: "flex-start", gap: 0.75, py: 1.25 }}
        >
          <ListItemIcon sx={{ minWidth: "0 !important", mt: "3px" }}>
            <Iconify
              icon="solar:link-linear"
              width={16}
              sx={{ color: "text.primary" }}
            />
          </ListItemIcon>
          <ListItemText
            primary="Copy read-only link"
            secondary={
              <Box
                component="span"
                sx={{ fontFamily: "monospace", wordBreak: "break-all" }}
              >{`…?shared=${token}`}</Box>
            }
            {...itemText}
          />
        </MenuItem>
        <MenuItem
          onClick={() => {
            setAnchor(null);
            window.open(url, "_blank", "noopener");
          }}
          sx={{ alignItems: "flex-start", gap: 0.75, py: 1.25 }}
        >
          <ListItemIcon sx={{ minWidth: "0 !important", mt: "3px" }}>
            <Iconify
              icon="solar:eye-linear"
              width={16}
              sx={{ color: "text.primary" }}
            />
          </ListItemIcon>
          <ListItemText
            primary="Open the read-only view"
            secondary="What someone with the link sees, in a new tab."
            {...itemText}
          />
        </MenuItem>
        <MenuItem
          onClick={() => {
            setAnchor(null);
            setPrinting(true);
          }}
          sx={{ alignItems: "flex-start", gap: 0.75, py: 1.25 }}
        >
          <ListItemIcon sx={{ minWidth: "0 !important", mt: "3px" }}>
            <Iconify
              icon="solar:printer-linear"
              width={16}
              sx={{ color: "text.primary" }}
            />
          </ListItemIcon>
          <ListItemText
            primary="Download PDF"
            secondary="Opens the print dialog — choose Save as PDF. Evidence links stay clickable."
            {...itemText}
          />
        </MenuItem>
        {/* The trajectories themselves, for tools outside Future AGI. */}
        {tasks && (
          <MenuItem
            onClick={() => {
              setAnchor(null);
              downloadJson(
                trajectoryExportOf({
                  env,
                  run: (envState?.runs || []).find((r) => r.id === identity.id),
                  tasks,
                }),
                `${env.id}-run-${identity.ordinal ?? identity.id}-trajectories.json`,
              );
            }}
            sx={{ alignItems: "flex-start", gap: 0.75, py: 1.25 }}
          >
            <ListItemIcon sx={{ minWidth: "0 !important", mt: "3px" }}>
              <Iconify
                icon="solar:download-minimalistic-linear"
                width={16}
                sx={{ color: "text.primary" }}
              />
            </ListItemIcon>
            <ListItemText
              primary="Export trajectories"
              secondary="JSON: the resolved configuration, each trial's outcome, turns and tool calls. Hidden evaluation material stays out."
              {...itemText}
            />
          </MenuItem>
        )}
      </Menu>

      {printing && (
        <PrintPortal
          env={env}
          envState={envState}
          identity={identity}
          report={report}
          token={token}
          hrefFor={hrefFor}
          onDone={() => setPrinting(false)}
        />
      )}
    </>
  );
}

ShareMenu.propTypes = {
  /* The run's trials — offers Export trajectories when given. */
  tasks: PropTypes.array,
  compact: PropTypes.bool,
  env: PropTypes.object,
  envState: PropTypes.object,
  identity: PropTypes.object,
  report: PropTypes.object,
};
