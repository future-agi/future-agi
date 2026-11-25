import PropTypes from "prop-types";
import { Dialog, IconButton } from "@mui/material";
import Iconify from "../iconify";

/*
  Design system: every dialog that can be closed shows a cross top right,
  like ConfirmDialog and ModalWrapper. A drop-in for MUI Dialog.
  The cross calls onClose(event, "closeButton"); a popup that can't be
  closed (no onClose) shows none. The title keeps clear of it.
*/
export default function ClosableDialog({
  onClose,
  PaperProps,
  closeLabel = "Close",
  children,
  ...other
}) {
  return (
    <Dialog
      onClose={onClose}
      {...other}
      PaperProps={{
        ...PaperProps,
        sx: [
          { "& .MuiDialogTitle-root": { pr: 6 } },
          ...(Array.isArray(PaperProps?.sx) ? PaperProps.sx : [PaperProps?.sx]),
        ],
      }}
    >
      {onClose && (
        <IconButton
          size="small"
          aria-label={closeLabel}
          onClick={(e) => onClose(e, "closeButton")}
          sx={{
            position: "absolute",
            top: 12,
            right: 12,
            zIndex: 1,
            color: "text.secondary",
          }}
        >
          <Iconify icon="solar:close-linear" width={18} />
        </IconButton>
      )}
      {children}
    </Dialog>
  );
}
ClosableDialog.propTypes = {
  onClose: PropTypes.func,
  PaperProps: PropTypes.object,
  closeLabel: PropTypes.string,
  children: PropTypes.node,
};
