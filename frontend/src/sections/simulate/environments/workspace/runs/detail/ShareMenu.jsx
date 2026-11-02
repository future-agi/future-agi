import PropTypes from "prop-types";
import { useState } from "react";
import { enqueueSnackbar } from "notistack";
import {
  Box,
  Button,
  ListItemIcon,
  ListItemText,
  Menu,
  MenuItem,
  Stack,
  Typography,
} from "@mui/material";

import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import { exportRunResults } from "src/api/simulate-environments/runAnalytics";

import UiOnlyFlag from "../../../components/UiOnlyFlag";
import { UI_ONLY_SAVED } from "../../gaps/NeedsInputButton";

const itemText = {
  primaryTypographyProps: { typography: "s2", fontWeight: 600 },
  secondaryTypographyProps: {
    typography: "s3",
    color: "text.subtitle",
    sx: { whiteSpace: "normal" },
  },
};
const itemSx = { alignItems: "flex-start", gap: 0.75, py: 1.25 };

function ShareItem({ icon, title, description, uiOnly = false, onClick }) {
  return (
    <MenuItem onClick={onClick} sx={itemSx}>
      <ListItemIcon sx={{ minWidth: "0 !important", mt: "3px" }}>
        <Iconify icon={icon} width={16} sx={{ color: "text.primary" }} />
      </ListItemIcon>
      <ListItemText
        primary={
          <Stack direction="row" alignItems="center" spacing={0.75}>
            <span>{title}</span>
            {uiOnly && <UiOnlyFlag feature="shareRun" />}
          </Stack>
        }
        secondary={description}
        {...itemText}
      />
    </MenuItem>
  );
}
ShareItem.propTypes = {
  icon: PropTypes.string.isRequired,
  title: PropTypes.string.isRequired,
  description: PropTypes.string.isRequired,
  uiOnly: PropTypes.bool,
  onClick: PropTypes.func.isRequired,
};

/**
 * Share a finished run: the real results export first, then Vel's read-only
 * link and PDF, which have no backend yet and save nothing.
 */
export default function ShareMenu({ executionId, disabled = false }) {
  const [anchor, setAnchor] = useState(null);
  const [exporting, setExporting] = useState(false);

  const exportResults = async () => {
    setAnchor(null);
    setExporting(true);
    try {
      await exportRunResults(executionId);
      enqueueSnackbar("Run export downloaded", { variant: "success" });
    } catch {
      enqueueSnackbar("Run export could not be generated", {
        variant: "error",
      });
    } finally {
      setExporting(false);
    }
  };
  const uiOnly = () => {
    setAnchor(null);
    enqueueSnackbar(UI_ONLY_SAVED, { variant: "info" });
  };

  return (
    <>
      <CustomTooltip
        show
        arrow
        size="small"
        title={exporting ? "Exporting…" : "Share"}
      >
        {/* A disabled button fires no hover events; the box carries the tip. */}
        <Box sx={{ display: "flex", flexShrink: 0 }}>
          <Button
            variant="outlined"
            size="small"
            aria-label="Share"
            onClick={(e) => setAnchor(e.currentTarget)}
            disabled={disabled || exporting}
            sx={{
              minWidth: 0,
              px: 0.75,
              color: "text.primary",
              borderColor: "divider",
            }}
          >
            <Iconify icon="solar:share-linear" width={16} />
          </Button>
        </Box>
      </CustomTooltip>
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
            sx={{ typography: "s3", fontWeight: 600, color: "text.subtitle" }}
          >
            Share this run&apos;s report
          </Typography>
          <Typography
            sx={{ typography: "s3", color: "text.subtitle", mt: 0.25 }}
          >
            Pass rate and every scenario&apos;s result, each with its trace.
            Read-only, stamped with every version this run held fixed.
          </Typography>
        </Box>
        <ShareItem
          icon="solar:download-minimalistic-linear"
          title="Export results"
          description="Every call's result as a file."
          onClick={exportResults}
        />
        <ShareItem
          icon="solar:link-linear"
          title="Copy read-only link"
          description="Anyone with the link sees the report, read-only."
          uiOnly
          onClick={uiOnly}
        />
        <ShareItem
          icon="solar:eye-linear"
          title="Open the read-only view"
          description="What someone with the link sees, in a new tab."
          uiOnly
          onClick={uiOnly}
        />
        <ShareItem
          icon="solar:printer-linear"
          title="Download PDF"
          description="Opens the print dialog. Choose Save as PDF. Evidence links stay clickable."
          uiOnly
          onClick={uiOnly}
        />
      </Menu>
    </>
  );
}
ShareMenu.propTypes = {
  executionId: PropTypes.string,
  disabled: PropTypes.bool,
};
