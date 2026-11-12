import Box from "@mui/material/Box/Box";
import React from "react";
import InitialSetup from "./InitialSetup";
import { Divider } from "@mui/material";
import InitialSetupOptions from "./InitialSetupOptions/InitialSetupOptions";
import PropTypes from "prop-types";

const SetupStepsView = ({
  setShowDemoModal,
  data,
  setCurrentLabel,
  currentLabel,
}) => {
  return (
    <Box
      sx={{
        width: "100%",
        minHeight: "480px",
        // The page is a column of fixed height: without this the step box is
        // squeezed to its minimum and a step's own button falls below it.
        flexShrink: 0,
        // mt:'24px',
        borderRadius: "8px",
        border: "1px solid",
        borderColor: "background.neutral",
        display: "flex",
        // gap: "5px",
      }}
    >
      <InitialSetup
        currentLabel={currentLabel}
        setCurrentLabel={setCurrentLabel}
        setShowDemoModal={setShowDemoModal}
        data={data}
      />
      <Divider orientation="vertical" flexItem />
      <InitialSetupOptions
        currentLabel={currentLabel}
        setCurrentLabel={setCurrentLabel}
        data={data}
      />
    </Box>
  );
};

export default SetupStepsView;

SetupStepsView.propTypes = {
  setShowDemoModal: PropTypes.func,
  data: PropTypes.object,
  setCurrentLabel: PropTypes.func,
  currentLabel: PropTypes.string,
};
