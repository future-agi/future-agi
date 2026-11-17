import { useCallback, useState } from "react";
import { useSnackbar } from "notistack";

import { useSandboxesApi } from "../api/SandboxesApiContext";
import { baseName, fileErrorMessage, saveBlob } from "../lib/files";

/**
 * Download one file from a sandbox to this computer: the bytes stream from
 * inside the sandbox, then the browser saves them under the file's own name.
 * A refusal is a snackbar in Future AGI's words; `pending` is the path in flight.
 */
export default function useFileDownload(sandboxId) {
  const api = useSandboxesApi();
  const { enqueueSnackbar } = useSnackbar();
  const [pending, setPending] = useState(null);
  const download = useCallback(
    async (path) => {
      setPending(path);
      try {
        const blob = await api.downloadFile(sandboxId, path);
        saveBlob(blob, baseName(path));
      } catch (error) {
        enqueueSnackbar(`${baseName(path)}: ${fileErrorMessage(error)}`, {
          variant: "error",
        });
      } finally {
        setPending(null);
      }
    },
    [api, sandboxId, enqueueSnackbar],
  );
  return { download, pending };
}
