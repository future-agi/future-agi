import { Box, Typography } from "@mui/material";
import React from "react";
import Iconify from "src/components/iconify";
import PropTypes from "prop-types";

const DetailItem = ({ title, content }) => {
  return (
    <Box sx={{ display: "flex", gap: "12px" }}>
      <Box>
        <Iconify
          icon="solar:double-alt-arrow-right-bold-duotone"
          color="primary.main"
          width={24}
        />
      </Box>
      <Box sx={{ gap: 1, display: "flex", flexDirection: "column" }}>
        <Box>
          <Typography fontSize={14} fontWeight={700} color="text.disabled">
            {title}
          </Typography>
        </Box>
        <Box>{content}</Box>
      </Box>
    </Box>
  );
};

DetailItem.propTypes = {
  title: PropTypes.string,
  content: PropTypes.any,
};

export default DetailItem;
