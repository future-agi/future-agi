import { useState, useCallback, useMemo, useEffect, useRef } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  Chip,
  CircularProgress,
  Divider,
  IconButton,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import { alpha } from "@mui/material/styles";
import Iconify from "src/components/iconify";
import { useQueueItemsForSource } from "src/api/annotation-queues/annotation-queues";
import { useBulkCreateScores } from "src/api/scores/scores";
import LabelInput from "src/sections/annotations/queues/annotate/label-input";

const SOURCE_TYPE_LABELS = {
  trace: "Trace",
  observation_span: "Span",
  dataset_row: "Dataset Row",
  call_execution: "Execution",
  prototype_run: "Run",
  trace_session: "Session",
};

const SHORTCUT_ROWS = [
  { keys: ["Tab"], desc: "Next label" },
  { keys: ["Shift", "Tab"], desc: "Previous label" },
  { keys: ["1-9"], desc: "Select option / set rating" },
  { keys: ["\u2318", "Enter"], desc: "Save annotation" },
  { keys: ["N"], desc: "Toggle notes" },
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
      bgcolor: (theme) =>
        theme.palette.mode === "dark" ? "action.hover" : "action.disabled",
      border: "1px solid",
      borderColor: "divider",
      fontSize: 11,
      fontWeight: 600,
      fontFamily: "inherit",
      color: (theme) =>
        theme.palette.mode === "dark" ? "primary.contrastText" : "text.primary",
      lineHeight: 1,
    }}
  >
    {children}
  </Box>
);

Kbd.propTypes = {
  children: PropTypes.node.isRequired,
};

function ShortcutsHelp({ onClose }) {
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
          borderRadius: 2,
          p: 2.5,
          width: 280,
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

ShortcutsHelp.propTypes = {
  onClose: PropTypes.func.isRequired,
};

/**
 * @param {Object} props
 * @param {Array<{sourceType: string, sourceId: string, spanNotesSourceId?: string}>} props.sources
 * @param {Function} props.onClose
 * @param {Function} props.onScoresChanged
 */
export default function AnnotationSidebarContent({
  sources = [],
  onClose,
  onScoresChanged,
  onAddLabel,
  showHeader = true,
  hideEmpty = false,
}) {
  const validSources = sources.filter((s) => s.sourceId);
  const {
    data: queueItems,
    isLoading,
    isFetching,
    refetch,
  } = useQueueItemsForSource(validSources);
  const [showShortcuts, setShowShortcuts] = useState(false);

  if (validSources.length === 0) {
    if (hideEmpty) return null;
    return (
      <Box sx={{ p: 3 }}>
        <Typography color="text.secondary">No item selected.</Typography>
      </Box>
    );
  }

  if (isLoading) {
    if (hideEmpty) return null;
    return (
      <Box sx={{ display: "flex", justifyContent: "center", py: 6 }}>
        <CircularProgress size={24} />
      </Box>
    );
  }

  const hasQueues = queueItems && queueItems.length > 0;

  if (hideEmpty && !hasQueues) {
    return null;
  }

  // Build a lookup from source_type → sourceId for saving scores
  const sourceMap = {};
  const spanNotesSourceMap = {};
  for (const s of validSources) {
    sourceMap[s.sourceType] = s.sourceId;
    if (s.spanNotesSourceId) {
      spanNotesSourceMap[s.sourceType] = s.spanNotesSourceId;
    }
  }

  return (
    <Box
      sx={{
        display: "flex",
        flexDirection: "column",
        height: "100%",
        position: "relative",
      }}
    >
      {showShortcuts && (
        <ShortcutsHelp onClose={() => setShowShortcuts(false)} />
      )}

      {showHeader && (
        <>
          {/* Header */}
          <Box
            sx={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
              p: 2.5,
              pb: 1.5,
            }}
          >
            <Typography variant="subtitle1" fontWeight={600}>
              Annotate
            </Typography>
            <Stack direction="row" spacing={0.5} alignItems="center">
              <Button
                size="small"
                variant="outlined"
                onClick={() => refetch()}
                disabled={isFetching}
                startIcon={
                  <Iconify
                    icon="mingcute:refresh-2-line"
                    width={16}
                    sx={{
                      animation: isFetching
                        ? "spin 1s linear infinite"
                        : "none",
                      "@keyframes spin": {
                        from: { transform: "rotate(0deg)" },
                        to: { transform: "rotate(360deg)" },
                      },
                    }}
                  />
                }
                sx={{ fontSize: 12, px: 1, py: 0.5, minWidth: 0 }}
              >
                Refresh
              </Button>
              <IconButton
                size="small"
                onClick={() => setShowShortcuts((p) => !p)}
                sx={{
                  color: showShortcuts ? "primary.main" : "text.secondary",
                  bgcolor: (theme) =>
                    showShortcuts
                      ? alpha(theme.palette.primary.main, 0.12)
                      : "transparent",
                  "&:hover": {
                    bgcolor: (theme) =>
                      showShortcuts
                        ? alpha(theme.palette.primary.main, 0.18)
                        : theme.palette.action.hover,
                  },
                }}
              >
                <Iconify icon="solar:keyboard-bold-duotone" width={20} />
              </IconButton>
              <IconButton size="small" onClick={onClose}>
                <Iconify icon="mingcute:close-line" />
              </IconButton>
            </Stack>
          </Box>

          <Divider />
        </>
      )}

      {/* Content */}
      <Box sx={{ flex: 1, overflowY: "auto", p: showHeader ? 2.5 : 0 }}>
        {!hasQueues ? (
          <Box
            sx={{
              display: "flex",
              flexDirection: "column",
              alignItems: "center",
              justifyContent: "center",
              py: 8,
              gap: 1,
            }}
          >
            <Iconify
              icon="solar:document-text-linear"
              width={40}
              color="text.disabled"
            />
            <Typography
              variant="body2"
              color="text.secondary"
              textAlign="center"
            >
              No labels assigned to you for this item.
            </Typography>
            <Typography
              variant="caption"
              color="text.disabled"
              textAlign="center"
            >
              This item is not part of any annotation queue assigned to you.
            </Typography>
            {onAddLabel && (
              <Button
                size="small"
                startIcon={<Iconify icon="mingcute:add-line" width={16} />}
                onClick={onAddLabel}
                sx={{ mt: 1 }}
              >
                Add Label
              </Button>
            )}
          </Box>
        ) : (
          <Stack spacing={3}>
            {queueItems.map((queueEntry) => (
              <QueueAnnotationSection
                key={queueEntry.queue.id}
                queueEntry={queueEntry}
                sourceMap={sourceMap}
                spanNotesSourceMap={spanNotesSourceMap}
                onScoresChanged={onScoresChanged}
                showShortcuts={showShortcuts}
                setShowShortcuts={setShowShortcuts}
              />
            ))}
            {onAddLabel && (
              <Button
                size="small"
                startIcon={<Iconify icon="mingcute:add-line" width={16} />}
                onClick={onAddLabel}
                sx={{ alignSelf: "flex-start" }}
              >
                Add Label
              </Button>
            )}
          </Stack>
        )}
      </Box>
    </Box>
  );
}

AnnotationSidebarContent.propTypes = {
  sources: PropTypes.arrayOf(
    PropTypes.shape({
      sourceType: PropTypes.string,
      sourceId: PropTypes.string,
      spanNotesSourceId: PropTypes.string,
    }),
  ),
  onClose: PropTypes.func,
  onScoresChanged: PropTypes.func,
  onAddLabel: PropTypes.func,
  showHeader: PropTypes.bool,
  hideEmpty: PropTypes.bool,
};

// ---------------------------------------------------------------------------
// Per-queue annotation section with keyboard navigation
// ---------------------------------------------------------------------------
function QueueAnnotationSection({
  queueEntry,
  sourceMap,
  spanNotesSourceMap,
  onScoresChanged,
  _showShortcuts,
  setShowShortcuts,
}) {
  const {
    queue,
    item,
    labels,
    existingScores,
    existingNotes,
    existingLabelNotes,
  } = queueEntry;
  const [values, setValues] = useState({});
  const [notes, setNotes] = useState("");
  const [notesTouched, setNotesTouched] = useState(false);
  const [labelNotes, setLabelNotes] = useState({});
  const [showNotes, setShowNotes] = useState(false);
  const [focusedIndex, setFocusedIndex] = useState(0);
  const { mutate: bulkCreate, isPending: isSaving } = useBulkCreateScores();
  const isCompleted = item?.status === "completed";
  const containerRef = useRef(null);
  const hasInitializedScoresRef = useRef(false);
  const hasInitializedNotesRef = useRef(false);
  const itemSourceType =
    item?.sourceType || item?.source_type || Object.keys(sourceMap)[0];
  const sourceId = sourceMap[itemSourceType];
  const spanNotesSourceId =
    spanNotesSourceMap[itemSourceType] ||
    queueEntry.spanNotesSourceId ||
    queueEntry.span_notes_source_id ||
    (itemSourceType === "observation_span" ? sourceId : undefined);
  const sourceLabel = SOURCE_TYPE_LABELS[itemSourceType] || itemSourceType;
  const sourceKey = `${queue.id}:${item?.id || "default"}:${itemSourceType || ""}:${sourceId || ""}`;

  const handleLabelNotesChange = useCallback((labelId, val) => {
    setLabelNotes((prev) => ({ ...prev, [labelId]: val }));
  }, []);

  useEffect(() => {
    hasInitializedScoresRef.current = false;
    hasInitializedNotesRef.current = false;
    setValues({});
    setLabelNotes({});
    setNotes("");
    setNotesTouched(false);
    setShowNotes(false);
  }, [sourceKey]);

  // Pre-populate form with existing scores and notes when editing — only once
  useEffect(() => {
    if (
      !hasInitializedScoresRef.current &&
      existingScores &&
      Object.keys(existingScores).length > 0
    ) {
      hasInitializedScoresRef.current = true;
      setValues(existingScores);
      if (existingLabelNotes && Object.keys(existingLabelNotes).length > 0) {
        setLabelNotes(existingLabelNotes);
      }
    }
    if (existingNotes && !hasInitializedNotesRef.current) {
      hasInitializedNotesRef.current = true;
      setNotes(existingNotes);
      setNotesTouched(false);
      setShowNotes(true);
    }
  }, [existingScores, existingNotes, existingLabelNotes]);

  const handleChange = useCallback((labelId, value) => {
    setValues((prev) => ({ ...prev, [labelId]: value }));
  }, []);

  const hasValues = useMemo(
    () =>
      Object.values(values).some(
        (v) => v !== null && v !== undefined && v !== "",
      ),
    [values],
  );

  const handleSubmit = useCallback(() => {
    const scores = Object.entries(values)
      .filter(([_, v]) => v !== null && v !== undefined)
      .map(([labelId, value]) => ({
        label_id: labelId,
        value,
        notes: labelNotes[labelId] || "",
      }));

    if (scores.length === 0) return;

    bulkCreate(
      {
        sourceType: itemSourceType,
        sourceId,
        scores,
        notes: "",
        spanNotes: notes,
        spanNotesSourceId,
        includeSpanNotes: Boolean(
          spanNotesSourceId && (notesTouched || notes || existingNotes),
        ),
      },
      {
        onSuccess: () => {
          onScoresChanged?.();
        },
      },
    );
  }, [
    values,
    notes,
    notesTouched,
    existingNotes,
    labelNotes,
    itemSourceType,
    sourceId,
    spanNotesSourceId,
    bulkCreate,
    onScoresChanged,
  ]);

  // Keyboard shortcut handler — scoped to this section's container
  useEffect(() => {
    const handleKeyDown = (e) => {
      // Only handle events when focus is within this section
      if (!containerRef.current?.contains(e.target)) return;

      // Skip when typing in a text input
      const tag = e.target.tagName;
      const isInput = tag === "INPUT" || tag === "TEXTAREA";
      const isTextInput = isInput && e.target.type !== "range";

      // Cmd/Ctrl+Enter → submit from anywhere
      if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
        e.preventDefault();
        handleSubmit();
        return;
      }

      // Don't intercept other keys when in a text field
      if (isTextInput) return;

      // ? → toggle shortcuts help
      if (e.key === "?") {
        e.preventDefault();
        setShowShortcuts?.((p) => !p);
        return;
      }

      // N → toggle notes
      if (e.key === "n" || e.key === "N") {
        e.preventDefault();
        setShowNotes((p) => !p);
        return;
      }

      // Tab / Shift+Tab → navigate labels
      if (e.key === "Tab") {
        e.preventDefault();
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
        const label = labels[focusedIndex];
        if (!label) return;

        e.preventDefault();
        const currentVal = values[label.id] || null;

        if (label.type === "star") {
          const max = label.settings?.no_of_stars || 5;
          if (num <= max) {
            const current = currentVal?.rating || 0;
            handleChange(label.id, { rating: num === current ? 0 : num });
          }
        } else if (label.type === "thumbs_up_down") {
          if (num === 1) {
            const isUp = currentVal?.value === "up";
            handleChange(label.id, { value: isUp ? null : "up" });
          } else if (num === 2) {
            const isDown = currentVal?.value === "down";
            handleChange(label.id, { value: isDown ? null : "down" });
          }
        } else if (label.type === "categorical") {
          const rawOptions = label.settings?.options || [];
          const options = rawOptions
            .map((opt) =>
              typeof opt === "string" ? opt : opt?.label || opt?.value || "",
            )
            .filter(Boolean);
          const optIndex = num - 1;
          if (optIndex < options.length) {
            const opt = options[optIndex];
            const selected = currentVal?.selected || [];
            const isMulti = label.settings?.multi_choice || false;
            if (isMulti) {
              const next = selected.includes(opt)
                ? selected.filter((v) => v !== opt)
                : [...selected, opt];
              handleChange(label.id, { selected: next });
            } else {
              handleChange(label.id, {
                selected: selected[0] === opt ? [] : [opt],
              });
            }
          }
        }
      }
    };

    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [
    labels,
    focusedIndex,
    values,
    handleChange,
    handleSubmit,
    setShowShortcuts,
  ]);

  return (
    <Box ref={containerRef}>
      {/* Queue header */}
      <Box
        sx={{
          display: "flex",
          alignItems: "center",
          gap: 1,
          mb: 2,
          px: 1,
          py: 0.75,
          borderRadius: 0.75,
          bgcolor: "background.neutral",
        }}
      >
        <Iconify
          icon="solar:clipboard-list-linear"
          width={18}
          color="primary.main"
        />
        <Typography variant="subtitle2" color="text.primary" sx={{ flex: 1 }}>
          {queue.name}
        </Typography>
        {isCompleted && (
          <Chip
            label="Submitted"
            size="small"
            color="success"
            variant="soft"
            sx={{ height: 20, fontSize: 10 }}
          />
        )}
        <Chip
          label={sourceLabel}
          size="small"
          variant="outlined"
          sx={{ height: 20, fontSize: 10 }}
        />
      </Box>

      {queue.instructions && (
        <Typography
          variant="caption"
          color="text.secondary"
          sx={{ mb: 1.5, display: "block" }}
        >
          {queue.instructions}
        </Typography>
      )}

      {/* Labels */}
      <Stack spacing={1.5}>
        {labels.map((label, i) => (
          <Box
            key={label.id}
            onClick={() => setFocusedIndex(i)}
            sx={{ cursor: "default" }}
          >
            <LabelInput
              label={{
                name: label.name,
                type: label.type,
                settings: label.settings || {},
                description: label.description,
                allow_notes: label.allow_notes ?? false,
              }}
              value={values[label.id] || null}
              onChange={(val) => handleChange(label.id, val)}
              index={i}
              focused={focusedIndex === i}
              labelNotes={labelNotes[label.id] || ""}
              onLabelNotesChange={(val) =>
                handleLabelNotesChange(label.id, val)
              }
            />
          </Box>
        ))}

        {showNotes ? (
          <Box sx={{ position: "relative" }}>
            <TextField
              fullWidth
              size="small"
              multiline
              minRows={2}
              maxRows={4}
              placeholder="Add your notes here..."
              value={notes}
              onChange={(e) => {
                setNotesTouched(true);
                setNotes(e.target.value);
              }}
              autoFocus
            />
            <IconButton
              size="small"
              onClick={() => {
                setNotesTouched(true);
                setShowNotes(false);
                setNotes("");
              }}
              sx={{
                position: "absolute",
                top: 4,
                right: 4,
                p: 0.25,
                color: "text.disabled",
                "&:hover": { color: "text.secondary" },
              }}
            >
              <Iconify icon="mingcute:close-line" width={14} />
            </IconButton>
          </Box>
        ) : (
          <Button
            size="small"
            startIcon={<Iconify icon="mingcute:add-line" width={14} />}
            onClick={() => setShowNotes(true)}
            sx={{
              alignSelf: "flex-start",
              color: "text.secondary",
              fontSize: 12,
              fontWeight: 500,
              px: 0.5,
              "&:hover": { color: "text.primary", bgcolor: "transparent" },
            }}
          >
            Notes
          </Button>
        )}

        <Button
          variant="contained"
          size="small"
          fullWidth
          onClick={handleSubmit}
          disabled={isSaving || !hasValues}
          startIcon={
            isSaving ? <CircularProgress size={14} color="inherit" /> : null
          }
          sx={{ position: "relative" }}
        >
          {isCompleted ? "Update" : "Save"}
          <Box
            component="span"
            sx={{
              ml: 1,
              display: "inline-flex",
              alignItems: "center",
              gap: 0.25,
              opacity: 0.7,
              fontSize: 10,
            }}
          >
            <Kbd>{"\u2318"}</Kbd>
            <Kbd>{"\u23CE"}</Kbd>
          </Box>
        </Button>
      </Stack>
    </Box>
  );
}

QueueAnnotationSection.propTypes = {
  queueEntry: PropTypes.shape({
    queue: PropTypes.object,
    item: PropTypes.object,
    labels: PropTypes.array,
    existingScores: PropTypes.object,
    existingNotes: PropTypes.string,
    existingLabelNotes: PropTypes.object,
    spanNotesSourceId: PropTypes.string,
    span_notes_source_id: PropTypes.string,
  }).isRequired,
  sourceMap: PropTypes.object.isRequired,
  spanNotesSourceMap: PropTypes.object.isRequired,
  onScoresChanged: PropTypes.func,
  _showShortcuts: PropTypes.bool,
  setShowShortcuts: PropTypes.func,
};
