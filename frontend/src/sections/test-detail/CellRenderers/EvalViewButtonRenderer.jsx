import { Button } from "@mui/material";
import React from "react";
import PropTypes from "prop-types";
import { useTestDetailSideDrawerStoreShallow } from "../states";

const EvalViewButtonRenderer = ({ data }) => {
  const setEvalView = useTestDetailSideDrawerStoreShallow(
    (state) => state.setEvalView,
  );

  return (
    <Button
      className="row-hover-action"
      variant="text"
      size="small"
      color="primary"
      onClick={() =>
        setEvalView({
          metricDetail: data?.metricDetails,
          errorLocalizerTask: data?.errorLocalizerTask,
        })
      }
      disabled={data?.isLoading}
    >
      View Detail
    </Button>
  );
};

EvalViewButtonRenderer.propTypes = {
  data: PropTypes.object,
};

export default EvalViewButtonRenderer;
