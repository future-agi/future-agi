import PropTypes from "prop-types";
import React from "react";
import { Box, Typography } from "@mui/material";
import ComingSoonChip from "src/sections/simulate/environments/components/ComingSoonChip";
import { Card } from "./bits";

/** An area that isn't live yet: its name, a Coming soon chip, what it will do. */
export default function ComingSoonCard({ title, description }) {
  return (
    <Box component="section" aria-label={title}>
      <Card title={title} action={<ComingSoonChip />}>
        <Typography
          sx={{ px: 2, py: 1.5, typography: "s2", color: "text.subtitle" }}
        >
          {description}
        </Typography>
      </Card>
    </Box>
  );
}

ComingSoonCard.propTypes = {
  title: PropTypes.string.isRequired,
  description: PropTypes.string.isRequired,
};
