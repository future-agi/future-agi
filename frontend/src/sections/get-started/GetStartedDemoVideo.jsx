import { Modal, Typography, Button, Box } from "@mui/material";
import PropTypes from "prop-types";
import React from "react";

const GetStartedDemoVideo = ({
  showDemoModal,
  handleClose,
  viewDockAction,
  viewDocTitle,
  buttonAction,
  buttonTitle,
  headingTitle,
  headingDescription = "",
  videoComponent,
  showAction = true,
  sx = {},
}) => {
  return (
    <Modal open={showDemoModal} onClose={handleClose}>
      <Box
        // height={"65vh"}
        maxHeight={"90vh"}
        width={"50vw"}
        bgcolor={"background.paper"}
        borderRadius={"12px"}
        sx={{
          position: "absolute",
          top: "50%",
          left: "50%",
          transform: "translate(-50%, -50%)",
          boxShadow: 24,
          borderRadius: "12px",
          // py: "24px",
          // px: "32px",
          p: "16px",
          overflow: "auto",
        }}
      >
        <Box
          display={"flex"}
          flexDirection={"row"}
          justifyContent={"space-between"}
        >
          <Box display={"flex"} flexDirection={"column"} gap="4px">
            <Typography
              variant="m3"
              fontWeight={"fontWeightSemiBold"}
              color="text.primary"
            >
              {headingTitle}
            </Typography>
            {headingDescription && (
              <Typography
                variant="s2"
                fontWeight={"fontWeightRegular"}
                color="text.secondary"
              >
                {headingDescription}
              </Typography>
            )}
          </Box>
        </Box>
        <Box
          sx={{
            // height: "calc(55vh - 48px)",
            width: "100%",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            flexDirection: "column",
            pt: 2,
          }}
        >
          <Box
            sx={{
              width: "100%",
              borderRadius: "8px",
              overflow: "hidden",
              backgroundColor: "background.neutral",
              ...sx,
            }}
          >
            {videoComponent}
          </Box>
          {showAction && (
            <Box sx={{ mt: "16px", display: "flex", gap: "12px" }}>
              <Button
                variant="outlined"
                color="primary"
                onClick={viewDockAction}
                sx={{
                  color: "primary.main",
                  fontWeight: "500",
                  fontSize: 14,
                  px: "24px",
                  py: "6px",
                  borderColor: "primary.main",
                }}
              >
                {viewDocTitle}
              </Button>
              <Button
                variant="contained"
                color="primary"
                onClick={buttonAction}
                sx={{
                  bgcolor: "primary.main",
                  color: "common.white",
                  fontWeight: "500",
                  fontSize: 14,
                  px: "24px",
                  py: "6px",
                }}
              >
                {buttonTitle}
              </Button>
            </Box>
          )}
        </Box>
      </Box>
    </Modal>
  );
};

export default GetStartedDemoVideo;

GetStartedDemoVideo.propTypes = {
  showDemoModal: PropTypes.bool,
  handleClose: PropTypes.func,
  buttonAction: PropTypes.func,
  buttonTitle: PropTypes.string,
  headingTitle: PropTypes.string,
  headingDescription: PropTypes.string,
  videoComponent: PropTypes.any,
  viewDockAction: PropTypes.func,
  viewDocTitle: PropTypes.string,
  showAction: PropTypes.bool,
  sx: PropTypes.object,
};
