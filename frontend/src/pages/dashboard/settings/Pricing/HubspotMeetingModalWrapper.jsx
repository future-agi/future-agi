import {
  Dialog,
  DialogContent,
  Box,
  CircularProgress,
  Typography,
  styled,
  Backdrop,
  IconButton,
  useTheme,
} from "@mui/material";
import PropTypes from "prop-types";
import React, { useState, useEffect } from "react";
import Iconify from "src/components/iconify";
const CustomBackdrop = styled(Backdrop)(({ theme }) => ({
  backgroundColor:
    theme.palette.mode === "light"
      ? "rgba(0, 0, 0, 0.1)"
      : "rgba(0, 0, 0, 0.3)",
}));
export const HubspotMeetingModalWrapper = ({ open, onClose }) => {
  const theme = useTheme();
  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    if (open) {
      setIsLoading(true);
    }
  }, [open]);

  const handleClose = () => {
    setIsLoading(true);
    onClose();
  };

  const hubspotUrl =
    "https://meetings.hubspot.com/salil-kolhe/help-futureagi-app?uuid=e62632d9-9a9b-4fc3-b051-c2022d735bc0&embed=true&hideBranding=true";

  return (
    <Dialog
      onClose={handleClose}
      open={open}
      maxWidth={false}
      fullWidth
      PaperProps={{
        sx: {
          width: "900px",
          height: "700px",
          maxWidth: "none",
          maxHeight: "none",
          margin: 0,
          borderRadius: 2,
          boxShadow: 2,
          display: "flex",
          flexDirection: "column",
        },
      }}
      slots={{ backdrop: CustomBackdrop }}
    >
      <DialogContent
        sx={{
          position: "relative",
          flex: 1,
          overflow: "hidden",
          display: "flex",
          flexDirection: "column",
          minHeight: 0,
        }}
      >
        {isLoading && (
          <Box
            sx={{
              position: "absolute",
              top: 0,
              left: 0,
              right: 0,
              bottom: 0,
              display: "flex",
              flexDirection: "column",
              alignItems: "center",
              justifyContent: "center",
              backgroundColor:
                theme.palette.mode === "light"
                  ? "rgba(255, 255, 255, 0.9)"
                  : "rgba(33, 43, 54, 0.9)",
              backdropFilter: "blur(2px)",
              zIndex: 10,
              minHeight: "400px",
            }}
          >
            <CircularProgress size={56} thickness={4} sx={{ mb: 2 }} />
            <Typography
              variant="h6"
              sx={{
                color: "text.primary",
                textAlign: "center",
                fontWeight: 500,
              }}
            >
              Loading Meeting Scheduler
            </Typography>
            <Typography
              variant="body2"
              sx={{
                mt: 1,
                color: "text.secondary",
                textAlign: "center",
              }}
            >
              Please wait a moment...
            </Typography>
          </Box>
        )}

        <iframe
          src={hubspotUrl}
          width="100%"
          height="100%"
          style={{
            border: "none",
            display: "block",
            position: "absolute",
            top: 0,
            left: 0,
            right: 0,
            bottom: 0,
            visibility: isLoading ? "hidden" : "visible",
          }}
          title="HubSpot Meeting Scheduler"
          onLoad={() => {
            setTimeout(() => setIsLoading(false), 500);
          }}
          onError={() => {
            setIsLoading(false);
            onClose();
          }}
        />
        {!isLoading && (
          <IconButton
            onClick={onClose}
            sx={{
              width: "30px",
              height: "30px",
              position: "absolute",
              marginLeft: "8px",
              top: 0,
              right: 1,
              zIndex: 11,
              "&:hover": { backgroundColor: "action.hover" },
            }}
          >
            <Iconify
              icon="line-md:close"
              sx={{
                width: (theme) => theme.spacing(2),
                height: (theme) => theme.spacing(2),
                color: "text.primary",
              }}
            />
          </IconButton>
        )}
      </DialogContent>
    </Dialog>
  );
};

HubspotMeetingModalWrapper.propTypes = {
  open: PropTypes.bool.isRequired,
  onClose: PropTypes.func.isRequired,
};
