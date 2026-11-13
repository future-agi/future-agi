import { useMutation } from "@tanstack/react-query";
import { enqueueSnackbar } from "src/components/snackbar";
import axios, { endpoints } from "src/utils/axios";
import { RESPONSE_CODES } from "src/utils/constants";

// The backend's error code for a comparison that is unknown or expired. Other
// 404s (e.g. a compared dataset that was deleted) are reported as errors.
export const COMPARE_NOT_FOUND_CODE = "compare_not_found";
// The backend's error code (503) when the comparison's storage could not be
// read. The comparison may still exist, so it is not rebuilt; the error is
// shown and the user tries again.
export const COMPARE_STORAGE_UNAVAILABLE_CODE = "compare_storage_unavailable";

export const COMPARE_STORAGE_UNAVAILABLE_MESSAGE =
  "Comparison data is temporarily unavailable. Please try again.";

export const isCompareStorageUnavailable = (error) =>
  error?.statusCode === RESPONSE_CODES.MAINTENANCE &&
  error?.code === COMPARE_STORAGE_UNAVAILABLE_CODE;

export const COMPARISON_EXPIRED_MESSAGE =
  "This comparison expired and is being rebuilt. Download it again once the table has loaded.";

export const useDeleteCompare = () => {
  return useMutation({
    mutationFn: async (compareId) => {
      try {
        const response = await axios.delete(
          endpoints.dataset.deleteCompareDataset(compareId),
        );
        return response.data;
      } catch (error) {
        // The comparison is already gone (cleaned up or deleted earlier),
        // which is what deleting it asked for.
        if (error?.statusCode === RESPONSE_CODES.NOT_FOUND) return null;
        throw error;
      }
    },
  });
};

// Downloads the comparison as CSV. A comparison that expired answers 404 with
// COMPARE_NOT_FOUND_CODE: that is not an error to show. The table is rebuilt
// (onComparisonExpired) and the user is asked to download again once it has
// loaded. A comparison whose storage could not be read (503,
// COMPARE_STORAGE_UNAVAILABLE_CODE) is not rebuilt: the user is asked to try
// again.
export const useDownloadCompare = ({
  compareId,
  selectedDatasetsValues,
  baseColumn,
  onComparisonExpired,
}) => {
  return useMutation({
    mutationFn: async () => {
      const requestCompareId = compareId?.current ?? null;
      try {
        const response = await axios.post(
          endpoints.dataset.getCompareDatasetDownload(
            selectedDatasetsValues[0],
          ),
          {
            dataset_ids: selectedDatasetsValues.slice(1),
            base_column_name: baseColumn,
            compare_id: requestCompareId,
          },
        );
        return { response };
      } catch (error) {
        if (
          requestCompareId &&
          error?.statusCode === RESPONSE_CODES.NOT_FOUND &&
          error?.code === COMPARE_NOT_FOUND_CODE
        ) {
          return { expiredCompareId: requestCompareId };
        }
        if (isCompareStorageUnavailable(error)) {
          return { storageUnavailable: true };
        }
        throw error;
      }
    },
    onSuccess: ({ response, expiredCompareId, storageUnavailable }) => {
      if (storageUnavailable) {
        enqueueSnackbar(COMPARE_STORAGE_UNAVAILABLE_MESSAGE, {
          variant: "error",
        });
        return;
      }
      if (expiredCompareId) {
        // Rebuild only if the table has not already moved on to a new one;
        // the message is shown only when a rebuild starts.
        if (compareId?.current === expiredCompareId) {
          onComparisonExpired?.();
          enqueueSnackbar(COMPARISON_EXPIRED_MESSAGE, { variant: "info" });
        }
        return;
      }
      const url = window.URL.createObjectURL(new Blob([response.data]));
      const link = document.createElement("a");
      link.href = url;
      link.setAttribute("download", `dataset-${selectedDatasetsValues[0]}.csv`);
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.URL.revokeObjectURL(url);

      enqueueSnackbar("Dataset downloaded successfully", {
        variant: "success",
      });
    },
  });
};
