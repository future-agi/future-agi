import React from "react";
import { Box, Typography } from "@mui/material";
import PropTypes from "prop-types";

const MAX_VISIBLE_NAMES = 3;

const DisconnectedNodesToast = ({ names = [] }) => {
  const visible = names.slice(0, MAX_VISIBLE_NAMES);
  const remaining = names.length - visible.length;
  const label =
    visible.join(", ") + (remaining > 0 ? `, +${remaining} more` : "");

  return (
    <Box sx={{ display: "flex", flexDirection: "column", gap: 0.5 }}>
      <Typography typography="s1">
        {names.length === 1
          ? "Node not reachable from Start: "
          : "Nodes not reachable from Start: "}
        <Box component="span" fontWeight="fontWeightSemiBold">
          {label}
        </Box>
      </Typography>
      <Typography typography="s2" color="text.secondary">
        The flow only follows outgoing edges from the Start node. Connect these
        nodes downstream of Start before saving.
      </Typography>
    </Box>
  );
};

DisconnectedNodesToast.propTypes = {
  names: PropTypes.arrayOf(PropTypes.string),
};

export default DisconnectedNodesToast;
