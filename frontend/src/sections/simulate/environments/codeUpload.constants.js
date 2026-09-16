// Folder upload, matching the product's Create-RL-environment "Upload folder"
// (HarnessCreate): a webkitdirectory input sends a copy of a local folder to the
// isolated runner, leaving .env and generated dependency folders behind.
export const CODE_UPLOAD_COPY = {
  title: "Upload your agent folder",
  hint: "Send a copy of a local folder to the isolated runner. .env files and generated dependency folders are left behind.",
  choose: "Choose folder",
  replace: "Replace",
  uploaded: "Files",
  emptyHint: "Choose a folder to continue",
  uploadingHint: "Uploading your folder to the runner…",
  entryHelper: "Which file defines the agent's step function?",
  // The runner rejects an upload whose form-field count exceeds the server
  // limit; each file contributes two fields, so very large folders trip it.
  tooManyFiles:
    "This folder has too many files for the runner to accept in one upload. Trim it to your agent's own source, or upload a smaller subfolder.",
};
