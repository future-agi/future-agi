import PropTypes from "prop-types";
import React, {
  useState,
  useEffect,
  useCallback,
  forwardRef,
  useImperativeHandle,
  useRef,
} from "react";
import {
  Alert,
  Box,
  Button,
  CircularProgress,
  Collapse,
  Divider,
  IconButton,
  MenuItem,
  Select,
  Stack,
  TextField,
  Tooltip,
  Typography,
} from "@mui/material";
import { enqueueSnackbar } from "notistack";
import Iconify from "src/components/iconify";
import CellMarkdown from "src/sections/common/CellMarkdown";
import LabelInput from "./label-input";
import AnnotationHistory from "./annotation-history";

const SHORTCUT_ROWS = [
  { keys: ["Tab"], desc: "Next label" },
  { keys: ["Shift", "Tab"], desc: "Previous label" },
  { keys: ["1-9"], desc: "Select option / set rating" },
  { keys: ["\u2318/Ctrl", "Enter"], desc: "Submit & next" },
  { keys: ["s"], desc: "Skip item" },
  { keys: ["\u2190 / \u2192"], desc: "Previous / next item" },
  { keys: ["?"], desc: "Toggle this help" },
];

const Kbd = ({ children }) => (
  <Box
    component="kbd"
    sx={{
      display: "inline-flex",
      alignItems: "center",
      justifyContent: "center",
      minWidth: 20,
      height: 20,
      px: 0.5,
      borderRadius: 0.5,
      bgcolor: "action.hover",
      border: "1px solid",
      borderColor: "divider",
      fontSize: 11,
      fontWeight: 600,
      fontFamily: "inherit",
      color: "text.secondary",
      lineHeight: 1,
    }}
  >
    {children}
  </Box>
);

Kbd.propTypes = {
  children: PropTypes.node,
};

function ShortcutsOverlay({ onClose }) {
  return (
    <Box
      onClick={onClose}
      sx={{
        position: "absolute",
        inset: 0,
        zIndex: 10,
        bgcolor: "rgba(0,0,0,0.3)",
        display: "flex",
        alignItems: "flex-start",
        justifyContent: "center",
        pt: 8,
      }}
    >
      <Box
        onClick={(e) => e.stopPropagation()}
        sx={{
          bgcolor: "background.paper",
          border: "1px solid",
          borderColor: "divider",
          borderRadius: 0.5,
          p: 2.5,
          width: 300,
          boxShadow: 8,
        }}
      >
        <Box
          sx={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            mb: 2,
          }}
        >
          <Stack direction="row" spacing={0.75} alignItems="center">
            <Iconify
              icon="solar:keyboard-bold-duotone"
              width={18}
              color="primary.main"
            />
            <Typography variant="subtitle2" fontWeight={700}>
              Keyboard Shortcuts
            </Typography>
          </Stack>
          <IconButton size="small" onClick={onClose} sx={{ p: 0.25 }}>
            <Iconify icon="mingcute:close-line" width={16} />
          </IconButton>
        </Box>
        <Stack spacing={1}>
          {SHORTCUT_ROWS.map(({ keys, desc }) => (
            <Box
              key={desc}
              sx={{
                display: "flex",
                alignItems: "center",
                justifyContent: "space-between",
              }}
            >
              <Typography variant="caption" color="text.secondary">
                {desc}
              </Typography>
              <Stack direction="row" spacing={0.5}>
                {keys.map((k) => (
                  <Kbd key={k}>{k}</Kbd>
                ))}
              </Stack>
            </Box>
          ))}
        </Stack>
      </Box>
    </Box>
  );
}

ShortcutsOverlay.propTypes = {
  onClose: PropTypes.func.isRequired,
};

const LabelPanel = forwardRef(function LabelPanel(
  {
    labels = [],
    annotations = [],
    initialItemNotes = "",
    instructions,
    onSubmit,
    isPending,
    queueId,
    itemId,
    onDirtyChange,
    readOnly = false,
    readOnlyReason = null,
    reviewFeedback = "",
    annotators = null,
    viewingAnnotatorId = null,
    currentUserId = null,
    onViewingAnnotatorChange,
    isAnnotatorSwitchPending = false,
  },
  ref,
) {
  const [values, setValues] = useState({});
  const [labelNotes, setLabelNotes] = useState({});
  const [itemNotes, setItemNotes] = useState(initialItemNotes || "");
  const [showInstructions, setShowInstructions] = useState(!!instructions);
  const [focusedIndex, setFocusedIndex] = useState(0);
  const [showShortcuts, setShowShortcuts] = useState(false);
  const [errorLabels, setErrorLabels] = useState(new Set());

  // Refs for flushing debounced text inputs before submit
  const textFlushRefs = useRef({});
  // Mirror of values state, always up-to-date (including after flush)
  const valuesRef = useRef(values);
  valuesRef.current = values;

  // Reset form state immediately when the loaded item changes — without
  // this, navigating to a new un-annotated item can leave the previous
  // item's values pre-filled while the new item's annotations are still
  // fetching, and the user can accidentally re-submit those values onto
  // the wrong trace or annotator.
  useEffect(() => {
    setValues({});
    setLabelNotes({});
    setItemNotes("");
    setErrorLabels(new Set());
    onDirtyChange?.(false);
  }, [itemId, viewingAnnotatorId, onDirtyChange]);

  // Initialize values from existing annotations once they arrive (or
  // re-arrive after a refetch). Runs after the itemId-change reset above,
  // so values land on the correct item's prior annotations.
  useEffect(() => {
    const initial = {};
    const initialNotes = {};
    for (const ann of annotations) {
      const labelId = ann.label_id;
      if (labelId) {
        initial[labelId] = ann.value;
        if (Object.prototype.hasOwnProperty.call(ann, "notes")) {
          initialNotes[labelId] = ann.notes || "";
        }
      }
    }
    setValues(initial);
    setLabelNotes(initialNotes);
    setErrorLabels(new Set());
    onDirtyChange?.(false);
  }, [annotations, onDirtyChange]);

  useEffect(() => {
    setItemNotes(initialItemNotes || "");
    onDirtyChange?.(false);
  }, [initialItemNotes, onDirtyChange]);

  const handleChange = useCallback(
    (labelId, value) => {
      if (readOnly) return;
      setValues((prev) => {
        const next = { ...prev, [labelId]: value };
        valuesRef.current = next;
        return next;
      });
      // Clear error for this label when user changes it
      setErrorLabels((prev) => {
        const next = new Set(prev);
        next.delete(labelId);
        return next;
      });
      onDirtyChange?.(true);
    },
    [onDirtyChange, readOnly],
  );

  const handleLabelNotesChange = useCallback(
    (labelId, value) => {
      if (readOnly) return;
      setLabelNotes((prev) => ({ ...prev, [labelId]: value }));
      onDirtyChange?.(true);
    },
    [onDirtyChange, readOnly],
  );

  const handleItemNotesChange = useCallback(
    (value) => {
      if (readOnly) return;
      setItemNotes(value);
      onDirtyChange?.(true);
    },
    [onDirtyChange, readOnly],
  );

  const handleSubmit = useCallback(() => {
    if (readOnly) return;
    // Flush any pending debounced text inputs so valuesRef is up-to-date
    Object.values(textFlushRefs.current).forEach((r) => r?.flush?.());

    // Read from ref to capture any values flushed above
    const currentValues = valuesRef.current;

    // Check required labels have values
    const missingRequired = labels.filter((ql) => {
      if (!ql.required) return false;
      const labelId = ql.label_id;
      const v = currentValues[labelId];
      if (v === null || v === undefined) return true;
      if (ql.type === "star" && !v.rating) return true;
      if (ql.type === "categorical" && (!v.selected || v.selected.length === 0))
        return true;
      if (ql.type === "text" && !v.text?.trim()) return true;
      if (ql.type === "thumbs_up_down" && !v.value) return true;
      if (ql.type === "numeric" && (v.value === null || v.value === undefined))
        return true;
      return false;
    });

    if (missingRequired.length > 0) {
      const names = missingRequired.map((l) => l.name).join(", ");
      enqueueSnackbar(`Required labels missing: ${names}`, {
        variant: "warning",
      });
      // Highlight the missing required labels
      const errorSet = new Set(missingRequired.map((l) => l.label_id));
      setErrorLabels(errorSet);
      return;
    }

    const labelsById = new Map(labels.map((label) => [label.label_id, label]));
    const annotationsList = Object.entries(currentValues)
      .filter(([_, v]) => v !== null && v !== undefined)
      .map(([labelId, value]) => {
        const annotation = {
          label_id: labelId,
          value,
        };
        if (labelsById.get(labelId)?.allow_notes) {
          annotation.notes = labelNotes[labelId] ?? "";
        }
        return annotation;
      });
    if (annotationsList.length > 0) {
      onDirtyChange?.(false);
      onSubmit({ annotations: annotationsList, itemNotes });
    }
  }, [itemNotes, labelNotes, onSubmit, labels, onDirtyChange, readOnly]);

  useImperativeHandle(ref, () => ({ submit: handleSubmit }), [handleSubmit]);

  const hasValues = Object.values(values).some(
    (v) => v !== null && v !== undefined && v !== "",
  );

  // Keyboard navigation for labels (Tab/Shift+Tab + number keys + ? for help)
  useEffect(() => {
    const handler = (e) => {
      const tag = e.target.tagName;
      const isInput = tag === "INPUT" || tag === "TEXTAREA";
      const isTextInput = isInput && e.target.type !== "range";

      // Don't intercept keys when in a text field (except Tab)
      if (isTextInput && e.key !== "Tab") return;

      // ? → toggle shortcuts overlay
      if (e.key === "?") {
        e.preventDefault();
        setShowShortcuts((p) => !p);
        return;
      }

      // In read-only mode, only the help overlay shortcut is allowed.
      if (readOnly) return;

      // Tab / Shift+Tab → navigate labels
      if (e.key === "Tab") {
        e.preventDefault();
        e.stopImmediatePropagation();
        setFocusedIndex((prev) => {
          const total = labels.length;
          if (total === 0) return 0;
          if (e.shiftKey) return (prev - 1 + total) % total;
          return (prev + 1) % total;
        });
        return;
      }

      // Number keys → dispatch to focused label
      const num = parseInt(e.key, 10);
      if (num >= 1 && num <= 9 && labels.length > 0) {
        const ql = labels[focusedIndex];
        if (!ql) return;
        const labelId = ql.label_id;
        const currentVal = values[labelId] ?? null;

        e.preventDefault();

        if (ql.type === "star") {
          const max = ql.settings?.no_of_stars || 5;
          if (num <= max) {
            const current = currentVal?.rating || 0;
            handleChange(labelId, { rating: num === current ? 0 : num });
          }
        } else if (ql.type === "thumbs_up_down") {
          if (num === 1) {
            handleChange(labelId, {
              value: currentVal?.value === "up" ? null : "up",
            });
          } else if (num === 2) {
            handleChange(labelId, {
              value: currentVal?.value === "down" ? null : "down",
            });
          }
        } else if (ql.type === "categorical") {
          const rawOptions = ql.settings?.options || [];
          const options = rawOptions
            .map((opt) =>
              typeof opt === "string" ? opt : opt?.label || opt?.value || "",
            )
            .filter(Boolean);
          const optIndex = num - 1;
          if (optIndex < options.length) {
            const opt = options[optIndex];
            const selected = currentVal?.selected || [];
            const isMulti = ql.settings?.multi_choice || false;
            if (isMulti) {
              const next = selected.includes(opt)
                ? selected.filter((v) => v !== opt)
                : [...selected, opt];
              handleChange(labelId, { selected: next });
            } else {
              handleChange(labelId, {
                selected: selected[0] === opt ? [] : [opt],
              });
            }
          }
        }
      }
    };

    document.addEventListener("keydown", handler);
    return () => document.removeEventListener("keydown", handler);
  }, [labels, focusedIndex, values, handleChange, readOnly]);

  return (
    <Box
      sx={{
        p: 3,
        overflow: "auto",
        height: "100%",
        display: "flex",
        flexDirection: "column",
        position: "relative",
      }}
    >
      {showShortcuts && (
        <ShortcutsOverlay onClose={() => setShowShortcuts(false)} />
      )}

      {readOnly && readOnlyReason && (
        <Box
          sx={{
            mb: 2,
            px: 1.5,
            py: 1,
            borderRadius: 0.5,
            bgcolor: "action.hover",
            border: "1px solid",
            borderColor: "divider",
            display: "flex",
            alignItems: "center",
            gap: 1,
          }}
        >
          <Iconify
            icon="mingcute:lock-fill"
            width={16}
            sx={{ color: "text.secondary" }}
          />
          <Typography variant="caption" color="text.secondary">
            {readOnlyReason}
          </Typography>
        </Box>
      )}

      {reviewFeedback && (
        <Alert severity="warning" icon={false} sx={{ mb: 2 }}>
          <Typography variant="caption" fontWeight={700} display="block">
            Reviewer feedback
          </Typography>
          <Typography variant="body2">{reviewFeedback}</Typography>
        </Alert>
      )}

      {/* Shortcuts toggle + Instructions row */}
      <Stack
        direction="row"
        alignItems="center"
        justifyContent="space-between"
        sx={{ mb: 1 }}
      >
        <Typography variant="subtitle2" fontWeight={600}>
          Labels
        </Typography>
        <Tooltip title="Keyboard shortcuts (?)" placement="left">
          <IconButton
            size="small"
            onClick={() => setShowShortcuts((p) => !p)}
            sx={{
              color: showShortcuts ? "primary.main" : "text.secondary",
              bgcolor: showShortcuts ? "primary.lighter" : "transparent",
              "&:hover": {
                bgcolor: showShortcuts ? "primary.lighter" : "action.hover",
              },
            }}
          >
            <Iconify icon="solar:keyboard-bold-duotone" width={20} />
          </IconButton>
        </Tooltip>
      </Stack>

      {/* Instructions */}
      {instructions && (
        <Box sx={{ mb: 2 }}>
          <Stack
            direction="row"
            alignItems="center"
            justifyContent="space-between"
            onClick={() => setShowInstructions(!showInstructions)}
            sx={{
              cursor: "pointer",
              py: 0.5,
              "&:hover": { opacity: 0.8 },
            }}
          >
            <Stack direction="row" alignItems="center" spacing={0.75}>
              <Iconify
                icon="solar:document-text-bold"
                width={16}
                sx={{ color: "text.secondary" }}
              />
              <Typography variant="subtitle2" color="text.secondary">
                Instructions
              </Typography>
            </Stack>
            <Iconify
              icon={
                showInstructions
                  ? "eva:chevron-up-fill"
                  : "eva:chevron-down-fill"
              }
              width={18}
              sx={{ color: "text.disabled" }}
            />
          </Stack>
          <Collapse in={showInstructions}>
            <Box
              sx={{
                bgcolor: "background.neutral",
                borderRadius: 0.5,
                p: 2,
                mt: 1,
                maxHeight: 280,
                overflow: "auto",
                fontSize: 13,
              }}
            >
              <CellMarkdown text={instructions} fontSize={13} spacing={6} />
            </Box>
          </Collapse>
          <Divider sx={{ mt: 2 }} />
        </Box>
      )}

      {Array.isArray(annotators) && annotators.length > 1 && (
        <Box sx={{ mb: 2 }}>
          <Stack direction="row" alignItems="center" spacing={1} sx={{ mb: 1 }}>
            <Iconify
              icon="solar:user-id-bold"
              width={16}
              sx={{ color: "text.secondary" }}
            />
            <Typography variant="subtitle2" color="text.secondary">
              Viewing annotator
            </Typography>
            {isAnnotatorSwitchPending && (
              <CircularProgress size={12} thickness={5} sx={{ ml: 0.5 }} />
            )}
          </Stack>
          <Select
            fullWidth
            size="small"
            value={viewingAnnotatorId || ""}
            onChange={(e) => onViewingAnnotatorChange?.(e.target.value || null)}
            sx={{
              borderRadius: 0.5,
              "& .MuiSelect-select": { py: 1 },
            }}
          >
            {annotators.map((annotator) => {
              const isSelf =
                currentUserId &&
                String(annotator.user_id) === String(currentUserId);
              return (
                <MenuItem
                  key={String(annotator.user_id)}
                  value={String(annotator.user_id)}
                >
                  {annotator.name || annotator.email || "Unknown"}
                  {isSelf ? " (you)" : ""}
                </MenuItem>
              );
            })}
          </Select>
          {viewingAnnotatorId &&
            (() => {
              const selected = annotators.find(
                (annotator) =>
                  String(annotator.user_id) === String(viewingAnnotatorId),
              );
              const isSelf =
                currentUserId &&
                String(viewingAnnotatorId) === String(currentUserId);
              const name =
                selected?.name || selected?.email || "this annotator";
              return (
                <Typography
                  variant="caption"
                  color="text.secondary"
                  sx={{ display: "block", mt: 0.75 }}
                >
                  {isSelf
                    ? "You are viewing your own annotations"
                    : `You are viewing annotations of ${name}`}
                </Typography>
              );
            })()}
          <Divider sx={{ mt: 2 }} />
        </Box>
      )}

      {/* Label inputs */}
      <Stack
        spacing={2}
        sx={{
          flex: 1,
          ...((readOnly || isAnnotatorSwitchPending) && {
            pointerEvents: "none",
            opacity: isAnnotatorSwitchPending ? 0.4 : 0.7,
            transition: "opacity 120ms ease-out",
          }),
        }}
      >
        {labels.map((ql, i) => {
          const labelId = ql.label_id;
          return (
            <Box
              key={ql.id}
              onClick={() => !readOnly && setFocusedIndex(i)}
              sx={{ cursor: "default" }}
            >
              <LabelInput
                label={{
                  name: ql.name,
                  type: ql.type,
                  settings: ql.settings || {},
                  description: ql.description,
                  required: ql.required,
                  allow_notes: ql.allow_notes ?? false,
                }}
                value={values[labelId] ?? null}
                onChange={(val) => handleChange(labelId, val)}
                index={i}
                focused={focusedIndex === i}
                hasError={errorLabels.has(labelId)}
                labelNotes={labelNotes[labelId] ?? ""}
                onLabelNotesChange={(val) =>
                  handleLabelNotesChange(labelId, val)
                }
                textFlushRef={
                  ql.type === "text"
                    ? (el) => {
                        textFlushRefs.current[labelId] = el;
                      }
                    : undefined
                }
              />
            </Box>
          );
        })}

        <Box>
          <Typography
            variant="caption"
            color="text.secondary"
            sx={{ display: "block", mb: 0.75 }}
          >
            Notes (optional)
          </Typography>
          <TextField
            fullWidth
            size="small"
            multiline
            minRows={3}
            maxRows={6}
            placeholder="Add notes for this item..."
            value={itemNotes}
            onChange={(e) => handleItemNotesChange(e.target.value)}
            disabled={readOnly}
          />
        </Box>
      </Stack>

      {/* Annotation History */}
      <AnnotationHistory queueId={queueId} itemId={itemId} />

      {/* Submit (hidden in read-only mode) */}
      {!readOnly && (
        <Tooltip title="Ctrl+Enter" placement="top">
          <span>
            <Button
              variant="contained"
              fullWidth
              color="primary"
              sx={{ mt: 2 }}
              onClick={handleSubmit}
              disabled={isPending || !hasValues}
              startIcon={
                isPending ? (
                  <CircularProgress size={16} color="inherit" />
                ) : null
              }
            >
              Submit & Next
            </Button>
          </span>
        </Tooltip>
      )}
    </Box>
  );
});

LabelPanel.propTypes = {
  labels: PropTypes.array,
  annotations: PropTypes.array,
  initialItemNotes: PropTypes.string,
  instructions: PropTypes.string,
  onSubmit: PropTypes.func.isRequired,
  isPending: PropTypes.bool,
  queueId: PropTypes.string,
  itemId: PropTypes.string,
  onDirtyChange: PropTypes.func,
  readOnly: PropTypes.bool,
  readOnlyReason: PropTypes.string,
  reviewFeedback: PropTypes.string,
  annotators: PropTypes.array,
  viewingAnnotatorId: PropTypes.string,
  currentUserId: PropTypes.string,
  onViewingAnnotatorChange: PropTypes.func,
  isAnnotatorSwitchPending: PropTypes.bool,
};

export default LabelPanel;
