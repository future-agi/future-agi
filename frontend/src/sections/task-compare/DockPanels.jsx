import React, { useState } from "react";
import PropTypes from "prop-types";
import {
  Avatar,
  Box,
  Button,
  Checkbox,
  FormControlLabel,
  IconButton,
  InputBase,
  Popover,
  Stack,
  Switch,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import Truncate from "src/sections/common/ux/Truncate";
import CopyButton from "src/sections/common/ux/CopyButton";
import RelativeTime from "src/sections/common/ux/RelativeTime";
import KeyCap from "src/sections/common/ux/KeyCap";
import Term from "src/sections/common/ux/Term";
import TextDiff from "src/sections/common/ValueViewer/TextDiff";
import { fmtScore, NUM_SX } from "src/sections/common/format/fmt";
import { laneTemplate } from "./LanesGrid";

// ---------------------------------------------------------- final answers

/** One card per lane (lane order): the answer, `Word diff vs baseline`, copy. */
export function FinalAnswers({ lanes }) {
  const [diff, setDiff] = useState(false);
  const base = lanes[0]?.trial?.final_answer ?? "";
  return (
    <Box sx={{ p: 1.25 }}>
      <Stack direction="row" alignItems="center" spacing={1} sx={{ mb: 1 }}>
        <CustomTooltip
          describeChild
          show
          size="small"
          title="Highlight the words each answer adds or drops compared with the baseline"
        >
          <FormControlLabel
            control={
              <Switch
                size="small"
                checked={diff}
                onChange={(e) => setDiff(e.target.checked)}
              />
            }
            label={
              <Typography typography="s2">
                Word diff vs <Term k="baseline">baseline</Term>
              </Typography>
            }
            sx={{ ml: 0 }}
          />
        </CustomTooltip>
      </Stack>
      <Box
        sx={{
          display: "grid",
          gridTemplateColumns: laneTemplate(lanes.length),
          gap: 1,
        }}
      >
        {lanes.map((l, i) => {
          const text = l.trial?.final_answer ?? "";
          return (
            <Box
              key={l.id}
              sx={{
                border: "1px solid",
                borderColor: "divider",
                borderTop: "3px solid",
                borderTopColor: l.color?.solid,
                borderRadius: 1,
                p: 1,
                minWidth: 0,
                position: "relative",
              }}
            >
              <Stack
                direction="row"
                alignItems="center"
                spacing={0.5}
                sx={{ mb: 0.5 }}
              >
                <Typography typography="s2" fontWeight={700}>
                  {l.blindName ?? l.label}
                </Typography>
                <Typography typography="s3" color="text.secondary">
                  {i === 0 && !l.blindName ? "baseline" : ""}
                </Typography>
                <Box sx={{ flex: 1 }} />
                {text ? (
                  <CopyButton value={text} tooltip="Copy this answer" />
                ) : null}
              </Stack>
              {!text ? (
                <Typography typography="s2" color="text.disabled">
                  No reply to the customer
                </Typography>
              ) : diff && i > 0 ? (
                <TextDiff from={base} to={text} kind="words" />
              ) : (
                <Truncate
                  text={text}
                  lines={6}
                  expandable
                  sx={{ typography: "s2" }}
                />
              )}
              {l.trial?.last_reply && l.trial.last_reply !== text ? (
                <Typography
                  typography="s3"
                  color="text.secondary"
                  sx={{ mt: 0.5 }}
                >
                  Last message: “{l.trial.last_reply}”
                </Typography>
              ) : null}
            </Box>
          );
        })}
      </Box>
    </Box>
  );
}
FinalAnswers.propTypes = { lanes: PropTypes.array.isRequired };

// ---------------------------------------------------------- checks matrix

const GLYPH = {
  true: { g: "✓", color: "success.main", word: "passed" },
  false: { g: "✕", color: "error.main", word: "failed" },
  null: { g: "–", color: "text.disabled", word: "not checked" },
};

/** Checks × lanes (`✓ ✕ –`, judge scores); a cell opens its evidence. */
export function ChecksMatrix({ rowChecks, lanes, onGoToStep }) {
  const [pop, setPop] = useState(null);
  return (
    <Box sx={{ p: 1.25, overflowX: "auto" }}>
      <Box
        component="table"
        sx={{
          borderCollapse: "collapse",
          minWidth: "100%",
          "& td, & th": {
            borderBottom: "1px solid",
            borderColor: "divider",
            px: 1,
            py: 0.5,
            typography: "s2",
          },
        }}
      >
        <thead>
          <tr>
            <Box
              component="th"
              sx={{
                textAlign: "left",
                fontWeight: 600,
                color: "text.secondary",
                position: "sticky",
                left: 0,
                bgcolor: "background.paper",
              }}
            >
              <Term k="check">Check</Term>
            </Box>
            {lanes.map((l) => (
              <Box
                key={l.id}
                component="th"
                sx={{
                  textAlign: "center",
                  fontWeight: 700,
                  color: l.color?.solid,
                  whiteSpace: "nowrap",
                }}
              >
                {l.blindName ?? l.label}
              </Box>
            ))}
          </tr>
        </thead>
        <tbody>
          {rowChecks.map((rc) => (
            <tr key={rc.key}>
              <Box
                component="td"
                sx={{
                  position: "sticky",
                  left: 0,
                  bgcolor: "background.paper",
                  whiteSpace: "nowrap",
                }}
              >
                <CustomTooltip
                  describeChild
                  show
                  size="small"
                  title={rc.description}
                >
                  <span>
                    {rc.name}
                    {rc.kind === "judge" ? (
                      <Typography
                        component="span"
                        typography="s3"
                        color="text.secondary"
                      >
                        {" "}
                        · <Term k="judge">judge</Term>
                      </Typography>
                    ) : null}
                  </span>
                </CustomTooltip>
              </Box>
              {lanes.map((l) => {
                const c = (l.trial?.checks ?? []).find((x) => x.key === rc.key);
                if (!c)
                  return (
                    <Box
                      key={l.id}
                      component="td"
                      sx={{ textAlign: "center", color: "text.disabled" }}
                    >
                      Not scored
                    </Box>
                  );
                const g = GLYPH[String(c.passed ?? null)];
                return (
                  <Box
                    key={l.id}
                    component="td"
                    sx={{ textAlign: "center", p: 0 }}
                  >
                    <Box
                      component="button"
                      type="button"
                      onClick={(e) =>
                        setPop({ el: e.currentTarget, lane: l, check: c, rc })
                      }
                      aria-label={`${rc.name} ${g.word} on ${l.blindName ?? l.label}`}
                      sx={{
                        all: "unset",
                        cursor: "pointer",
                        px: 1,
                        py: 0.25,
                        borderRadius: 1,
                        display: "inline-flex",
                        gap: 0.5,
                        alignItems: "center",
                        "&:hover": { bgcolor: "action.hover" },
                        "&:focus-visible": {
                          outline: "2px solid",
                          outlineColor: "primary.main",
                        },
                      }}
                    >
                      <Box
                        component="span"
                        sx={{ color: g.color, fontWeight: 700 }}
                      >
                        {g.g}
                      </Box>
                      {Number.isFinite(c.score) ? (
                        <Box
                          component="span"
                          sx={{
                            ...NUM_SX,
                            color: "text.secondary",
                            typography: "s3",
                          }}
                        >
                          {fmtScore(c.score)}
                        </Box>
                      ) : null}
                    </Box>
                  </Box>
                );
              })}
            </tr>
          ))}
        </tbody>
      </Box>
      <Popover
        open={Boolean(pop)}
        anchorEl={pop?.el}
        onClose={() => setPop(null)}
        anchorOrigin={{ vertical: "bottom", horizontal: "center" }}
        transformOrigin={{ vertical: "top", horizontal: "center" }}
      >
        {pop ? (
          <Box sx={{ p: 1.5, maxWidth: 360 }}>
            <Typography typography="s2" fontWeight={700}>
              {pop.rc.name} · {pop.lane.blindName ?? pop.lane.label} ·{" "}
              {GLYPH[String(pop.check.passed ?? null)].word}
            </Typography>
            {pop.check.evidence ? (
              <Typography typography="s2" sx={{ mt: 0.5 }}>
                {pop.check.evidence}
              </Typography>
            ) : null}
            {pop.check.reason && pop.check.reason !== pop.check.evidence ? (
              <Typography
                typography="s2"
                color="text.secondary"
                sx={{ mt: 0.5 }}
              >
                {pop.check.reason}
              </Typography>
            ) : null}
            {pop.check.judge_model ? (
              <Typography
                typography="s3"
                color="text.secondary"
                sx={{ mt: 0.5 }}
              >
                Judged by {pop.check.judge_model}
                {Number.isFinite(pop.check.score)
                  ? ` · score ${fmtScore(pop.check.score)}`
                  : ""}
              </Typography>
            ) : null}
            {Number.isFinite(pop.check.cause_step_n) ? (
              <Button
                size="small"
                sx={{ mt: 1, px: 0 }}
                onClick={() => (
                  onGoToStep(pop.lane.id, pop.check.cause_step_n), setPop(null)
                )}
              >
                Go to step {pop.check.cause_step_n}
              </Button>
            ) : null}
          </Box>
        ) : null}
      </Popover>
    </Box>
  );
}
ChecksMatrix.propTypes = {
  rowChecks: PropTypes.array.isRequired,
  lanes: PropTypes.array.isRequired,
  onGoToStep: PropTypes.func.isRequired,
};

// ----------------------------------------------------------------- notes

const initials = (name) =>
  String(name ?? "?")
    .split(/\s+/)
    .map((w) => w[0])
    .slice(0, 2)
    .join("")
    .toUpperCase();

/** Shared notes: author, relative time, optional `on step 3`; `A` focuses, ⌘↵ saves. */
export function NotesPanel({
  notes,
  onAdd,
  onDelete,
  inputRef,
  anchorStep,
  laneLabelOf,
  me,
  busy,
}) {
  const [text, setText] = useState("");
  const [attach, setAttach] = useState(true);
  const save = () => {
    if (!text.trim()) return;
    onAdd(text.trim(), attach ? anchorStep : null);
    setText("");
  };
  return (
    <Box
      sx={{
        p: 1.25,
        display: "flex",
        flexDirection: "column-reverse",
        gap: 1.25,
        minHeight: 0,
      }}
    >
      <Stack spacing={1} sx={{ flex: 1, minWidth: 0 }}>
        {!notes.length ? (
          <Typography typography="s2" color="text.secondary">
            No notes on this task yet. Notes are shared with everyone who opens
            this task.
          </Typography>
        ) : null}
        {notes.map((n) => (
          <Stack key={n.id} direction="row" spacing={1} alignItems="flex-start">
            <Avatar
              sx={{
                width: 24,
                height: 24,
                typography: "s3",
                bgcolor: "primary.lighter",
                color: "primary.darker",
              }}
            >
              {initials(n.author)}
            </Avatar>
            <Box sx={{ minWidth: 0, flex: 1 }}>
              <Stack direction="row" spacing={0.75} alignItems="baseline">
                <Typography typography="s2" fontWeight={600}>
                  {n.author}
                </Typography>
                <RelativeTime
                  iso={n.at}
                  by={n.author}
                  sx={{ typography: "s3", color: "text.secondary" }}
                />
                {Number.isFinite(n.step_n) ? (
                  <Typography typography="s3" color="text.secondary">
                    on {n.run_id ? `${laneLabelOf(n.run_id)} ` : ""}step{" "}
                    {n.step_n}
                  </Typography>
                ) : null}
                <Box sx={{ flex: 1 }} />
                {n.author === me ? (
                  <CustomTooltip
                    describeChild
                    show
                    size="small"
                    title="Delete this note (you can undo)"
                  >
                    <IconButton
                      size="small"
                      onClick={() => onDelete(n)}
                      aria-label="Delete note"
                      sx={{ p: 0.25 }}
                    >
                      <Iconify icon="mdi:trash-can-outline" width={15} />
                    </IconButton>
                  </CustomTooltip>
                ) : null}
              </Stack>
              <Typography
                typography="s2"
                sx={{ whiteSpace: "pre-wrap", wordBreak: "break-word" }}
              >
                {n.text}
              </Typography>
            </Box>
          </Stack>
        ))}
      </Stack>
      <Stack spacing={0.5} sx={{ width: "100%", flexShrink: 0 }}>
        <InputBase
          inputRef={inputRef}
          multiline
          minRows={3}
          maxRows={6}
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
              e.preventDefault();
              save();
            }
            if (e.key === "Escape") {
              e.stopPropagation();
              e.currentTarget.blur();
            }
          }}
          placeholder="Add a note for your team…"
          inputProps={{ "aria-label": "Add a note" }}
          sx={{
            typography: "s2",
            p: 1,
            border: "1px solid",
            borderColor: "divider",
            borderRadius: 1,
            alignItems: "flex-start",
          }}
        />
        <Stack direction="row" alignItems="center" spacing={1}>
          {anchorStep ? (
            <FormControlLabel
              control={
                <Checkbox
                  size="small"
                  checked={attach}
                  onChange={(e) => setAttach(e.target.checked)}
                />
              }
              label={
                <Typography typography="s3">
                  On {anchorStep.label} step {anchorStep.stepN}
                </Typography>
              }
              sx={{ ml: 0, mr: 0 }}
            />
          ) : null}
          <Box sx={{ flex: 1 }} />
          <Typography typography="s3" color="text.disabled">
            <KeyCap>⌘↵</KeyCap>
          </Typography>
          <Button
            size="small"
            variant="contained"
            onClick={save}
            disabled={!text.trim() || busy}
          >
            Add note
          </Button>
        </Stack>
      </Stack>
    </Box>
  );
}
NotesPanel.propTypes = {
  notes: PropTypes.array.isRequired,
  onAdd: PropTypes.func.isRequired,
  onDelete: PropTypes.func.isRequired,
  inputRef: PropTypes.object,
  anchorStep: PropTypes.object,
  laneLabelOf: PropTypes.func.isRequired,
  me: PropTypes.string,
  busy: PropTypes.bool,
};
