import { LoadingButton } from "@mui/lab";
import {
  Box,
  Button,
  Dialog,
  DialogActions,
  DialogTitle,
  IconButton,
  Stack,
  Typography,
  useTheme,
} from "@mui/material";
import PropTypes from "prop-types";
import React from "react";
import Iconify from "src/components/iconify";

export default function PromptModalWrapper({
  children,
  onSubmit,
  isValid,
  title,
  subTitle,
  open,
  onClose,
  actionBtnTitle = "Save",
  cancelBtnTitle = "Cancel",
  hideCancelBtn,
  isLoading = false,
}) {
  const theme = useTheme();
  return (
    <Dialog
      open={open}
      onClose={onClose}
      PaperProps={{
        sx: {
          width: "570px",
          borderRadius: theme.spacing(1),
          padding: theme.spacing(2),
          display: "flex",
          flexDirection: "column",
          gap: theme.spacing(2),
        },
      }}
    >
      <DialogTitle sx={{ padding: 0, lineHeight: 0 }}>
        <Stack>
          <Typography
            typography={"s1"}
            color={"text.primary"}
            fontWeight={"fontWeightSemiBold"}
          >
            {title}
          </Typography>
          <IconButton
            onClick={onClose}
            sx={{
              position: "absolute",
              top: "12px",
              right: "12px",
              color: "text.primary",
            }}
          >
            <Iconify icon="akar-icons:cross" />
          </IconButton>
        </Stack>
        {subTitle && (
          <Typography
            variant="s2"
            color={"text.secondary"}
            fontWeight={"fontWeightRegular"}
          >
            {subTitle}
          </Typography>
        )}
      </DialogTitle>
      <Box>
        {children}
        <DialogActions
          sx={{
            padding: 0,
            display: "flex",
            flexDirection: "row",
            gap: "16px",
            mt: "32px",
            "& button": {
              margin: 0,
            },
          }}
        >
          {!hideCancelBtn && (
            <Button
              onClick={onClose}
              variant="outlined"
              type="button"
              sx={{
                minWidth: "180px",
                minHeight: "38px",
                "&:hover": {
                  borderColor: "divider",
                },
              }}
            >
              <Typography
                variant="s1"
                color="text.disabled"
                fontWeight="fontWeightMedium"
              >
                {cancelBtnTitle}
              </Typography>
            </Button>
          )}
          <LoadingButton
            variant="contained"
            color="primary"
            type="submit"
            onClick={onSubmit}
            sx={{
              minWidth: "180px",
              minHeight: "38px",
              marginLeft: "0 !important",
            }}
            disabled={!isValid}
            loading={isLoading}
          >
            <Typography variant="s1" fontWeight="fontWeightSemiBold">
              {actionBtnTitle}
            </Typography>
          </LoadingButton>
        </DialogActions>
      </Box>
    </Dialog>
  );
}

PromptModalWrapper.propTypes = {
  children: PropTypes.node,
  onSubmit: PropTypes.func,
  isValid: PropTypes.bool,
  title: PropTypes.string,
  subTitle: PropTypes.string,
  open: PropTypes.bool,
  onClose: PropTypes.func,
  actionBtnTitle: PropTypes.string,
  cancelBtnTitle: PropTypes.string,
  hideCancelBtn: PropTypes.bool,
  isLoading: PropTypes.bool,
};
