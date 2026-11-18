import { Box, IconButton, Typography } from "@mui/material";
import PropTypes from "prop-types";
import React from "react";
import Iconify from "src/components/iconify";
import RenderFileIcons from "src/utils/RenderFileIcon";
import { formatFileSize } from "src/utils/utils";

const UploadCard = ({ fileData, onDelete }) => {
  return (
    <Box
      elevation={0}
      sx={{
        height: "75px",
        width: "100%",
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        p: "14px",
        border: "1px solid",
        borderColor: "divider",
        borderRadius: 1,
      }}
    >
      <Box
        sx={{ display: "flex", width: "100%", alignItems: "center", gap: 1.5 }}
      >
        <Box
          sx={{
            display: "flex",
            alignSelf: "self-start",
          }}
        >
          {RenderFileIcons(fileData?.type)}
        </Box>
        <Box
          width="100%"
          sx={{
            display: "flex",
            flexDirection: "column",
            gap: (theme) => theme.spacing(0.25),
          }}
        >
          <Box
            sx={{
              width: "100%",
              display: "flex",
              justifyContent: "space-between",
            }}
          >
            <Typography variant="body2" sx={{ fontWeight: 600 }}>
              {fileData?.name}
            </Typography>
            <IconButton onClick={onDelete} size="small">
              <Iconify icon="solar:close-linear" color="text.primary" />
            </IconButton>
          </Box>
          <Typography variant="caption" color="text.secondary">
            {formatFileSize(fileData?.size)} •
          </Typography>
        </Box>
      </Box>
    </Box>
  );
};

UploadCard.propTypes = {
  fileData: PropTypes.object,
  onDelete: PropTypes.func,
};

export default UploadCard;
