import React from "react";
import PropTypes from "prop-types";
import { Typography } from "@mui/material";

// A Users row is one user *within one project* (TH-5037): the same user ID
// active in two projects is two rows. This cell names the row's project so
// those rows never read as duplicates of one cross-project user.
const ProjectCellRenderer = ({ data }) => {
  const projectName = data?.project_name;
  return (
    <Typography
      variant="s1"
      color={projectName ? "text.primary" : "text.disabled"}
      title={projectName || data?.project_id || undefined}
      sx={{
        overflow: "hidden",
        textOverflow: "ellipsis",
        whiteSpace: "nowrap",
      }}
    >
      {projectName || "Unknown project"}
    </Typography>
  );
};

ProjectCellRenderer.propTypes = {
  data: PropTypes.object,
};

export default ProjectCellRenderer;
