import PropTypes from "prop-types";
import { Box } from "@mui/material";
import ShimmerText from "../components/ShimmerText";

// The one "still working" line on the design page, while a part is read or a
// build stage runs.
export default function ReadingLine({ text }) {
  return (
    <Box sx={{ px: 1.5, py: 1.25 }}>
      <ShimmerText>{text}</ShimmerText>
    </Box>
  );
}
ReadingLine.propTypes = { text: PropTypes.string.isRequired };
