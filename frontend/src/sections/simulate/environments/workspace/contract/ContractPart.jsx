import PropTypes from "prop-types";
import { Box, Stack, Typography } from "@mui/material";
import SectionCard from "../../components/SectionCard";

// A numbered part of the contract — a SectionCard whose title carries a small
// neutral index tag.
export function Part({ n, title, blurb, children }) {
  return (
    <SectionCard
      sx={{ mb: 2 }}
      title={
        <Stack direction="row" alignItems="center" spacing={1.25}>
          <Box
            sx={{
              width: 20, height: 18, borderRadius: "4px", display: "grid", placeItems: "center", flexShrink: 0,
              color: "text.secondary", bgcolor: "action.selected",
              typography: "s3", fontWeight: 500,
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

// A small column label used inside the spaces and episode grids.
export function Label({ children }) {
  return (
    <Typography
      sx={{
        typography: "s1", fontWeight: 500, color: "text.secondary", mb: 1,
      }}
    >
      {children}
    </Typography>
  );
}
Label.propTypes = { children: PropTypes.node };
