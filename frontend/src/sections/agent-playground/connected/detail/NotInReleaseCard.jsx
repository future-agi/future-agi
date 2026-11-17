import PropTypes from "prop-types";
import React from "react";
import { Box, Typography } from "@mui/material";
import NotInReleaseChip from "src/components/NotInRelease/NotInReleaseChip";
import { Card } from "./bits";

/** An area not built in this release: its name, the shared line, what it does. */
export default function NotInReleaseCard({ title, description }) {
  return (
    <Box component="section" aria-label={title}>
      <Card title={title} action={<NotInReleaseChip />}>
        <Typography
          sx={{ px: 1.5, py: 1.5, typography: "s1", color: "text.subtitle" }}
        >
          {description}
        </Typography>
      </Card>
    </Box>
  );
}

NotInReleaseCard.propTypes = {
  title: PropTypes.string.isRequired,
  description: PropTypes.string.isRequired,
};
