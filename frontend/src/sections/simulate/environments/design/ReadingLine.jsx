import PropTypes from "prop-types";
import { CircularProgress, Stack, Typography } from "@mui/material";

/* The one "still working" line on the design page, while a part is read or a
   build stage runs. Kept apart so the shared shimmer can replace it. */
export default function ReadingLine({ text }) {
  return (
    <Stack
      direction="row"
      alignItems="center"
      spacing={1}
      sx={{ px: 1.5, py: 1.25 }}
    >
      <CircularProgress
        size={12}
        thickness={5}
        sx={{
          "@media (prefers-reduced-motion: reduce)": {
            animation: "none",
            "& circle": { animation: "none" },
          },
        }}
      />
      <Typography sx={{ typography: "s1", color: "text.tertiary" }}>
        {text}
      </Typography>
    </Stack>
  );
}
ReadingLine.propTypes = { text: PropTypes.string.isRequired };
