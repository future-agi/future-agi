import React, { useContext } from "react";
import { QueryClientContext } from "@tanstack/react-query";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  TextField,
  Typography,
} from "@mui/material";
import CopyButton from "src/sections/common/ux/CopyButton";
import PublicLinkSection from "./PublicLinkSection";

/** The dataset's Data tab link, as a teammate opens it. */
export const shareLinkOf = (origin, datasetId) =>
  `${origin}/dashboard/develop/${datasetId}?tab=data`;

/**
 * Share (DL#35, options-truth L5 G5 W0-2): the dataset's link with an
 * in-place copy check, and one `Done`. W4-6: below it, a public read-only
 * link on the platform's share-link system when the workspace allows them
 * (PublicLinkSection; it needs the app's query client, and renders nothing
 * until the workspace policy has loaded).
 */
export default function ShareDatasetDialog({ open, onClose, datasetId, name }) {
  const link = shareLinkOf(window.location.origin, datasetId);
  const queryClient = useContext(QueryClientContext);
  return (
    <Dialog
      open={open}
      onClose={onClose}
      maxWidth={queryClient ? "sm" : "xs"}
      fullWidth
    >
      <DialogTitle sx={{ pb: 0.5 }}>
        <Typography variant="m3" fontWeight={600} component="span">
          Share &ldquo;{name}&rdquo;
        </Typography>
      </DialogTitle>
      <DialogContent
        sx={{ display: "flex", flexDirection: "column", gap: 1.5 }}
      >
        <Typography typography="s2" color="text.secondary">
          People in your workspace can open it. Send them this link to the Data
          tab.
        </Typography>
        <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
          <TextField
            size="small"
            value={link}
            fullWidth
            InputProps={{
              readOnly: true,
              sx: { typography: "s2", fontFamily: "monospace" },
            }}
            inputProps={{ "aria-label": "Dataset link" }}
            onFocus={(e) => e.target.select()}
          />
          <CopyButton value={link} tooltip="Copy link" />
        </Box>
        {queryClient && <PublicLinkSection datasetId={datasetId} />}
      </DialogContent>
      <DialogActions>
        <Button variant="contained" onClick={onClose}>
          Done
        </Button>
      </DialogActions>
    </Dialog>
  );
}

ShareDatasetDialog.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  datasetId: PropTypes.string,
  name: PropTypes.string,
};
