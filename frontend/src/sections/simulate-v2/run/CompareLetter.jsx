import PropTypes from "prop-types";
import { Box } from "@mui/material";
import { alpha } from "@mui/material/styles";
import { textTone } from "../components/textTone";

/* The run's identity badge — the same letter and colour the runs list gave it. */
export default function CompareLetter({ letter, color, size = 20 }) {
  return (
    <Box
      sx={{
        width: size,
        height: size,
        borderRadius: "4px",
        flexShrink: 0,
        display: "grid",
        placeItems: "center",
        typography: "s3",
        fontWeight: 600,
        color: textTone(color),
        bgcolor: (t) => alpha(color, t.palette.mode === "dark" ? 0.22 : 0.14),
      }}
    >
      {letter}
    </Box>
  );
}
CompareLetter.propTypes = {
  letter: PropTypes.string,
  color: PropTypes.string,
  size: PropTypes.number,
};
