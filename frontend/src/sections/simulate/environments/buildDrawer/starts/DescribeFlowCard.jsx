import PropTypes from "prop-types";
import { useRef, useState } from "react";
import {
  Box,
  Button,
  ButtonBase,
  CircularProgress,
  IconButton,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import { BTN_SX, T12, T13, focusRing } from "../drawerStyles";
import UploadingFiles from "../forms/shared/UploadingFiles";

const toolSx = { p: 0.5, color: "text.secondary" };

export default function DescribeFlowCard({
  prompt,
  description,
  placeholder,
  chips,
  value,
  onChange,
  files,
  onAdd,
  onRetry,
  onRemove,
  onNext,
  canSubmit,
  disabled = false,
  submitLabel,
  accept,
  filesFooter = null,
  preparing = false,
}) {
  const inputRef = useRef(null);
  const [dragging, setDragging] = useState(false);

  const onFiles = (list) => {
    // Copied now: the input is cleared right after, which empties a live FileList.
    if (!disabled && list?.length) onAdd(Array.from(list));
  };
  const words = value.trim() ? value.trim().split(/\s+/).length : 0;

  return (
    <Box
      sx={{
        borderRadius: "8px",
        border: "1px solid",
        borderColor: "divider",
        bgcolor: "background.paper",
        overflow: "hidden",
      }}
    >
      <Stack
        direction="row"
        alignItems="center"
        spacing={1}
        sx={{ px: 1.75, py: 1.25 }}
      >
        <Box
          sx={{
            px: 0.75,
            borderRadius: 999,
            flexShrink: 0,
            bgcolor: "action.selected",
            color: "text.secondary",
            ...T12,
            fontWeight: 600,
            fontVariantNumeric: "tabular-nums",
          }}
        >
          1/1
        </Box>
        <Typography sx={{ ...T13, fontWeight: 600, flex: 1, minWidth: 0 }}>
          {prompt}
        </Typography>
      </Stack>

      <Box sx={{ px: 1.75, pb: 1.25 }}>
        {description && (
          <Typography sx={{ ...T12, color: "text.secondary", mb: 1.25 }}>
            {description}
          </Typography>
        )}

        <Box
          onDragOver={(e) => {
            e.preventDefault();
            if (!disabled) setDragging(true);
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={(e) => {
            e.preventDefault();
            setDragging(false);
            onFiles(e.dataTransfer.files);
          }}
          sx={{
            borderRadius: "8px",
            border: "1.5px solid",
            borderColor: dragging ? "primary.main" : "divider",
            bgcolor: "action.hover",
            transition: "border-color .15s ease, background-color .15s ease",
            "&:focus-within": { borderColor: "text.disabled" },
            overflow: "hidden",
          }}
        >
          <Box sx={{ p: 1.5 }}>
            <TextField
              fullWidth
              multiline
              minRows={5}
              maxRows={12}
              variant="standard"
              placeholder={placeholder}
              disabled={disabled}
              value={value}
              onChange={(e) => onChange(e.target.value)}
              inputProps={{ "aria-label": prompt }}
              InputProps={{
                disableUnderline: true,
                sx: { ...T13, lineHeight: 1.6, px: 0.5, py: 0.25 },
              }}
            />
            <UploadingFiles
              chips
              label="Attached files"
              files={files}
              accept={accept}
              onRetry={disabled ? undefined : onRetry}
              onRemove={disabled ? undefined : onRemove}
              preparing={preparing}
              sx={{ mt: 1 }}
            />
            <Stack
              direction="row"
              alignItems="center"
              spacing={0.5}
              sx={{ mt: 0.5 }}
            >
              <Typography sx={{ ...T12, color: "text.disabled", flex: 1 }}>
                {words
                  ? `${words} word${words === 1 ? "" : "s"}`
                  : "A description is required"}
              </Typography>
              <CustomTooltip
                show
                arrow
                size="small"
                title="Attach reference material"
              >
                <IconButton
                  size="small"
                  disabled={disabled}
                  aria-label="Attach reference material"
                  onClick={() => inputRef.current?.click()}
                  sx={toolSx}
                >
                  <Iconify icon="solar:paperclip-linear" width={16} />
                </IconButton>
              </CustomTooltip>
            </Stack>
          </Box>
        </Box>

        {chips?.length > 0 && (
          <Stack
            direction="row"
            spacing={0.75}
            flexWrap="wrap"
            rowGap={0.75}
            useFlexGap
            sx={{ mt: 1.25 }}
          >
            {chips.map((c) => (
              <ButtonBase
                key={c}
                disabled={disabled}
                onClick={() => onChange(value ? `${value.trim()} ${c}` : c)}
                sx={{
                  px: 1,
                  py: 0.375,
                  borderRadius: "6px",
                  border: "1px solid",
                  borderColor: "divider",
                  bgcolor: "action.hover",
                  ...T12,
                  color: "text.secondary",
                  "&:hover": {
                    borderColor: "text.disabled",
                    color: "text.primary",
                  },
                  "&.Mui-focusVisible": focusRing,
                }}
              >
                {c}
              </ButtonBase>
            ))}
          </Stack>
        )}

        {filesFooter && <Box sx={{ mt: 1.5 }}>{filesFooter}</Box>}

        <input
          ref={inputRef}
          type="file"
          disabled={disabled}
          multiple
          hidden
          accept={accept}
          data-testid="describe-files"
          onChange={(e) => {
            onFiles(e.target.files);
            e.target.value = "";
          }}
        />
      </Box>

      <Stack
        direction="row"
        alignItems="center"
        justifyContent="flex-end"
        sx={{ px: 1.5, py: 1.25 }}
      >
        <Button
          size="small"
          variant="contained"
          color="primary"
          onClick={onNext}
          startIcon={
            preparing ? (
              <CircularProgress size={14} color="inherit" />
            ) : undefined
          }
          disabled={!canSubmit}
          sx={BTN_SX}
        >
          {submitLabel}
        </Button>
      </Stack>
    </Box>
  );
}
DescribeFlowCard.propTypes = {
  prompt: PropTypes.string.isRequired,
  description: PropTypes.string,
  placeholder: PropTypes.string,
  chips: PropTypes.arrayOf(PropTypes.string),
  value: PropTypes.string.isRequired,
  onChange: PropTypes.func.isRequired,
  // Rows from useFileUploads.
  files: PropTypes.array.isRequired,
  // Called with the picked or dropped files, as an array.
  onAdd: PropTypes.func.isRequired,
  onRetry: PropTypes.func,
  onRemove: PropTypes.func.isRequired,
  onNext: PropTypes.func.isRequired,
  canSubmit: PropTypes.bool,
  disabled: PropTypes.bool,
  submitLabel: PropTypes.string.isRequired,
  accept: PropTypes.string,
  // What the attached files need before building, right under the box.
  filesFooter: PropTypes.node,
  preparing: PropTypes.bool,
};
