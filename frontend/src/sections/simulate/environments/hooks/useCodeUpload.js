import { useReducer, useRef } from "react";
import { fData } from "src/utils/format-number";
import {
  prepareSourceFolder,
  uploadErrorText,
  uploadSourceFolder,
} from "src/pages/dashboard/harness/sourceUpload";
import { errorMessage } from "src/pages/dashboard/harness/harnessShared";
import { DEFAULT_SCENARIOS } from "../panels/scenarioCountRules";
import { CODE_UPLOAD_COPY } from "../codeUpload.constants";

const initial = {
  folderName: "",
  fileNames: [],
  entry: "",
  envText: "",
  egress: "",
  secretFiles: [],
  scenarioCount: DEFAULT_SCENARIOS,
  observabilityEnabled: false,
  summary: null, // { fileCount, totalBytes, excluded } from the upload response
  archiveArtifactId: null,
  uploading: false,
  progress: 0,
  error: "",
};

function reducer(s, a) {
  switch (a.type) {
    case "reset":
      return initial;
    // A folder is uploaded as one archive, so a new selection replaces the old
    // one wholesale and re-gates the CTA until this upload resolves.
    case "selected":
      return {
        ...s,
        folderName: a.folderName,
        fileNames: a.fileNames,
        entry: s.entry || a.fileNames[0] || "",
        summary: null,
        archiveArtifactId: null,
        uploading: true,
        progress: 0,
        error: "",
      };
    case "progress":
      return { ...s, progress: a.value };
    case "prepareError":
      return { ...s, error: a.message };
    case "uploadSuccess":
      return {
        ...s,
        uploading: false,
        progress: 100,
        archiveArtifactId: a.id,
        summary: a.summary,
        error: "",
      };
    case "uploadFail":
      return {
        ...s,
        uploading: false,
        archiveArtifactId: null,
        error: a.message,
      };
    case "set": {
      const value =
        typeof a.value === "function" ? a.value(s[a.field]) : a.value;
      return { ...s, [a.field]: value };
    }
    default:
      return s;
  }
}

/**
 * The Build environment drawer's folder-upload form state: the picked folder, its upload to the runner,
 * and the source preflight and the build read. `build` is usePanelBuild().
 */
export default function useCodeUpload(build) {
  const [form, dispatch] = useReducer(reducer, initial);
  // Any edit invalidates a prior preflight result, so re-disable Build.
  const set = (field) => (value) => {
    dispatch({ type: "set", field, value });
    build.resetPreflight();
  };
  // Each new folder selection bumps this; an in-flight upload only applies its
  // result if it is still the latest, so switching folders mid-upload can't let
  // a stale upload clobber the current selection.
  const uploadSeq = useRef(0);
  const {
    folderName,
    fileNames,
    entry,
    envText,
    egress,
    secretFiles,
    scenarioCount,
    observabilityEnabled,
    summary,
    archiveArtifactId,
    uploading,
    progress,
  } = form;

  const chosen = uploading || !!summary;
  const canGo = !!archiveArtifactId && !uploading;

  const summaryLine = () => {
    if (uploading) return `${CODE_UPLOAD_COPY.uploadingHint} ${progress}%`;
    if (!summary) return "";
    const parts = [
      `${summary.fileCount} file${summary.fileCount === 1 ? "" : "s"}`,
    ];
    if (summary.totalBytes) parts.push(fData(summary.totalBytes));
    if (summary.excluded) parts.push(`${summary.excluded} excluded`);
    return parts.join(" · ");
  };

  const buildSource = () => ({
    kind: "upload",
    folderName,
    entry: entry.trim(),
    files: fileNames.map((name) => ({ name })),
    archive_artifact_id: archiveArtifactId,
    envText: envText.trim() || null,
    egress: egress.trim() || null,
    secretFiles,
    scenarioCount: Number(scenarioCount) || undefined,
    observabilityEnabled,
  });

  const onDrop = async (list) => {
    // A new folder supersedes both any upload in flight and any prior preflight.
    build.resetPreflight();
    const seq = (uploadSeq.current += 1);
    let prepared;
    try {
      prepared = prepareSourceFolder(list);
    } catch (e) {
      dispatch({ type: "prepareError", message: e.message });
      return;
    }
    dispatch({
      type: "selected",
      folderName: prepared.name,
      fileNames: prepared.paths,
    });

    try {
      const result = await uploadSourceFolder(prepared, (value) => {
        if (seq === uploadSeq.current) dispatch({ type: "progress", value });
      });
      if (seq !== uploadSeq.current) return;
      dispatch({
        type: "uploadSuccess",
        id: result?.source_id,
        summary: {
          name: result?.name ?? prepared.name,
          fileCount: result?.file_count ?? prepared.files.length,
          totalBytes: result?.total_bytes ?? prepared.totalBytes,
          excluded: prepared.excludedCount,
        },
      });
    } catch (e) {
      if (seq !== uploadSeq.current) return;
      dispatch({
        type: "uploadFail",
        message: uploadErrorText(e, errorMessage),
      });
    }
  };

  return { form, set, onDrop, chosen, canGo, summaryLine, buildSource };
}
