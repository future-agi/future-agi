import React, { useId } from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import FormControlLabel from "@mui/material/FormControlLabel";
import Link from "@mui/material/Link";
import Radio from "@mui/material/Radio";
import RadioGroup from "@mui/material/RadioGroup";
import Typography from "@mui/material/Typography";

import { OTHER_FLAGS, findingPlace } from "../lib/decision";

// A titled part of the review page: an h2, an optional count or note beside
// it, one line saying what to do, then the content.
export function ReviewSection({
  title,
  count,
  note,
  description,
  children,
  testId,
}) {
  const titleId = useId();
  return (
    <Box
      component="section"
      aria-labelledby={titleId}
      data-testid={testId}
      sx={{ display: "flex", flexDirection: "column", gap: 1.5, minWidth: 0 }}
    >
      <Box>
        <Box
          sx={{
            display: "flex",
            alignItems: "center",
            gap: 1,
            flexWrap: "wrap",
          }}
        >
          <Typography
            component="h2"
            id={titleId}
            typography="m3"
            fontWeight="fontWeightSemiBold"
            color="text.primary"
          >
            {title}
          </Typography>
          {count !== undefined && (
            <Box
              component="span"
              sx={{
                px: 0.75,
                borderRadius: 0.75,
                bgcolor: "action.selected",
                color: "text.secondary",
                typography: "s2",
                fontWeight: 500,
                lineHeight: "18px",
              }}
            >
              {count}
            </Box>
          )}
          {note && (
            <Typography component="span" typography="s2" color="text.secondary">
              {note}
            </Typography>
          )}
        </Box>
        {description && (
          <Typography typography="s1" color="text.secondary" sx={{ mt: 0.25 }}>
            {description}
          </Typography>
        )}
      </Box>
      {children}
    </Box>
  );
}

ReviewSection.propTypes = {
  title: PropTypes.node.isRequired,
  count: PropTypes.node,
  note: PropTypes.node,
  description: PropTypes.node,
  children: PropTypes.node,
  testId: PropTypes.string,
};

function Location({ finding, onOpenFile, files }) {
  const place = findingPlace(finding);
  const openable = Boolean(place && onOpenFile && files?.has(finding.file));
  return (
    <Box
      component="li"
      sx={{
        display: "grid",
        gridTemplateColumns: {
          xs: "minmax(0, 1fr)",
          sm: "minmax(120px, max-content) minmax(0, 1fr)",
        },
        columnGap: 2,
        rowGap: 0.25,
        px: 1.25,
        py: 1,
        borderRadius: 0.75,
        bgcolor: "background.neutral",
      }}
    >
      <Box sx={{ minWidth: 0 }}>
        {openable ? (
          <Link
            component="button"
            type="button"
            typography="s2"
            underline="hover"
            onClick={() => onOpenFile(finding.file, finding.line)}
            aria-label={`Open ${place} in Files`}
            sx={{
              fontFamily: "monospace",
              textAlign: "left",
              overflowWrap: "anywhere",
            }}
          >
            {place}
          </Link>
        ) : (
          <Typography
            typography="s2"
            color="text.secondary"
            sx={{ fontFamily: "monospace", overflowWrap: "anywhere" }}
          >
            {place || "No file named"}
          </Typography>
        )}
      </Box>
      <Box sx={{ minWidth: 0 }}>
        <Typography
          typography="s2"
          color="text.primary"
          sx={{ overflowWrap: "anywhere" }}
        >
          {finding.message || "The checker gave no message for this flag."}
        </Typography>
        <Typography
          typography="s2"
          color="text.secondary"
          sx={{ fontFamily: "monospace", overflowWrap: "anywhere" }}
        >
          {finding.id}
        </Typography>
        {finding.fix && (
          <Typography typography="s2" color="text.secondary">
            {`Fix: ${finding.fix}`}
          </Typography>
        )}
      </Box>
    </Box>
  );
}

Location.propTypes = {
  finding: PropTypes.object.isRequired,
  onOpenFile: PropTypes.func,
  files: PropTypes.instanceOf(Set),
};

function FlagGroup({ group, answer, onAnswer, onOpenFile, files }) {
  const titleId = useId();
  const askId = useId();
  return (
    <Box
      component="section"
      aria-labelledby={titleId}
      data-testid="hub-flag-group"
      data-rule={group.key}
      data-answer={answer || undefined}
      sx={{
        border: "1px solid",
        borderColor:
          answer === "no"
            ? "status.warning.border"
            : answer === "yes"
              ? "status.success.border"
              : "divider",
        borderRadius: 1,
        bgcolor: "background.paper",
        p: 2,
        display: "flex",
        flexDirection: "column",
        gap: 1.5,
        minWidth: 0,
      }}
    >
      <Box
        sx={{ display: "flex", alignItems: "center", gap: 1, flexWrap: "wrap" }}
      >
        <Typography
          component="h3"
          id={titleId}
          typography="s1"
          fontWeight="fontWeightSemiBold"
          color="text.primary"
        >
          {group.title}
        </Typography>
        <Typography component="span" typography="s2" color="text.secondary">
          {`${group.flags.length} flag${group.flags.length === 1 ? "" : "s"}`}
        </Typography>
        {group.key !== OTHER_FLAGS && group.key !== group.title && (
          <Typography
            component="span"
            typography="s2"
            color="text.secondary"
            sx={{ fontFamily: "monospace" }}
          >
            {group.key}
          </Typography>
        )}
      </Box>
      <Box
        component="ul"
        sx={{ m: 0, p: 0, listStyle: "none", display: "grid", gap: 0.75 }}
      >
        {group.findings.map((finding) => (
          <Location
            key={finding.id}
            finding={finding}
            onOpenFile={onOpenFile}
            files={files}
          />
        ))}
      </Box>
      <Typography typography="s2" color="text.secondary">
        {group.hint}
      </Typography>
      {onAnswer && (
        <Box
          sx={{
            display: "flex",
            alignItems: "center",
            columnGap: 2,
            rowGap: 0.5,
            flexWrap: "wrap",
            pt: 1.25,
            borderTop: "1px solid",
            borderColor: "divider",
          }}
        >
          <Typography
            id={askId}
            typography="s1"
            fontWeight={500}
            color="text.primary"
          >
            Expected for this entry?
          </Typography>
          <RadioGroup
            row
            aria-labelledby={`${askId} ${titleId}`}
            value={answer || ""}
            onChange={(event) => onAnswer(group.key, event.target.value)}
            sx={{ columnGap: 1 }}
          >
            <FormControlLabel
              value="yes"
              control={
                <Radio
                  size="small"
                  inputProps={{
                    "data-testid": "hub-flag-ack",
                    "data-rule": group.key,
                    "data-flags": group.flags.join(" "),
                  }}
                />
              }
              label={
                <Typography typography="s1">Yes, it needs this</Typography>
              }
            />
            <FormControlLabel
              value="no"
              control={
                <Radio
                  size="small"
                  inputProps={{
                    "data-testid": "hub-flag-no",
                    "data-rule": group.key,
                  }}
                />
              }
              label={
                <Typography typography="s1">No, ask for a change</Typography>
              }
            />
          </RadioGroup>
        </Box>
      )}
    </Box>
  );
}

FlagGroup.propTypes = {
  group: PropTypes.object.isRequired,
  answer: PropTypes.oneOf(["yes", "no"]),
  onAnswer: PropTypes.func,
  onOpenFile: PropTypes.func,
  files: PropTypes.instanceOf(Set),
};

// The flags of a submission, one card per checker rule (lib/decision.js
// flagGroups): where each flag is, the rule's fixed hint, and, while the
// submission is in review, one Yes or No for the whole group.
export default function FlagGroups({
  groups,
  answers,
  onAnswer,
  onOpenFile,
  files,
}) {
  const total = groups.reduce((sum, group) => sum + group.flags.length, 0);
  return (
    <ReviewSection
      title="Flags"
      count={total || undefined}
      testId="hub-flag-groups"
      description={
        groups.length === 0
          ? undefined
          : onAnswer
            ? "Grouped by what the code does. For each group, say whether this entry needs it."
            : "Grouped by what the code does."
      }
    >
      {groups.length === 0 ? (
        <Typography typography="s1" color="text.secondary">
          The checks flagged nothing in this version.
        </Typography>
      ) : (
        groups.map((group) => (
          <FlagGroup
            key={group.key}
            group={group}
            answer={answers?.[group.key]}
            onAnswer={onAnswer}
            onOpenFile={onOpenFile}
            files={files}
          />
        ))
      )}
    </ReviewSection>
  );
}

FlagGroups.propTypes = {
  groups: PropTypes.array.isRequired,
  answers: PropTypes.object,
  onAnswer: PropTypes.func,
  onOpenFile: PropTypes.func,
  files: PropTypes.instanceOf(Set),
};
