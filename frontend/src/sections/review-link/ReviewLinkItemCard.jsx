import React from "react";
import PropTypes from "prop-types";
import { Box, Button, Stack, TextField, Typography } from "@mui/material";
import ValueViewer from "src/sections/common/ValueViewer/ValueViewer";
import TextDiff from "src/sections/common/ValueViewer/TextDiff";
import LabelInput from "src/sections/annotations/queues/annotate/label-input";
import { asText, isPairLabel } from "./reviewLinkModel";

function Section({ title, children, action }) {
  return (
    <Box>
      <Stack direction="row" alignItems="center" sx={{ mb: 0.5 }}>
        <Typography variant="overline" color="text.secondary" sx={{ flex: 1 }}>
          {title}
        </Typography>
        {action}
      </Stack>
      {children}
    </Box>
  );
}
Section.propTypes = {
  title: PropTypes.node,
  children: PropTypes.node,
  action: PropTypes.node,
};

function Fields({ values }) {
  const entries = Object.entries(values ?? {});
  if (!entries.length)
    return (
      <Typography variant="body2" color="text.disabled">
        Empty
      </Typography>
    );
  return (
    <Stack spacing={1}>
      {entries.map(([k, v]) => (
        <Box key={k}>
          <Typography variant="caption" color="text.secondary">
            {k}
          </Typography>
          <ValueViewer value={v} />
        </Box>
      ))}
    </Stack>
  );
}
Fields.propTypes = { values: PropTypes.object };

/** Pairwise choice for outsiders: always blind, keys 1 / 2 / 3. */
function PairChoice({ candidates, value, onChange }) {
  const sideOf = (slot) => candidates.find((c) => c.slot === slot)?.side;
  const options = [
    { key: "1", label: "Response 1 is better", stored: sideOf(1) },
    { key: "2", label: "Tie", stored: "Tie" },
    { key: "3", label: "Response 2 is better", stored: sideOf(2) },
  ];
  const picked = value?.selected?.[0];
  return (
    <Stack direction="row" gap={1} flexWrap="wrap">
      {options.map((o) => (
        <Button
          key={o.key}
          size="small"
          variant={picked === o.stored ? "contained" : "outlined"}
          onClick={() => onChange({ selected: [o.stored] })}
        >
          {o.label} ({o.key})
        </Button>
      ))}
    </Stack>
  );
}
PairChoice.propTypes = {
  candidates: PropTypes.array.isRequired,
  value: PropTypes.object,
  onChange: PropTypes.func.isRequired,
};

function Answers({ candidates, pairwise }) {
  return (
    <Box
      sx={{
        display: "grid",
        gap: 1.5,
        gridTemplateColumns: pairwise ? "1fr 1fr" : "1fr",
      }}
    >
      {candidates.map((c) => (
        <Box
          key={c.slot}
          sx={{
            p: 1.5,
            border: "1px solid",
            borderColor: "divider",
            borderRadius: 0.5,
            minWidth: 0,
          }}
        >
          {pairwise ? (
            <Typography variant="subtitle2" sx={{ mb: 0.5 }}>
              Response {c.slot}
            </Typography>
          ) : null}
          {c.status === "error" ? (
            <Typography variant="body2" color="error.main">
              {c.error || "This answer errored."}
            </Typography>
          ) : (
            <ValueViewer value={c.output} emptyLabel="Empty output" />
          )}
        </Box>
      ))}
    </Box>
  );
}
Answers.propTypes = {
  candidates: PropTypes.array.isRequired,
  pairwise: PropTypes.bool,
};

/** Expected (the reference) with `Correct` when the review allows it. */
function Expected({ content, expectedName, correction }) {
  const { correcting, corrected, setCorrecting, setCorrected } = correction;
  const now = asText(content?.expected?.[expectedName]);
  return (
    <Section
      title="Expected"
      action={
        expectedName && !correcting ? (
          <Button
            size="small"
            variant="outlined"
            onClick={() => {
              setCorrected(now);
              setCorrecting(true);
            }}
          >
            Correct
          </Button>
        ) : null
      }
    >
      {correcting ? (
        <Stack spacing={1}>
          <TextField
            multiline
            minRows={3}
            fullWidth
            size="small"
            value={corrected}
            onChange={(e) => setCorrected(e.target.value)}
            inputProps={{ "aria-label": `Corrected ${expectedName}` }}
          />
          {corrected !== now ? (
            <Box
              sx={{
                px: 1,
                py: 0.75,
                borderRadius: 0.5,
                bgcolor: "background.neutral",
              }}
            >
              <TextDiff from={now} to={corrected} />
            </Box>
          ) : null}
          <Button
            size="small"
            color="inherit"
            sx={{ alignSelf: "flex-start" }}
            onClick={() => setCorrecting(false)}
          >
            Keep the expected answer
          </Button>
        </Stack>
      ) : (
        <Fields values={content?.expected} />
      )}
    </Section>
  );
}
Expected.propTypes = {
  content: PropTypes.object,
  expectedName: PropTypes.string,
  correction: PropTypes.object.isRequired,
};

/**
 * One sampled item on the outside reviewer's page (W4-4): the input, the
 * answer or answers to judge (always blind), the expected answer with
 * `Correct`, and the labels (label-input.jsx, the queue's own inputs).
 */
export default function ReviewLinkItemCard({
  content,
  labels,
  values,
  setValues,
  notes,
  setNotes,
  expectedName,
  correction,
}) {
  const pairwise = content?.mode === "pairwise";
  const setOne = (id) => (v) => setValues((cur) => ({ ...cur, [id]: v }));
  return (
    <>
      <Section title="Input">
        <Fields values={content?.input} />
      </Section>
      <Section title={pairwise ? "Which reply is better?" : "Answer"}>
        <Answers candidates={content?.candidates ?? []} pairwise={pairwise} />
      </Section>
      <Expected
        content={content}
        expectedName={expectedName}
        correction={correction}
      />
      <Section title="Your answer">
        <Stack spacing={1.25}>
          {labels.map((l, i) =>
            isPairLabel(l) && pairwise ? (
              <PairChoice
                key={l.id}
                candidates={content.candidates}
                value={values[l.id]}
                onChange={setOne(l.id)}
              />
            ) : (
              <LabelInput
                key={l.id}
                label={l}
                index={i}
                value={values[l.id] ?? null}
                onChange={setOne(l.id)}
                labelNotes={notes[l.id] ?? ""}
                onLabelNotesChange={(t) =>
                  setNotes((cur) => ({ ...cur, [l.id]: t }))
                }
              />
            ),
          )}
        </Stack>
      </Section>
    </>
  );
}

ReviewLinkItemCard.propTypes = {
  content: PropTypes.object,
  labels: PropTypes.array.isRequired,
  values: PropTypes.object.isRequired,
  setValues: PropTypes.func.isRequired,
  notes: PropTypes.object.isRequired,
  setNotes: PropTypes.func.isRequired,
  expectedName: PropTypes.string,
  correction: PropTypes.shape({
    correcting: PropTypes.bool,
    corrected: PropTypes.string,
    setCorrecting: PropTypes.func,
    setCorrected: PropTypes.func,
  }).isRequired,
};
