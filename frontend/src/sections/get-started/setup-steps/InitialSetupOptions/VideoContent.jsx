import { Box, Button, Link, Typography } from "@mui/material";
import PropTypes from "prop-types";
import React from "react";
import Image from "src/components/image";

const VideoContent = ({
  title,
  description,
  srcThumbnail = "",
  buttonTitle = "",
  linkTitle = "",
  viewDocLink = "",
  checkNowAction = () => {},
  onViewDocsLinkClick,
}) => {
  return (
    <Box
      my={"20px"}
      sx={{
        display: "flex",
        gap: "12px",
        alignItems: "center",
        padding: "12px",
        backgroundColor: "blue.o5",
        border: "1px solid",
        borderColor: "blue.200",
        borderRadius: "4px",
      }}
    >
      <Box
        sx={{
          width: "160px",
          // height: "60px",
          borderRadius: "4px",
          position: "relative",
          overflow: "hidden",
        }}
      >
        <Image
          ratio="16/9"
          alt={title}
          src={srcThumbnail}
          onClick={checkNowAction}
          sx={{
            "&:hover": {
              cursor: "pointer",
            },
          }}
        />
      </Box>
      <Box
        sx={{
          flex: 1,
          display: "flex",
          flexDirection: "column",
          gap: "8px",
          alignContent: "space-between",
        }}
      >
        <Box sx={{ display: "flex", flexDirection: "column", gap: "2px" }}>
          <Typography
            variant="s2"
            fontWeight={"fontWeightSemiBold"}
            color="text.primary"
          >
            {title}
          </Typography>
          <Typography
            variant="s2"
            fontWeight={"fontWeightRegular"}
            color="text.secondary"
          >
            {description}
          </Typography>
        </Box>
        <Box display={"flex"} sx={{ alignItems: "center", gap: "12px" }}>
          <Button
            color="primary"
            variant="contained"
            onClick={checkNowAction}
            size="small"
          >
            {buttonTitle}
          </Button>
          <Link
            underline="none"
            href={viewDocLink}
            target="_blank"
            variant="s2"
            fontWeight={"fontWeightMedium"}
            color="primary.main"
            onClick={onViewDocsLinkClick ? onViewDocsLinkClick : () => {}}
            sx={{
              border: "1px solid",
              borderColor: "primary.main",
              borderRadius: "8px",
              padding: "6px 24px",
              minHeight: "30px",
              "&:hover": {
                color: "primary.dark",
              },
            }}
          >
            {linkTitle}
          </Link>
        </Box>
      </Box>
    </Box>
  );
};

export default VideoContent;

VideoContent.propTypes = {
  title: PropTypes.string,
  description: PropTypes.string,
  time: PropTypes.string,
  srcThumbnail: PropTypes.string,
  srcVideo: PropTypes.string,
  checkNowAction: PropTypes.func,
  buttonTitle: PropTypes.string,
  linkTitle: PropTypes.string,
  viewDocLink: PropTypes.string,
  onViewDocsLinkClick: PropTypes.func,
};
