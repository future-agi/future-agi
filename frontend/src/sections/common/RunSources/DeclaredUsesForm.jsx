// IT-R4 G1 P5: `What this run uses` for Your code: Model, Tools, MCP
// servers, all optional (Review lists them and estimates the model cost).
import React from "react";
import PropTypes from "prop-types";
import { Autocomplete, Stack, TextField, Typography } from "@mui/material";
import { DECLARED_MODELS } from "./declaredUses";

const field = { "& .MuiInputBase-root": { typography: "s2", py: 0.25 } };

export default function DeclaredUsesForm({ value, onChange, tools, mcp }) {
  const v = value ?? {};
  const set = (patch) => onChange({ ...v, ...patch });
  return (
    <Stack spacing={1.5} data-testid="declared-uses">
      <Typography typography="s3" color="text.secondary" sx={{ mb: 0.25 }}>
        Optional: lets us estimate cost and list them like harness runs.
      </Typography>
      <Autocomplete
        freeSolo
        size="small"
        options={DECLARED_MODELS}
        value={v.model ?? ""}
        onInputChange={(_, model) => set({ model })}
        renderInput={(params) => (
          <TextField
            {...params}
            label="Model"
            placeholder="gpt-4.1"
            inputProps={{
              ...params.inputProps,
              "data-testid": "declared-model",
            }}
            sx={field}
          />
        )}
      />
      <Autocomplete
        multiple
        freeSolo
        size="small"
        options={tools ?? []}
        value={v.tools ?? []}
        onChange={(_, list) => set({ tools: list })}
        renderInput={(params) => (
          <TextField
            {...params}
            label="Tools"
            placeholder="lookup_order"
            sx={field}
          />
        )}
      />
      <Autocomplete
        multiple
        freeSolo
        size="small"
        options={mcp ?? []}
        value={v.mcp ?? []}
        onChange={(_, list) => set({ mcp: list })}
        renderInput={(params) => (
          <TextField
            {...params}
            label="MCP servers"
            placeholder="acme-support-sandbox"
            sx={field}
          />
        )}
      />
    </Stack>
  );
}

DeclaredUsesForm.propTypes = {
  value: PropTypes.object,
  onChange: PropTypes.func.isRequired,
  tools: PropTypes.array,
  mcp: PropTypes.array,
};
