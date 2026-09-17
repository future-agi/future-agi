import { PROJECT_SOURCE } from "src/utils/constants";

// Return a row-type update only after the current project's kind is known.
// `null` means leave the form untouched, including while the query is pending
// and for immutable existing tasks.
export function nextTaskRowTypeForProject({
  isProjectSelected,
  projectDetailsResolved,
  projectSource,
  rowType,
  rowTypeLocked = false,
}) {
  if (!isProjectSelected || !projectDetailsResolved || rowTypeLocked) {
    return null;
  }
  const resolvedRowType =
    projectSource === PROJECT_SOURCE.SIMULATOR
      ? "voiceCalls"
      : rowType === "voiceCalls"
        ? "spans"
        : rowType;
  return resolvedRowType === rowType ? null : resolvedRowType;
}

// A create-page preview must wait until its default/draft row type agrees
// with the resolved project kind; otherwise it issues a disposable list read.
export function isTaskPreviewProjectKindReady({
  waitForProjectKind,
  projectDetailsResolved,
  projectSource,
  rowType,
}) {
  if (!waitForProjectKind) return true;
  if (!projectDetailsResolved) return false;
  const isVoiceProject = projectSource === PROJECT_SOURCE.SIMULATOR;
  return isVoiceProject ? rowType === "voiceCalls" : rowType !== "voiceCalls";
}
