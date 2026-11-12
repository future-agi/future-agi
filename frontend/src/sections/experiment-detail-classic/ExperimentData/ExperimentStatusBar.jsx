import { Box, Typography } from "@mui/material";
import PropTypes from "prop-types";
import React, { useEffect, useState } from "react";
import { getColorFromSeed } from "src/utils/utils";

const ExperimentStatusBar = ({ api, columnAvgs }) => {
  const [count, setCount] = useState(0);

  const updateStatusBar = () => {
    setCount(api.getDisplayedRowCount());
  };

  useEffect(() => {
    api.addEventListener("modelUpdated", updateStatusBar);

    return () => {
      if (!api.isDestroyed()) {
        api.removeEventListener("modelUpdated", updateStatusBar);
      }
    };
  }, []);

  return (
    <Box sx={{ display: "flex", alignItems: "center", gap: 2, padding: 1 }}>
      {/* Total Rows */}
      <Typography
        variant="body2"
        sx={{
          marginRight: 2,
        }}
      >
        Rows: {count}
      </Typography>

      {/* Iterate over dummy_avg array */}
      {columnAvgs?.map((item) => (
        <Box
          key={item.id}
          sx={{
            display: "flex",
            alignItems: "center",
            gap: 0.5,
            border: "1px solid",
            borderColor: "divider",
            paddingX: 1.5,
            paddingY: 0.5,
            borderRadius: "8px",
          }}
        >
          <Box
            sx={{
              width: "8px",
              height: "8px",
              borderRadius: "50%",
              marginRight: 0.5,
              backgroundColor: getColorFromSeed(item.id),
            }}
          />
          <Typography variant="body2">{item.value.toFixed(2)}%</Typography>
        </Box>
      ))}
    </Box>
  );
};

ExperimentStatusBar.propTypes = {
  api: PropTypes.object.isRequired,
  columnAvgs: PropTypes.arrayOf(
    PropTypes.shape({
      id: PropTypes.string.isRequired,
      value: PropTypes.number.isRequired,
    }),
  ),
};

export default ExperimentStatusBar;
