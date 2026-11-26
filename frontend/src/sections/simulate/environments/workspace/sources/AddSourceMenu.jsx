import PropTypes from "prop-types";
import { useRef } from "react";
import { Menu, MenuItem, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import { SOURCES_COPY } from "./sources.constants";

const COPY = SOURCES_COPY.menu;
const rowSx = { gap: 1, minHeight: 36 };

export default function AddSourceMenu({
  anchorEl,
  onClose,
  onAddFiles,
  onAddKb,
  limits,
}) {
  const fileRef = useRef(null);
  const accept = (limits?.formats || []).map((ext) => `.${ext}`).join(",");

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
            aria-label={COPY.upload}
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
        {onAddKb && (
          <CustomTooltip show arrow placement="left" title={COPY.kbHint}>
            <MenuItem
              aria-label={COPY.kb}
              onClick={() => {
                onClose();
                onAddKb();
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
        )}
      </Menu>
      <input
        data-testid="build-files"
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
  // Opens the Knowledge base picker; without it the row is left out.
  onAddKb: PropTypes.func,
  limits: PropTypes.shape({
    formats: PropTypes.arrayOf(PropTypes.string),
    max_files: PropTypes.number,
    max_file_bytes: PropTypes.number,
    max_total_bytes: PropTypes.number,
  }),
};
