import PropTypes from "prop-types";
import UploadDropZone from "src/sections/agent-playground/connect/UploadDropZone";
import { saveConnectDraft } from "src/sections/agent-playground/connect/connectDraft";
import { UPLOAD_SOURCE } from "src/sections/agent-playground/connect/connectSources";
import { buildDraftKey } from "../buildDraft";

/**
 * The build drawer's folder drop zone. A dropped folder is uploaded and read
 * here, then the drawer's upload opens on the agents it found. Lives with the
 * drawer's forms, which already reuse the Agents page's connect flows.
 */
export default function DrawerUploadZone({ item, onPick }) {
  return (
    <UploadDropZone
      onScanned={({ folder, scanId }) => {
        // Opening the upload clears its drafts, so the found agents are
        // saved after it, for its form to open on.
        onPick(item);
        saveConnectDraft(
          { stage: "agents", folder, scanId },
          UPLOAD_SOURCE,
          buildDraftKey(item.id, "flow"),
        );
      }}
    />
  );
}

DrawerUploadZone.propTypes = {
  item: PropTypes.object.isRequired,
  onPick: PropTypes.func.isRequired,
};
