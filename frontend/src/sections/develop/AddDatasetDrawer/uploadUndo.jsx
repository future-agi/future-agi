import { Box, Button, IconButton } from "@mui/material";
import { closeSnackbar } from "notistack";
import Iconify from "src/components/iconify";
import axios, { endpoints } from "src/utils/axios";
import { getSafeActionErrorMessage } from "src/utils/errorUtils";
import { notify } from "src/sections/common/ux/toast";

// Keep the created dataset identity in the toast's lifetime, not the form's.
// Navigation may unmount the uploader before Undo or a later retry is clicked.
export function createUploadUndo({ id, name, queryClient, navigate }) {
  let pending = false;
  let removed = false;
  let errorToast;
  const removeDataset = async () => {
    if (!id || pending || removed) return;
    pending = true;
    try {
      await axios.delete(endpoints.develop.deleteDataset(), {
        data: { dataset_ids: [id] },
      });
    } catch (error) {
      errorToast = notify(
        getSafeActionErrorMessage(error, `Couldn't remove "${name}".`),
        {
          variant: "error",
          key: `dataset-created-undo-error:${id}`,
          persist: true,
          action: (snackbarId) => (
            <Box sx={{ display: "flex", flexWrap: "wrap", gap: 0.5 }}>
              <Button size="small" color="inherit" onClick={removeDataset}>
                Retry removal
              </Button>
              <Button
                size="small"
                color="inherit"
                onClick={() => navigate(`/dashboard/develop/${id}?tab=data`)}
              >
                Open dataset
              </Button>
              <IconButton
                size="small"
                aria-label="Dismiss"
                onClick={() => closeSnackbar(snackbarId)}
                sx={{ p: 0.5, color: "text.secondary" }}
              >
                <Iconify width={18} icon="mingcute:close-line" />
              </IconButton>
            </Box>
          ),
        },
      );
      return;
    } finally {
      pending = false;
    }
    removed = true;
    errorToast?.close();
    queryClient.invalidateQueries({ queryKey: ["datasets", "list"] });
    navigate("/dashboard/develop");
    notify(`Removed "${name}"`, { key: "dataset-created-undo" });
  };
  return removeDataset;
}
