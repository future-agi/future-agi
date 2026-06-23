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

const cellStyles = {
  Processing: {
    backgroundColor: "blue.o10",
    color: "blue.500",
  },
  Failed: {
    backgroundColor: "red.o10",
    color: "red.500",
  },
  Completed: {
    backgroundColor: "green.o10",
    color: "green.500",
  },
};

export function ProcessingStatusCell(props) {
  const { value } = props;

  const statusIconSrc = statusIcons[value];

  return (
    <Box
      sx={{
        height: "100%",
        width: "fit-content",
        display: "flex",
        justifyContent: "flex-start",
        alignItems: "center",
      }}
    >
      <Stack
        sx={{
          px: "12px",
          py: "4px",
          gap: "8px",
          height: "24px",
          borderRadius: "20px",
          ...cellStyles[value],
        }}
        direction={"row"}
        alignItems={"center"}
      >
        <Box
          component={"img"}
          sx={{
            height: "12px",
            width: "12px",
          }}
          alt="document icon"
          src={statusIconSrc}
        />
        <Typography variant="s2" fontWeight={"fontWeightMedium"}>
          {value}
        </Typography>
      </Stack>
    </Box>
  );
}

ProcessingStatusCell.propTypes = {
  value: PropTypes.string,
};
