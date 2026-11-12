import React, { useEffect, useMemo, useState } from "react";
import PropTypes from "prop-types";
import { useNavigate } from "react-router-dom";
import { Box, Button, Chip, Stack, Typography } from "@mui/material";
import { useQueryClient } from "@tanstack/react-query";
import Iconify from "src/components/iconify";
import notify from "src/sections/common/ux/toast";
import TextDiff from "src/sections/common/ValueViewer/TextDiff";
import {
  latestOf,
  useDatasetVersions,
  useSaveDatasetVersion,
} from "src/api/develop/dataset-versions";
import {
  patchCorrection,
  refreshCorrections,
  useReviewCorrections,
} from "src/api/develop/review";
import { useChangeSetStore } from "src/sections/develop-detail/DataTab/Versions/changeSetStore";
import {
  correctionOps,
  correctionSource,
  correctionTitle,
  defaultAccepted,
  saveLabel,
  statusWords,
  versionNote,
} from "./correctionModel";
// HO R6: Re-score runs on the version Save corrections made.
import useRescoreOnVersion from "./useRescoreOnVersion";

const errorText = (e, fallback) =>
  (typeof e?.result === "string" && e.result) ||
  (typeof e?.error === "string" && e.error) ||
  fallback;

function CorrectionRow({
  c,
  accepted,
  canApply,
  busy,
  latestNumber,
  onAccept,
  onDismiss,
  onMarkSaved,
}) {
  const open = c.status === "suggested";
  // the row already holds it (saved through pending changes or by hand):
  // link the suggestion to the version that carries it instead of saving
  // the same text again
  const carried = open && c.in_dataset;
  return (
    <Stack
      spacing={0.75}
      data-testid="correction-row"
      sx={{ px: 1.5, py: 1.25, borderTop: "1px solid", borderColor: "divider" }}
    >
      <Stack direction="row" alignItems="center" gap={1}>
        <Typography
          typography="s1"
          fontWeight="fontWeightMedium"
          sx={{ flex: 1 }}
        >
          {correctionTitle(c)}
        </Typography>
        {!open || c.stale || c.in_dataset ? (
          <Chip
            size="small"
            variant="outlined"
            label={statusWords(c)}
            color={c.stale ? "warning" : "default"}
            sx={{ borderRadius: 0.5 }}
          />
        ) : null}
        {carried && canApply && latestNumber ? (
          <Button
            size="small"
            variant="outlined"
            disabled={busy}
            onClick={onMarkSaved}
          >
            Mark as saved in v{latestNumber}
          </Button>
        ) : null}
        {open && canApply && !carried ? (
          <>
            <Button
              size="small"
              variant={accepted ? "contained" : "outlined"}
              color="primary"
              disabled={busy || (c.stale && !accepted)}
              onClick={onAccept}
              startIcon={
                accepted ? <Iconify icon="mdi:check" width={14} /> : null
              }
              aria-pressed={accepted}
            >
              {accepted ? "Accepted" : "Accept"}
            </Button>
            <Button
              size="small"
              color="inherit"
              disabled={busy}
              onClick={onDismiss}
            >
              Dismiss
            </Button>
          </>
        ) : null}
      </Stack>
      <Box
        sx={{
          px: 1,
          py: 0.75,
          borderRadius: 0.5,
          bgcolor: "background.neutral",
          typography: "body2",
        }}
      >
        <TextDiff from={c.stale ? c.current : c.before} to={c.after} />
      </Box>
      {c.stale ? (
        <Typography typography="s3" color="warning.dark">
          The diff is against the row as it is now; it was edited after the
          review.
        </Typography>
      ) : null}
    </Stack>
  );
}

CorrectionRow.propTypes = {
  c: PropTypes.object.isRequired,
  accepted: PropTypes.bool,
  canApply: PropTypes.bool,
  busy: PropTypes.bool,
  latestNumber: PropTypes.number,
  onAccept: PropTypes.func,
  onDismiss: PropTypes.func,
  onMarkSaved: PropTypes.func,
};

/**
 * Corrections a review suggested (W4-3): each with its diff and provenance,
 * accepted or dismissed one by one, then saved as one version whose note
 * names every reviewer (N2), or moved to the pending changes to edit first.
 * People without edit access see the list read-only.
 */
export default function CorrectionsBlock({ datasetId, queueId }) {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const q = useReviewCorrections(datasetId, { queueId });
  const versionsQ = useDatasetVersions(datasetId);
  const save = useSaveDatasetVersion(datasetId);
  const addOp = useChangeSetStore((s) => s.addOp);
  const list = useMemo(() => q.data?.corrections ?? [], [q.data]);
  const canApply = Boolean(q.data?.can_apply);
  const [accepted, setAccepted] = useState(null);
  const [busy, setBusy] = useState(false);
  const rescoreOn = useRescoreOnVersion(datasetId);
  const defaults = useMemo(() => defaultAccepted(list), [list]);
  const picked = accepted ?? defaults;
  useEffect(() => setAccepted(null), [queueId]);

  if (!list.length) return null;
  const chosen = list.filter(
    (c) => c.status === "suggested" && picked.has(c.id),
  );
  const latest = latestOf(versionsQ.data);
  const next = (latest?.number ?? 0) + 1;
  // open suggestions the dataset does not hold yet
  const suggested = list.filter(
    (c) => c.status === "suggested" && !c.in_dataset,
  );

  const toggle = (c) => {
    const nextSet = new Set(picked);
    if (nextSet.has(c.id)) nextSet.delete(c.id);
    else {
      // one correction per cell
      list
        .filter(
          (o) =>
            o.id !== c.id &&
            o.row_id === c.row_id &&
            o.column_id === c.column_id,
        )
        .forEach((o) => nextSet.delete(o.id));
      nextSet.add(c.id);
    }
    setAccepted(nextSet);
  };

  const dismiss = async (c) => {
    setBusy(true);
    try {
      await patchCorrection(datasetId, c.id, { status: "dismissed" });
      refreshCorrections(queryClient);
      notify(`Dismissed the correction on ${c.row_label}`, {
        variant: "success",
        key: "correction-dismissed",
        undo: {
          onUndo: async () => {
            await patchCorrection(datasetId, c.id, { status: "suggested" });
            refreshCorrections(queryClient);
          },
        },
      });
    } catch (e) {
      notify(errorText(e, "Could not dismiss the correction."), {
        variant: "error",
      });
    } finally {
      setBusy(false);
    }
  };

  const markSaved = async (c) => {
    if (!latest) return;
    setBusy(true);
    try {
      await patchCorrection(datasetId, c.id, {
        status: "applied",
        version_number: latest.number,
      });
      refreshCorrections(queryClient);
      notify(`Linked the correction on ${c.row_label} to v${latest.number}`, {
        variant: "success",
        key: "correction-linked",
      });
    } catch (e) {
      notify(errorText(e, "Could not link the correction."), {
        variant: "error",
      });
    } finally {
      setBusy(false);
    }
  };

  const saveVersion = async () => {
    if (!chosen.length || !latest) return;
    setBusy(true);
    try {
      const out = await save.mutateAsync({
        base_version_id: latest.id,
        description: versionNote(chosen),
        operations: correctionOps(chosen),
      });
      const number = out?.version?.number;
      // eslint-disable-next-line no-await-in-loop
      for (const c of chosen)
        await patchCorrection(datasetId, c.id, {
          status: "applied",
          version_number: number,
        });
      refreshCorrections(queryClient);
      setAccepted(null);
      // HO R6: the door only when the server plans the re-score on vN.
      const door = await rescoreOn.offer(number);
      notify(`Saved v${number} · ${versionNote(chosen)}`, {
        variant: "success",
        key: "corrections-saved",
        ...door,
      });
    } catch (e) {
      notify(
        e?.isVersionConflict
          ? "Someone saved a newer version. Reload to see it, then save again."
          : errorText(e, "Could not save the corrections."),
        { variant: "error", key: "corrections-saved" },
      );
    } finally {
      setBusy(false);
    }
  };

  const toPending = () => {
    // W4 §9 (M6): N2 keeps each op's source (who corrected it, in which
    // review), and Save version's default note names the reviewers.
    const hint = versionNote(chosen);
    chosen.forEach((c) =>
      addOp(datasetId, {
        op: "update_cell",
        row_id: c.row_id,
        column_id: c.column_id,
        value: c.after,
        before: c.current,
        source: correctionSource(c),
        description_hint: hint,
      }),
    );
    notify(
      `Added ${chosen.length} ${chosen.length === 1 ? "correction" : "corrections"} to the pending changes`,
      {
        variant: "success",
        key: "corrections-pending",
        action: () => (
          <Button
            size="small"
            color="inherit"
            onClick={() => navigate(`/dashboard/develop/${datasetId}?tab=data`)}
          >
            Open the dataset
          </Button>
        ),
      },
    );
  };

  return (
    <Box
      data-testid="corrections-block"
      sx={{
        border: "1px solid",
        borderColor: "divider",
        borderRadius: 0.5,
        overflow: "hidden",
      }}
    >
      <Stack
        direction="row"
        alignItems="center"
        gap={1}
        sx={{ px: 1.5, py: 1 }}
      >
        <Iconify icon="mdi:file-document-edit-outline" width={18} />
        <Typography
          typography="s1"
          fontWeight="fontWeightSemiBold"
          sx={{ flex: 1 }}
        >
          Corrections ({list.length})
        </Typography>
        {suggested.length && !canApply ? (
          <Typography typography="s3" color="text.secondary">
            Only people who can edit this dataset save corrections.
          </Typography>
        ) : null}
      </Stack>
      {list.map((c) => (
        <CorrectionRow
          key={c.id}
          c={c}
          accepted={picked.has(c.id)}
          canApply={canApply}
          busy={busy}
          latestNumber={latest?.number}
          onAccept={() => toggle(c)}
          onDismiss={() => dismiss(c)}
          onMarkSaved={() => markSaved(c)}
        />
      ))}
      {canApply && suggested.length ? (
        <Stack
          direction="row"
          gap={1}
          alignItems="center"
          sx={{
            px: 1.5,
            py: 1,
            borderTop: "1px solid",
            borderColor: "divider",
          }}
        >
          <Typography typography="s3" color="text.secondary" sx={{ flex: 1 }}>
            {chosen.length
              ? versionNote(chosen)
              : "Accept the corrections to save."}
          </Typography>
          <Button
            size="small"
            variant="outlined"
            disabled={busy || !chosen.length}
            onClick={toPending}
          >
            Add to pending changes
          </Button>
          <Button
            size="small"
            variant="contained"
            disabled={busy || !chosen.length || !latest}
            onClick={saveVersion}
          >
            {saveLabel(chosen.length || 0, next)}
          </Button>
        </Stack>
      ) : null}
      {rescoreOn.line}
      {rescoreOn.dialog}
    </Box>
  );
}

CorrectionsBlock.propTypes = {
  datasetId: PropTypes.string,
  queueId: PropTypes.string,
};
