import PropTypes from "prop-types";
import { useRef, useState } from "react";
import {
  Box,
  Button,
  ButtonBase,
  IconButton,
  InputBase,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import SvgColor from "src/components/svg-color";
import CustomTooltip from "src/components/tooltip";
import { formatFileSize } from "src/utils/utils";
import UiOnlyFlag from "../../components/UiOnlyFlag";
import { BTN_SX, T12, T13, focusRing } from "../drawerStyles";
import { uiOnlyToast } from "./startUtils";

function iconForFile(name) {
  const ext = String(name).split(".").pop()?.toLowerCase();
  if (["csv", "tsv"].includes(ext)) return "solar:document-text-linear";
  if (["json"].includes(ext)) return "solar:code-square-linear";
  if (["md", "txt"].includes(ext)) return "solar:document-linear";
  if (["pdf"].includes(ext)) return "solar:document-medicine-linear";
  if (["docx", "doc"].includes(ext)) return "solar:document-add-linear";
  if (["wav", "mp3", "m4a"].includes(ext)) return "solar:microphone-3-linear";
  return "solar:paperclip-linear";
}

const toolSx = { p: 0.5, color: "text.secondary" };

function FalconBar({ value, onClose }) {
  const [ask, setAsk] = useState("");
  const [asked, setAsked] = useState(false);
  const submit = () => {
    if (ask.trim()) setAsked(true);
  };
  return (
    <Box
      sx={{
        borderBottom: "1px solid",
        borderColor: "divider",
        bgcolor: "background.aiSurface",
      }}
    >
      <Stack direction="row" alignItems="center" sx={{ px: 1.5, py: 0.75 }}>
        <InputBase
          autoFocus
          fullWidth
          placeholder={
            value?.trim()
              ? "Describe changes, e.g. 'make it escalate faster'"
              : "Describe your agent, e.g. 'triages GitHub issues into Linear'"
          }
          value={ask}
          onChange={(e) => {
            setAsk(e.target.value);
            setAsked(false);
          }}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              submit();
            }
            if (e.key === "Escape") onClose();
          }}
          inputProps={{ "aria-label": "Ask Falcon AI" }}
          sx={{ ...T13, flex: 1 }}
        />
        <Stack
          direction="row"
          alignItems="center"
          spacing={0.5}
          sx={{ ml: 1, flexShrink: 0 }}
        >
          <IconButton
            size="small"
            onClick={submit}
            disabled={!ask.trim()}
            aria-label="Draft with Falcon AI"
            sx={{ p: 0.5 }}
          >
            <Iconify
              icon="solar:round-arrow-up-linear"
              width={20}
              sx={{ color: ask.trim() ? "primary.main" : "text.disabled" }}
            />
          </IconButton>
          <IconButton
            size="small"
            onClick={onClose}
            aria-label="Close Falcon AI"
            sx={{ p: 0.25 }}
          >
            <Iconify
              icon="solar:close-linear"
              width={16}
              sx={{ color: "text.disabled" }}
            />
          </IconButton>
        </Stack>
      </Stack>
      {asked && (
        <Stack
          direction="row"
          alignItems="center"
          spacing={1}
          sx={{ px: 1.5, pb: 1 }}
        >
          <UiOnlyFlag />
          <Typography sx={{ ...T12, color: "text.secondary" }}>
            Falcon AI drafting isn&apos;t wired to the API yet. Write the flow
            yourself for now.
          </Typography>
        </Stack>
      )}
    </Box>
  );
}
FalconBar.propTypes = {
  value: PropTypes.string,
  onClose: PropTypes.func.isRequired,
};

/*
  Vel's describe-your-flow card: one composer for prose, attached files and
  a Falcon AI draft. Falcon drafting and voice input aren't wired, so the
  bar takes the ask and says so instead of drafting.
*/
export default function DescribeFlowCard({
  prompt,
  description,
  placeholder,
  chips,
  value,
  onChange,
  files,
  onAdd,
  onRemove,
  onNext,
  canSubmit,
  submitLabel,
  accept,
}) {
  const inputRef = useRef(null);
  const [dragging, setDragging] = useState(false);
  const [aiOpen, setAiOpen] = useState(false);

  const onFiles = (list) => {
    if (!list || !list.length) return;
    onAdd(
      Array.from(list).map((f) => ({
        id: `${f.name}-${f.size}-${f.lastModified}`,
        name: f.name,
        size: f.size,
        type: f.type,
      })),
    );
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
            setDragging(true);
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
          {aiOpen && (
            <FalconBar value={value} onClose={() => setAiOpen(false)} />
          )}
          <Box sx={{ p: 1.5 }}>
            <TextField
              fullWidth
              multiline
              minRows={5}
              maxRows={12}
              variant="standard"
              placeholder={placeholder}
              value={value}
              onChange={(e) => onChange(e.target.value)}
              inputProps={{ "aria-label": prompt }}
              InputProps={{
                disableUnderline: true,
                sx: { ...T13, lineHeight: 1.6, px: 0.5, py: 0.25 },
              }}
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
                  : "Type, drop a file, or use the tools →"}
              </Typography>
              <CustomTooltip
                show
                arrow
                size="small"
                title="Write with Falcon AI"
                placement="top"
              >
                <IconButton
                  size="small"
                  aria-label="Write with Falcon AI"
                  onClick={() => setAiOpen(true)}
                  sx={{
                    ...toolSx,
                    color: aiOpen ? "primary.main" : "text.secondary",
                    "&:hover": {
                      bgcolor: "background.aiHover",
                      color: "primary.main",
                    },
                  }}
                >
                  <SvgColor
                    src="/assets/icons/navbar/ic_falcon_ai.svg"
                    sx={{ width: 18, height: 18 }}
                  />
                </IconButton>
              </CustomTooltip>
              <CustomTooltip
                show
                arrow
                size="small"
                title="Attach reference material"
              >
                <IconButton
                  size="small"
                  aria-label="Attach reference material"
                  onClick={() => inputRef.current?.click()}
                  sx={toolSx}
                >
                  <Iconify icon="solar:paperclip-linear" width={16} />
                </IconButton>
              </CustomTooltip>
              <CustomTooltip show arrow size="small" title="Voice input">
                <IconButton
                  size="small"
                  aria-label="Voice input"
                  onClick={() => uiOnlyToast("voice input")}
                  sx={toolSx}
                >
                  <Iconify icon="solar:microphone-linear" width={16} />
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

        {files.length > 0 && (
          <Stack
            component="ul"
            aria-label="Attached files"
            spacing={0.5}
            sx={{
              listStyle: "none",
              m: 0,
              p: 0,
              mt: 1.25,
              maxHeight: 180,
              overflowY: "auto",
            }}
          >
            {files.map((f) => (
              <Stack
                component="li"
                key={f.id}
                direction="row"
                alignItems="center"
                spacing={1}
                sx={{
                  px: 1.25,
                  py: 0.75,
                  borderRadius: "6px",
                  bgcolor: "action.hover",
                }}
              >
                <Iconify
                  icon={iconForFile(f.name)}
                  width={16}
                  sx={{ color: "text.secondary", flexShrink: 0 }}
                />
                <Typography noWrap sx={{ ...T13, flex: 1, minWidth: 0 }}>
                  {f.name}
                </Typography>
                <Typography
                  sx={{ ...T12, color: "text.disabled", flexShrink: 0 }}
                >
                  {formatFileSize(f.size)}
                </Typography>
                <IconButton
                  size="small"
                  aria-label={`Remove ${f.name}`}
                  onClick={() => onRemove(f.id)}
                  sx={{ p: 0.25, color: "text.secondary" }}
                >
                  <Iconify icon="solar:close-linear" width={12} />
                </IconButton>
              </Stack>
            ))}
          </Stack>
        )}

        <input
          ref={inputRef}
          type="file"
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
          variant="contained"
          color="primary"
          onClick={onNext}
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
  files: PropTypes.array.isRequired,
  onAdd: PropTypes.func.isRequired,
  onRemove: PropTypes.func.isRequired,
  onNext: PropTypes.func.isRequired,
  canSubmit: PropTypes.bool,
  submitLabel: PropTypes.string.isRequired,
  accept: PropTypes.string,
};
