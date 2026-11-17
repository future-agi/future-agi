export const PROPOSED_HF_IMPORT_COPY = Object.freeze({
  title: "Hugging Face",
  createAction: "Create dataset",
  addAction: "Add rows",
  notice:
    "This install lacks the datasets package needed for Hugging Face import.",
  addSubtitle:
    "Hugging Face import needs the datasets package on this install.",
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
