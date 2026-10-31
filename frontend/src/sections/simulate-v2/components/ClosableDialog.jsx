import PropTypes from "prop-types";
import { Dialog, IconButton } from "@mui/material";
import Iconify from "src/components/iconify";

/*
  Every popup that can be closed shows a cross top right, like ConfirmDialog.
  The cross calls onClose(event, "closeButton"); a popup that can't be
  closed (no onClose) shows none. The title keeps clear of it.
*/
export default function ClosableDialog({
  onClose,
  PaperProps,
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
          aria-label="Close"
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
  children: PropTypes.node,
};
