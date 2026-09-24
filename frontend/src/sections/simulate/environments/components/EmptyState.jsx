import PropTypes from "prop-types";
import { Box, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";

// Centred placeholder for a section with nothing to show yet: a muted icon
// tile, a title, optional body copy and an optional action node.
export default function EmptyState({ icon, title, body, action }) {
  return (
    <Stack
      alignItems="center"
      justifyContent="center"
      spacing={1.5}
      sx={{ py: 8, px: 3, textAlign: "center" }}
    >
      <Box
        sx={{
          width: 52,
          height: 52,
          borderRadius: 1.5,
          display: "grid",
          placeItems: "center",
          bgcolor: "background.neutral",
          color: "text.subtitle",
        }}
      >
        <Iconify icon={icon || "solar:box-minimalistic-linear"} width={26} />
      </Box>
      <Box>
        <Typography sx={{ typography: "s1", fontWeight: "fontWeightSemiBold" }}>
          {title}
        </Typography>
        {body && (
          <Typography
            sx={{ typography: "s2", color: "text.subtitle", maxWidth: 420, mt: 0.5 }}
          >
            {body}
          </Typography>
        )}
      </Box>
      {action}
    </Stack>
  );
}

EmptyState.propTypes = {
  icon: PropTypes.string,
  title: PropTypes.node,
  body: PropTypes.node,
  action: PropTypes.node,
};
