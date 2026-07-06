import React, { useState, useCallback } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  Divider,
  IconButton,
  Popover,
  TextField,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";

const SaveViewPopover = ({ anchorEl, open, onClose, onSave, isLoading }) => {
  const [name, setName] = useState("");

  const handleSave = useCallback(() => {
    if (!name.trim()) return;
    onSave(name.trim());
    setName("");
  }, [name, onSave]);

  const handleClose = useCallback(() => {
    setName("");
    onClose();
  }, [onClose]);

  return (
    <Popover
      open={open}
      anchorEl={anchorEl}
      onClose={handleClose}
      anchorOrigin={{ vertical: "bottom", horizontal: "right" }}
      transformOrigin={{ vertical: "top", horizontal: "right" }}
      slotProps={{
        paper: {
          sx: {
            width: 280,
            borderRadius: "4px",
            border: "1px solid",
            borderColor: "divider",
            boxShadow: "1px 1px 12px 10px rgba(0,0,0,0.04)",
            p: 1,
            mt: 0.5,
          },
        },
      }}
    >
      {/* Header */}
      <Box
        sx={{
          display: "flex",
          alignItems: "flex-start",
          gap: 0.5,
          px: 0.5,
          py: 0.25,
        }}
      >
        <Box sx={{ flex: 1 }}>
          <Typography
            sx={{
              fontSize: 15,
              fontWeight: 600,
              color: "text.primary",
              fontFamily: "'IBM Plex Sans', sans-serif",
              lineHeight: "22px",
            }}
          >
            Save view
          </Typography>
          <Typography
            sx={{
              fontSize: 12,
              color: "text.secondary",
              fontFamily: "'IBM Plex Sans', sans-serif",
              lineHeight: "18px",
            }}
          >
            Save your current trace view for quick access later.
          </Typography>
        </Box>
        <IconButton size="small" onClick={handleClose} sx={{ p: 0.25 }}>
          <Iconify icon="mdi:close" width={16} />
        </IconButton>
      </Box>

      <Divider sx={{ my: 1 }} />

      {/* Input */}
      <Box sx={{ px: 0.5, py: 0.25 }}>
        <TextField
          fullWidth
          size="small"
          label={
            <span>
              View Name<span style={{ color: "#d92d20" }}>*</span>
            </span>
          }
          placeholder="Enter your view name"
          value={name}
          onChange={(e) => setName(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") handleSave();
          }}
          autoFocus
          InputLabelProps={{
            sx: {
              fontSize: 11,
              fontWeight: 500,
              fontFamily: "'IBM Plex Sans', sans-serif",
              color: "text.secondary",
            },
          }}
          InputProps={{
            sx: {
              fontSize: 14,
              fontFamily: "'IBM Plex Sans', sans-serif",
              borderRadius: "4px",
            },
          }}
        />
      </Box>

      <Divider sx={{ mt: 1.5, mb: 0.5 }} />

      {/* Actions */}
      <Box
        sx={{
          display: "flex",
          justifyContent: "flex-end",
          gap: 1,
          px: 0.5,
          pb: 0.5,
        }}
      >
        <Button
          size="small"
          variant="outlined"
          onClick={handleClose}
          sx={{
            textTransform: "none",
            fontSize: 12,
            fontWeight: 500,
            fontFamily: "'IBM Plex Sans', sans-serif",
            borderColor: "text.disabled",
            color: "text.primary",
            borderRadius: "2px",
            px: 2,
            py: 0.25,
          }}
        >
          Cancel
        </Button>
        <Button
          size="small"
          variant="contained"
          onClick={handleSave}
          disabled={!name.trim() || isLoading}
          sx={{
            textTransform: "none",
            fontSize: 12,
            fontWeight: 500,
            fontFamily: "'IBM Plex Sans', sans-serif",
            borderRadius: "2px",
            px: 2,
            py: 0.25,
            ...(name.trim()
              ? {}
              : {
                  bgcolor: "text.disabled",
                  color: "background.paper",
                  "&:hover": { bgcolor: "text.disabled" },
                }),
            "&.Mui-disabled": {
              bgcolor: "text.disabled",
              color: "background.paper",
            },
          }}
        >
          Save view
        </Button>
      </Box>
    </Popover>
  );
};

SaveViewPopover.propTypes = {
  anchorEl: PropTypes.any,
  open: PropTypes.bool.isRequired,
  onClose: PropTypes.func.isRequired,
  onSave: PropTypes.func.isRequired,
  isLoading: PropTypes.bool,
};

export default React.memo(SaveViewPopover);
