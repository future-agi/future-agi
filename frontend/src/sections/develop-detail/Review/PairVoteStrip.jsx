import React, { useId } from "react";
import PropTypes from "prop-types";
import { Box, Button, Stack, Typography } from "@mui/material";

/**
 * The pair's votes in the Compare tab (baseline
 * better / tie / candidate better), J / K move rows. Saving reviews isn't
 * available in this release: the votes stay on screen turned off, show no
 * key, and are described by the sentence the strip shows once. `vote` is
 * usePairVote's result.
 */
const PairVoteStrip = ({ vote }) => {
  const unavailableId = useId();
  if (!vote?.active) return null;
  return (
    <Stack
      gap={0.5}
      data-testid="compare-pair-vote"
      sx={{
        px: 1.25,
        py: 0.75,
        border: "1px solid",
        borderColor: "divider",
        borderRadius: 0.5,
      }}
    >
      <Stack direction="row" alignItems="center" gap={1} flexWrap="wrap">
        <Typography typography="s2" fontWeight={600} sx={{ mr: 0.5 }}>
          Which answer is better?
        </Typography>
        {vote.options.map((o) => (
          <Button
            key={o.key}
            size="small"
            variant="outlined"
            color="inherit"
            disabled
            aria-describedby={unavailableId}
            data-testid={`compare-vote-${o.choice}`}
            sx={{ height: 26, typography: "s2", px: 1 }}
          >
            {o.label}
          </Button>
        ))}
        <Box sx={{ flex: 1 }} />
        <Typography typography="s3" color="text.disabled" noWrap>
          J / K: next / previous row
        </Typography>
      </Stack>
      <Typography id={unavailableId} typography="s3" color="text.secondary">
        Saving reviews isn&apos;t available in this release.
      </Typography>
    </Stack>
  );
};

PairVoteStrip.propTypes = { vote: PropTypes.object };

export default PairVoteStrip;
