import React from "react";
import { Box, Typography } from "@mui/material";
import Iconify from "../iconify";
import PropTypes from "prop-types";

const DetailItem = ({ title, content }) => {
  return (
    <Box sx={{ display: "flex", gap: "12px", width: "100%" }}>
      <Box>
        <Iconify
          icon="solar:double-alt-arrow-right-bold-duotone"
          color="primary.main"
          width={24}
        />
      </Box>
      <Box sx={{ gap: 1, display: "flex", flexDirection: "column", flex: 1 }}>
        <Box>
          <Typography fontSize={13} fontWeight={600} color="text.disabled">
            {title}
          </Typography>
        </Box>
        <Box sx={{ width: "100%" }}>{content}</Box>
      </Box>
    </Box>
  );
};

DetailItem.propTypes = {
  title: PropTypes.string,
  content: PropTypes.any,
};

export default DetailItem;
