import PropTypes from "prop-types";
import { useRef, useState } from "react";
import { Box, Menu, MenuItem, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import {
  SUPPORTED_ACCEPT,
  SUPPORTED_LINE,
  KB_SNAPSHOT_LINE,
  UNAVAILABLE_CONNECTORS,
} from "../../_mock/orgData";
import KnowledgeBasePicker from "./KnowledgeBasePicker";

/**
 * Add a source: upload files, or copy one from the Knowledge base. One menu
 * for every place a file can be added — World & tools → Sources, supplying
 * missing records, and citing evidence on a scenario. The upload menu, the
 * file picker and the error message all read the same list of types.
 */
export default function AddSourceMenu({
  anchorEl,
  onClose,
  onAddFiles,
  onAddOrg,
  multiple = true,
  added = [],
  title = null,
}) {
  const fileRef = useRef(null);
  const [kbOpen, setKbOpen] = useState(false);
  return (
    <>
      <Menu
        anchorEl={anchorEl}
        open={!!anchorEl}
        onClose={onClose}
        slotProps={{ paper: { sx: { width: 320 } } }}
      >
        {title && (
          <Typography
            sx={{
              typography: "s3",
              color: "text.secondary",
              px: 2,
              pt: 0.5,
              pb: 1,
              whiteSpace: "normal",
            }}
          >
            {title}
          </Typography>
        )}
        <MenuItem
          onClick={() => {
            onClose();
            fileRef.current?.click();
          }}
          sx={{ alignItems: "flex-start", gap: 1, whiteSpace: "normal" }}
        >
          <Iconify icon="solar:upload-linear" width={16} sx={{ mt: "2px" }} />
          <Box>
            <Typography sx={{ typography: "s2", fontWeight: 600 }}>
              Upload files
            </Typography>
            <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
              {SUPPORTED_LINE}
            </Typography>
          </Box>
        </MenuItem>
        <MenuItem
          onClick={() => {
            onClose();
            setKbOpen(true);
          }}
          sx={{ alignItems: "flex-start", gap: 1, whiteSpace: "normal" }}
        >
          <Iconify
            icon="solar:document-text-linear"
            width={16}
            sx={{ mt: "2px" }}
          />
          <Box>
            <Typography sx={{ typography: "s2", fontWeight: 600 }}>
              Choose from Knowledge base
            </Typography>
            <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
              {KB_SNAPSHOT_LINE}
            </Typography>
          </Box>
        </MenuItem>
        <Typography
          sx={{
            typography: "s3",
            color: "text.tertiary",
            px: 2,
            pt: 1,
            pb: 0.5,
            borderTop: "1px solid",
            borderColor: "divider",
          }}
        >
          Other connectors
        </Typography>
        {UNAVAILABLE_CONNECTORS.map((c) => (
          <MenuItem key={c.id} disabled sx={{ gap: 1 }}>
            <Iconify icon={c.icon} width={16} />
            <Box sx={{ flex: 1, minWidth: 0 }}>
              <Typography noWrap sx={{ typography: "s2" }}>
                {c.name}
              </Typography>
              <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
                Not available yet
              </Typography>
            </Box>
          </MenuItem>
        ))}
      </Menu>
      <KnowledgeBasePicker
        open={kbOpen}
        onClose={() => setKbOpen(false)}
        added={added}
        onRequestAccess={(o) => {
          setKbOpen(false);
          onAddOrg(o);
        }}
        onAddOrg={(o) => {
          setKbOpen(false);
          onAddOrg(o);
        }}
      />
      <input
        ref={fileRef}
        hidden
        multiple={multiple}
        type="file"
        accept={SUPPORTED_ACCEPT}
        onChange={(e) => {
          onAddFiles(Array.from(e.target.files || []));
          e.target.value = "";
        }}
      />
    </>
  );
}
AddSourceMenu.propTypes = {
  added: PropTypes.arrayOf(PropTypes.string),
  anchorEl: PropTypes.any,
  onClose: PropTypes.func.isRequired,
  onAddFiles: PropTypes.func.isRequired,
  onAddOrg: PropTypes.func.isRequired,
  multiple: PropTypes.bool,
  title: PropTypes.string,
};
