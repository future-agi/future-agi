// W7-4 remote scorers: the inputs sent with the output (the editor's
// mapping: a name the scorer reads -> the dataset column it comes from).
import React from "react";
import PropTypes from "prop-types";
import {
  Alert,
  Box,
  Button,
  IconButton,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";

export default function RemoteInputsField({ inputs, onChange, error }) {
  const edit = (i, k) => (e) =>
    onChange(
      inputs.map((y, j) => (j === i ? { ...y, [k]: e.target.value } : y)),
    );
  return (
    <Box>
      <Typography variant="caption" color="text.secondary">
        Inputs sent with the output (name → dataset column)
      </Typography>
      {inputs.map((x, i) => (
        <Stack key={i} direction="row" spacing={1} sx={{ mt: 0.75 }}>
          <TextField
            size="small"
            label="Name"
            value={x.name}
            onChange={edit(i, "name")}
          />
          <TextField
            size="small"
            label="Column"
            value={x.column}
            onChange={edit(i, "column")}
          />
          <IconButton
            size="small"
            aria-label="Remove input"
            onClick={() => onChange(inputs.filter((_, j) => j !== i))}
          >
            <Iconify icon="mdi:close" width={16} />
          </IconButton>
        </Stack>
      ))}
      <Button
        size="small"
        sx={{ mt: 0.5 }}
        onClick={() => onChange([...inputs, { name: "", column: "" }])}
      >
        Add input
      </Button>
      {error && <Alert severity="warning">{error}</Alert>}
    </Box>
  );
}

RemoteInputsField.propTypes = {
  inputs: PropTypes.array.isRequired,
  onChange: PropTypes.func.isRequired,
  error: PropTypes.string,
};
