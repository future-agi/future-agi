import PropTypes from "prop-types";
import { useRef } from "react";
import { enqueueSnackbar } from "notistack";
import { Box, Menu, MenuItem, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import { copyToClipboard } from "src/utils/utils";
import { SOURCES_COPY, UNAVAILABLE_CONNECTORS } from "./sources.constants";

const COPY = SOURCES_COPY.menu;
const rowSx = { gap: 1, minHeight: 36 };

/**
 * Add a source: upload files, copy one from the Knowledge base, or the same
 * import from code. `limits` is the upload route's own (formats and sizes).
 */
export default function AddSourceMenu({
  anchorEl,
  onClose,
  onAddFiles,
  onAddKb,
  limits,
  command,
}) {
  const fileRef = useRef(null);
  const accept = (limits?.formats || []).map((ext) => `.${ext}`).join(",");

  const copyCommand = async () => {
    onClose();
    const ok = await copyToClipboard(command);
    enqueueSnackbar(ok ? COPY.copied : COPY.copyFailed, {
      variant: ok ? "success" : "error",
    });
  };

  return (
    <>
      <Menu
        anchorEl={anchorEl}
        open={Boolean(anchorEl)}
        onClose={onClose}
        slotProps={{ paper: { sx: { width: 280 } } }}
      >
        <CustomTooltip
          show={Boolean(limits)}
          arrow
          placement="left"
          title={COPY.uploadHint(limits)}
        >
          <MenuItem
            onClick={() => {
              onClose();
              fileRef.current?.click();
            }}
            sx={rowSx}
          >
            <Iconify icon="solar:upload-linear" width={16} />
            <Typography sx={{ typography: "s1", flex: 1 }}>
              {COPY.upload}
            </Typography>
            <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
              {COPY.uploadLimit(limits)}
            </Typography>
          </MenuItem>
        </CustomTooltip>
        <CustomTooltip show arrow placement="left" title={COPY.kbHint}>
          <MenuItem
            onClick={() => {
              onClose();
              onAddKb?.();
            }}
            sx={rowSx}
          >
            <Iconify icon="solar:document-text-linear" width={16} />
            <Typography sx={{ typography: "s1", flex: 1 }}>
              {COPY.kb}
            </Typography>
            <Iconify
              icon="solar:alt-arrow-right-linear"
              width={14}
              sx={{ color: "text.tertiary" }}
            />
          </MenuItem>
        </CustomTooltip>
        <CustomTooltip show arrow placement="left" title={command}>
          <MenuItem onClick={copyCommand} sx={rowSx}>
            <Iconify icon="solar:code-linear" width={16} />
            <Typography sx={{ typography: "s1", flex: 1 }}>
              {COPY.code}
            </Typography>
            <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
              {COPY.codeNote}
            </Typography>
          </MenuItem>
        </CustomTooltip>
        <CustomTooltip
          show
          arrow
          placement="left"
          title={UNAVAILABLE_CONNECTORS.map((one) => one.name).join(", ")}
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
              {UNAVAILABLE_CONNECTORS.map((one) => (
                <Iconify key={one.id} icon={one.icon} width={14} />
              ))}
            </Box>
            <Typography sx={{ typography: "s3", flex: 1 }}>
              {COPY.unavailable}
            </Typography>
          </Box>
        </CustomTooltip>
      </Menu>
      <input
        ref={fileRef}
        hidden
        multiple
        type="file"
        accept={accept}
        onChange={(event) => {
          const files = Array.from(event.target.files || []);
          event.target.value = "";
          if (files.length) onAddFiles(files);
        }}
      />
    </>
  );
}

AddSourceMenu.propTypes = {
  anchorEl: PropTypes.any,
  onClose: PropTypes.func.isRequired,
  onAddFiles: PropTypes.func.isRequired,
  // Opens the Knowledge base picker.
  onAddKb: PropTypes.func,
  limits: PropTypes.shape({
    formats: PropTypes.arrayOf(PropTypes.string),
    max_files: PropTypes.number,
    max_file_bytes: PropTypes.number,
    max_total_bytes: PropTypes.number,
  }),
  command: PropTypes.string.isRequired,
};
