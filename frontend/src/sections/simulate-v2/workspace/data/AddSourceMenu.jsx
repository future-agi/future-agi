import PropTypes from "prop-types";
import { useRef } from "react";
import { Box, Menu, MenuItem, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import {
  ORG_SOURCES,
  SUPPORTED_LINE,
  KB_LIMIT_LINE,
  UNAVAILABLE_CONNECTORS,
} from "../../_mock/orgData";

/**
 * Add a source: upload files, or pick one from the Knowledge base by
 * reference. One menu for every place a file can be added — World & tools →
 * Sources, and citing evidence on a scenario.
 */
export default function AddSourceMenu({
  anchorEl,
  onClose,
  onAddFiles,
  onAddOrg,
  multiple = true,
}) {
  const fileRef = useRef(null);
  return (
    <>
      <Menu
        anchorEl={anchorEl}
        open={!!anchorEl}
        onClose={onClose}
        slotProps={{ paper: { sx: { width: 320 } } }}
      >
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
        <Typography
          sx={{
            typography: "s3",
            color: "text.tertiary",
            px: 2,
            pt: 1,
            pb: 0.5,
          }}
        >
          Choose from Knowledge base
        </Typography>
        {ORG_SOURCES.map((o) => (
          <MenuItem key={o.id} onClick={() => onAddOrg(o)} sx={{ gap: 1 }}>
            <Iconify
              icon={
                o.access
                  ? "solar:document-text-linear"
                  : "solar:lock-keyhole-minimalistic-linear"
              }
              width={16}
              sx={{ color: "text.secondary" }}
            />
            <Box sx={{ flex: 1, minWidth: 0 }}>
              <Typography noWrap sx={{ typography: "s2" }}>
                {o.access ? o.name : `${o.owner} source`}
              </Typography>
              <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
                {o.access
                  ? `${o.collection} · revision ${o.revision} · pinned`
                  : "No access — Request access"}
              </Typography>
            </Box>
          </MenuItem>
        ))}
        <Typography
          sx={{
            typography: "s3",
            color: "text.tertiary",
            px: 2,
            pt: 0.5,
            pb: 1,
          }}
        >
          {`${KB_LIMIT_LINE} Updates are never pulled in on their own — a new revision waits for you to review it.`}
        </Typography>
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
      <input
        ref={fileRef}
        hidden
        multiple={multiple}
        type="file"
        onChange={(e) => {
          onAddFiles(Array.from(e.target.files || []));
          e.target.value = "";
        }}
      />
    </>
  );
}
AddSourceMenu.propTypes = {
  anchorEl: PropTypes.any,
  onClose: PropTypes.func.isRequired,
  onAddFiles: PropTypes.func.isRequired,
  onAddOrg: PropTypes.func.isRequired,
  multiple: PropTypes.bool,
};
