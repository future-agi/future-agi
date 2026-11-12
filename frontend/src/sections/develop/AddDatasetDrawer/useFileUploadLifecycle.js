import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { apiPath } from "src/api/contracts/api-surface";
import { isPrototypeMode } from "src/utils/prototype";
import { notify } from "src/sections/common/ux/toast";

/** Queued acceptance is not creation. Keep the drawer until a terminal state. */
export function useFileUploadLifecycle({ onSuccess, onError }) {
  const [pending, setPending] = useState(null);
  const [failure, setFailure] = useState(null);
  const callbacks = useRef({ onSuccess, onError });
  callbacks.current = { onSuccess, onError };
  const consumed = useRef(null);
  const mutation = useMutation({
    mutationFn: (data) =>
      axios.post(endpoints.develop.uploadDatasetLocalFile, data, {
        headers: { "Content-Type": "multipart/form-data" },
      }),
    onMutate: () => setFailure(null),
    onSuccess: (response, form) => {
      const result = response?.data?.result;
      if (!result?.dataset_id)
        throw new Error("Upload did not return a dataset ID.");
      if (
        (result.processing_status === "completed" &&
          !["pending", "failed"].includes(result.media_dispatch_status)) ||
        (isPrototypeMode() && !result.processing_status)
      ) {
        callbacks.current.onSuccess(response, form);
        return;
      }
      consumed.current = null;
      setPending({ id: result.dataset_id, response, form });
      notify("Import started. Waiting for all values to be saved.", {
        key: "dataset-import-started",
      });
    },
    onError: (error) => callbacks.current.onError(error),
    meta: { errorHandled: true },
  });
  const progress = useQuery({
    queryKey: ["dataset-creation-progress", pending?.id],
    queryFn: ({ signal }) =>
      axios
        .get(
          apiPath(
            "/model-hub/develops/dataset-creation-progress/{dataset_id}/",
            { dataset_id: pending.id },
          ),
          { signal },
        )
        .then((response) => response.data.result),
    enabled: Boolean(pending),
    staleTime: 0,
    refetchInterval: (query) => {
      const data = query.state.data;
      if (data?.media_dispatch_status === "pending") return 1000;
      return ["completed", "failed"].includes(data?.processing_status)
        ? false
        : 1000;
    },
    retry: false,
    meta: { errorHandled: true },
  });
  useEffect(() => {
    if (!pending || consumed.current === pending.id) return;
    const state = progress.data?.processing_status;
    if (state !== "completed" && state !== "failed") return;
    if (
      state === "completed" &&
      ["pending", "failed"].includes(progress.data?.media_dispatch_status)
    ) {
      // The table exists: never offer a duplicate upload. Keep its progress id
      // so the existing recheck button can observe an operator's redrive.
      return;
    }
    consumed.current = pending.id;
    setPending(null);
    if (state === "completed") {
      callbacks.current.onSuccess(pending.response, pending.form);
    } else {
      setFailure(
        progress.data.error_message ||
          "Dataset import failed. Try uploading the file again.",
      );
    }
  }, [pending, progress.data]);
  const mediaFailed =
    progress.data?.processing_status === "completed" &&
    progress.data?.media_dispatch_status === "failed";
  return {
    uploadDataset: (data) => {
      if (!mutation.isPending && !pending) mutation.mutate(data);
    },
    // Retain the id for rechecks/duplicate protection, not as a busy flag.
    pendingDatasetId: pending?.id,
    isPending: mutation.isPending || (Boolean(pending) && !mediaFailed),
    clearUpload: () => {
      consumed.current = pending?.id;
      setPending(null);
      setFailure(null);
    },
    progressText: pending
      ? progress.data?.media_dispatch_status === "failed"
        ? null
        : progress.data?.media_dispatch_status === "pending"
          ? "Table saved. Waiting for media processing to be scheduled."
          : "Import in progress. Waiting for all values to be saved."
      : null,
    importError:
      (pending && progress.data?.media_dispatch_status === "failed"
        ? "Media processing failed. Your table is saved. Ask an operator to retry media processing, then check progress again."
        : null) ||
      failure ||
      (pending && progress.isError
        ? "Could not check import progress. Retrying; do not upload again."
        : null),
    retryProgress: progress.refetch,
  };
}
