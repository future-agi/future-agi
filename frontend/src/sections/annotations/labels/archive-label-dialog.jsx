import PropTypes from "prop-types";
import {
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Typography,
} from "@mui/material";
import { enqueueSnackbar } from "notistack";
import { useQueryClient } from "@tanstack/react-query";
import axios from "src/utils/axios";
import {
  useDeleteAnnotationLabel,
  annotationLabelEndpoints,
  annotationLabelKeys,
} from "src/api/annotation-labels/annotation-labels";
import { getAnnotationLabelUsageCount } from "./annotation-label-usage";

ArchiveLabelDialog.propTypes = {
  label: PropTypes.object,
  onClose: PropTypes.func.isRequired,
};

export default function ArchiveLabelDialog({ label, onClose }) {
  const { mutate: archiveLabel, isPending } = useDeleteAnnotationLabel();
  const queryClient = useQueryClient();

  if (!label) return null;

  const usageCount = getAnnotationLabelUsageCount(label);

  const handleArchive = () => {
    // Capture label ID before component unmounts
    const labelId = label.id;
    archiveLabel(labelId, {
      onSuccess: () => {
        enqueueSnackbar("Label archived", {
          variant: "info",
          action: () => (
            <Button
              color="inherit"
              size="small"
              onClick={async () => {
                try {
                  await axios.post(annotationLabelEndpoints.restore(labelId));
                  enqueueSnackbar("Label restored", { variant: "success" });
                  queryClient.invalidateQueries({
                    queryKey: annotationLabelKeys.all,
                  });
                } catch {
                  enqueueSnackbar("Failed to restore label", {
                    variant: "error",
                  });
                }
              }}
            >
              Undo
            </Button>
          ),
        });
        onClose();
      },
    });
  };

  return (
    <Dialog open onClose={onClose} maxWidth="xs" fullWidth>
      <DialogTitle>Archive Label</DialogTitle>
      <DialogContent>
        <Typography>
          Are you sure you want to archive <strong>{label.name}</strong>?
        </Typography>
        {usageCount > 0 ? (
          <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>
            This label is currently used in {usageCount} annotation{" "}
            {usageCount === 1 ? "task" : "tasks"}. Archiving it will prevent it
            from being added to new tasks, but existing annotations will not be
            affected.
          </Typography>
        ) : (
          <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>
            This label is not currently in use.
          </Typography>
        )}
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} disabled={isPending}>
          Cancel
        </Button>
        <Button
          onClick={handleArchive}
          color="error"
          variant="contained"
          disabled={isPending}
        >
          Archive
        </Button>
      </DialogActions>
    </Dialog>
  );
}
