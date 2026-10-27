import PropTypes from "prop-types";
import { Box } from "@mui/material";
import { alpha, lighten } from "@mui/material/styles";

const TYPE_CHIPS = {
  text: { hue: "#3b6ce7", lightBg: "#f0f4ff" },
  numeric: { hue: "#1a8a4a", lightBg: "#f0faf4" },
  categorical: { hue: "#c4631a", lightBg: "#fef6ee" },
  thumbs_up_down: { hue: "#c026a3", lightBg: "#fdf2f8" },
  star: { hue: "#b45309", lightBg: "#fffbeb" },
};

const FALLBACK = { hue: "#6b7280", lightBg: "#f5f5f5" };

export default function LabelTypeChip({ type }) {
  const label = (type || "")
    .replace(/_/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase());
  const { hue, lightBg } = TYPE_CHIPS[type] || FALLBACK;

  return (
    <Box
      sx={{
        px: 1,
        py: 0.25,
        borderRadius: 0.5,
        fontSize: 11,
        fontWeight: 500,
        whiteSpace: "nowrap",
        border: "1px solid",
        bgcolor: (theme) =>
          theme.palette.mode === "dark" ? alpha(hue, 0.18) : lightBg,
        borderColor: (theme) =>
          theme.palette.mode === "dark" ? alpha(hue, 0.32) : "transparent",
        color: (theme) =>
          theme.palette.mode === "dark" ? lighten(hue, 0.45) : hue,
      }}
    >
      {label}
    </Box>
  );
}

LabelTypeChip.propTypes = {
  type: PropTypes.string,
};
