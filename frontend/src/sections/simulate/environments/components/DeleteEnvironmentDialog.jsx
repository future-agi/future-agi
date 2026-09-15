import PropTypes from "prop-types";
import {
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogContentText,
  DialogTitle,
} from "@mui/material";
import { DELETE_DIALOG_COPY, DELETE_TONE } from "../myEnvironments.constants";

export default function DeleteEnvironmentDialog({ env, onCancel, onConfirm }) {
  return (
    <Dialog open={!!env} onClose={onCancel} maxWidth="xs" fullWidth>
      <DialogTitle sx={{ typography: "m2", fontWeight: "fontWeightBold" }}>
        {DELETE_DIALOG_COPY.title}
      </DialogTitle>
      <DialogContent>
        <DialogContentText sx={{ typography: "s2" }}>
          <b>{env?.name}</b> {DELETE_DIALOG_COPY.body}
        </DialogContentText>
      </DialogContent>
      <DialogActions sx={{ px: 3, pb: 2 }}>
        <Button
          onClick={onCancel}
          sx={{ typography: "s2", fontWeight: "fontWeightSemiBold", color: "text.secondary" }}
        >
          {DELETE_DIALOG_COPY.cancel}
        </Button>
        <Button
          variant="contained"
          onClick={onConfirm}
          sx={{
            typography: "s2",
            fontWeight: "fontWeightBold",
            bgcolor: DELETE_TONE.main,
            color: "#fff",
            "&:hover": { bgcolor: DELETE_TONE.hover, color: "#fff" },
          }}
        >
          {DELETE_DIALOG_COPY.confirm}
        </Button>
      </DialogActions>
    </Dialog>
  );
}

DeleteEnvironmentDialog.propTypes = {
  env: PropTypes.shape({ name: PropTypes.string }),
  onCancel: PropTypes.func,
  onConfirm: PropTypes.func,
};
