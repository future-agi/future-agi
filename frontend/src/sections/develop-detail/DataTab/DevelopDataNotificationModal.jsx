import { LoadingButton } from "@mui/lab";
import {
  Box,
  Dialog,
  DialogActions,
  DialogTitle,
  IconButton,
  Typography,
} from "@mui/material";
import PropTypes from "prop-types";
import React from "react";
import Iconify from "src/components/iconify";

const DevelopDataNotificationModal = ({ open, onClose, onClick, data }) => {
  return (
    <Dialog
      open={open}
      onClose={onClose}
      maxWidth={false}
      PaperProps={{ sx: { maxWidth: 720 } }}
    >
      <Box sx={{ padding: "16px", width: "480px" }}>
        <DialogTitle sx={{ padding: 0, margin: 0 }}>
          <Box
            sx={{
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
            }}
          >
            <Typography
              fontWeight={600}
              color="text.primary"
              fontSize="16px"
              lineHeight={"24px"}
              variant="m2"
            >
              Reasons for Unable to Process File
            </Typography>
            <IconButton onClick={onClose}>
              <Iconify icon="solar:close-linear" color="text.primary" />
            </IconButton>
          </Box>

          <Box
            sx={{
              display: "flex",
              alignItems: "center",
              width: "100%",
              padding: 0,
              margin: 0,
              boxSizing: "border-box",
            }}
          >
            <Box
              sx={{
                width: "100%",
                display: "flex",
                flexDirection: "column",
                maxHeight: 110,
                overflowY: "auto",
                margin: 0,
                padding: 0,
                boxSizing: "border-box",
              }}
            >
              {data?.reason?.map((reason, index) => (
                <Typography
                  key={index}
                  variant="body2"
                  color="text.secondary"
                  fontSize={"13px"}
                  sx={{
                    margin: 0,
                    padding: 0,
                    px: 0.5,
                    fontWeight: 400,
                    lineHeight: "22px",
                  }}
                >
                  {`${index + 1}. ${reason}`}
                </Typography>
              ))}
            </Box>
          </Box>
        </DialogTitle>
        <Box>
          <DialogActions sx={{ padding: 0, marginTop: "28px" }}>
            <LoadingButton
              size="small"
              variant="contained"
              //   loading={isPending}
              color="primary"
              onClick={onClick}
              sx={{
                minWidth: "90px",
                mx: "auto",
                p: "6px 24px",
              }}
            >
              <Typography
                variant="s2"
                fontSize="12px"
                fontWeight={600}
                lineHeight={"18px"}
              >
                Okay, got it
              </Typography>
            </LoadingButton>
          </DialogActions>
        </Box>
      </Box>
    </Dialog>
  );
};

DevelopDataNotificationModal.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  onClick: PropTypes.any,
  data: PropTypes.object,
};

export default DevelopDataNotificationModal;
