import PropTypes from "prop-types";
import { useState } from "react";
import { Box, Stack, Typography, Button, ButtonBase } from "@mui/material";

import Iconify from "src/components/iconify";

/**
 * One goal the run's evals say broke: how often, what the eval expected, and
 * the ways it broke that have findings. Every number is a count of calls,
 * and each View link opens the calls included in its count.
 */

const VISIBLE_WAYS = 3;
// An opened one-off group lists this many explanations before "Show all".
const VISIBLE_DETAILS = 5;

const sentence = (text) =>
  text ? text.charAt(0).toUpperCase() + text.slice(1) : "";

const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;

const num = { fontVariantNumeric: "tabular-nums" };

const wayShape = PropTypes.shape({
  id: PropTypes.string,
  title: PropTypes.string,
  phrase: PropTypes.string,
  callIds: PropTypes.arrayOf(PropTypes.string),
});

// A quiet text action: secondary until hovered, so a column of them doesn't
// compete with the findings they sit next to.
function ViewAction({ label, onClick }) {
  return (
    <Button
      size="small"
      onClick={onClick}
      sx={{
        minWidth: 0,
        flexShrink: 0,
        px: 0.75,
        color: "text.secondary",
        "&:hover": { color: "text.primary", bgcolor: "action.hover" },
      }}
    >
      {label}
    </Button>
  );
}
ViewAction.propTypes = {
  label: PropTypes.string.isRequired,
  onClick: PropTypes.func,
};

function CountRow({ count, text, onView }) {
  return (
    <Stack
      direction="row"
      alignItems="center"
      spacing={1.25}
      sx={{ minHeight: 30 }}
    >
      <Typography
        sx={{
          typography: "s2",
          fontWeight: 600,
          width: 20,
          textAlign: "right",
          flexShrink: 0,
          ...num,
        }}
      >
        {count}
      </Typography>
      <Typography
        component="div"
        sx={{ typography: "s2", flex: 1, minWidth: 0 }}
      >
        {text}
      </Typography>
      <ViewAction label="View" onClick={onView} />
    </Stack>
  );
}
CountRow.propTypes = {
  count: PropTypes.number,
  text: PropTypes.node,
  onView: PropTypes.func,
};

// Up to this many ways show in full; past it the rest fold into one row whose
// count still makes the rows add up to the card's.
const FOLD_AFTER = 5;

export default function GoalCard({ goal, onViewCalls }) {
  const [expanded, setExpanded] = useState(false);
  const folds = !expanded && goal.ways.length > FOLD_AFTER;
  const ways = folds ? goal.ways.slice(0, VISIBLE_WAYS) : goal.ways;
  const folded = goal.ways.slice(ways.length);
  const foldedCallIds = [...new Set(folded.flatMap((way) => way.callIds))];
  const broken = goal.brokenCallIds.length;

  return (
    <Box sx={{ px: 2, py: 1.75 }}>
      <Typography sx={{ typography: "s1", fontWeight: 500 }}>
        {goal.label}
      </Typography>
      {goal.expected && (
        <Typography
          sx={{ typography: "s3", color: "text.secondary", mt: 0.25 }}
        >
          Passes when {goal.expected}
        </Typography>
      )}

      <Stack direction="row" alignItems="center" spacing={1} sx={{ mt: 0.75 }}>
        <Typography
          sx={{
            typography: "s3",
            fontWeight: 500,
            color: "status.danger.fg",
            flex: 1,
            ...num,
          }}
        >
          Broke on {broken} of {goal.testedCalls} calls
        </Typography>
        <Button
          size="small"
          variant="outlined"
          onClick={() => onViewCalls?.(goal.brokenCallIds)}
          startIcon={<Iconify icon="solar:eye-linear" width={14} />}
          sx={{ flexShrink: 0 }}
        >
          View {plural(broken, "call")}
        </Button>
      </Stack>

      {goal.ways.length > 0 && (
        <Box
          sx={{
            mt: 1.25,
            pt: 1,
            borderTop: "1px solid",
            borderColor: "divider",
          }}
        >
          <Typography
            sx={{
              typography: "s3",
              color: "text.subtitle",
              mb: 0.25,
            }}
          >
            Ways it broke
          </Typography>
          {ways.map((way) => (
            <CountRow
              key={way.id}
              count={way.callIds.length}
              text={sentence(way.phrase)}
              onView={() => onViewCalls?.(way.callIds)}
            />
          ))}
          {folded.length > 0 && (
            <CountRow
              count={foldedCallIds.length}
              text={
                <Button
                  size="small"
                  onClick={() => setExpanded(true)}
                  sx={{ p: 0, minWidth: 0 }}
                >
                  {folded.length} other ways
                </Button>
              }
              onView={() => onViewCalls?.(foldedCallIds)}
            />
          )}
        </Box>
      )}
    </Box>
  );
}

GoalCard.propTypes = {
  goal: PropTypes.shape({
    goal: PropTypes.string,
    label: PropTypes.string,
    expected: PropTypes.string,
    brokenCallIds: PropTypes.arrayOf(PropTypes.string),
    testedCalls: PropTypes.number,
    ways: PropTypes.arrayOf(wayShape),
  }).isRequired,
  onViewCalls: PropTypes.func,
};

/**
 * One-off agent issues that share a name, as one row: how many calls, the
 * name, and the calls. Each call's own explanation opens under it.
 */
export function OneOffGroup({ phrase, ways, onViewCalls }) {
  const [open, setOpen] = useState(false);
  const [showAll, setShowAll] = useState(false);
  const callIds = [...new Set(ways.flatMap((way) => way.callIds))];
  const details = ways.filter(
    (way) => way.title && way.title.toLowerCase() !== phrase.toLowerCase(),
  );

  return (
    <Box>
      <Stack
        direction="row"
        alignItems="center"
        spacing={1.25}
        sx={{ minHeight: 36, px: 2 }}
      >
        <Typography
          sx={{
            typography: "s2",
            fontWeight: 600,
            width: 20,
            textAlign: "right",
            flexShrink: 0,
            ...num,
          }}
        >
          {ways.length}
        </Typography>
        <ButtonBase
          disabled={!details.length}
          onClick={() => setOpen((v) => !v)}
          aria-expanded={open}
          sx={{
            flex: 1,
            minWidth: 0,
            justifyContent: "flex-start",
            gap: 0.5,
            borderRadius: "4px",
            textAlign: "left",
          }}
        >
          <Typography sx={{ typography: "s2", minWidth: 0 }} noWrap>
            {phrase}
          </Typography>
          {details.length > 0 && (
            <Iconify
              icon="solar:alt-arrow-down-linear"
              width={14}
              sx={{
                color: "text.disabled",
                flexShrink: 0,
                transition: "transform 150ms",
                transform: open ? "rotate(180deg)" : "none",
              }}
            />
          )}
        </ButtonBase>
        <ViewAction
          label={
            callIds.length === 1 ? "View call" : `View ${callIds.length} calls`
          }
          onClick={() => onViewCalls?.(callIds)}
        />
      </Stack>
      {open && (
        <Box sx={{ pl: 5.75, pr: 2, pb: 1 }}>
          {(showAll ? details : details.slice(0, VISIBLE_DETAILS)).map(
            (way) => (
              <Stack
                key={way.id}
                direction="row"
                alignItems="flex-start"
                spacing={1}
                sx={{
                  py: 0.75,
                  borderTop: "1px solid",
                  borderColor: "divider",
                }}
              >
                <Typography
                  sx={{
                    typography: "s3",
                    color: "text.secondary",
                    flex: 1,
                    minWidth: 0,
                  }}
                >
                  {way.title}
                </Typography>
                {ways.length > 1 && (
                  <ViewAction
                    label="View"
                    onClick={() => onViewCalls?.(way.callIds)}
                  />
                )}
              </Stack>
            ),
          )}
          {!showAll && details.length > VISIBLE_DETAILS && (
            <Button
              size="small"
              onClick={() => setShowAll(true)}
              sx={{ mt: 0.25, ml: -0.75, minWidth: 0 }}
            >
              Show all {details.length}
            </Button>
          )}
        </Box>
      )}
    </Box>
  );
}
OneOffGroup.propTypes = {
  phrase: PropTypes.string.isRequired,
  ways: PropTypes.arrayOf(wayShape).isRequired,
  onViewCalls: PropTypes.func,
};

/** Group one-off issues by their name, most frequent first. */
export function groupOneOffs(oneOffs) {
  const groups = new Map();
  oneOffs.forEach((way) => {
    const phrase = sentence(way.phrase || way.title || "Other");
    const key = phrase.toLowerCase();
    if (!groups.has(key)) groups.set(key, { phrase, ways: [] });
    groups.get(key).ways.push(way);
  });
  return [...groups.values()].sort((a, b) => b.ways.length - a.ways.length);
}
