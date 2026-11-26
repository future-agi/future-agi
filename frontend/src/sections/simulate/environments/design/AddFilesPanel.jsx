import PropTypes from "prop-types";
import { useEffect, useRef, useState } from "react";
import { Box, Button, Stack } from "@mui/material";
import { enqueueSnackbar } from "notistack";
import BuildFilesField from "../buildDrawer/forms/shared/BuildFilesField";
import {
  draftFilesOf,
  uploadsLabel,
  useFileUploads,
} from "../buildDrawer/forms/shared/useFileUploads";
import { OUTLINED_BTN_SX } from "../buildDrawer/drawerStyles";

const refusedRow = (file) => ({
  id: `taken-${file.name}-${file.size}-${file.lastModified}`,
  name: file.name,
  size: file.size || 0,
  type: file.type || "",
  status: "failed",
  progress: 0,
  error: "duplicate_name",
  file,
});

/**
 * Files added to a design while it waits: each uploads as it's picked, with
 * its progress or why it failed, and only the finished ones join the design,
 * together, when Add is pressed. A name already in the design is refused, as
 * the design's documents can't share one. Unmount it to drop the uploads.
 */
export default function AddFilesPanel({ taken, onAdd, onCancel }) {
  const uploads = useFileUploads();
  const [refused, setRefused] = useState([]);
  const box = useRef(null);

  useEffect(() => box.current?.focus(), []);

  const field = {
    files: [...uploads.files, ...refused],
    add: (list) => {
      const picked = Array.from(list || []);
      const dup = picked.filter((f) => taken.includes(f.name)).map(refusedRow);
      setRefused((prev) => [
        ...prev.filter((r) => !dup.some((d) => d.id === r.id)),
        ...dup,
      ]);
      uploads.add(picked.filter((f) => !taken.includes(f.name)));
    },
    retry: (id) => {
      const row = refused.find((r) => r.id === id);
      if (!row) return uploads.retry(id);
      if (taken.includes(row.name))
        return enqueueSnackbar(
          `${row.name} is already in the design. Rename the file and add it again.`,
          { variant: "info" },
        );
      setRefused((prev) => prev.filter((r) => r.id !== id));
      return uploads.add([row.file]);
    },
    remove: (id) =>
      refused.some((r) => r.id === id)
        ? setRefused((prev) => prev.filter((r) => r.id !== id))
        : uploads.remove(id),
  };

  const busy = uploads.uploading.length > 0;
  const n = uploads.done.length;
  const label =
    ((busy || n > 0) &&
      uploadsLabel(
        { ...uploads, failed: [...uploads.failed, ...refused] },
        "Add",
      )) ||
    (n ? `Add ${n} file${n === 1 ? "" : "s"}` : "Add files");

  return (
    <Box
      ref={box}
      tabIndex={-1}
      role="group"
      aria-label="Add files to the design"
      sx={{ outline: "none" }}
    >
      {/* The page's own Add source menu replaces this picker later. */}
      <BuildFilesField uploads={field} />
      <Stack
        direction="row"
        justifyContent="flex-end"
        spacing={1}
        sx={{ mt: 1.5 }}
      >
        <Button
          size="small"
          variant="outlined"
          onClick={onCancel}
          sx={OUTLINED_BTN_SX}
        >
          Cancel
        </Button>
        <Button
          size="small"
          variant="outlined"
          disabled={busy || n === 0}
          onClick={() => onAdd(draftFilesOf(uploads.done))}
          sx={OUTLINED_BTN_SX}
        >
          {label}
        </Button>
      </Stack>
    </Box>
  );
}
AddFilesPanel.propTypes = {
  // Names of the files already in the design.
  taken: PropTypes.arrayOf(PropTypes.string).isRequired,
  // Uploaded rows ({ id, name, size, type, documentId }) to add.
  onAdd: PropTypes.func.isRequired,
  onCancel: PropTypes.func.isRequired,
};
