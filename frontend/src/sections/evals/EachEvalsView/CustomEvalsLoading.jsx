import { Box, IconButton, Skeleton, useTheme } from "@mui/material";
import PropTypes from "prop-types";
import React from "react";
import Iconify from "src/components/iconify";

const CustomEvalsLoading = ({ onClose }) => {
  const theme = useTheme();
  return (
    <Box sx={{ display: "flex", flexDirection: "column", gap: 2, padding: 2 }}>
      <Box display="flex" gap={0.5} justifyContent={"space-between"}>
        <Box
          sx={{
            display: "flex",
            flexDirection: "column",
            gap: theme.spacing(0.5),
          }}
        >
          <Skeleton
            variant="rectangular"
            width={200}
            height={24}
            sx={{ borderRadius: 1 }}
          />
          <Skeleton
            variant="rectangular"
            width={400}
            height={20}
            sx={{ borderRadius: 1 }}
          />
        </Box>
        <IconButton onClick={onClose} size="small">
          <Iconify
            icon="solar:close-linear"
            sx={{
              width: theme.spacing(4),
              height: theme.spacing(2),
              color: "text.primary",
            }}
          />
        </IconButton>
      </Box>
      <Skeleton variant="rectangular" height={40} sx={{ borderRadius: 1 }} />
      <Skeleton variant="rectangular" height={40} sx={{ borderRadius: 1 }} />
      <Skeleton variant="rectangular" height={200} sx={{ borderRadius: 1 }} />
      <Skeleton variant="rectangular" height={40} sx={{ borderRadius: 1 }} />
      <Skeleton variant="rectangular" height={40} sx={{ borderRadius: 1 }} />
      <Skeleton variant="rectangular" height={200} sx={{ borderRadius: 1 }} />
    </Box>
  );
};

CustomEvalsLoading.propTypes = {
  onClose: PropTypes.func,
};

export default CustomEvalsLoading;
