import {
  Box,
  Dialog,
  DialogContent,
  DialogTitle,
  IconButton,
  Skeleton,
  Typography,
} from "@mui/material";
import PropTypes from "prop-types";
import React, { useState } from "react";
import Iconify from "src/components/iconify";

const AgentVideoPlayerModal = ({ open, onClose, content }) => {
  const { title, subtitle, url } = content || {};
  const [isLoading, setIsLoading] = useState(true);
  return (
    <Dialog
      open={open}
      onClose={onClose}
      maxWidth="lg"
      fullWidth
      PaperProps={{
        sx: {
          borderRadius: 3,
          width: 1200,
          height: 730,
          display: "flex",
          flexDirection: "column",
          overflow: "hidden",
        },
      }}
    >
      <DialogTitle
        sx={{
          px: 3,
          pt: 2,
          pb: 0,
          position: "relative",
        }}
      >
        {/* Title + Subtitle */}
        <Box sx={{ display: "flex", flexDirection: "column", gap: 0.5 }}>
          <Typography
            typography="m1"
            fontWeight="fontWeightMedium"
            color="text.primary"
          >
            {title || "Video Tutorial"}
          </Typography>
          {subtitle && (
            <Typography
              typography="m3"
              fontWeight="fontWeightRegular"
              color="text.secondary"
            >
              {subtitle}
            </Typography>
          )}
        </Box>

        {/* Close Icon */}
        <IconButton
          onClick={onClose}
          sx={{
            position: "absolute",
            top: 8,
            right: 8,
            color: "text.primary",
            p: 1,
          }}
        >
          <Iconify icon="mdi:close" height={24} width={24} />
        </IconButton>
      </DialogTitle>

      <DialogContent
        sx={{
          flex: 1,
          display: "flex",
          justifyContent: "center",
          alignItems: "center",
          px: 2,
          overflow: "hidden",
        }}
      >
        <Box
          sx={{
            flex: 1,
            width: "100%",
            height: "100%",
            borderRadius: "0 0 12px 12px",
            overflow: "hidden",
          }}
        >
          {isLoading && (
            <Skeleton
              variant="rectangular"
              sx={{
                width: "100%",
                height: "93%",
                borderRadius: "8px",
                mt: 2,
              }}
            />
          )}
          {url ? (
            <iframe
              src={url}
              title={title || "Video Player"}
              frameBorder="0"
              loading="lazy"
              onLoad={() => setIsLoading(false)}
              allowFullScreen
              allow="clipboard-write"
              style={{
                width: "100%",
                height: "100%",
                opacity: isLoading ? 0 : 1,
                border: "none",
              }}
            />
          ) : (
            <Typography color="text.secondary" textAlign="center">
              No video available.
            </Typography>
          )}
        </Box>
      </DialogContent>
    </Dialog>
  );
};

AgentVideoPlayerModal.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  content: PropTypes.object,
};

export default AgentVideoPlayerModal;
