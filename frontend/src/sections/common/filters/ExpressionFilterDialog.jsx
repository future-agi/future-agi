import React, { useEffect, useMemo, useRef, useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  Chip,
  Collapse,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  IconButton,
  Link,
  Stack,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import ExpressionInput from "./ExpressionInput";
import ExpressionHelp from "./ExpressionHelp";
import { parseExpression, validateExpression } from "./expression";
import {
  COUNT_DEBOUNCE_MS,
  PLACEHOLDER,
  countLine,
  withQuickPick,
} from "./expressionDialogModel";

/**
 * the interface contract · `Filter by expression` (640 px). Generic over the scope: the
 * door passes the catalogue, examples, quick picks and the live count
 * (L7c g1, the capability contract a: no Copy as API call; neither door's printed call was
 * served by the real API as written). The count (debounced 300 ms) comes from the server, which also
 * validates; Apply is disabled while there is an error.
 *
 * Props: open, onClose, onApply(expr), initialExpr, catalog, hints,
 *   examples [{label, expr}], quickPicks [{label, expr}],
 *   count(expr) → Promise<{matched, total} | {error: string}>,
 *   onSaveAsColumn(expr) (optional door to custom columns), title,
 *   placeholder, examplesTitle.
 */
const ExpressionFilterDialog = ({
  open,
  onClose,
  onApply,
  initialExpr = "",
  catalog,
  hints,
  examples = [],
  quickPicks = [],
  count,
  onSaveAsColumn,
  title = "Filter by expression",
  applyLabel = "Apply filter",
  placeholder = PLACEHOLDER,
  examplesTitle = "Examples from the runs in view",
}) => {
  const [text, setText] = useState(initialExpr);
  const [live, setLive] = useState({ state: "idle" });
  const [help, setHelp] = useState(false);
  const seq = useRef(0);

  useEffect(() => {
    if (open) {
      setText(initialExpr ?? "");
      setLive({ state: "idle" });
    }
  }, [open, initialExpr]);

  const trimmed = text.trim();
  // Syntax errors show at once; names are checked locally for the underline,
  // and by the server (the one evaluator) for the verdict.
  const parsed = useMemo(
    () => (trimmed ? parseExpression(trimmed) : null),
    [trimmed],
  );
  const local = useMemo(() => {
    if (!parsed || parsed.error || !catalog) return null;
    const v = validateExpression(parsed.ast, catalog);
    return v.error ? v : null;
  }, [parsed, catalog]);

  // Live count, debounced; the server's answer is the truth (it validates too).
  useEffect(() => {
    if (!open || !trimmed || !count) {
      setLive({ state: "idle" });
      return undefined;
    }
    if (parsed?.error) {
      setLive({ state: "idle" });
      return undefined;
    }
    const id = ++seq.current;
    setLive((l) => ({ ...l, state: "counting" }));
    const timer = setTimeout(() => {
      count(trimmed)
        .then((res) => {
          if (id !== seq.current) return;
          setLive(
            res?.error
              ? { state: "error", message: res.error }
              : { state: "done", ...res },
          );
        })
        .catch((e) => {
          if (id !== seq.current) return;
          setLive({
            state: "error",
            message: e?.result ?? e?.message ?? "Could not count the rows.",
          });
        });
    }, COUNT_DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [open, trimmed, count, parsed]);

  // The error shown: a parse error at once; else the server's message (with
  // the local span when both agree); else nothing.
  let error = null;
  if (parsed?.error) error = parsed.error;
  else if (live.state === "error")
    error = {
      message: live.message,
      ...(local?.error?.message === live.message
        ? { at: local.error.at, length: local.error.length }
        : {}),
    };
  else if (live.state !== "done" && local?.error) error = local.error;
  const canApply = !!trimmed && !error && live.state === "done";

  const apply = () => {
    if (!canApply) return;
    onApply(trimmed);
  };

  return (
    <Dialog
      open={open}
      onClose={onClose}
      PaperProps={{ sx: { width: 640, maxWidth: "calc(100vw - 32px)" } }}
      data-testid="expression-dialog"
    >
      <DialogTitle sx={{ pb: 1 }}>
        <Stack direction="row" alignItems="center" gap={1}>
          <Iconify icon="mdi:code-parentheses" width={20} />
          <Typography
            typography="m3"
            fontWeight="fontWeightSemiBold"
            sx={{ flex: 1 }}
          >
            {title}
          </Typography>
          <IconButton aria-label="Close" size="small" onClick={onClose}>
            <Iconify icon="mdi:close" width={18} />
          </IconButton>
        </Stack>
      </DialogTitle>
      <DialogContent sx={{ pt: "4px !important" }}>
        <Stack spacing={1.5}>
          <ExpressionInput
            value={text}
            onChange={setText}
            catalog={catalog}
            hints={hints}
            error={error}
            placeholder={placeholder}
            onSubmit={apply}
            autoFocus
          />
          <Stack
            direction="row"
            alignItems="center"
            gap={1}
            sx={{ minHeight: 22 }}
          >
            <Typography
              typography="s2"
              color={live.state === "done" ? "text.primary" : "text.secondary"}
              data-testid="expression-count"
              sx={{ fontVariantNumeric: "tabular-nums" }}
            >
              {live.state === "done"
                ? countLine(live)
                : live.state === "counting"
                  ? "Counting…"
                  : ""}
            </Typography>
            <Box sx={{ flex: 1 }} />
            {onSaveAsColumn && (
              <Link
                component="button"
                type="button"
                typography="s2"
                underline="hover"
                disabled={!canApply}
                onClick={() => onSaveAsColumn(trimmed)}
                data-testid="expression-save-column"
                sx={{ opacity: canApply ? 1 : 0.5 }}
              >
                Save as a column…
              </Link>
            )}
          </Stack>
          {quickPicks.length > 0 && (
            <Stack
              direction="row"
              gap={0.75}
              flexWrap="wrap"
              alignItems="center"
            >
              <Typography typography="s2" color="text.secondary">
                Quick picks
              </Typography>
              {quickPicks.map((q) => (
                <Chip
                  key={q.label}
                  size="small"
                  variant="outlined"
                  label={q.label}
                  onClick={() => setText(withQuickPick(trimmed, q.expr))}
                  data-testid="expression-quick-pick"
                />
              ))}
            </Stack>
          )}
          {examples.length > 0 && (
            <Stack spacing={0.5}>
              <Typography typography="s2" color="text.secondary">
                {examplesTitle}
              </Typography>
              {examples.map((ex) => (
                <Box
                  key={ex.expr}
                  component="button"
                  type="button"
                  onClick={() => setText(ex.expr)}
                  data-testid="expression-example"
                  sx={{
                    textAlign: "left",
                    border: "1px solid",
                    borderColor: "divider",
                    borderRadius: 0.5,
                    bgcolor: "background.neutral",
                    px: 1,
                    py: 0.5,
                    cursor: "pointer",
                    "&:hover": { borderColor: "primary.main" },
                  }}
                >
                  <Typography
                    component="span"
                    sx={{
                      fontFamily:
                        "ui-monospace, SFMono-Regular, Menlo, monospace",
                      fontSize: 12.5,
                    }}
                  >
                    {ex.expr}
                  </Typography>
                  {ex.label && (
                    <Typography
                      component="span"
                      typography="s3"
                      color="text.secondary"
                      sx={{ ml: 1 }}
                    >
                      {ex.label}
                    </Typography>
                  )}
                </Box>
              ))}
            </Stack>
          )}
          <Box>
            <Link
              component="button"
              type="button"
              typography="s2"
              underline="hover"
              onClick={() => setHelp((h) => !h)}
            >
              {help ? "Hide how expressions work" : "How expressions work"}
            </Link>
            <Collapse in={help}>
              <Box sx={{ mt: 1 }}>
                <ExpressionHelp />
              </Box>
            </Collapse>
          </Box>
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button size="small" onClick={onClose}>
          Cancel
        </Button>
        <Button
          size="small"
          variant="contained"
          color="primary"
          disabled={!canApply}
          onClick={apply}
          data-testid="expression-apply"
        >
          {applyLabel}
        </Button>
      </DialogActions>
    </Dialog>
  );
};

ExpressionFilterDialog.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func.isRequired,
  onApply: PropTypes.func.isRequired,
  initialExpr: PropTypes.string,
  catalog: PropTypes.object,
  hints: PropTypes.object,
  examples: PropTypes.arrayOf(
    PropTypes.shape({ label: PropTypes.string, expr: PropTypes.string }),
  ),
  quickPicks: PropTypes.arrayOf(
    PropTypes.shape({ label: PropTypes.string, expr: PropTypes.string }),
  ),
  count: PropTypes.func,
  onSaveAsColumn: PropTypes.func,
  title: PropTypes.string,
  applyLabel: PropTypes.string,
  placeholder: PropTypes.string,
  examplesTitle: PropTypes.string,
};

export default ExpressionFilterDialog;
