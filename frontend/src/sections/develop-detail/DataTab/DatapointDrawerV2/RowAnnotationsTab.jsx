import React, { useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import PropTypes from "prop-types";
import {
  Avatar,
  Box,
  Button,
  Collapse,
  Skeleton,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import { alpha } from "@mui/material/styles";
import { enqueueSnackbar } from "notistack";
import { copyToClipboard } from "src/utils/utils";
import { useQueryClient } from "@tanstack/react-query";
import Iconify from "src/components/iconify";
import ScoresListSection from "src/components/ScoresListSection/ScoresListSection";
import AddLabelDrawer from "src/components/traceDetailDrawer/AddLabelDrawer";
import AnnotationSidebarContent from "src/components/traceDetailDrawer/AnnotationSidebarContent";
import { rowViewKeys } from "src/api/develop/row-view";
import { useScoresForSource } from "src/api/scores/scores";
import CustomTooltip from "src/components/tooltip";
import EmptyState from "src/sections/common/ux/EmptyState";
import Section from "src/sections/common/ux/oneview/Section";
import {
  fmtTimeAbsolute,
  fmtTimeRelative,
} from "src/sections/common/format/fmt";
import { sectionLabelSx } from "../Versions/ChangeParts";
import {
  groupScores,
  scoresSummary,
  SCORES_OPEN_MAX,
} from "./annotationsModel";

const AGREE_TONE = {
  agree: { light: ["green", 700], dark: ["green", 400], icon: "mdi:check-all" },
  split: {
    light: ["amber", 700],
    dark: ["amber", 500],
    icon: "mdi:call-split",
  },
};
const toneColor = (tone) => (t) => {
  if (!tone) return t.palette.text.secondary;
  const [hue, shade] = t.palette.mode === "dark" ? tone.dark : tone.light;
  return t.palette[hue]?.[shade];
};

/**
 * RV#18: one line per label — the label, each annotator's value side by
 * side (initial + `2/5`; hover: who, when, note) and the agreement flag
 * (`2 of 2 agree · Down`, `Split · 2/5 vs 4/5`).
 */
const ByLabel = ({ groups }) => (
  <Stack
    data-testid="annotations-by-label"
    sx={{
      border: "1px solid",
      borderColor: "divider",
      borderRadius: 1,
      mb: 1.5,
    }}
    divider={<Box sx={{ borderTop: "1px solid", borderColor: "divider" }} />}
  >
    {groups.map((g) => {
      const tone = AGREE_TONE[g.agreement.state];
      return (
        <Stack
          key={g.label}
          direction="row"
          alignItems="center"
          gap={1.25}
          sx={{ px: 1.5, py: 1, minWidth: 0, flexWrap: "wrap" }}
        >
          <Typography
            typography="s1"
            fontWeight="fontWeightMedium"
            noWrap
            sx={{ minWidth: 96, maxWidth: 180 }}
          >
            {g.label}
          </Typography>
          <Stack
            direction="row"
            gap={0.75}
            sx={{ flex: 1, minWidth: 0, flexWrap: "wrap" }}
          >
            {g.entries.map((e) => (
              <CustomTooltip
                key={e.id}
                show
                size="small"
                placement="top"
                arrow
                title={
                  <Stack gap={0.25} sx={{ maxWidth: 280 }}>
                    <Typography typography="s2" fontWeight="fontWeightMedium">
                      {`${e.annotator} · ${e.text}`}
                    </Typography>
                    {e.at && (
                      <Typography typography="s3">
                        {fmtTimeAbsolute(e.at)}
                      </Typography>
                    )}
                    {e.notes && (
                      <Typography typography="s3" sx={{ fontStyle: "italic" }}>
                        “{e.notes}”
                      </Typography>
                    )}
                  </Stack>
                }
              >
                <Stack
                  direction="row"
                  alignItems="center"
                  gap={0.5}
                  tabIndex={0}
                  aria-label={`${e.annotator}: ${e.text}`}
                  sx={{
                    pl: 0.25,
                    pr: 0.75,
                    height: 24,
                    borderRadius: 3,
                    bgcolor: "background.neutral",
                    maxWidth: 220,
                  }}
                >
                  <Avatar
                    sx={{
                      width: 20,
                      height: 20,
                      fontSize: 10,
                      fontWeight: 700,
                      bgcolor: "primary.lighter",
                      color: "primary.darker",
                    }}
                  >
                    {String(e.annotator).charAt(0).toUpperCase()}
                  </Avatar>
                  <Typography
                    typography="s2"
                    fontWeight="fontWeightMedium"
                    noWrap
                    sx={{ fontVariantNumeric: "tabular-nums" }}
                  >
                    {e.text}
                  </Typography>
                  {e.at && (
                    <Typography typography="s3" color="text.disabled" noWrap>
                      {fmtTimeRelative(e.at)}
                    </Typography>
                  )}
                </Stack>
              </CustomTooltip>
            ))}
          </Stack>
          {g.agreement.text && (
            <Stack
              direction="row"
              alignItems="center"
              gap={0.5}
              sx={{
                flexShrink: 0,
                px: 0.75,
                height: 22,
                borderRadius: 0.5,
                typography: "s3",
                fontWeight: 500,
                color: toneColor(tone),
                bgcolor: (t) =>
                  tone
                    ? alpha(
                        g.agreement.state === "agree"
                          ? t.palette.green[600]
                          : t.palette.amber[500],
                        0.12,
                      )
                    : t.palette.action.hover,
              }}
            >
              {tone && <Iconify icon={tone.icon} width={14} />}
              {g.agreement.text}
            </Stack>
          )}
        </Stack>
      );
    })}
  </Stack>
);

ByLabel.propTypes = { groups: PropTypes.array.isRequired };

/**
 * Annotations on the row (PRODUCT-SPEC §3.4): the existing scores list for
 * `dataset_row` (the server leaves out experiment-review items), `Annotate`
 * opening the existing annotation form inline, and `Add to annotation
 * queue…`.
 */
/** Put `text` into a React-controlled textarea and focus it at the end. */
function fillTextarea(el, text) {
  const setter = Object.getOwnPropertyDescriptor(
    window.HTMLTextAreaElement.prototype,
    "value",
  )?.set;
  setter?.call(el, text);
  el.dispatchEvent(new Event("input", { bubbles: true }));
  el.focus();
  el.setSelectionRange(text.length, text.length);
}

/**
 * One-shot `noteDraft=<text>` (R2 §3.3.8, `Add a note` from the Matrix and
 * the trace pair): open the annotation form with its notes field shown,
 * prefilled and focused, then drop the param.
 */
function useNoteDraft({ canEdit, setAnnotating, formRef, onPending }) {
  const [searchParams, setSearchParams] = useSearchParams();
  const param = searchParams.get("noteDraft");
  const [draft, setDraft] = useState(null);
  // One-shot: take the draft, then drop the param at once.
  useEffect(() => {
    if (param == null) return;
    if (canEdit) setDraft(param);
    const next = new URLSearchParams(window.location.search);
    next.delete("noteDraft");
    setSearchParams(next, { replace: true });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [param]);
  useEffect(() => {
    if (draft == null) return undefined;
    setAnnotating(true);
    // The notes field shows only once the form has labels for this row; keep
    // watching (a label added from the form brings it) and fill it then.
    let done = false;
    let ticks = 0;
    const attempt = () => {
      if (done) return;
      ticks += 1;
      const root = formRef.current;
      const area = root?.querySelector("textarea:not([aria-hidden])");
      if (area) {
        done = true;
        onPending(null);
        fillTextarea(area, area.value ? `${area.value}\n${draft}` : draft);
        setDraft(null);
        return;
      }
      // No labels on this row yet: keep the draft in view until there are.
      if (ticks === 12) onPending(draft);
      const notesButton = root
        ? [...root.querySelectorAll("button")].find(
            (b) => b.textContent?.trim() === "Notes",
          )
        : null;
      notesButton?.click();
    };
    const timer = setInterval(attempt, 100);
    const stop = setTimeout(() => clearInterval(timer), 120000);
    return () => {
      clearInterval(timer);
      clearTimeout(stop);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [draft]);
}

const RowAnnotationsTab = ({ datasetId, rowId, onAddToQueue, canEdit }) => {
  const queryClient = useQueryClient();
  const [annotating, setAnnotating] = useState(false);
  const formRef = useRef(null);
  const [pendingNote, setPendingNote] = useState(null);
  useNoteDraft({ canEdit, setAnnotating, formRef, onPending: setPendingNote });
  const [labelDrawer, setLabelDrawer] = useState(false);
  // Same query (and cache) as ScoresListSection below.
  const scoresQ = useScoresForSource("dataset_row", rowId);
  const groups = groupScores(scoresQ.data ?? []);
  const scoreCount = scoresQ.data?.length ?? 0;
  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: ["scores"] });
    queryClient.invalidateQueries({
      queryKey: rowViewKeys.activity(datasetId, rowId),
    });
    queryClient.invalidateQueries({ queryKey: ["dataset-detail"] });
  };
  return (
    <Box sx={{ px: 2, py: 1.5 }}>
      <Stack direction="row" gap={1} sx={{ mb: 1.5 }}>
        <Button
          size="small"
          variant="outlined"
          color={annotating ? "primary" : "inherit"}
          disabled={!canEdit}
          startIcon={<Iconify icon="mdi:pencil-outline" width={16} />}
          onClick={() => setAnnotating((a) => !a)}
        >
          {annotating ? "Close annotation form" : "Annotate"}
        </Button>
        <Button
          size="small"
          variant="outlined"
          color="inherit"
          startIcon={<Iconify icon="mdi:tray-arrow-down" width={16} />}
          onClick={(e) => onAddToQueue(e.currentTarget)}
          sx={{ borderColor: "divider" }}
        >
          Add to annotation queue…
        </Button>
      </Stack>
      {pendingNote != null && annotating && (
        <Box
          sx={{
            mb: 1.5,
            p: 1.25,
            border: "1px dashed",
            borderColor: "divider",
            borderRadius: 1,
          }}
        >
          <TextField
            fullWidth
            size="small"
            multiline
            minRows={2}
            label="Your note"
            value={pendingNote}
            onChange={(e) => setPendingNote(e.target.value)}
            autoFocus
            inputProps={{ "data-testid": "pending-note" }}
            onFocus={(e) =>
              e.target.setSelectionRange(
                e.target.value.length,
                e.target.value.length,
              )
            }
          />
          <Stack direction="row" alignItems="center" gap={1} sx={{ mt: 0.75 }}>
            <Typography typography="s3" color="text.secondary" sx={{ flex: 1 }}>
              Add a label in the form below; the note moves into its notes field
              and saves with the annotation.
            </Typography>
            <Button
              size="small"
              color="inherit"
              startIcon={<Iconify icon="basil:copy-outline" width={14} />}
              onClick={() => {
                copyToClipboard(pendingNote);
                enqueueSnackbar("Note copied", { variant: "success" });
              }}
            >
              Copy
            </Button>
          </Stack>
        </Box>
      )}
      <Collapse in={annotating} unmountOnExit>
        <Box
          ref={formRef}
          sx={{
            mb: 2,
            border: "1px solid",
            borderColor: "divider",
            borderRadius: 1,
            maxHeight: 520,
            overflow: "auto",
          }}
        >
          <AnnotationSidebarContent
            sources={[{ sourceType: "dataset_row", sourceId: rowId }]}
            onClose={() => setAnnotating(false)}
            onScoresChanged={refresh}
            onAddLabel={() => setLabelDrawer(true)}
          />
        </Box>
      </Collapse>
      {scoresQ.isLoading ? (
        <Skeleton variant="rounded" height={88} sx={{ mb: 1.5 }} />
      ) : scoreCount === 0 ? (
        !annotating && (
          <EmptyState
            compact
            icon="mdi:tag-outline"
            title="No annotations yet"
            body="Label this row (correct? quality?) so experiments can be checked against people, not only judges."
            actions={
              canEdit
                ? [
                    {
                      label: "Annotate",
                      icon: "mdi:pencil-outline",
                      onClick: () => setAnnotating(true),
                    },
                  ]
                : []
            }
          />
        )
      ) : (
        <>
          <Typography sx={{ ...sectionLabelSx, mb: 0.75 }}>By label</Typography>
          <ByLabel groups={groups} />
          {/* A1-31: every score in a Section — open up to 5 scores, else
              collapsed and still saying its answer; the user's toggle is
              remembered. */}
          <Box data-testid="annotations-all" sx={{ mt: 0.5, mx: -1 }}>
            <Section
              id="row.scores"
              surface="rowView"
              title="Every annotation"
              count={scoreCount}
              summary={scoresSummary(scoresQ.data, {
                relative: fmtTimeRelative,
              })}
              defaultOpen={scoreCount <= SCORES_OPEN_MAX}
            >
              <ScoresListSection sourceType="dataset_row" sourceId={rowId} />
            </Section>
          </Box>
        </>
      )}
      <AddLabelDrawer
        open={labelDrawer}
        onClose={() => setLabelDrawer(false)}
        datasetId={datasetId}
        onLabelsChanged={() => {
          queryClient.invalidateQueries({ queryKey: ["annotation-queues"] });
          queryClient.invalidateQueries({
            queryKey: ["annotation-queues", "for-source"],
          });
        }}
      />
    </Box>
  );
};

RowAnnotationsTab.propTypes = {
  datasetId: PropTypes.string.isRequired,
  rowId: PropTypes.string.isRequired,
  onAddToQueue: PropTypes.func.isRequired,
  canEdit: PropTypes.bool,
};

export default RowAnnotationsTab;
