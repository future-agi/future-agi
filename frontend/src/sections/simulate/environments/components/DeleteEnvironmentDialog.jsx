import PropTypes from "prop-types";
import { Button, Typography } from "@mui/material";
import { ConfirmDialog } from "src/components/custom-dialog";
import { DELETE_DIALOG_COPY } from "../myEnvironments.constants";

export default function DeleteEnvironmentDialog({ env, onCancel, onConfirm }) {
  return (
    <ConfirmDialog
      open={!!env}
      onClose={onCancel}
      title={DELETE_DIALOG_COPY.title}
      content={(
        <Typography component="span" sx={{ typography: "s2" }}>
          <b>{env?.name}</b> {DELETE_DIALOG_COPY.body}
        </Typography>
      )}
      action={(
        <Button size="small" variant="contained" color="error" onClick={onConfirm}>
          {DELETE_DIALOG_COPY.confirm}
        </Button>
      )}
    />
  );
}

DeleteEnvironmentDialog.propTypes = {
  env: PropTypes.shape({ name: PropTypes.string }),
  onCancel: PropTypes.func,
  onConfirm: PropTypes.func,
};
