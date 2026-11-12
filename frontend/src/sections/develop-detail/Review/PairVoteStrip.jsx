import React from "react";
import PropTypes from "prop-types";
import { Box, Button, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import KeyCap from "src/sections/common/ux/KeyCap";

/**
 * HO results R5 (W4-3): score the pair from the Compare tab. `1` / `2` /
 * `3` vote baseline better / tie / candidate better (the Review tab's
 * keys), the same key again clears it, J / K move rows. `vote` is
 * usePairVote's result.
 */
const PairVoteStrip = ({ vote }) => {
  if (!vote?.active) return null;
  return (
    <Stack
      direction="row"
      alignItems="center"
      gap={1}
      flexWrap="wrap"
      data-testid="compare-pair-vote"
      data-choice={vote.myChoice ?? ""}
      sx={{
        px: 1.25,
        py: 0.75,
        border: "1px solid",
        borderColor: "divider",
        borderRadius: 0.5,
      }}
    >
      <Typography typography="s2" fontWeight={600} sx={{ mr: 0.5 }}>
        Which answer is better?
      </Typography>
      {vote.options.map((o) => {
        const on = vote.myChoice === o.choice;
        return (
          <Button
            key={o.key}
            size="small"
            variant={on ? "contained" : "outlined"}
            color={on ? "primary" : "inherit"}
            onClick={() => vote.vote(o.choice)}
            aria-pressed={on}
            data-testid={`compare-vote-${o.choice}`}
            sx={{ height: 26, typography: "s2", gap: 0.75, px: 1 }}
          >
            <KeyCap sx={{ height: 18, minWidth: 18, px: 0.5 }}>{o.key}</KeyCap>
            {o.label}
          </Button>
        );
      })}
      <Box sx={{ flex: 1 }} />
      {vote.myChoice ? (
        <Stack direction="row" alignItems="center" gap={0.5}>
          <Iconify
            icon={vote.saved ? "mdi:check" : "mdi:progress-clock"}
            width={14}
            sx={{ color: vote.saved ? "green.600" : "text.disabled" }}
          />
          <Typography typography="s3" color="text.secondary" noWrap>
            {vote.saved ? `Saved · ${vote.text}` : "Saving…"}
          </Typography>
        </Stack>
      ) : (
        <Typography typography="s3" color="text.disabled" noWrap>
          J / K: next / previous row
        </Typography>
      )}
    </Stack>
  );
};

PairVoteStrip.propTypes = { vote: PropTypes.object };

export default PairVoteStrip;
