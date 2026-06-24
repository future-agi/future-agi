import React from "react";
import PropTypes from "prop-types";
import { Box, Typography } from "@mui/material";
import { alpha } from "@mui/material/styles";
import Iconify from "src/components/iconify";

// Pass/fail counts + progress bar; `onFix` (when set) renders the Fix-with-Falcon CTA.
const SummaryBar = ({ passed, total, failedCount, passRate, onFix }) => (
  <Box
    sx={{
      px: 1.5,
      py: 1,
      borderBottom: "1px solid",
      borderColor: "divider",
      flexShrink: 0,
    }}
  >
    <Box sx={{ display: "flex", alignItems: "center", gap: 0.75, mb: 0.5 }}>
      <Typography sx={{ fontSize: 13, fontWeight: 600 }}>
        {passed}/{total} passed
      </Typography>
      {failedCount > 0 && (
        <Typography
          sx={{
            fontSize: 11,
            color: "error.main",
            fontWeight: 500,
            bgcolor: (t) => alpha(t.palette.error.main, 0.08),
            px: 0.5,
            py: 0.1,
            borderRadius: "3px",
          }}
        >
          {failedCount} failed
        </Typography>
      )}
    </Box>
    <Box
      sx={{
        height: 4,
        borderRadius: 2,
        bgcolor: (t) => alpha(t.palette.text.disabled, 0.12),
        overflow: "hidden",
      }}
    >
      <Box
        sx={{
          height: "100%",
          width: `${passRate}%`,
          bgcolor:
            passRate >= 80
              ? "success.main"
              : passRate >= 50
                ? "warning.main"
                : "error.main",
          borderRadius: 2,
          transition: "width 300ms",
        }}
      />
    </Box>
    {onFix && (
      <Box
        onClick={onFix}
        sx={{
          display: "inline-flex",
          alignItems: "center",
          gap: 0.5,
          mt: 1,
          px: 1,
          py: 0.35,
          border: "1px solid",
          borderColor: (t) => alpha(t.palette.primary.main, 0.4),
          borderRadius: "6px",
          cursor: "pointer",
          bgcolor: (t) => alpha(t.palette.primary.main, 0.06),
          "&:hover": {
            bgcolor: (t) => alpha(t.palette.primary.main, 0.12),
            borderColor: (t) => alpha(t.palette.primary.main, 0.5),
          },
        }}
      >
        <Iconify icon="mdi:creation" width={14} color="primary.main" />
        <Typography sx={{ fontSize: 11, fontWeight: 600, color: "primary.main" }}>
          Fix with Falcon
        </Typography>
      </Box>
    )}
  </Box>
);

SummaryBar.propTypes = {
  passed: PropTypes.number,
  total: PropTypes.number,
  failedCount: PropTypes.number,
  passRate: PropTypes.number,
  onFix: PropTypes.func,
};

export default SummaryBar;
