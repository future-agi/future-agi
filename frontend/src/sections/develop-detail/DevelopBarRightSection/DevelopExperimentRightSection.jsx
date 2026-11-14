import { Box } from "@mui/material";
import React from "react";
import PropTypes from "prop-types";
import DevelopeSearch from "src/sections/common/DevelopeSearch";

const DevelopExperimentRightSection = ({
  experimentSearch,
  setExperimentSearch,
}) => {
  return (
    <Box sx={{ display: "flex", alignItems: "center" }}>
      <DevelopeSearch
        experimentSearch={experimentSearch}
        setExperimentSearch={setExperimentSearch}
      />
      {/* <IconButton size="small" sx={{ color: "text.secondary" }} onClick={() => {}}>
        <Iconify icon="solar:filter-linear" />
      </IconButton> */}
    </Box>
  );
};

DevelopExperimentRightSection.propTypes = {
  experimentSearch: PropTypes.string,
  setExperimentSearch: PropTypes.func,
};

export default DevelopExperimentRightSection;
