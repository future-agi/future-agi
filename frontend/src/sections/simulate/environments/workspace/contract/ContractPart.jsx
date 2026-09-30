import PropTypes from "prop-types";
import { alpha } from "@mui/material/styles";
import { Box, Stack, Typography } from "@mui/material";
import SectionCard from "../../components/SectionCard";

// A numbered part of the contract — a SectionCard whose title carries a small
// round index badge.
export function Part({ n, title, blurb, children }) {
  return (
    <SectionCard
      sx={{ mb: 2 }}
      title={
        <Stack direction="row" alignItems="center" spacing={1.25}>
          <Box
            sx={{
              width: 20, height: 20, borderRadius: "50%", display: "grid", placeItems: "center", flexShrink: 0,
              color: "primary.main", bgcolor: (t) => alpha(t.palette.primary.main, 0.12),
              typography: "s3", fontWeight: "fontWeightBold",
            }}
          >
            {n}
          </Box>
          <span>{title}</span>
        </Stack>
      }
      subtitle={blurb}
    >
      {children}
    </SectionCard>
  );
}
Part.propTypes = {
  n: PropTypes.number,
  title: PropTypes.string,
  blurb: PropTypes.string,
  children: PropTypes.node,
};

// A small uppercase column label used inside the spaces and episode grids.
export function Label({ children }) {
  return (
    <Typography
      sx={{
        typography: "s3", fontWeight: "fontWeightBold", color: "text.subtitle",
        textTransform: "uppercase", letterSpacing: 0.4, mb: 1,
      }}
    >
      {children}
    </Typography>
  );
}
Label.propTypes = { children: PropTypes.node };
