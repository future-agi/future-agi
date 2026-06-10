import React from "react";
import CellActionToolTip from "../CellActionToolTip/CellActionToolTip";
import PropTypes from "prop-types";
import { Box } from "@mui/material";

const RunButtonContent = ({ onClick, component, runButtonTestId }) => {
  return (
    <Box
      onClick={onClick}
      data-testid={runButtonTestId}
      sx={{
        padding: "8px",
      }}
    >
      {component}
    </Box>
  );
};

RunButtonContent.propTypes = {
  onClick: PropTypes.func,
  component: PropTypes.node,
  runButtonTestId: PropTypes.string,
};

const CellRunButton = ({
  onClick,
  component,
  children,
  show = true,
  runButtonTestId,
}) => {
  if (!show) return <>{children}</>;

  return (
    <CellActionToolTip
      title={
        <RunButtonContent
          onClick={onClick}
          component={component}
          runButtonTestId={runButtonTestId}
        />
      }
      placement="right"
    >
      {children}
    </CellActionToolTip>
  );
};

export default CellRunButton;

CellRunButton.propTypes = {
  onClick: PropTypes.func,
  component: PropTypes.node,
  children: PropTypes.node,
  show: PropTypes.bool,
  runButtonTestId: PropTypes.string,
};
