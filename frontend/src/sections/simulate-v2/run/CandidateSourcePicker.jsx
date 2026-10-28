import PropTypes from "prop-types";
import { useState } from "react";
import {
  Box,
  Button,
  InputBase,
  Menu,
  MenuItem,
  ListItemText,
  Stack,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import {
  candidateParam,
  isSha,
  uploadedArchivesOf,
} from "../_mock/candidateSource";

/**
 * Where a run's candidate comes from: the agent version (default), a commit
 * SHA, or an uploaded archive of its code. A mock picker — the choice goes on
 * the URL (`?candidate=`) and the run is labelled "from snapshot <sha>".
 * Picked in the run plan, under the agent version it stands in for.
 */
export default function CandidateSourcePicker({
  agent,
  agentVersion,
  current,
  onPick,
  /* Shown where the version is already said: the line offers the other
     sources instead of repeating it. */
  offer = null,
  sx,
}) {
  const [anchor, setAnchor] = useState(null);
  const [mode, setMode] = useState(null); // null | "commit"
  const [sha, setSha] = useState("");
  const archives = uploadedArchivesOf(agent);
  const close = () => {
    setAnchor(null);
    setMode(null);
    setSha("");
  };
  const pick = (value) => {
    onPick(value);
    close();
  };

  return (
    <Stack
      direction="row"
      alignItems="center"
      spacing={1}
      sx={{
        px: 2,
        py: 1.25,
        borderTop: "1px solid",
        borderColor: "divider",
        ...sx,
      }}
    >
      <Iconify
        icon="solar:code-square-linear"
        width={16}
        sx={{ color: "text.secondary", flexShrink: 0 }}
      />
      <Typography
        sx={{ typography: "s2", color: "text.secondary", flex: 1, minWidth: 0 }}
        noWrap
      >
        {current
          ? `Testing ${current.label}`
          : offer || `Testing agent ${agentVersion || "—"}`}
      </Typography>
      <Button
        size="small"
        onClick={(e) => setAnchor(e.currentTarget)}
        endIcon={<Iconify icon="solar:alt-arrow-down-linear" width={12} />}
        sx={{ color: "text.primary", flexShrink: 0 }}
      >
        {offer && !current ? "Choose" : "Change"}
      </Button>
      <Menu
        anchorEl={anchor}
        open={!!anchor}
        onClose={close}
        anchorOrigin={{ vertical: "bottom", horizontal: "right" }}
        transformOrigin={{ vertical: "top", horizontal: "right" }}
        slotProps={{ paper: { sx: { width: 320 } } }}
      >
        <MenuItem
          onClick={() => pick(null)}
          selected={!current}
          sx={{ typography: "s2" }}
        >
          <ListItemText
            primary={`Agent ${agentVersion || "version"}`}
            secondary="As connected"
            primaryTypographyProps={{ typography: "s2", fontWeight: 600 }}
            secondaryTypographyProps={{ typography: "s3" }}
          />
        </MenuItem>
        {mode === "commit" ? (
          <Box sx={{ px: 2, py: 1 }} onKeyDown={(e) => e.stopPropagation()}>
            <Typography
              sx={{ typography: "s3", color: "text.tertiary", mb: 0.5 }}
            >
              Commit SHA
            </Typography>
            <Stack direction="row" spacing={1}>
              <InputBase
                autoFocus
                value={sha}
                onChange={(e) => setSha(e.target.value)}
                placeholder="e.g. 3f9c2ab"
                sx={{
                  flex: 1,
                  px: 1,
                  border: "1px solid",
                  borderColor: "divider",
                  borderRadius: "4px",
                  typography: "s2",
                  fontFamily: "ui-monospace, Menlo, monospace",
                }}
              />
              <Button
                size="small"
                variant="outlined"
                disabled={!isSha(sha)}
                onClick={() =>
                  pick(candidateParam({ kind: "commit", value: sha.trim() }))
                }
                sx={{ color: "text.primary", borderColor: "divider" }}
              >
                Use
              </Button>
            </Stack>
          </Box>
        ) : (
          <MenuItem
            onClick={() => setMode("commit")}
            selected={current?.kind === "commit"}
            sx={{ typography: "s2" }}
          >
            <ListItemText
              primary="A commit SHA…"
              secondary="Built into a sealed snapshot"
              primaryTypographyProps={{ typography: "s2", fontWeight: 600 }}
              secondaryTypographyProps={{ typography: "s3" }}
            />
          </MenuItem>
        )}
        <Typography
          sx={{
            px: 2,
            pt: 1,
            pb: 0.5,
            typography: "s3",
            fontWeight: 600,
            color: "text.tertiary",
            textTransform: "none",
            letterSpacing: 0.5,
          }}
        >
          Uploaded archives
        </Typography>
        {archives.map((name) => (
          <MenuItem
            key={name}
            onClick={() =>
              pick(candidateParam({ kind: "upload", value: name }))
            }
            selected={current?.name === name}
            sx={{
              typography: "s2",
              fontFamily: "ui-monospace, Menlo, monospace",
            }}
          >
            {name}
          </MenuItem>
        ))}
      </Menu>
    </Stack>
  );
}

CandidateSourcePicker.propTypes = {
  agent: PropTypes.object,
  agentVersion: PropTypes.string,
  current: PropTypes.object,
  onPick: PropTypes.func.isRequired,
  offer: PropTypes.string,
  sx: PropTypes.object,
};
