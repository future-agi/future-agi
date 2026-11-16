import EmptyContent from "src/components/empty-content";
import { Button } from "@mui/material";
import React from "react";
import Iconify from "src/components/iconify";
import PropTypes from "prop-types";

const NoAnnotationLabelsSection = ({ onCreateLabel }) => {
  return (
    <EmptyContent
      title="No annotations yet"
      action={
        <Button
          variant="outlined"
          color="primary"
          size="small"
          startIcon={<Iconify icon="solar:add-linear" width={16} height={16} />}
          onClick={onCreateLabel}
        >
          Add Label
        </Button>
      }
    />
  );
};

NoAnnotationLabelsSection.propTypes = {
  onCreateLabel: PropTypes.func,
};

export default NoAnnotationLabelsSection;
