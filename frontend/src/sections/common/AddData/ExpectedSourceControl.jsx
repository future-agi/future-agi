import React from "react";
import PropTypes from "prop-types";
import {
  Box,
  Chip,
  MenuItem,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import { alpha, useTheme } from "@mui/material/styles";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import { EXPECTED_OPTIONS, PRODUCTION_WARNING } from "./shapes";

/** The amber chip for an expected value copied from the model's output. */
export const ProductionWarningChip = ({ text = PRODUCTION_WARNING }) => {
  const theme = useTheme();
  const dark = theme.palette.mode === "dark";
  return (
    <Chip
      size="small"
      icon={<Iconify icon="mdi:alert-outline" width={14} />}
      label={text}
      sx={{
        height: "auto",
        py: 0.25,
        alignSelf: "flex-start",
        typography: "s3",
        fontWeight: 500,
        bgcolor: alpha(theme.palette.warning.main, dark ? 0.2 : 0.12),
        color: dark ? "warning.light" : "warning.dark",
        "& .MuiChip-icon": { color: "inherit", ml: 0.75 },
        "& .MuiChip-label": { whiteSpace: "normal", px: 0.75 },
      }}
    />
  );
};
ProductionWarningChip.propTypes = { text: PropTypes.string };

/**
 * Expected output, safely (R2-SPEC §7.3.4): `Expected reply from` with
 * `Leave empty, needs review` as the default. `Production reply` shows the
 * amber chip. With `locked`, the value is fixed (annotation queues take the
 * reviewer's correction) and the reason shows in a tooltip.
 */
const ExpectedSourceControl = ({ value = "empty", onChange, locked }) => (
  <Stack spacing={0.75}>
    <Stack direction="row" spacing={1} alignItems="center">
      <Typography
        typography="s2"
        color="text.secondary"
        sx={{ flexShrink: 0, width: 128 }}
        component="label"
        htmlFor="add-data-expected"
      >
        Expected reply
      </Typography>
      <CustomTooltip show={Boolean(locked)} title={locked} size="small" arrow>
        <Box sx={{ flex: 1, minWidth: 0 }}>
          <TextField
            id="add-data-expected"
            select
            size="small"
            fullWidth
            disabled={Boolean(locked)}
            value={locked ? "human_correction" : value}
            onChange={(e) => onChange?.(e.target.value)}
            sx={{ "& .MuiSelect-select": { typography: "s2", py: 0.75 } }}
          >
            {EXPECTED_OPTIONS.map((o) => (
              <MenuItem key={o.id} value={o.id} sx={{ typography: "s2" }}>
                {o.id === "empty"
                  ? "Leave empty (you review it later)"
                  : o.id === "human_correction" && locked
                    ? "Reviewer correction, else empty"
                    : o.label}
              </MenuItem>
            ))}
          </TextField>
        </Box>
      </CustomTooltip>
    </Stack>
    {value === "production_output" && !locked && <ProductionWarningChip />}
  </Stack>
);

ExpectedSourceControl.propTypes = {
  value: PropTypes.oneOf(["empty", "human_correction", "production_output"]),
  onChange: PropTypes.func,
  locked: PropTypes.string,
};

export default ExpectedSourceControl;
