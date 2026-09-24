import PropTypes from "prop-types";
import { Box, Stack, Typography, Button, CircularProgress } from "@mui/material";
import Iconify from "src/components/iconify";

export default function ContinueRow({ disabled, busy = false, hint, label = "Build environment", onClick }) {
  return (
    <Stack
      direction="row"
      alignItems="center"
      justifyContent="space-between"
      spacing={2}
      sx={{ pt: 1.25, borderTop: "1px solid", borderColor: "divider", mx: -2.5, px: 2.5, pb: 0 }}
    >
      {disabled && hint ? (
        <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
          {hint}
        </Typography>
      ) : <Box />}
      <Button
        variant="contained"
        color="primary"
        disabled={disabled || busy}
        onClick={onClick}
        startIcon={
          busy
            ? <CircularProgress size={14} color="inherit" />
            : <Iconify icon="solar:magic-stick-3-linear" width={14} />
        }
        endIcon={busy ? null : <Iconify icon="solar:alt-arrow-right-linear" width={14} />}
        sx={{ typography: "s1", fontWeight: "fontWeightBold", px: 2, flexShrink: 0 }}
      >
        {busy ? "Building…" : label}
      </Button>
    </Stack>
  );
}
ContinueRow.propTypes = {
  disabled: PropTypes.bool,
  busy: PropTypes.bool,
  hint: PropTypes.node,
  label: PropTypes.node,
  onClick: PropTypes.func,
};
