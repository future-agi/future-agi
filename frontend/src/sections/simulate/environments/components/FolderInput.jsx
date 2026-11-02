import PropTypes from "prop-types";
import { Box } from "@mui/material";

// Hidden folder picker reused by both the empty drop zone and the Replace
// button. webkitdirectory/directory are spread via Box (a raw <input> trips
// react/no-unknown-property), matching the product's HarnessCreate.
export default function FolderInput({ onPick }) {
  return (
    <Box
      component="input"
      hidden
      multiple
      directory=""
      webkitdirectory=""
      type="file"
      onChange={(e) => {
        // Copy the FileList before resetting value="" — the reset empties
        // e.target.files, and a live reference would empty with it.
        const list = Array.from(e.target.files || []);
        e.target.value = "";
        if (list.length) onPick(list);
      }}
    />
  );
}
FolderInput.propTypes = { onPick: PropTypes.func.isRequired };
