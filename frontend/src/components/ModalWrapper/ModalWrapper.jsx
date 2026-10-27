import { LoadingButton } from "@mui/lab";
import {
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

const actionBtnBaseSx = {
  minWidth: "90px",
  minHeight: "32px",
  textTransform: "none",
};

export default function ModalWrapper({
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
  isLoading,
  actionBtnSx = {},
  cancelBtnSx = {},
  dialogActionSx = {},
  modalWidth = "570px",
  actionBtnProps = {},
  cancelBtnProps = {},
  onCancelBtn,
  paperSx = {},
}) {
  const theme = useTheme();
  return (
    <Dialog
      open={open}
      disableEscapeKeyDown={isLoading}
      onClose={() => {
        if (isLoading) return;
        onClose();
      }}
      PaperProps={{
        sx: {
          width: modalWidth,
          borderRadius: theme.spacing(1),
          padding: theme.spacing(2),
          display: "flex",
          flexDirection: "column",
          gap: theme.spacing(2),
          ...paperSx,
        },
      }}
    >
      <DialogTitle sx={{ padding: 0, lineHeight: 0 }}>
        <Stack>
          <Typography
            typography={"m3"}
            color={"text.primary"}
            fontWeight={"fontWeightSemiBold"}
          >
            {title}
          </Typography>
          <IconButton
            disabled={isLoading}
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
            variant="s1"
            color={"text.secondary"}
            fontWeight={"fontWeightRegular"}
          >
            {subTitle}
          </Typography>
        )}
      </DialogTitle>
      {children}
      <DialogActions
        sx={{
          padding: 0,
          display: "flex",
          flexDirection: "row",
          gap: "16px",
          mt: 2,
          "& button": {
            margin: 0,
          },
          ...dialogActionSx,
        }}
      >
        {!hideCancelBtn && (
          <Button
            size="small"
            disabled={isLoading}
            onClick={onCancelBtn ? onCancelBtn : onClose}
            variant="outlined"
            type="button"
            sx={{
              ...actionBtnBaseSx,
              "&:hover": {
                borderColor: "divider",
              },
              color: "text.disabled",
              ...cancelBtnSx,
            }}
            {...cancelBtnProps}
          >
            <Typography variant="s1" fontWeight="fontWeightMedium">
              {cancelBtnTitle}
            </Typography>
          </Button>
        )}
        <LoadingButton
          loading={isLoading}
          variant="contained"
          color="primary"
          type="submit"
          size="small"
          onClick={onSubmit}
          sx={{
            ...actionBtnBaseSx,
            marginLeft: "0 !important",
            ...actionBtnSx,
          }}
          disabled={!isValid || isLoading}
          {...actionBtnProps}
        >
          <Typography variant="s1" fontWeight="fontWeightSemiBold">
            {actionBtnTitle}
          </Typography>
        </LoadingButton>
      </DialogActions>
    </Dialog>
  );
}

ModalWrapper.propTypes = {
  children: PropTypes.node,
  onSubmit: PropTypes.func,
  onCancelBtn: PropTypes.func,
  isValid: PropTypes.bool,
  title: PropTypes.string,
  subTitle: PropTypes.string,
  open: PropTypes.bool,
  onClose: PropTypes.func,
  actionBtnTitle: PropTypes.string,
  cancelBtnTitle: PropTypes.string,
  hideCancelBtn: PropTypes.bool,
  isLoading: PropTypes.bool,
  actionBtnSx: PropTypes.object,
  cancelBtnSx: PropTypes.object,
  dialogActionSx: PropTypes.object,
  modalWidth: PropTypes.string,
  actionBtnProps: PropTypes.object,
  cancelBtnProps: PropTypes.object,
  paperSx: PropTypes.object,
};
