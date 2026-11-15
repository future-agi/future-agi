import { Button } from "@mui/material";
import { saveAs } from "file-saver";
import { enqueueSnackbar } from "notistack";
import PropTypes from "prop-types";
import { useDownloadKnowledgeBaseFile } from "src/api/knowledge-base/files";

export default function DownloadFileCell({ file, knowledgeId }) {
  const { mutateAsync: downloadFile, isPending: downloading } = useDownloadKnowledgeBaseFile();

  const download = async (event) => {
    event.stopPropagation();
    try {
      const blob = await downloadFile({ kbId: knowledgeId, fileId: file.id });
      saveAs(blob, file.name);
    } catch {
      enqueueSnackbar("Unable to download this file. Check your access and try again.", {
        variant: "error",
      });
    }
  };

  return (
    <Button
      size="small"
      variant="outlined"
      disabled={downloading || file.status === "Deleting"}
      onClick={download}
      aria-label={`Download ${file.name}`}
    >
      {downloading ? "Downloading…" : "Download"}
    </Button>
  );
}

DownloadFileCell.propTypes = {
  file: PropTypes.shape({
    id: PropTypes.string.isRequired,
    name: PropTypes.string.isRequired,
    status: PropTypes.string,
  }).isRequired,
  knowledgeId: PropTypes.string.isRequired,
};
