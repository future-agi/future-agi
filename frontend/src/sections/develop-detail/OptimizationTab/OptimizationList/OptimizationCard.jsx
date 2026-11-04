import { Box, Typography } from "@mui/material";
import StatusBadge from "src/components/status-badge/StatusBadge";
import { format } from "date-fns";
import PropTypes from "prop-types";
import React from "react";

const OptimizationCard = ({ optimization, selected, onClick }) => {
  const statusRole =
    { Completed: "success", Running: "warning" }[optimization.status] ??
    "neutral";

  return (
    <Box
      sx={{
        width: "100%",
        padding: 1,
        border: "2px solid",
        borderColor: selected ? "primary.light" : "divider",
        borderRadius: "8px",
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        cursor: "pointer",
      }}
      onClick={onClick}
    >
      <Box sx={{ width: "50%", overflow: "hidden" }}>
        <Typography
          fontWeight={500}
          fontSize="13px"
          variant="subtitle2"
          noWrap
          sx={{
            textOverflow: "ellipsis",
            overflow: "hidden",
            display: "block",
          }}
          title={optimization.name}
        >
          {optimization.name}
        </Typography>
      </Box>
      <Box
        sx={{
          display: "flex",
          alignItems: "center",
          gap: 1,
          flex: 1,
          justifyContent: "flex-end",
        }}
      >
        <StatusBadge
          status={optimization.status}
          label={optimization.status}
          role={statusRole}
        />
        <Typography
          fontWeight={400}
          fontSize="13px"
          variant="subtitle2"
          sx={{ whiteSpace: "nowrap" }}
        >
          {format(new Date(optimization.created_at), "dd-MM-yy")}
        </Typography>
      </Box>
    </Box>
  );
};

OptimizationCard.propTypes = {
  optimization: PropTypes.object.isRequired,
  selected: PropTypes.bool,
  onClick: PropTypes.func,
};

export default OptimizationCard;
