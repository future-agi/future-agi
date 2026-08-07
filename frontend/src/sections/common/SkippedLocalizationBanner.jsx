import PropTypes from "prop-types";
import { Box, Typography } from "@mui/material";
import Iconify from "src/components/iconify";

const DEFAULT_SKIPPED_LOCALIZATION_MESSAGE =
  "Error localization was skipped — input data isn't available to localize on.";

export default function SkippedLocalizationBanner({ message, sx }) {
  return (
    <Box
      sx={{
        display: "flex",
        alignItems: "center",
        gap: 1.25,
        px: 1.5,
        py: 1,
        borderRadius: "6px",
        border: "1px solid",
        borderColor: "info.light",
        backgroundColor: (theme) =>
          theme.palette.mode === "dark"
            ? "rgba(47, 124, 247, 0.08)"
            : "rgba(47, 124, 247, 0.06)",
        ...sx,
      }}
    >
      <Iconify
        icon="solar:forbidden-circle-bold"
        width={18}
        sx={{ color: "info.main", flexShrink: 0 }}
      />
      <Typography variant="caption" color="text.secondary" sx={{ fontSize: 11 }}>
        {message || DEFAULT_SKIPPED_LOCALIZATION_MESSAGE}
      </Typography>
    </Box>
  );
}

SkippedLocalizationBanner.propTypes = {
  message: PropTypes.string,
  sx: PropTypes.object,
};
