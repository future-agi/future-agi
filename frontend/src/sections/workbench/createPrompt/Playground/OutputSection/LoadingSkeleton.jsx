import { Skeleton } from "@mui/material";
import PropTypes from "prop-types";

const LoadingSkeleton = ({ sx }) => {
  return (
    <Skeleton
      animation="pulse"
      sx={{
        height: "200px",
        width: "100%",
        borderRadius: "4px",
        ...sx,
      }}
    />
  );
};

LoadingSkeleton.propTypes = {
  sx: PropTypes.object,
};

export default LoadingSkeleton;
