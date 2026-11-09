// APPROVED — site DSX-COPY activation-001 (approvals c37c14a1; adopted aef0249c):
// PROPOSED_HF_IMPORT_COPY.notice and .addSubtitle, plus the launch empty-state
// description in DevelopView.jsx. Existing export names stay unchanged.
// APPROVED — site DSX-ADDROWS activation-001 (approvals 0efdf49e):
// all 10 PROPOSED_CLASSIC_ROW_UPLOAD_COPY fields and
// PROPOSED_ADD_ROWS_COPY.emptyRowSubtitle. Existing export names stay unchanged.
// PROPOSED — all other fields retain proposed status.
export const PROPOSED_HF_IMPORT_COPY = Object.freeze({
  title: "Hugging Face",
  createAction: "Create dataset",
  addAction: "Add rows",
  notice:
    "Hugging Face imports can leave imported cells blank; they are turned off in this release. Instead, create a dataset from an uploaded file or copy rows from another dataset.",
  addSubtitle: "Hugging Face imports are unavailable in this release.",
});

export const PROPOSED_ADD_ROWS_COPY = Object.freeze({
  toolbarHint: "View row import options and availability.",
  sheetSubtitle:
    "Review import availability below; disabled options cannot add rows.",
  emptyRowSubtitle: "Empty rows for this dataset",
});

// APPROVED — classic append copy under site DSX-ADDROWS activation-001.
export const PROPOSED_CLASSIC_ROW_UPLOAD_COPY = Object.freeze({
  title: "Upload a file",
  subtitle: "Upload a file to add rows to this dataset.",
  heading: "Choose a file or drag & drop it here",
  formats: "Supports JSONL, JSON, and CSV file format up to 25 MB",
  browse: "Browse files",
  cancel: "Close",
  close: "Close",
  done: "Add rows",
  success: "Rows added to this dataset.",
  error: "Couldn't upload the file",
});
