import PropTypes from "prop-types";
import { Button } from "@mui/material";
import { ConfirmDialog } from "src/components/custom-dialog";
import { useRemoveSource } from "src/api/simulate-environments/sources";
import { SOURCES_COPY as COPY } from "./sources.constants";

/** Confirms removing `source`: a saved file goes to Removed, one not saved yet is dropped. */
export default function RemoveSourceDialog({
  envId,
  source,
  onClose,
  onRemoved,
}) {
  const remove = useRemoveSource(envId);
  return (
    <ConfirmDialog
      open={Boolean(source)}
      onClose={onClose}
      title={source ? COPY.removeTitle(source) : ""}
      content={source ? COPY.removeBody(source) : ""}
      action={
        <Button
          size="small"
          variant="contained"
          color="error"
          onClick={() => {
            remove(source);
            onClose();
            onRemoved?.();
          }}
        >
          {COPY.removeConfirm}
        </Button>
      }
    />
  );
}

RemoveSourceDialog.propTypes = {
  envId: PropTypes.string,
  // The file to confirm; the dialog is closed while null.
  source: PropTypes.object,
  onClose: PropTypes.func.isRequired,
  onRemoved: PropTypes.func,
};
