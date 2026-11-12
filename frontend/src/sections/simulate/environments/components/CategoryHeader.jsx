import PropTypes from "prop-types";
import { Box, Stack, Typography } from "@mui/material";

export default function CategoryHeader({ label, count, blurb }) {
  return (
    <Box>
      <Stack direction="row" alignItems="center" spacing={1.25}>
        <Typography sx={{ typography: "s1_2", fontWeight: 500 }}>
          {label}
        </Typography>
        <Typography
          sx={{
            typography: "s3",
            fontWeight: 500,
            color: "text.subtitle",
            fontVariantNumeric: "tabular-nums",
          }}
        >
          {String(count).padStart(2, "0")}
        </Typography>
        <Box sx={{ flex: 1, height: "1px", bgcolor: "divider" }} />
      </Stack>
      {blurb && (
        <Typography sx={{ typography: "s3", color: "text.subtitle", mt: 0.5 }}>
          {blurb}
        </Typography>
      )}
    </Box>
  );
}
CategoryHeader.propTypes = { label: PropTypes.string, count: PropTypes.number, blurb: PropTypes.string };
