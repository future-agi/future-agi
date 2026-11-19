import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useUploadDesignDocument } from "src/api/simulate-environments/designDocuments";

// Refusals Retry can fix: a dropped upload, or a name freed since.
export const RETRYABLE_UPLOADS = ["upload_failed", "duplicate_name"];

const rowIdOf = (file) => `${file.name}-${file.size}-${file.lastModified}`;

const taken = (rows, name, id) =>
  rows.some((f) => f.name === name && f.id !== id && f.status !== "failed");

// Picked files merged into the rows: an identical re-pick is skipped, a pick
// replaces a failed row of its name, and a name already kept is refused here,
// since the design start refuses documents that share a name.
function addRows(prev, picked) {
  let rows = prev;
  const start = [];
  picked.forEach((file) => {
    const id = rowIdOf(file);
    if (rows.some((f) => f.id === id && f.status !== "failed")) return;
    const dup = taken(rows, file.name, id);
    const row = {
      id,
      name: file.name,
      size: file.size || 0,
      type: file.type || "",
      status: dup ? "failed" : "uploading",
      progress: 0,
      error: dup ? "duplicate_name" : null,
      documentId: null,
    };
    const at = rows.findIndex(
      (f) => f.name === file.name && f.status === "failed",
    );
    rows = at < 0 ? [...rows, row] : rows.map((f, i) => (i === at ? row : f));
    if (!dup) start.push(id);
  });
  return { rows, start };
}

/**
 * Files uploaded one request each as they're picked. `files` are plain rows
 * ({ id, name, size, type, status: uploading | done | failed, progress,
 * error, documentId }); `uploading`, `failed` and `done` split them. A failed
 * file never blocks the others.
 */
export function useFileUploads() {
  const upload = useUploadDesignDocument();
  const [files, setFiles] = useState([]);
  const filesRef = useRef(files);
  filesRef.current = files;
  // Row id -> { file, controller }: the File for Retry, and its upload.
  const live = useRef(new Map());

  const patch = useCallback(
    (id, change) =>
      setFiles((prev) =>
        prev.map((f) => (f.id === id ? { ...f, ...change } : f)),
      ),
    [],
  );

  const start = useCallback(
    (id) => {
      const entry = live.current.get(id);
      if (!entry) return;
      entry.controller?.abort();
      const controller = new AbortController();
      entry.controller = controller;
      upload(entry.file, {
        signal: controller.signal,
        onProgress: (progress) => patch(id, { progress }),
      })
        .then((doc) => {
          if (!controller.signal.aborted)
            patch(id, { status: "done", progress: 100, documentId: doc.id });
        })
        .catch((err) => {
          if (!controller.signal.aborted)
            patch(id, {
              status: "failed",
              error: err?.code || "upload_failed",
            });
        });
    },
    [upload, patch],
  );

  const add = useCallback(
    (list) => {
      const picked = Array.from(list || []);
      if (!picked.length) return;
      const { rows, start: ids } = addRows(filesRef.current, picked);
      picked.forEach((file) => {
        const id = rowIdOf(file);
        if (ids.includes(id)) live.current.get(id)?.controller?.abort();
        if (ids.includes(id) || !live.current.has(id))
          live.current.set(id, { file });
      });
      // A failed row a new pick replaced.
      [...live.current.keys()].forEach((id) => {
        if (!rows.some((f) => f.id === id)) live.current.delete(id);
      });
      filesRef.current = rows;
      setFiles((prev) => addRows(prev, picked).rows);
      ids.forEach(start);
    },
    [start],
  );

  const retry = useCallback(
    (id) => {
      const row = filesRef.current.find((f) => f.id === id);
      if (!row || row.status !== "failed") return;
      if (taken(filesRef.current, row.name, id)) return;
      patch(id, { status: "uploading", progress: 0, error: null });
      start(id);
    },
    [patch, start],
  );

  const remove = useCallback((id) => {
    live.current.get(id)?.controller?.abort();
    live.current.delete(id);
    setFiles((prev) => prev.filter((f) => f.id !== id));
  }, []);

  useEffect(() => {
    const entries = live.current;
    return () => entries.forEach((e) => e.controller?.abort());
  }, []);

  return useMemo(
    () => ({
      files,
      add,
      retry,
      remove,
      uploading: files.filter((f) => f.status === "uploading"),
      failed: files.filter((f) => f.status === "failed"),
      done: files.filter((f) => f.status === "done"),
    }),
    [files, add, retry, remove],
  );
}

/** Uploaded rows as a design draft keeps them, with their document ids. */
export const draftFilesOf = (done) =>
  done.map(({ id, name, size, type, documentId }) => ({
    id,
    name,
    size,
    type,
    documentId,
  }));

/**
 * The build button's words while files upload ("Uploading 2 of 3…") or after
 * some failed ("Build without 1 failed file"); null when neither.
 */
export function uploadsLabel({ uploading, failed, done }, verb = "Build") {
  if (uploading.length)
    return `Uploading ${done.length + 1} of ${done.length + uploading.length}…`;
  if (failed.length)
    return `${verb} without ${failed.length} failed file${failed.length === 1 ? "" : "s"}`;
  return null;
}
