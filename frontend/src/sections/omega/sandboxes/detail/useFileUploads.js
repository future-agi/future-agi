import { useCallback, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";

import { sandboxKeys, useScope } from "../api/queries";
import { useSandboxesApi } from "../api/SandboxesApiContext";
import { fileErrorMessage, joinPath, nameProblem } from "../lib/files";

/**
 * Upload files from this computer into one folder of a sandbox, one after the
 * other (each streams into the sandbox and replaces a same-named file only
 * once all of it arrived). `progress` is `{ done, total, current }` while it
 * runs; `results` lists every file's outcome in Future AGI's words.
 */
export default function useFileUploads(sandboxId) {
  const api = useSandboxesApi();
  const scope = useScope();
  const queryClient = useQueryClient();
  const [progress, setProgress] = useState(null);
  const [results, setResults] = useState([]);

  const upload = useCallback(
    async (dir, fileList) => {
      const files = Array.from(fileList || []);
      if (files.length === 0) return [];
      const outcomes = [];
      setResults([]);
      for (const [index, file] of files.entries()) {
        setProgress({ done: index, total: files.length, current: file.name });
        const problem = nameProblem(file.name);
        if (problem) {
          outcomes.push({ name: file.name, ok: false, message: problem });
          continue;
        }
        try {
          const saved = await api.uploadFile(
            sandboxId,
            joinPath(dir, file.name),
            file,
          );
          outcomes.push({
            name: file.name,
            ok: true,
            bytes: saved.bytes,
            path: saved.path,
          });
        } catch (error) {
          outcomes.push({
            name: file.name,
            ok: false,
            message: fileErrorMessage(error),
          });
        }
      }
      setProgress(null);
      setResults(outcomes);
      // Refresh the listing, the open file and Activity, as every other file action does.
      await queryClient.invalidateQueries({ queryKey: sandboxKeys.all(scope) });
      return outcomes;
    },
    [api, sandboxId, queryClient, scope],
  );

  return { upload, progress, results, clear: () => setResults([]) };
}
