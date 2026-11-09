// IT-R2 G4 P1 · `Tool choice: Auto ▾` beside the Tools button (the Params
// popover is components/custom-model-options, another lane's file, so the
// choice sits next to the tools it governs). Options: Auto (the model
// decides), Required (it must call a tool), or one named tool. Sent as the
// run's `configuration.tool_choice` (WorkbenchProvider).
import React, { useState } from "react";
import PropTypes from "prop-types";
import { Box, Button, Menu, MenuItem, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";

const BASE = [
  { value: "auto", label: "Auto", hint: "The model decides whether to call" },
  {
    value: "required",
    label: "Required",
    hint: "The model must call a tool",
  },
  // v2.1 g8 (action:workbench:run): the run's script never calls a tool
  // under `none` (promptRunScripts).
  { value: "none", label: "None", hint: "The model never calls a tool" },
];

/** Tool names of the prompt's tools (picker `{name}` or `{function:{name}}`). */
export function toolNamesOf(tools) {
  return (tools ?? [])
    .map((t) => t?.name ?? t?.function?.name ?? t?.config?.function?.name)
    .filter(Boolean);
}

/** `Auto` · `Required` · `lookup_order` for a wire value. Pure. */
export function toolChoiceLabel(value) {
  const v =
    typeof value === "object" && value
      ? value.function?.name ?? value.name
      : value;
  if (!v || v === "auto") return "Auto";
  if (v === "required") return "Required";
  if (v === "none") return "None";
  return String(v);
}

export default function ToolChoiceSelect({ value, tools, onChange, disabled }) {
  const [anchor, setAnchor] = useState(null);
  const names = toolNamesOf(tools);
  if (!names.length) return null;
  const options = [
    ...BASE,
    ...names.map((n) => ({
      value: n,
      label: n,
      hint: `Always call ${n}`,
      mono: true,
    })),
  ];
  const current = toolChoiceLabel(value);
  return (
    <>
      <Button
        size="small"
        variant="outlined"
        color="inherit"
        disabled={disabled}
        onClick={(e) => setAnchor(e.currentTarget)}
        endIcon={<Iconify icon="mdi:chevron-down" width={16} />}
        data-testid="tool-choice"
        aria-label={`Tool choice: ${current}`}
        sx={{
          height: 32,
          flexShrink: 0,
          borderColor: "divider",
          color: "text.primary",
          fontWeight: "fontWeightRegular",
          typography: "s2",
          whiteSpace: "nowrap",
          px: 1.25,
        }}
      >
        <Box component="span" sx={{ color: "text.secondary", mr: 0.5 }}>
          Tool choice:
        </Box>
        {current}
      </Button>
      <Menu
        anchorEl={anchor}
        open={Boolean(anchor)}
        onClose={() => setAnchor(null)}
        anchorOrigin={{ vertical: "bottom", horizontal: "left" }}
      >
        {options.map((o) => (
          <MenuItem
            key={o.value}
            selected={toolChoiceLabel(value) === toolChoiceLabel(o.value)}
            onClick={() => {
              onChange(o.value);
              setAnchor(null);
            }}
          >
            <Stack sx={{ minWidth: 0 }}>
              <Typography
                typography="s1"
                sx={o.mono ? { fontFamily: "ui-monospace, monospace" } : {}}
              >
                {o.label}
              </Typography>
              <Typography typography="s3" color="text.secondary">
                {o.hint}
              </Typography>
            </Stack>
          </MenuItem>
        ))}
      </Menu>
    </>
  );
}

ToolChoiceSelect.propTypes = {
  value: PropTypes.oneOfType([PropTypes.string, PropTypes.object]),
  tools: PropTypes.array,
  onChange: PropTypes.func.isRequired,
  disabled: PropTypes.bool,
};
