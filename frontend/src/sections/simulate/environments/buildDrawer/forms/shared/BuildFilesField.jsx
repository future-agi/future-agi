import PropTypes from "prop-types";
import { useRef, useState } from "react";
import { Box, Button, Stack, Typography } from "@mui/material";
import { alpha } from "@mui/material/styles";
import Iconify from "src/components/iconify";
import UiOnlyFlag from "../../../components/UiOnlyFlag";
import { OUTLINED_BTN_SX, T12 } from "../../drawerStyles";
import { BUILD_FILES_ACCEPT, mergeBuildFiles } from "./buildFiles";
import UploadingFiles from "./UploadingFiles";

/**
 * Files for the environment a form builds: a policy becomes rules, a CSV the
 * world's records. With `uploads` (useFileUploads) each file uploads as it's
 * picked and shows its progress or why it failed; without, picked files go
 * to `onChange` as plain rows.
 */
export default function BuildFilesField({
  uploads,
  files = [],
  onChange,
  accept = BUILD_FILES_ACCEPT,
  flagged = false,
}) {
  const input = useRef(null);
  const [over, setOver] = useState(false);
  const add = (list) => {
    if (!list?.length) return;
    if (uploads) uploads.add(list);
    else onChange(mergeBuildFiles(files, list));
  };
  return (
    <Box>
      <Stack direction="row" alignItems="center" spacing={0.75}>
        <Typography sx={{ ...T12, fontWeight: 600 }}>
          Files and data
          <Box
            component="span"
            sx={{ color: "text.disabled", fontWeight: 400, ml: 0.75 }}
          >
            optional
          </Box>
        </Typography>
        {flagged && <UiOnlyFlag feature="environmentFiles" />}
      </Stack>
      <Typography sx={{ ...T12, color: "text.secondary", mb: 0.75 }}>
        Your policy and records, used instead of generated ones.
      </Typography>
      <Box
        onDragOver={(e) => {
          e.preventDefault();
          setOver(true);
        }}
        onDragLeave={() => setOver(false)}
        onDrop={(e) => {
          e.preventDefault();
          setOver(false);
          add(e.dataTransfer?.files);
        }}
        sx={{
          display: "flex",
          alignItems: "center",
          gap: 1.5,
          px: 1.75,
          py: 1.5,
          border: "1px dashed",
          borderColor: over ? "text.primary" : "divider",
          borderRadius: "6px",
          bgcolor: (t) =>
            over ? alpha(t.palette.text.primary, 0.05) : "transparent",
          transition: "background-color .12s ease, border-color .12s ease",
        }}
      >
        <Iconify
          icon="solar:cloud-upload-linear"
          width={20}
          sx={{ color: "text.secondary", flexShrink: 0 }}
        />
        <Box flex={1} minWidth={0}>
          <Typography sx={{ ...T12, fontWeight: 600 }}>
            {over
              ? "Drop to add"
              : "Drop a policy, a CSV of records or notes here"}
          </Typography>
          <Typography sx={{ ...T12, color: "text.secondary" }}>
            PDF, Excel, CSV, JSON or text · up to 25 MB
          </Typography>
        </Box>
        <input
          ref={input}
          type="file"
          hidden
          multiple
          accept={accept}
          data-testid="build-files"
          onChange={(e) => {
            add(e.target.files);
            e.target.value = "";
          }}
        />
        <Button
          size="small"
          variant="outlined"
          onClick={() => input.current?.click()}
          startIcon={
            <Iconify icon="solar:upload-minimalistic-linear" width={14} />
          }
          sx={{ ...OUTLINED_BTN_SX, flexShrink: 0 }}
        >
          Choose files
        </Button>
      </Box>
      <UploadingFiles
        label="Files and data"
        files={uploads ? uploads.files : files}
        accept={accept}
        onRetry={uploads?.retry}
        onRemove={
          uploads
            ? uploads.remove
            : (id) => onChange(files.filter((x) => x.id !== id))
        }
        sx={{ mt: 1 }}
      />
    </Box>
  );
}
BuildFilesField.propTypes = {
  // A "UI only" pill, for a form whose build is wired but ignores this.
  flagged: PropTypes.bool,
  // What useFileUploads returns; used instead of files and onChange.
  uploads: PropTypes.object,
  // [{ id, name, size, type }]: plain rows, so they can travel with a draft.
  files: PropTypes.arrayOf(PropTypes.object),
  onChange: PropTypes.func,
  accept: PropTypes.string,
};
