import PropTypes from "prop-types";
import { useState } from "react";
import { Box, Stack, Typography, Button } from "@mui/material";

import Iconify from "src/components/iconify";

/**
 * One goal the run's evals say broke: how often, what the eval expected, and
 * the ways it broke. Every number is a count of calls, so each matches what its
 * View link opens and the rows add up to the card's.
 */

const VISIBLE_WAYS = 3;

const sentence = (text) =>
  text ? text.charAt(0).toUpperCase() + text.slice(1) : "";

const wayShape = PropTypes.shape({
  id: PropTypes.string,
  title: PropTypes.string,
  phrase: PropTypes.string,
  callIds: PropTypes.arrayOf(PropTypes.string),
});

function CountRow({ count, text, onView }) {
  return (
    <Stack
      direction="row"
      alignItems="baseline"
      spacing={1.25}
      sx={{ py: 0.5 }}
    >
      <Typography
        sx={{
          typography: "s2",
          fontWeight: 700,
          width: 24,
          textAlign: "right",
          flexShrink: 0,
          fontVariantNumeric: "tabular-nums",
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
      <Button
        size="small"
        onClick={onView}
        sx={{ minWidth: 0, typography: "s3", flexShrink: 0 }}
      >
        View
      </Button>
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
    <Box
      sx={{
        p: 2,
        borderRadius: 1,
        border: "1px solid",
        borderColor: "divider",
      }}
    >
      <Stack direction="row" alignItems="baseline" spacing={1.5}>
        <Typography
          sx={{ typography: "s1", fontWeight: 700, flex: 1, minWidth: 0 }}
        >
          {goal.label}
        </Typography>
        <Typography
          sx={{
            typography: "s2",
            fontWeight: 600,
            color: "error.main",
            flexShrink: 0,
            fontVariantNumeric: "tabular-nums",
          }}
        >
          Broke on {broken} of {goal.testedCalls} calls
        </Typography>
      </Stack>
      {goal.expected && (
        <Typography sx={{ typography: "s3", color: "text.secondary", mt: 0.5 }}>
          Passes when {goal.expected}
        </Typography>
      )}

      {(goal.ways.length > 0 || goal.unexplainedCallIds.length > 0) && (
        <Box sx={{ mt: 1.5 }}>
          <Typography
            sx={{
              typography: "s3",
              fontWeight: 700,
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
                  sx={{ typography: "s2", p: 0, minWidth: 0 }}
                >
                  {folded.length} other ways
                </Button>
              }
              onView={() => onViewCalls?.(foldedCallIds)}
            />
          )}
          {goal.unexplainedCallIds.length > 0 && (
            <CountRow
              count={goal.unexplainedCallIds.length}
              text="Not explained yet"
              onView={() => onViewCalls?.(goal.unexplainedCallIds)}
            />
          )}
        </Box>
      )}

      <Stack direction="row" justifyContent="flex-end" sx={{ mt: 1.25 }}>
        <Button
          size="small"
          variant="outlined"
          onClick={() => onViewCalls?.(goal.brokenCallIds)}
          startIcon={<Iconify icon="solar:eye-linear" width={15} />}
          sx={{ typography: "s3", fontWeight: 600 }}
        >
          View {broken} {broken === 1 ? "call" : "calls"}
        </Button>
      </Stack>
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
    unexplainedCallIds: PropTypes.arrayOf(PropTypes.string),
  }).isRequired,
  onViewCalls: PropTypes.func,
};

/** An agent issue on a single call that no broken goal covers. */
export function OneOffRow({ way, onViewCalls }) {
  const title = sentence(way.phrase);
  return (
    <Box sx={{ py: 1.25, borderTop: "1px solid", borderColor: "divider" }}>
      <Stack direction="row" alignItems="baseline" spacing={1.5}>
        <Typography
          sx={{ typography: "s2", fontWeight: 700, flex: 1, minWidth: 0 }}
        >
          {title}
        </Typography>
        <Button
          size="small"
          onClick={() => onViewCalls?.(way.callIds)}
          sx={{ minWidth: 0, typography: "s3", flexShrink: 0 }}
        >
          View call
        </Button>
      </Stack>
      {way.title && way.title.toLowerCase() !== title.toLowerCase() && (
        <Typography
          sx={{ typography: "s2", color: "text.secondary", mt: 0.25 }}
        >
          {way.title}
        </Typography>
      )}
    </Box>
  );
}
OneOffRow.propTypes = {
  way: wayShape.isRequired,
  onViewCalls: PropTypes.func,
};
