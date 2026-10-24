import React, { useState, useEffect, useCallback } from "react";
import PropTypes from "prop-types";
import Dialog from "@mui/material/Dialog";
import DialogTitle from "@mui/material/DialogTitle";
import DialogContent from "@mui/material/DialogContent";
import DialogActions from "@mui/material/DialogActions";
import Button from "@mui/material/Button";
import Box from "@mui/material/Box";
import Typography from "@mui/material/Typography";
import Switch from "@mui/material/Switch";
import CircularProgress from "@mui/material/CircularProgress";
import Divider from "@mui/material/Divider";
import { useTheme } from "@mui/material/styles";
import Iconify from "src/components/iconify";
import {
  discoverConnectorTools,
  updateConnectorTools,
} from "src/sections/falcon-ai/hooks/useFalconAPI";

export default function ConnectorToolList({ open, connector, onClose }) {
  const _theme = useTheme();
  const [tools, setTools] = useState([]);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);

  const loadTools = useCallback(async () => {
    if (!connector?.id) return;
    setLoading(true);
    try {
      const data = await discoverConnectorTools(connector.id);
      setTools(data.tools || data || []);
    } catch {
      setTools([]);
    } finally {
      setLoading(false);
    }
  }, [connector?.id]);

  useEffect(() => {
    if (open && connector) {
      loadTools();
    }
  }, [open, connector, loadTools]);

  const handleToggle = (index) => {
    setTools((prev) =>
      prev.map((t, i) => (i === index ? { ...t, enabled: !t.enabled } : t)),
    );
  };

  const handleEnableAll = () => {
    setTools((prev) => prev.map((t) => ({ ...t, enabled: true })));
  };

  const handleDisableAll = () => {
    setTools((prev) => prev.map((t) => ({ ...t, enabled: false })));
  };

  const handleSave = async () => {
    if (!connector?.id) return;
    setSaving(true);
    try {
      await updateConnectorTools(connector.id, tools);
      onClose();
    } catch {
      // silent
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog
      open={open}
      onClose={onClose}
      maxWidth={false}
      PaperProps={{ sx: { maxWidth: 720 } }}
      fullWidth
    >
      <DialogTitle>
        <Box display="flex" alignItems="center" justifyContent="space-between">
          <Typography variant="h6" component="span">
            {connector?.name} — Tools
          </Typography>
          <Box display="flex" gap={1}>
            <Button size="small" onClick={handleEnableAll}>
              Enable All
            </Button>
            <Button size="small" onClick={handleDisableAll}>
              Disable All
            </Button>
          </Box>
        </Box>
      </DialogTitle>

      <DialogContent dividers>
        {loading ? (
          <Box display="flex" justifyContent="center" py={4}>
            <CircularProgress size={28} />
          </Box>
        ) : tools.length === 0 ? (
          <Box py={4} textAlign="center">
            <Iconify
              icon="solar:toolbox-linear"
              width={36}
              sx={{ color: "text.disabled", mb: 1 }}
            />
            <Typography variant="body2" color="text.disabled">
              No tools discovered. Try refreshing.
            </Typography>
          </Box>
        ) : (
          tools.map((tool, index) => (
            <React.Fragment key={tool.name || index}>
              {index > 0 && <Divider />}
              <Box
                sx={{
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "space-between",
                  py: 1,
                  px: 0.5,
                }}
              >
                <Box sx={{ minWidth: 0, flex: 1 }}>
                  <Typography
                    variant="subtitle2"
                    sx={{
                      color: "text.primary",
                      fontSize: 13,
                      fontWeight: 500,
                    }}
                  >
                    {tool.name}
                  </Typography>
                  {tool.description && (
                    <Typography
                      variant="caption"
                      sx={{
                        color: "text.tertiary",
                        display: "block",
                        mt: 0.25,
                      }}
                    >
                      {tool.description}
                    </Typography>
                  )}
                </Box>
                <Switch
                  size="small"
                  checked={tool.enabled !== false}
                  onChange={() => handleToggle(index)}
                />
              </Box>
            </React.Fragment>
          ))
        )}
      </DialogContent>

      <DialogActions sx={{ px: 3, pb: 2 }}>
        <Button
          onClick={loadTools}
          disabled={loading}
          startIcon={<Iconify icon="solar:refresh-linear" width={20} />}
          sx={{ mr: "auto" }}
        >
          Refresh
        </Button>
        <Button onClick={onClose} color="inherit">
          Cancel
        </Button>
        <Button
          onClick={handleSave}
          variant="contained"
          disabled={saving}
          startIcon={
            saving ? <CircularProgress size={16} color="inherit" /> : null
          }
        >
          Save
        </Button>
      </DialogActions>
    </Dialog>
  );
}

ConnectorToolList.propTypes = {
  open: PropTypes.bool.isRequired,
  connector: PropTypes.object,
  onClose: PropTypes.func.isRequired,
};
