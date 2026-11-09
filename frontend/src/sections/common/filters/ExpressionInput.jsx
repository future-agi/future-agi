import React, { useEffect, useMemo, useRef, useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  ListItemButton,
  Paper,
  Popper,
  Stack,
  Typography,
} from "@mui/material";
import { applySuggestion, suggest } from "./expressionSuggest";

const MONO = "ui-monospace, SFMono-Regular, Menlo, monospace";

/**
 * W5-1 A2 · one monospace line with autocomplete (fields, run refs,
 * operators, values with counts) and the error under it, its span
 * underlined. Tab / Enter accept a suggestion; Esc closes the list;
 * Ctrl+Space opens it (typing opens it too; an empty line keeps the
 * examples and quick picks in view). Text set from outside (an example, a quick pick, an
 * edited pill) puts the cursor at the end and keeps the list closed.
 * Props: value, onChange(text), catalog, hints {values{field: [{value,count}]}},
 * error {message, at, length} | null, placeholder, onSubmit(), autoFocus.
 */
const ExpressionInput = ({
  value,
  onChange,
  catalog,
  hints,
  error,
  placeholder,
  onSubmit,
  autoFocus,
  inputProps,
}) => {
  const ref = useRef(null);
  const [cursor, setCursor] = useState(value?.length ?? 0);
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  // The last text this input produced itself (typing, accepting): any other
  // value came from outside, so the cursor moves to its end.
  const own = useRef(value ?? "");
  useEffect(() => {
    if ((value ?? "") === own.current) return;
    own.current = value ?? "";
    setCursor((value ?? "").length);
    setOpen(false);
  }, [value]);

  const list = useMemo(
    () =>
      open && catalog
        ? suggest(value ?? "", cursor, catalog, hints)
        : { items: [], from: 0, to: 0 },
    [open, value, cursor, catalog, hints],
  );
  const items = list.items;

  const accept = (item) => {
    const next = applySuggestion(value ?? "", list, item);
    own.current = next.text;
    onChange(next.text);
    setCursor(next.cursor);
    setActive(0);
    requestAnimationFrame(() => {
      const el = ref.current;
      if (el) {
        el.focus();
        el.setSelectionRange(next.cursor, next.cursor);
      }
    });
  };

  const onKeyDown = (e) => {
    if (e.key === " " && e.ctrlKey) {
      e.preventDefault();
      setCursor(e.target.selectionStart ?? (value ?? "").length);
      setOpen(true);
      return;
    }
    if (open && items.length) {
      if (e.key === "ArrowDown") {
        e.preventDefault();
        setActive((a) => (a + 1) % items.length);
        return;
      }
      if (e.key === "ArrowUp") {
        e.preventDefault();
        setActive((a) => (a - 1 + items.length) % items.length);
        return;
      }
      if (e.key === "Tab" || (e.key === "Enter" && !e.metaKey && !e.ctrlKey)) {
        e.preventDefault();
        accept(items[Math.min(active, items.length - 1)]);
        return;
      }
    }
    if (e.key === "Escape" && open) {
      e.preventDefault();
      e.stopPropagation();
      setOpen(false);
      return;
    }
    if (e.key === "Enter") {
      e.preventDefault();
      onSubmit?.();
    }
  };

  const text = value ?? "";
  const hasSpan = error && Number.isFinite(error.at) && text.length > 0;
  const at = hasSpan ? Math.min(error.at, text.length) : 0;
  const len = hasSpan
    ? Math.max(1, Math.min(error.length ?? 1, text.length - at))
    : 0;

  return (
    <Stack spacing={0.5}>
      <Box
        sx={{
          position: "relative",
          border: "1px solid",
          borderColor: error ? "error.main" : "divider",
          borderRadius: 0.75,
          bgcolor: "background.paper",
          "&:focus-within": {
            borderColor: error ? "error.main" : "primary.main",
          },
        }}
      >
        {/* The underline layer: same text, transparent, the error span underlined. */}
        {hasSpan && (
          <Box
            aria-hidden
            sx={{
              position: "absolute",
              inset: 0,
              px: 1.25,
              py: 1,
              fontFamily: MONO,
              fontSize: 13,
              lineHeight: "20px",
              whiteSpace: "pre",
              overflow: "hidden",
              color: "transparent",
              pointerEvents: "none",
            }}
          >
            {text.slice(0, at)}
            <Box
              component="span"
              data-testid="expression-error-span"
              sx={{
                textDecoration: "underline wavy",
                textDecorationColor: (t) => t.palette.error.main,
                textUnderlineOffset: 3,
              }}
            >
              {text.slice(at, at + len) || " "}
            </Box>
          </Box>
        )}
        <Box
          component="input"
          ref={ref}
          value={text}
          placeholder={placeholder}
          autoFocus={autoFocus}
          spellCheck={false}
          autoComplete="off"
          aria-label="Expression"
          aria-invalid={!!error}
          data-testid="expression-input"
          onChange={(e) => {
            own.current = e.target.value;
            onChange(e.target.value);
            setCursor(e.target.selectionStart ?? e.target.value.length);
            // An emptied line shows the examples and quick picks, not the list.
            setOpen(e.target.value.trim().length > 0);
            setActive(0);
          }}
          onKeyDown={onKeyDown}
          onClick={(e) => setCursor(e.target.selectionStart ?? 0)}
          onKeyUp={(e) => setCursor(e.target.selectionStart ?? 0)}
          onBlur={() => setTimeout(() => setOpen(false), 150)}
          sx={{
            position: "relative",
            width: "100%",
            border: 0,
            outline: 0,
            bgcolor: "transparent",
            px: 1.25,
            py: 1,
            fontFamily: MONO,
            fontSize: 13,
            lineHeight: "20px",
            color: "text.primary",
          }}
          {...inputProps}
        />
      </Box>
      <Popper
        open={open && items.length > 0}
        anchorEl={ref.current}
        placement="bottom-start"
        style={{ zIndex: 1500 }}
      >
        <Paper
          elevation={6}
          sx={{
            mt: 0.5,
            width: 560,
            maxHeight: 280,
            overflowY: "auto",
            py: 0.5,
          }}
          data-testid="expression-suggestions"
        >
          {items.map((item, i) => (
            <ListItemButton
              key={`${item.kind}-${item.label}`}
              selected={i === active}
              onMouseDown={(e) => {
                e.preventDefault();
                accept(item);
              }}
              sx={{ py: 0.4, px: 1.25, gap: 1 }}
            >
              <Typography
                sx={{ fontFamily: MONO, fontSize: 12.5, flex: 1, minWidth: 0 }}
                noWrap
              >
                {item.label}
              </Typography>
              {item.detail && (
                <Typography
                  typography="s3"
                  color="text.secondary"
                  noWrap
                  sx={{ maxWidth: 260 }}
                >
                  {item.detail}
                </Typography>
              )}
            </ListItemButton>
          ))}
        </Paper>
      </Popper>
      {error && (
        <Typography
          typography="s2"
          color="error.main"
          role="alert"
          data-testid="expression-error"
        >
          {error.message}
        </Typography>
      )}
    </Stack>
  );
};

ExpressionInput.propTypes = {
  value: PropTypes.string,
  onChange: PropTypes.func.isRequired,
  catalog: PropTypes.object,
  hints: PropTypes.object,
  error: PropTypes.shape({
    message: PropTypes.string,
    at: PropTypes.number,
    length: PropTypes.number,
  }),
  placeholder: PropTypes.string,
  onSubmit: PropTypes.func,
  autoFocus: PropTypes.bool,
  inputProps: PropTypes.object,
};

export default ExpressionInput;
