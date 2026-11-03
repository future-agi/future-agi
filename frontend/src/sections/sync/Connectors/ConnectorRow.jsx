import { Box, Typography } from "@mui/material";
import React from "react";
import PropTypes from "prop-types";

import ConnectorCard from "./ConnectorCard";

const ConnectorRow = ({ title, connectors }) => {
  return (
    <Box
      sx={{ padding: 3, display: "flex", flexDirection: "column", gap: 1.5 }}
    >
      <Typography fontWeight={600} fontSize={13} color="text.primary">
        {title}
      </Typography>
      <Box sx={{ display: "flex", gap: "12px", flexWrap: "wrap" }}>
        {connectors.map(({ title, icon, onClick }) => (
          <ConnectorCard
            onClick={onClick}
            key={title}
            title={title}
            icon={icon}
          />
        ))}
      </Box>
    </Box>
  );
};

ConnectorRow.propTypes = {
  title: PropTypes.string,
  connectors: PropTypes.arrayOf(
    PropTypes.shape({
      title: PropTypes.string,
      icon: PropTypes.any,
      onClick: PropTypes.func,
    }),
  ),
};

export default ConnectorRow;
