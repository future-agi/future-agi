import PropTypes from "prop-types";
import { useRef, useState } from "react";
import { Box, Button, IconButton, Stack, Typography } from "@mui/material";
import { alpha } from "@mui/material/styles";
import Iconify from "src/components/iconify";
import UiOnlyFlag from "../../../components/UiOnlyFlag";
import { OUTLINED_BTN_SX, ROW_BOX_SX, T12, T13 } from "../../drawerStyles";
import {
  BUILD_FILES_ACCEPT,
  extOf,
  mergeBuildFiles,
  readAsOf,
} from "./buildFiles";

/**
 * Files for the environment a form builds: a policy becomes rules, a CSV the
 * world's records. Files stay in the form for now; nothing sends them yet.
 */
export default function BuildFilesField({
  files,
  onChange,
  accept = BUILD_FILES_ACCEPT,
  flagged = false,
}) {
  const input = useRef(null);
  const [over, setOver] = useState(false);
  const add = (list) => {
    if (list?.length) onChange(mergeBuildFiles(files, list));
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
      {files.length > 0 && (
        <Box
          component="ul"
          aria-label="Files and data"
          sx={{
            ...ROW_BOX_SX,
            listStyle: "none",
            m: 0,
            p: 0,
            mt: 1,
            "& > * + *": { borderTop: "1px solid", borderColor: "divider" },
          }}
        >
          {files.map((f) => {
            const role = readAsOf(f.name, accept);
            return (
              <Stack
                component="li"
                key={f.id}
                direction="row"
                alignItems="center"
                spacing={1.25}
                sx={{ px: 1.5, minHeight: 40 }}
              >
                <Box
                  sx={{
                    px: 0.75,
                    height: 20,
                    display: "grid",
                    placeItems: "center",
                    border: "1px solid",
                    borderColor: "divider",
                    borderRadius: "4px",
                    ...T12,
                    fontWeight: 600,
                    color: "text.secondary",
                    fontFamily:
                      "ui-monospace, SFMono-Regular, Menlo, monospace",
                    flexShrink: 0,
                  }}
                >
                  {extOf(f.name).toUpperCase()}
                </Box>
                <Typography
                  noWrap
                  sx={{ ...T13, fontWeight: 500, minWidth: 0 }}
                >
                  {f.name}
                </Typography>
                <Typography
                  noWrap
                  sx={{ ...T12, color: "text.tertiary", flex: 1, minWidth: 0 }}
                >
                  {role ? `Read as ${role}` : "Can't be read"}
                </Typography>
                <Typography
                  sx={{
                    ...T12,
                    color: "text.disabled",
                    fontVariantNumeric: "tabular-nums",
                    flexShrink: 0,
                  }}
                >
                  {kb(f.size)}
                </Typography>
                <IconButton
                  size="small"
                  aria-label={`Remove ${f.name}`}
                  onClick={() => onChange(files.filter((x) => x.id !== f.id))}
                  sx={{ color: "text.secondary", p: 0.25, flexShrink: 0 }}
                >
                  <Iconify icon="solar:close-circle-linear" width={16} />
                </IconButton>
              </Stack>
            );
          })}
        </Box>
      )}
    </Box>
  );
}
BuildFilesField.propTypes = {
  // A "UI only" pill, for a form whose build is wired but ignores this.
  flagged: PropTypes.bool,
  // [{ id, name, size, type }]: plain rows, so they can travel with a draft.
  files: PropTypes.arrayOf(PropTypes.object).isRequired,
  onChange: PropTypes.func.isRequired,
  accept: PropTypes.string,
};
