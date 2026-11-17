import React from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Typography from "@mui/material/Typography";

import { formatDate, mondayOf } from "../lib/format";

import { EntryGrid, EntryGridSkeleton, hasDemo } from "./EntryCard";
import { HubError } from "./HubStates";

const SECTION_SX = { display: "flex", flexDirection: "column", gap: 1.5 };

function Heading({ children }) {
  return (
    <Typography
      id="hub-picks-title"
      component="h2"
      typography="m2"
      fontWeight="fontWeightSemiBold"
      color="text.primary"
    >
      {children}
    </Typography>
  );
}

Heading.propTypes = { children: PropTypes.node };

// When the picks were made: "this week" for the current week (picks are per
// UTC week, SPEC §6.4), else the week's date; the exact week and its time
// zone on hover.
function PickedWhen({ week }) {
  const date = formatDate(week);
  return (
    <Typography typography="s2" color="text.tertiary">
      {"Picked by Future AGI maintainers "}
      <Box
        component="time"
        dateTime={week}
        title={`Week starting ${date} (UTC)`}
      >
        {week === mondayOf() ? "this week" : `for the week of ${date}`}
      </Box>
    </Typography>
  );
}

PickedWhen.propTypes = { week: PropTypes.string.isRequired };

// The picks with a recorded demo first, then the others, each group in the
// maintainers' order.
const demosFirst = (picks) => [
  ...picks.filter(hasDemo),
  ...picks.filter((pick) => !hasDemo(pick)),
];

// "Start here" on the Hub home: this week's maintainer picks (at most 6),
// each led by the maintainer's short why, with what its recorded demo
// printed when it has one, else what it does, its tags and how it runs. The
// picks with a demo come first, so a wide first card shows one. The picks
// fill their rows (EntryGrid `fill`): one pick is one wide card, two are
// halves, three thirds and four quarters where four fit, so the row is
// never half empty, and each takes its row's height, so the feet line up.
// The page leaves them out of the grid below. While they load, placeholders
// in the cards' shape and no heading, so a week with no picks never shows
// "Start here" and then takes it away; if they fail, what failed and Try
// again; with no picks this week, nothing is drawn.
export default function PicksStrip({ picks, loading = false, error, onRetry }) {
  if (error) {
    return (
      <Box
        component="section"
        aria-labelledby="hub-picks-title"
        data-testid="hub-picks-error"
        sx={SECTION_SX}
      >
        <Heading>Start here</Heading>
        <HubError
          compact
          title="The maintainer picks did not load"
          error={error}
          onRetry={onRetry}
        />
      </Box>
    );
  }
  if (loading) {
    return (
      <Box data-testid="hub-picks-loading">
        <EntryGridSkeleton
          count={3}
          columns={3}
          label="Loading this week's picks"
          testId="hub-picks-skeleton"
        />
      </Box>
    );
  }
  if (!picks?.length) return null;
  const week = picks[0]?.pick?.week;
  return (
    <Box
      component="section"
      aria-labelledby="hub-picks-title"
      data-testid="hub-picks"
      sx={SECTION_SX}
    >
      <Box
        sx={{
          display: "flex",
          alignItems: "baseline",
          flexWrap: "wrap",
          columnGap: 1.25,
          rowGap: 0.25,
        }}
      >
        <Heading>Start here</Heading>
        {week && <PickedWhen week={week} />}
      </Box>
      <EntryGrid
        entries={demosFirst(picks.slice(0, 6))}
        variant="pick"
        fill
        testId="hub-picks-grid"
      />
    </Box>
  );
}

PicksStrip.propTypes = {
  picks: PropTypes.array,
  loading: PropTypes.bool,
  error: PropTypes.object,
  onRetry: PropTypes.func,
};
