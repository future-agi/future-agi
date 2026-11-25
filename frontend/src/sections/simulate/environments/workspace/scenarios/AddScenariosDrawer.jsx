import PropTypes from "prop-types";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { Box, Button, ButtonBase, Stack, Typography } from "@mui/material";

import Iconify from "src/components/iconify";
import { paths } from "src/routes/paths";
import SideDrawer from "../../components/SideDrawer";
import ImportTranscriptDialog from "../../evidence/ImportTranscriptDialog";
import { focusRing } from "../../buildDrawer/drawerStyles";
import { SCENARIOS_COPY } from "./scenarios.constants";

// The sources that work today. Vel's panel also has Generate, From chat, Write
// one, From a script, From an Error Feed issue and From failing cases; each
// joins this list once it has a working path.
const SOURCES = [
  {
    id: "observe",
    label: "From production",
    blurb: "Traces or sessions from Observe",
    icon: "solar:server-linear",
    to: paths.dashboard.observe,
  },
  {
    id: "dataset",
    label: "From a dataset",
    blurb: "Each row becomes a draft",
    icon: "solar:database-linear",
    to: paths.dashboard.develop,
  },
  {
    id: "files",
    label: "Upload transcript or file",
    blurb: "Transcripts, CSV or JSON",
    icon: "solar:upload-minimalistic-linear",
  },
];

// Add scenarios.
//
// Scenarios already exist by the time anyone reaches this panel: they are
// derived from the agent when the environment is built. So these sources add
// the ones the derivation could not know to write. Observe and dataset
// evidence is picked where it lives, and its import opens back on the
// Scenarios tab for review; an uploaded transcript's review opens at once.
export default function AddScenariosDrawer({
  open,
  onClose,
  envId,
  onImported,
}) {
  const navigate = useNavigate();
  const [uploading, setUploading] = useState(false);

  // The panel closes first, so the review that follows is the only drawer open.
  const pick = (source) => {
    onClose();
    if (source.to) navigate(source.to);
    else setUploading(true);
  };

  return (
    <>
      <SideDrawer open={open} onClose={onClose} width={960}>
        <Stack sx={{ height: "100%" }}>
          <Box
            sx={{
              px: 2.5,
              py: 2,
              // Room for the drawer's own close button.
              pr: 6,
              borderBottom: "1px solid",
              borderColor: "divider",
              flexShrink: 0,
            }}
          >
            <Typography
              sx={{ typography: "m2", fontWeight: "fontWeightSemiBold" }}
            >
              {SCENARIOS_COPY.addLabel}
            </Typography>
            <Typography sx={{ typography: "s2", color: "text.tertiary" }}>
              This environment already has its scenarios. Add the ones it
              couldn&apos;t work out from your agent on its own.
            </Typography>
          </Box>

          <Box sx={{ flex: 1, minHeight: 0, overflow: "auto", p: 2.5 }}>
            <Typography
              sx={{
                typography: "s3",
                color: "text.tertiary",
                fontWeight: "fontWeightMedium",
                mb: 0.75,
              }}
            >
              From what already happened
            </Typography>
            <Box
              sx={{
                display: "grid",
                gap: 1,
                // The design's five tiles to a row, so a tile keeps its size.
                gridTemplateColumns: {
                  xs: "repeat(2, minmax(0, 1fr))",
                  md: "repeat(5, minmax(0, 1fr))",
                },
              }}
            >
              {SOURCES.map((source) => (
                <SourceTile
                  key={source.id}
                  source={source}
                  onClick={() => pick(source)}
                />
              ))}
            </Box>
          </Box>

          <Stack
            direction="row"
            justifyContent="flex-end"
            sx={{
              px: 2.5,
              py: 1.75,
              borderTop: "1px solid",
              borderColor: "divider",
              flexShrink: 0,
            }}
          >
            <Button
              size="small"
              onClick={onClose}
              sx={{ color: "text.secondary" }}
            >
              Cancel
            </Button>
          </Stack>
        </Stack>
      </SideDrawer>
      {uploading ? (
        <ImportTranscriptDialog
          open
          onClose={() => setUploading(false)}
          envId={envId}
          onImported={onImported}
        />
      ) : null}
    </>
  );
}

AddScenariosDrawer.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func.isRequired,
  envId: PropTypes.string.isRequired,
  // Opens the review of a transcript import: (importId).
  onImported: PropTypes.func.isRequired,
};

// One source: icon, name and one line.
function SourceTile({ source, onClick }) {
  return (
    <ButtonBase
      onClick={onClick}
      sx={{
        display: "flex",
        width: 1,
        minWidth: 0,
        alignItems: "flex-start",
        justifyContent: "flex-start",
        gap: 1,
        px: 1.25,
        py: 1,
        textAlign: "left",
        borderRadius: "8px",
        border: "1px solid",
        borderColor: "divider",
        bgcolor: "background.paper",
        transition: "border-color .16s ease",
        "&:hover": { borderColor: "text.tertiary" },
        "&.Mui-focusVisible": focusRing,
      }}
    >
      <Iconify
        icon={source.icon}
        width={16}
        sx={{ color: "text.secondary", mt: "2px", flexShrink: 0 }}
      />
      <Box minWidth={0}>
        <Typography
          sx={{
            typography: "s2",
            fontWeight: "fontWeightSemiBold",
            color: "text.primary",
          }}
        >
          {source.label}
        </Typography>
        <Typography
          sx={{
            typography: "s3",
            color: "text.tertiary",
            display: "-webkit-box",
            WebkitLineClamp: 2,
            WebkitBoxOrient: "vertical",
            overflow: "hidden",
          }}
        >
          {source.blurb}
        </Typography>
      </Box>
    </ButtonBase>
  );
}

SourceTile.propTypes = {
  source: PropTypes.shape({
    label: PropTypes.string.isRequired,
    blurb: PropTypes.string.isRequired,
    icon: PropTypes.string.isRequired,
  }).isRequired,
  onClick: PropTypes.func.isRequired,
};
