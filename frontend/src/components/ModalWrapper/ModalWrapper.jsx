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
import React, { useId } from "react";
import Iconify from "src/components/iconify";

const actionBtnBaseSx = {
  minWidth: "90px",
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
  modalWidth = "560px",
  actionBtnProps = {},
  cancelBtnProps = {},
  onCancelBtn,
  paperSx = {},
}) {
  const theme = useTheme();
  const headerId = useId();
  const titleId = useId();
  const subTitleId = useId();
  return (
    <Dialog
      open={open}
      aria-labelledby={titleId}
      aria-describedby={subTitle ? subTitleId : undefined}
      disableEscapeKeyDown={isLoading}
      onClose={() => {
        if (isLoading) return;
        onClose();
      }}
      PaperProps={{
        sx: {
          width: modalWidth,
          padding: theme.spacing(2),
          display: "flex",
          flexDirection: "column",
          gap: theme.spacing(2),
          ...paperSx,
        },
      }}
    >
      {/* The dialog is named by the title text alone and described by the
          subtitle alone, never by its children (they are forms). The header
          keeps the dialog title's styles, which the subtitle inherits, but it
          is a plain container: it is not a heading, and its own id keeps it
          from taking the dialog's label id. The close button sits beside the
          title heading, placed against the dialog paper as before. */}
      <DialogTitle
        component="div"
        id={headerId}
        sx={{ padding: 0, lineHeight: 0 }}
      >
        <Stack>
          <Typography
            id={titleId}
            component={title ? "h2" : undefined}
            typography={"m3"}
            color={"text.primary"}
            fontWeight={"fontWeightSemiBold"}
          >
            {title}
          </Typography>
          <IconButton
            aria-label="Close dialog"
            disabled={isLoading}
            onClick={onClose}
            sx={{
              position: "absolute",
              top: "12px",
              right: "12px",
              color: "text.primary",
            }}
          >
            <Iconify icon="solar:close-linear" />
          </IconButton>
        </Stack>
        {subTitle && (
          <Typography
            id={subTitleId}
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
          gap: "8px",
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
              ...cancelBtnSx,
            }}
            {...cancelBtnProps}
          >
            {cancelBtnTitle}
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
          {actionBtnTitle}
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
