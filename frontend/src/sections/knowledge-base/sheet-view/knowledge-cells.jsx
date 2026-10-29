import { Box, Stack, Typography } from "@mui/material";
import PropTypes from "prop-types";
import React from "react";
import { getFileIcon, statusIcons } from "./icons";
import { usePdfPreviewStoreShallow } from "src/utils/CommonStores/pdfPreviewStore";

export function TitleCell(props) {
  const { value, data } = props;
  const fileType = value ? value.split(".").pop() : "";
  const iconSrc = getFileIcon(fileType);

  const setOpenPreviewPdfDrawer = usePdfPreviewStoreShallow(
    (state) => state.setOpenPreviewPdfDrawer,
  );

  const handlePreview = (e) => {
    if (data?.uploaded_url) {
      e.stopPropagation();
      setOpenPreviewPdfDrawer({
        isPublic: true,
        name: value,
        type: fileType,
        url: data.uploaded_url,
      });
    }
  };

  return (
    <Stack
      direction={"row"}
      gap={"8px"}
      alignItems={"center"}
      sx={{
        height: "100%",
      }}
    >
      <Box
        component={"img"}
        sx={{
          height: "16px",
          width: "16px",
        }}
        alt="document icon"
        src={iconSrc}
      />
      <Typography
        fontWeight={"fontWeightRegular"}
        variant="s1"
        sx={{
          cursor: data?.uploaded_url ? "pointer" : "default",
          "&:hover": {
            textDecoration: data?.uploaded_url ? "underline" : "none",
            color: data?.uploaded_url ? "primary.main" : "inherit",
          },
        }}
        onClick={handlePreview}
      >
        {value}
      </Typography>
    </Stack>
  );
}

TitleCell.propTypes = {
  value: PropTypes.string,
};
