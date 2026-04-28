import React, { memo } from "react";
import PropTypes from "prop-types";
import { Box, Stack, Typography, TextField, MenuItem, Select, InputLabel, FormControl } from "@mui/material";
import SvgColor from "src/components/svg-color";

const ApiCallNode = ({ data, isEditing, onEdit }) => {
  const { label, nodeConfig } = data;
  
  if (!isEditing) {
    return (
      <Box sx={{ minWidth: 200, p: 2 }}>
        <Stack direction="row" spacing={1} alignItems="center" sx={{ mb: 1 }}>
          <Box
            sx={{
              width: 20,
              height: 20,
              borderRadius: 0.5,
              border: "1px solid",
              borderColor: "divider",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              flexShrink: 0,
            }}
          >
            <SvgColor
              src="/assets/icons/navbar/ic_api.svg"
              sx={{
                width: 16,
                height: 16,
                bgcolor: "blue.600",
              }}
            />
          </Box>
          <Typography variant="subtitle2" noWrap>
            {label}
          </Typography>
        </Stack>
        <Typography variant="body2" color="text.secondary">
          {nodeConfig?.method || "GET"} {nodeConfig?.url || "https://api.example.com"}
        </Typography>
      </Box>
    );
  }

  // Edit mode
  const handleConfigChange = (field, value) => {
    onEdit({
      ...nodeConfig,
      [field]: value
    });
  };

  return (
    <Box sx={{ minWidth: 300, p: 2 }}>
      <TextField
        label="Node Label"
        fullWidth
        value={label}
        onChange={(e) => onEdit({ ...nodeConfig, label: e.target.value })}
        sx={{ mb: 2 }}
      />
      
      <FormControl fullWidth sx={{ mb: 2 }}>
        <InputLabel>HTTP Method</InputLabel>
        <Select
          value={nodeConfig?.method || "GET"}
          onChange={(e) => handleConfigChange("method", e.target.value)}
        >
          <MenuItem value="GET">GET</MenuItem>
          <MenuItem value="POST">POST</MenuItem>
          <MenuItem value="PUT">PUT</MenuItem>
          <MenuItem value="DELETE">DELETE</MenuItem>
          <MenuItem value="PATCH">PATCH</MenuItem>
        </Select>
      </FormControl>
      
      <TextField
        label="URL"
        fullWidth
        value={nodeConfig?.url || ""}
        onChange={(e) => handleConfigChange("url", e.target.value)}
        sx={{ mb: 2 }}
      />
      
      <TextField
        label="Headers (JSON)"
        fullWidth
        multiline
        rows={3}
        value={nodeConfig?.headers ? JSON.stringify(nodeConfig.headers, null, 2) : ""}
        onChange={(e) => {
          try {
            const parsed = JSON.parse(e.target.value);
            handleConfigChange("headers", parsed);
          } catch (err) {
            // If invalid JSON, just set as is for user to fix
            handleConfigChange("headers", e.target.value);
          }
        }}
        sx={{ mb: 2 }}
      />
      
      <TextField
        label="Body (JSON)"
        fullWidth
        multiline
        rows={3}
        value={nodeConfig?.body ? JSON.stringify(nodeConfig.body, null, 2) : ""}
        onChange={(e) => {
          try {
            const parsed = JSON.parse(e.target.value);
            handleConfigChange("body", parsed);
          } catch (err) {
            // If invalid JSON, just set as is for user to fix
            handleConfigChange("body", e.target.value);
          }
        }}
        sx={{ mb: 2 }}
      />
      
      <TextField
        label="Timeout (seconds)"
        type="number"
        fullWidth
        value={nodeConfig?.timeout || 30}
        onChange={(e) => handleConfigChange("timeout", parseInt(e.target.value) || 30)}
        sx={{ mb: 2 }}
      />
    </Box>
  );
};

ApiCallNode.propTypes = {
  data: PropTypes.shape({
    label: PropTypes.string,
    nodeConfig: PropTypes.object,
  }).isRequired,
  isEditing: PropTypes.bool,
  onEdit: PropTypes.func,
};

const MemoizedApiCallNode = memo(ApiCallNode);
MemoizedApiCallNode.displayName = "ApiCallNode";

export default MemoizedApiCallNode;