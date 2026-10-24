import { Box } from "@mui/material";
import PropTypes from "prop-types";
import React from "react";
import Iconify from "src/components/iconify";

const RankWithIndexRenderer = (props) => {
  const { value, data } = props;
  const isFirstRank = data?.rank === 1;
  return (
    <Box
      sx={{
        display: "flex",
        justifyContent: "center",
        alignItems: "center",
        gap: 0.5,
        paddingLeft: isFirstRank && 2.75,
      }}
    >
      {value}
      {isFirstRank ? (
        <Iconify
          icon="solar:crown-bold"
          sx={{
            // eslint-disable-next-line no-restricted-syntax -- ds: data colour
            color: "#FFCC00",
          }}
        />
      ) : (
        <></>
      )}
    </Box>
  );
};

RankWithIndexRenderer.propTypes = {
  value: PropTypes.any.isRequired,
  data: PropTypes.shape({
    rank: PropTypes.number,
  }).isRequired,
};

export default RankWithIndexRenderer;
