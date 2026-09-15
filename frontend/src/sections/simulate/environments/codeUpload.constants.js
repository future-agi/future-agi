// Folder upload, matching the product's Create-RL-environment "Upload folder"
// (HarnessCreate): a webkitdirectory input sends a copy of a local folder to the
// isolated runner, leaving .env and generated dependency folders behind.
export const CODE_UPLOAD_COPY = {
  title: "Upload your agent folder",
  hint: "Send a copy of a local folder to the isolated runner. .env files and generated dependency folders are left behind.",
  choose: "Choose folder",
  uploaded: "Files",
  emptyHint: "Choose a folder to continue",
  entryHelper: "Which file defines the agent's step function?",
};
