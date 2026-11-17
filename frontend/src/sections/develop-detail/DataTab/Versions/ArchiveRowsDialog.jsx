import React, { useEffect, useState } from "react";
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
import CustomTooltip from "src/components/tooltip";

/**
 * W3-3 `Archive…`: archiving needs a reason (`Why archive these 3 rows?`).
 * Archived rows stay in the dataset's history, leave the grid, runs and
 * counts, and come back with Restore.
 */
export default function ArchiveRowsDialog({ open, count, onClose, onArchive }) {
  const [reason, setReason] = useState("");
  useEffect(() => {
    if (open) setReason("");
  }, [open]);
  const blocked = reason.trim() ? null : "Add a reason to archive";
  return (
    <Dialog open={open} onClose={onClose} maxWidth="xs" fullWidth>
      <DialogTitle sx={{ pb: 0.5 }}>
        <Typography typography="m3" fontWeight="fontWeightSemiBold">
          {`Why archive ${count === 1 ? "this row" : `these ${count} rows`}?`}
        </Typography>
      </DialogTitle>
      <DialogContent>
        <Typography typography="s2" color="text.secondary" sx={{ mb: 1.5 }}>
          Archived rows leave the grid, new runs and row counts. Their history
          stays, and Restore brings them back.
        </Typography>
        <TextField
          autoFocus
          fullWidth
          size="small"
          label="Reason"
          placeholder="Duplicates of Row 12"
          value={reason}
          onChange={(e) => setReason(e.target.value)}
          inputProps={{ "data-testid": "archive-reason", maxLength: 200 }}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !blocked) onArchive(reason.trim());
          }}
        />
      </DialogContent>
      <DialogActions sx={{ px: 3, pb: 2 }}>
        <Button color="inherit" onClick={onClose}>
          Cancel
        </Button>
        <CustomTooltip
          show={Boolean(blocked)}
          title={blocked ?? ""}
          size="small"
        >
          <Box component="span">
            <Button
              variant="contained"
              color="primary"
              disabled={Boolean(blocked)}
              onClick={() => onArchive(reason.trim())}
              data-testid="archive-confirm"
            >
              {`Archive ${count === 1 ? "row" : `${count} rows`}`}
            </Button>
          </Box>
        </CustomTooltip>
      </DialogActions>
    </Dialog>
  );
}

ArchiveRowsDialog.propTypes = {
  open: PropTypes.bool,
  count: PropTypes.number,
  onClose: PropTypes.func.isRequired,
  onArchive: PropTypes.func.isRequired,
};
