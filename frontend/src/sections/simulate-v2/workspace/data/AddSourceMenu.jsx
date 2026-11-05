import PropTypes from "prop-types";
import { useRef, useState } from "react";
import { Box, Menu, MenuItem, Tooltip, Typography } from "@mui/material";
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
        slotProps={{ paper: { sx: { width: 280 } } }}
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
        <Tooltip arrow placement="left" title={SUPPORTED_LINE}>
          <MenuItem
            onClick={() => {
              onClose();
              fileRef.current?.click();
            }}
            sx={rowSx}
          >
            <Iconify icon="solar:upload-linear" width={16} />
            <Typography sx={{ typography: "s2", flex: 1 }}>
              Upload files
            </Typography>
            <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
              up to 25 MB
            </Typography>
          </MenuItem>
        </Tooltip>
        <Tooltip arrow placement="left" title={KB_SNAPSHOT_LINE}>
          <MenuItem
            onClick={() => {
              onClose();
              setKbOpen(true);
            }}
            sx={rowSx}
          >
            <Iconify icon="solar:document-text-linear" width={16} />
            <Typography sx={{ typography: "s2", flex: 1 }}>
              From the Knowledge base
            </Typography>
            <Iconify
              icon="solar:alt-arrow-right-linear"
              width={14}
              sx={{ color: "text.tertiary" }}
            />
          </MenuItem>
        </Tooltip>
        {/* Connectors that aren't ready: one quiet line, not a row each. */}
        <Tooltip
          arrow
          placement="left"
          title={UNAVAILABLE_CONNECTORS.map((c) => c.name).join(", ")}
        >
          <Box
            sx={{
              ...rowSx,
              display: "flex",
              alignItems: "center",
              px: 2,
              py: 1,
              mt: 0.5,
              borderTop: "1px solid",
              borderColor: "divider",
              color: "text.tertiary",
            }}
          >
            <Box sx={{ display: "flex", gap: 0.5, opacity: 0.5 }}>
              {UNAVAILABLE_CONNECTORS.map((c) => (
                <Iconify key={c.id} icon={c.icon} width={14} />
              ))}
            </Box>
            <Typography sx={{ typography: "s3", flex: 1 }}>
              Not available yet
            </Typography>
          </Box>
        </Tooltip>
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
const rowSx = { gap: 1, minHeight: 36, typography: "s2" };

AddSourceMenu.propTypes = {
  added: PropTypes.arrayOf(PropTypes.string),
  anchorEl: PropTypes.any,
  onClose: PropTypes.func.isRequired,
  onAddFiles: PropTypes.func.isRequired,
  onAddOrg: PropTypes.func.isRequired,
  multiple: PropTypes.bool,
  title: PropTypes.string,
};
