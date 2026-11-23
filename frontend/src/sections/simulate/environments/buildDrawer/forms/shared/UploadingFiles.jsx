import PropTypes from "prop-types";
import { TransitionGroup } from "react-transition-group";
import {
  Box,
  Button,
  Collapse,
  IconButton,
  LinearProgress,
  Stack,
  Typography,
  useMediaQuery,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { formatFileSize } from "src/utils/utils";
import { BTN_SX, ROW_BOX_SX, T12, T13 } from "../../drawerStyles";
import { BUILD_FILES_ACCEPT, extOf, readAsOf } from "./buildFiles";
import { RETRYABLE_UPLOADS } from "./useFileUploads";

function iconForFile(name) {
  const ext = extOf(name);
  if (["csv", "tsv"].includes(ext)) return "solar:document-text-linear";
  if (["json", "jsonl"].includes(ext)) return "solar:code-square-linear";
  if (["md", "txt"].includes(ext)) return "solar:document-linear";
  if (["pdf"].includes(ext)) return "solar:document-medicine-linear";
  return "solar:paperclip-linear";
}

/** Why an upload was refused, in plain words. */
function uploadErrorText({ error, name }) {
  if (error === "too_large") return "Too large. Files can be up to 25 MB.";
  if (error === "unsupported")
    return `Can't read .${extOf(name)} files. Use PDF, Excel, CSV, JSON or text.`;
  if (error === "duplicate_name")
    return "Another file here has this name. Rename one, or remove the other and retry.";
  return "The upload didn't finish. Retry it, or remove it.";
}

function FileRow({ file, accept, onRetry, onRemove, timeout }) {
  const status = file.status || "done";
  const role = readAsOf(file.name, accept);
  const sub =
    status === "uploading"
      ? `Uploading ${file.progress || 0}%`
      : status === "failed"
        ? uploadErrorText(file)
        : [
            formatFileSize(file.size, { wholeKb: true }),
            role ? `Likely read as ${role}` : "Can't be read",
          ].join(" · ");
  return (
    <Box>
      <Stack direction="row" alignItems="center" spacing={1}>
        <Box
          sx={{
            width: 28,
            height: 28,
            borderRadius: "6px",
            bgcolor: "action.hover",
            display: "grid",
            placeItems: "center",
            flexShrink: 0,
          }}
        >
          <Iconify
            icon={
              status === "failed"
                ? "solar:danger-circle-linear"
                : iconForFile(file.name)
            }
            width={16}
            sx={{
              color:
                status === "failed" ? "status.danger.fg" : "text.secondary",
            }}
          />
        </Box>
        <Box flex={1} minWidth={0}>
          <Typography noWrap sx={{ ...T13, fontWeight: 500 }}>
            {file.name}
          </Typography>
          <Typography
            role={status === "failed" ? "alert" : undefined}
            sx={{
              ...T12,
              color: status === "failed" ? "status.danger.fg" : "text.disabled",
              fontVariantNumeric: "tabular-nums",
            }}
          >
            {sub}
          </Typography>
        </Box>
        {status === "failed" &&
          onRetry &&
          RETRYABLE_UPLOADS.includes(file.error) && (
            <Button
              size="small"
              aria-label={`Retry ${file.name}`}
              onClick={() => onRetry(file.id)}
              sx={{ ...BTN_SX, color: "text.secondary", flexShrink: 0 }}
            >
              Retry
            </Button>
          )}
        {onRemove && (
          <IconButton
            size="small"
            aria-label={`Remove ${file.name}`}
            onClick={() => onRemove(file.id)}
            sx={{ p: 0.5, color: "text.secondary", flexShrink: 0 }}
          >
            <Iconify icon="solar:close-linear" width={14} />
          </IconButton>
        )}
      </Stack>
      <Collapse in={status === "uploading"} timeout={timeout} unmountOnExit>
        <LinearProgress
          variant="determinate"
          value={file.progress || 0}
          aria-label={`Uploading ${file.name}`}
          sx={{
            mt: 0.75,
            height: 2,
            "@media (prefers-reduced-motion: reduce)": {
              "& .MuiLinearProgress-bar": { transition: "none" },
            },
          }}
        />
      </Collapse>
    </Box>
  );
}
FileRow.propTypes = {
  file: PropTypes.object.isRequired,
  accept: PropTypes.string,
  onRetry: PropTypes.func,
  onRemove: PropTypes.func,
  timeout: PropTypes.number,
};

/**
 * Picked files, one row each: uploading with its progress, done with its
 * size and how it will likely be read, or failed with why, plus Retry when
 * that can help and Remove. Rows slide in and out. `chips` lays them out as
 * wrapping cards, for a composer.
 */
export default function UploadingFiles({
  files,
  onRetry,
  onRemove,
  label,
  accept = BUILD_FILES_ACCEPT,
  chips = false,
  sx,
}) {
  const reduce = useMediaQuery("(prefers-reduced-motion: reduce)");
  const timeout = reduce ? 0 : undefined;
  return (
    <Collapse in={files.length > 0} timeout={timeout} unmountOnExit>
      <Box
        component="ul"
        aria-label={label}
        sx={{
          listStyle: "none",
          m: 0,
          p: 0,
          ...(chips
            ? {
                display: "flex",
                flexWrap: "wrap",
                gap: 0.75,
                maxHeight: 180,
                overflowY: "auto",
              }
            : {
                ...ROW_BOX_SX,
                "& > * + *": { borderTop: "1px solid", borderColor: "divider" },
              }),
          ...sx,
        }}
      >
        <TransitionGroup component={null}>
          {files.map((f) => (
            <Collapse
              key={f.id}
              component="li"
              timeout={timeout}
              sx={
                chips
                  ? {
                      minWidth: 220,
                      maxWidth: "100%",
                      borderRadius: "6px",
                      border: "1px solid",
                      borderColor:
                        f.status === "failed" ? "status.danger.fg" : "divider",
                      bgcolor: "background.paper",
                      "& .MuiCollapse-wrapperInner": {
                        pl: 1,
                        pr: 0.5,
                        py: 0.75,
                      },
                    }
                  : { "& .MuiCollapse-wrapperInner": { px: 1.5, py: 0.75 } }
              }
            >
              <FileRow
                file={f}
                accept={accept}
                onRetry={onRetry}
                onRemove={onRemove}
                timeout={timeout}
              />
            </Collapse>
          ))}
        </TransitionGroup>
      </Box>
    </Collapse>
  );
}
UploadingFiles.propTypes = {
  // Rows from useFileUploads; a row without a status reads as done.
  files: PropTypes.arrayOf(PropTypes.object).isRequired,
  onRetry: PropTypes.func,
  onRemove: PropTypes.func,
  label: PropTypes.string.isRequired,
  accept: PropTypes.string,
  chips: PropTypes.bool,
  sx: PropTypes.object,
};
