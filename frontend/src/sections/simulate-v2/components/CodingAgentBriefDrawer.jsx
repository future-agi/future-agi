import PropTypes from "prop-types";
import { Box, Button, IconButton, Stack, Typography } from "@mui/material";
import { enqueueSnackbar } from "notistack";
import Iconify from "src/components/iconify";
import SideDrawer from "./SideDrawer";

/**
 * Copy for coding agent: shows exactly what is copied before it is, and who
 * pays for checking the attempts. `getText` builds the brief on open — it
 * reads the whole diagnosis, so it isn't built on every render.
 */
export default function CodingAgentBriefDrawer({ open, onClose, getText }) {
  const text = open ? getText() : "";
  return (
    <SideDrawer open={open} onClose={onClose} width={520}>
      <Stack sx={{ height: "100%", minHeight: 0 }}>
        <Stack
          direction="row"
          alignItems="flex-start"
          spacing={1}
          sx={{
            px: 2.5,
            pt: 2,
            pb: 1.5,
            flexShrink: 0,
            borderBottom: "1px solid",
            borderColor: "divider",
          }}
        >
          <Box sx={{ flex: 1, minWidth: 0 }}>
            <Typography sx={{ typography: "s1", fontWeight: 600 }}>
              Copy for coding agent
            </Typography>
            <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
              Paste this into your coding agent.
            </Typography>
          </Box>
          <IconButton size="small" aria-label="Close" onClick={onClose}>
            <Iconify icon="solar:close-linear" width={18} />
          </IconButton>
        </Stack>
        <Box sx={{ px: 2.5, py: 2, flex: 1, minHeight: 0, display: "flex" }}>
          <Box
            component="pre"
            sx={{
              m: 0,
              p: 1.5,
              flex: 1,
              minWidth: 0,
              overflow: "auto",
              fontFamily: "monospace",
              fontSize: 12,
              lineHeight: "18px",
              whiteSpace: "pre-wrap",
              wordBreak: "break-word",
              border: "1px solid",
              borderColor: "divider",
              borderRadius: 1,
              bgcolor: "background.neutral",
            }}
          >
            {text}
          </Box>
        </Box>
        <Stack
          direction="row"
          alignItems="center"
          spacing={1}
          sx={{
            px: 2.5,
            py: 2,
            flexShrink: 0,
            borderTop: "1px solid",
            borderColor: "divider",
          }}
        >
          <Typography
            sx={{ typography: "s3", color: "text.secondary", flex: 1 }}
          >
            Your coding agent&apos;s attempts are checked here and charged to
            this workspace&apos;s credits.
          </Typography>
          <Button
            size="small"
            variant="contained"
            color="primary"
            startIcon={<Iconify icon="solar:copy-linear" width={14} />}
            onClick={() => {
              navigator.clipboard?.writeText(text);
              enqueueSnackbar("Copied for your coding agent", {
                variant: "success",
              });
              onClose();
            }}
          >
            Copy
          </Button>
        </Stack>
      </Stack>
    </SideDrawer>
  );
}

CodingAgentBriefDrawer.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func.isRequired,
  /* () => the brief, as markdown. */
  getText: PropTypes.func.isRequired,
};
