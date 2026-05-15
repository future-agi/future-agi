import PropTypes from "prop-types";
import {
  useState,
  useEffect,
  useCallback,
  useMemo,
  useRef,
  forwardRef,
  useImperativeHandle,
} from "react";
import {
  Box,
  Button,
  Chip,
  CircularProgress,
  IconButton,
  Stack,
  TextField,
  Tooltip,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { useScoresForSource, useBulkCreateScores } from "src/api/scores/scores";
import LabelInput from "src/sections/annotations/queues/annotate/label-input";
import AddLabelDrawer from "src/components/traceDetailDrawer/AddLabelDrawer";
import { useGetOrCreateDefaultQueue } from "src/api/annotation-queues/annotation-queues";

// ---------------------------------------------------------------------------
// Score value display (read-only chip for existing scores)
// ---------------------------------------------------------------------------
ScoreValueChip.propTypes = {
  label: PropTypes.object.isRequired,
  score: PropTypes.object.isRequired,
};

function ScoreValueChip({ label, score }) {
  const { type } = label;
  const val = score.value;

  let display = "—";
  if (type === "star" && val?.rating != null) {
    display = `${"★".repeat(val.rating)}${"☆".repeat((label.settings?.no_of_stars || 5) - val.rating)}`;
  } else if (type === "categorical" && val?.selected) {
    display = Array.isArray(val.selected)
      ? val.selected.join(", ")
      : val.selected;
  } else if (type === "numeric" && val?.value != null) {
    display = String(val.value);
  } else if (type === "text" && val?.text) {
    display = val.text.length > 40 ? `${val.text.slice(0, 40)}…` : val.text;
  } else if (type === "thumbs_up_down" && val?.value) {
    display = val.value === "up" ? "👍" : "👎";
  }

  return (
    <Tooltip title={score.annotator_name || "Unknown"} placement="top">
      <Chip label={display} size="small" variant="soft" />
    </Tooltip>
  );
}

// ---------------------------------------------------------------------------
// Main InlineAnnotator component
// ---------------------------------------------------------------------------
const InlineAnnotator = forwardRef(function InlineAnnotator(
  { sourceType, sourceId, projectId, onScoresChanged, editTrigger = 0 },
  ref,
) {
  const [editing, setEditing] = useState(false);
  const [values, setValues] = useState({});
  const [notes, setNotes] = useState("");
  const [addLabelDrawerOpen, setAddLabelDrawerOpen] = useState(false);
  const [queueLabels, setQueueLabels] = useState([]);
  const [labelsFetchKey, setLabelsFetchKey] = useState(0);
  const getOrCreateDefault = useGetOrCreateDefaultQueue();

  useImperativeHandle(ref, () => ({
    startEditing: () => setEditing(true),
    stopEditing: () => {
      setEditing(false);
      setValues({});
      setNotes("");
    },
  }));

  // Reset when source changes
  useEffect(() => {
    setEditing(false);
    setValues({});
    setNotes("");
  }, [sourceId]);

  // Allow parent to trigger edit mode via incrementing editTrigger
  useEffect(() => {
    if (editTrigger > 0) setEditing(true);
  }, [editTrigger]);

  // Fetch labels from the project's default annotation queue
  useEffect(() => {
    if (!projectId) return;
    getOrCreateDefault.mutate(
      { projectId },
      {
        onSuccess: (response) => {
          const result = response.data?.result || response.data;
          setQueueLabels(result?.labels || []);
        },
      },
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId, labelsFetchKey]);

  const refetchQueueLabels = useCallback(() => {
    setLabelsFetchKey((k) => k + 1);
  }, []);

  const labels = queueLabels;

  // Fetch existing scores for this source
  const { data: existingScores, isLoading: scoresLoading } = useScoresForSource(
    sourceType,
    sourceId,
  );

  const { mutate: bulkCreate, isPending: isSaving } = useBulkCreateScores();

  // Build lookup: label_id → score
  const scoresByLabel = useMemo(() => {
    const map = {};
    if (existingScores) {
      for (const s of existingScores) {
        const lid = s.label_id || s.labelId;
        if (lid) map[lid] = s;
      }
    }
    return map;
  }, [existingScores]);

  // Pre-fill form from existing scores when entering edit mode — only once per edit session
  const editInitializedRef = useRef(false);
  useEffect(() => {
    if (editing && !editInitializedRef.current) {
      editInitializedRef.current = true;
      const initial = {};
      for (const [labelId, score] of Object.entries(scoresByLabel)) {
        initial[labelId] = score.value;
      }
      setValues(initial);
    }
    if (!editing) {
      editInitializedRef.current = false;
    }
  }, [editing, scoresByLabel]);

  const handleChange = useCallback((labelId, value) => {
    setValues((prev) => ({ ...prev, [labelId]: value }));
  }, []);

  const handleSubmit = useCallback(() => {
    const scores = Object.entries(values)
      .filter(([_, v]) => v !== null && v !== undefined)
      .map(([labelId, value]) => ({
        label_id: labelId,
        value,
      }));

    if (scores.length === 0) return;

    bulkCreate(
      { sourceType, sourceId, scores, notes, spanNotes: notes },
      {
        onSuccess: (response) => {
          // Inspect errors[] before exiting edit mode. The mutation hook
          // already shows a partial-failure snackbar; here we just keep the
          // user in edit mode so they can retry the failed labels without
          // having to re-open the annotator.
          const result = response?.data?.result || {};
          const errors = result.errors || [];
          if (errors.length > 0) {
            // Keep editing open; values stay populated for retry.
            onScoresChanged?.();
            return;
          }
          setEditing(false);
          setNotes("");
          onScoresChanged?.();
        },
      },
    );
  }, [values, notes, sourceType, sourceId, bulkCreate, onScoresChanged]);

  const handleCancel = () => {
    setEditing(false);
    setValues({});
    setNotes("");
  };

  const hasValues = Object.values(values).some(
    (v) => v !== null && v !== undefined && v !== "",
  );

  // Loading state
  if (scoresLoading) {
    return (
      <Box sx={{ display: "flex", justifyContent: "center", py: 3 }}>
        <CircularProgress size={20} />
      </Box>
    );
  }

  // No labels configured
  if (labels.length === 0) {
    return (
      <Box sx={{ textAlign: "center", py: 3 }}>
        <Typography variant="body2" color="text.secondary">
          No annotation labels added to this project.
        </Typography>
        {projectId && (
          <>
            <Button
              size="small"
              variant="outlined"
              startIcon={<Iconify icon="mingcute:add-line" width={16} />}
              onClick={() => setAddLabelDrawerOpen(true)}
              sx={{ mt: 1 }}
            >
              Add Label
            </Button>
            <AddLabelDrawer
              open={addLabelDrawerOpen}
              onClose={() => setAddLabelDrawerOpen(false)}
              projectId={projectId}
              onLabelsChanged={() => {
                refetchQueueLabels();
                onScoresChanged?.();
              }}
            />
          </>
        )}
      </Box>
    );
  }

  // ── Read mode: show existing scores ──────────────────────────────────
  if (!editing) {
    return (
      <Box>
        {/* Header */}
        <Stack
          direction="row"
          alignItems="center"
          justifyContent="space-between"
          sx={{ mb: 1.5 }}
        >
          <Typography variant="subtitle2" color="text.secondary">
            Annotations
          </Typography>
          <Stack direction="row" spacing={0.5}>
            {projectId && (
              <Button
                size="small"
                variant="outlined"
                startIcon={<Iconify icon="mingcute:add-line" width={14} />}
                onClick={() => setAddLabelDrawerOpen(true)}
              >
                Add Label
              </Button>
            )}
            <Button
              size="small"
              variant="outlined"
              data-inline-annotator-edit
              startIcon={<Iconify icon="eva:edit-2-fill" width={14} />}
              onClick={() => setEditing(true)}
            >
              {Object.keys(scoresByLabel).length > 0 ? "Edit" : "Annotate"}
            </Button>
          </Stack>
        </Stack>

        {/* Existing scores */}
        {Object.keys(scoresByLabel).length > 0 ? (
          <Stack spacing={1}>
            {labels.map((label) => {
              const score = scoresByLabel[label.id];
              if (!score) return null;
              return (
                <Stack
                  key={label.id}
                  direction="row"
                  alignItems="center"
                  justifyContent="space-between"
                  sx={{
                    px: 1.5,
                    py: 0.75,
                    borderRadius: 0.75,
                    bgcolor: "background.neutral",
                  }}
                >
                  <Typography variant="caption" fontWeight={600}>
                    {label.name}
                  </Typography>
                  <ScoreValueChip label={label} score={score} />
                </Stack>
              );
            })}
          </Stack>
        ) : (
          <Box
            sx={{
              textAlign: "center",
              py: 2,
              bgcolor: "background.neutral",
              borderRadius: 1,
            }}
          >
            <Typography variant="body2" color="text.disabled">
              No annotations yet
            </Typography>
          </Box>
        )}
        {projectId && (
          <AddLabelDrawer
            open={addLabelDrawerOpen}
            onClose={() => setAddLabelDrawerOpen(false)}
            projectId={projectId}
            onLabelsChanged={() => {
                refetchQueueLabels();
                onScoresChanged?.();
              }}
          />
        )}
      </Box>
    );
  }

  // ── Edit mode: annotation form ───────────────────────────────────────
  return (
    <Box>
      <Stack
        direction="row"
        alignItems="center"
        justifyContent="space-between"
        sx={{ mb: 1.5 }}
      >
        <Typography variant="subtitle2" color="text.secondary">
          Annotate
        </Typography>
        <IconButton size="small" onClick={handleCancel}>
          <Iconify icon="mingcute:close-line" width={18} />
        </IconButton>
      </Stack>

      <Stack spacing={1.5}>
        {labels.map((label) => (
          <LabelInput
            key={label.id}
            label={{
              name: label.name,
              type: label.type,
              settings: label.settings || {},
              description: label.description,
            }}
            value={values[label.id] ?? null}
            onChange={(val) => handleChange(label.id, val)}
          />
        ))}

        <TextField
          fullWidth
          size="small"
          multiline
          minRows={1}
          maxRows={3}
          placeholder="Notes (optional)"
          value={notes}
          onChange={(e) => setNotes(e.target.value)}
        />

        <Stack direction="row" spacing={1}>
          <Button
            variant="contained"
            size="small"
            fullWidth
            onClick={handleSubmit}
            disabled={isSaving || !hasValues}
            startIcon={
              isSaving ? <CircularProgress size={14} color="inherit" /> : null
            }
          >
            Save
          </Button>
          <Button
            variant="outlined"
            size="small"
            onClick={handleCancel}
            disabled={isSaving}
          >
            Cancel
          </Button>
        </Stack>
      </Stack>
      {projectId && (
        <AddLabelDrawer
          open={addLabelDrawerOpen}
          onClose={() => setAddLabelDrawerOpen(false)}
          projectId={projectId}
          onLabelsChanged={() => {
                refetchQueueLabels();
                onScoresChanged?.();
              }}
        />
      )}
    </Box>
  );
});

InlineAnnotator.propTypes = {
  sourceType: PropTypes.string.isRequired,
  sourceId: PropTypes.string.isRequired,
  projectId: PropTypes.string,
  onScoresChanged: PropTypes.func,
  editTrigger: PropTypes.number,
};

export default InlineAnnotator;
