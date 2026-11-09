import React, { useEffect, useMemo, useRef, useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  MenuItem,
  Stack,
  TextField,
  ToggleButton,
  ToggleButtonGroup,
  Typography,
  useTheme,
} from "@mui/material";
import { Editor } from "@monaco-editor/react";
import Iconify from "src/components/iconify";
import { KeyCap } from "src/sections/common/RowPanel/ShortcutSheet";
import { fmtCount } from "src/sections/common/format/fmt";
import { gridWritesClassic } from "src/sections/develop/realBackendCaps";
import {
  editStats,
  toYamlText,
  validateEdit,
  validateYamlEdit,
} from "./editValue";

const JSON_TYPES = new Set(["json", "array"]);

/**
 * Inline editor for one row field (PRODUCT-SPEC §3.4 Editing): multiline
 * text, Monaco for JSON (160–480 px, "Invalid JSON: {msg}" blocks Apply),
 * typed inputs for number, boolean and datetime. Apply (⌘↵) / Cancel (Esc).
 * R2 (P6, L6): JSON fields edit as `JSON | YAML`; YAML parses back to JSON
 * on Apply (`Invalid YAML: … (line 3)` blocks it; comments are dropped with
 * a one-line warning).
 * R3 (RV#13): grows up to 40vh; a live footer `1,204 chars · ~301 tokens`;
 * JSON fields say `Valid JSON` or `Invalid JSON: … · line 2, col 7` as you
 * type; Cancel shows its `Esc` key.
 */
const FieldEditor = ({ dataType, value, onApply, onCancel, applyRef }) => {
  const theme = useTheme();
  const isJson = JSON_TYPES.has(dataType);
  const initial = useMemo(() => {
    if (value == null) return "";
    if (isJson && typeof value === "string") {
      try {
        return JSON.stringify(JSON.parse(value), null, 2);
      } catch {
        return value;
      }
    }
    return typeof value === "string" ? value : JSON.stringify(value, null, 2);
  }, [value, isJson]);
  const [draft, setDraft] = useState(initial);
  const [format, setFormat] = useState("json");
  const check =
    isJson && format === "yaml"
      ? validateYamlEdit(draft)
      : validateEdit(draft, dataType);
  const switchFormat = (next) => {
    if (!next || next === format) return;
    if (next === "yaml") {
      const ok = validateEdit(draft, dataType);
      setDraft(ok.ok ? toYamlText(ok.value || "null") : draft);
    } else {
      const ok = validateYamlEdit(draft);
      if (ok.ok) {
        try {
          setDraft(JSON.stringify(JSON.parse(ok.value || "null"), null, 2));
        } catch {
          // keep the draft
        }
      }
    }
    setFormat(next);
  };
  const inputRef = useRef(null);
  const rootRef = useRef(null);
  // Opening an editor brings its footer (validity, Apply) into view.
  useEffect(() => {
    const t = setTimeout(
      () => rootRef.current?.scrollIntoView?.({ block: "nearest" }),
      160,
    );
    return () => clearTimeout(t);
  }, []);
  const stats = editStats(draft, isJson && format === "json" ? dataType : "");

  const apply = () => {
    if (!check.ok) return;
    onApply(check.value);
  };
  // The Row view's ⌘↵ (focus outside the editor) applies through this ref.
  useEffect(() => {
    if (!applyRef) return undefined;
    applyRef.current = apply;
    return () => {
      if (applyRef.current === apply) applyRef.current = null;
    };
  });

  const onKeyDown = (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
      e.preventDefault();
      e.stopPropagation();
      apply();
    } else if (e.key === "Escape") {
      e.preventDefault();
      e.stopPropagation();
      onCancel();
    }
  };

  let input;
  if (isJson) {
    const lines = Math.max(6, draft.split("\n").length + 1);
    input = (
      <Stack gap={0.75}>
        <ToggleButtonGroup
          size="small"
          exclusive
          value={format}
          onChange={(_, v) => switchFormat(v)}
          aria-label="Edit as"
          sx={{
            alignSelf: "flex-start",
            "& .MuiToggleButton-root": {
              py: 0,
              px: 1,
              typography: "s3",
              height: 24,
            },
          }}
        >
          <ToggleButton value="json">JSON</ToggleButton>
          <ToggleButton value="yaml">YAML</ToggleButton>
        </ToggleButtonGroup>
        <Box
          onKeyDownCapture={onKeyDown}
          sx={{
            height: `max(160px, min(${Math.max(160, lines * 19)}px, 40vh))`,
            border: "1px solid",
            borderColor: check.ok ? "divider" : "error.main",
            borderRadius: 1,
            overflow: "hidden",
            bgcolor: "background.paper",
          }}
        >
          <Editor
            height="100%"
            language={format}
            value={draft}
            theme={theme.palette.mode === "dark" ? "vs-dark" : "vs"}
            onChange={(v) => setDraft(v ?? "")}
            onMount={(editor) => editor.focus()}
            options={{
              minimap: { enabled: false },
              fontSize: 13,
              lineNumbers: "on",
              scrollBeyondLastLine: false,
              wordWrap: "on",
              tabSize: 2,
              automaticLayout: true,
            }}
          />
        </Box>
      </Stack>
    );
  } else if (dataType === "boolean") {
    input = (
      <TextField
        select
        size="small"
        value={String(draft)}
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={onKeyDown}
        sx={{ width: 160 }}
        autoFocus
      >
        <MenuItem value="true">True</MenuItem>
        <MenuItem value="false">False</MenuItem>
      </TextField>
    );
  } else {
    const typed = ["integer", "float", "number"].includes(dataType)
      ? "number"
      : dataType === "datetime"
        ? "datetime-local"
        : "text";
    input = (
      <TextField
        inputRef={inputRef}
        autoFocus
        fullWidth
        multiline={typed === "text"}
        minRows={typed === "text" ? 3 : undefined}
        maxRows={typed === "text" ? 400 : undefined}
        type={typed}
        size="small"
        value={draft}
        placeholder={typed === "text" ? "Type the value" : undefined}
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={onKeyDown}
        onFocus={(e) => {
          const el = e.target;
          if (typed === "text" && el.setSelectionRange) {
            el.setSelectionRange(el.value.length, el.value.length);
          }
        }}
        sx={{
          "& .MuiInputBase-root": {
            typography: "s1",
            bgcolor: "background.paper",
          },
          // RV#13: auto-grow up to 40vh, then scroll inside.
          "& textarea": { maxHeight: "40vh", overflowY: "auto !important" },
        }}
      />
    );
  }

  return (
    <Stack gap={1} ref={rootRef} sx={{ scrollMarginBottom: 12 }}>
      {input}
      <Stack direction="row" alignItems="center" gap={1}>
        <Stack
          direction="row"
          alignItems="center"
          gap={1}
          sx={{ flex: 1, minWidth: 0 }}
        >
          {stats.json && !stats.json.ok ? (
            <Typography
              typography="s2"
              color="error.main"
              data-testid="field-editor-json"
              sx={{ minWidth: 0 }}
            >
              {`Invalid JSON: ${stats.json.message} · line ${stats.json.line}, col ${stats.json.col}`}
            </Typography>
          ) : !check.ok ? (
            <Typography typography="s2" color="error.main" sx={{ minWidth: 0 }}>
              {check.error}
            </Typography>
          ) : stats.json ? (
            <Stack
              direction="row"
              alignItems="center"
              gap={0.5}
              data-testid="field-editor-json"
              sx={{ color: "green.600", flexShrink: 0 }}
            >
              <Iconify icon="mdi:check-circle-outline" width={14} />
              <Typography typography="s2" color="inherit">
                Valid JSON
              </Typography>
            </Stack>
          ) : (
            <Typography
              typography="s2"
              color="text.disabled"
              noWrap
              sx={{ minWidth: 0 }}
            >
              {check.commentsDropped
                ? "YAML comments are not kept."
                : gridWritesClassic()
                  ? "Saved when you apply."
                  : "Saved with the next version."}
            </Typography>
          )}
          <Typography
            typography="s2"
            color="text.disabled"
            noWrap
            data-testid="field-editor-count"
            sx={{
              ml: "auto",
              flexShrink: 0,
              fontVariantNumeric: "tabular-nums",
            }}
          >
            {`${fmtCount(stats.chars, "char")} · ~${fmtCount(stats.tokens, "token")}`}
          </Typography>
        </Stack>
        <Button
          size="small"
          variant="text"
          color="inherit"
          onClick={onCancel}
          endIcon={
            <Box component="span" sx={{ display: "inline-flex", opacity: 0.8 }}>
              <KeyCap>Esc</KeyCap>
            </Box>
          }
          sx={{ "& .MuiButton-endIcon kbd": { height: 18, minWidth: 18 } }}
        >
          Cancel
        </Button>
        <Button
          size="small"
          variant="contained"
          color="primary"
          disabled={!check.ok}
          onClick={apply}
          endIcon={
            <Box component="span" sx={{ display: "inline-flex", opacity: 0.8 }}>
              <KeyCap>⌘↵</KeyCap>
            </Box>
          }
          sx={{ "& .MuiButton-endIcon kbd": { height: 18, minWidth: 18 } }}
        >
          Apply
        </Button>
      </Stack>
    </Stack>
  );
};

FieldEditor.propTypes = {
  dataType: PropTypes.string,
  value: PropTypes.any,
  onApply: PropTypes.func.isRequired,
  onCancel: PropTypes.func.isRequired,
  applyRef: PropTypes.object,
};

export default FieldEditor;
