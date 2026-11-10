import { Button } from "@mui/material";
import { saveAs } from "file-saver";
import { enqueueSnackbar } from "notistack";
import PropTypes from "prop-types";
import { useState } from "react";
import axios, { endpoints } from "src/utils/axios";

export default function DownloadFileCell({ file, knowledgeId }) {
  const [downloading, setDownloading] = useState(false);

  const download = async (event) => {
    event.stopPropagation();
    setDownloading(true);
    try {
      const response = await axios.get(
        endpoints.knowledge.downloadFile(knowledgeId),
        { params: { file_id: file.id }, responseType: "blob" },
      );
      saveAs(response.data, file.name);
    } catch {
      enqueueSnackbar("Unable to download this file. Check your access and try again.", {
        variant: "error",
      });
    } finally {
      setDownloading(false);
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
