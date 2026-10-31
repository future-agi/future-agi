import PropTypes from "prop-types";
import { Box, Stack, Typography } from "@mui/material";

// A section's title and one-line hint. `flag` sits beside the title, for a
// UiOnlyFlag on a section the API doesn't back yet.
export default function SectionHeader({ title, hint, flag }) {
  return (
    <Box>
      <Stack
        direction="row"
        alignItems="center"
        spacing={0.75}
        sx={{ mb: 0.375 }}
      >
        <Typography
          sx={{
            typography: "s2",
            fontWeight: "fontWeightSemiBold",
            color: "text.primary",
          }}
        >
          {title}
        </Typography>
        {flag}
      </Stack>
      {hint && (
        <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
          {hint}
        </Typography>
      )}
    </Box>
  );
}
SectionHeader.propTypes = {
  title: PropTypes.string,
  hint: PropTypes.string,
  flag: PropTypes.node,
};
