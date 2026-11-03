import { Box, Button, useTheme } from "@mui/material";
import PropTypes from "prop-types";
import React from "react";
import { useLocation, useNavigate } from "react-router";
import CustomBreadcrumbs from "src/components/custom-breadcrumbs";
import Iconify from "src/components/iconify";

const ProjectBreadCrumbs = ({ links, onBack }) => {
  const navigate = useNavigate();
  const { pathname } = useLocation();
  const theme = useTheme();

  const internalBackButton = () => {
    const pathSplit = pathname.split("/");
    // const secondLastPath = pathSplit[pathSplit.length - 2];
    // if (secondLastPath === "project") {
    //   navigate("/dashboard/projects/experiment");
    // } else {
    navigate(pathSplit.slice(0, -1).join("/"));
    // }
  };

  const handleBack = onBack || internalBackButton;

  return (
    <Box display="flex" alignItems="center" gap={theme.spacing(1)}>
      {/* <ObserveBackButton onBack={handleBack} />  */}
      <Button
        size="small"
        startIcon={
          <Iconify
            icon="solar:alt-arrow-left-linear"
            width={16}
            height={16}
            color={"text.primary"}
          />
        }
        onClick={handleBack}
        variant="outlined"
        sx={{
          color: "text.primary",
          borderColor: "action.hover",
        }}
      >
        Back
      </Button>
      <CustomBreadcrumbs links={links} />
    </Box>
  );
};

ProjectBreadCrumbs.propTypes = {
  links: PropTypes.array,
  onBack: PropTypes.func,
};

export default ProjectBreadCrumbs;
