import React from "react";
import PropTypes from "prop-types";
import { Box, Checkbox, Stack, Typography } from "@mui/material";
import { candidateFrom } from "./agreementModel";

const clauseText = (c) =>
  c.kind === "remove" ? c.label ?? `Remove: "${c.clause}"` : c.clause;

/**
 * Improve judge step 2: the rule-based candidate edits (each with where it
 * came from) to tick, and the diff of the ticked ones naming their sources.
 */
const ImproveCandidates = ({ candidates, ticked, onTick, edits }) => (
  <>
    {candidates.length ? (
      candidates.map((c, i) => (
        <Stack
          key={`${c.kind}-${c.clause}`}
          direction="row"
          gap={1}
          alignItems="flex-start"
          data-testid={`w7-cand-${i}`}
        >
          <Checkbox
            size="small"
            checked={ticked.has(i)}
            onChange={() => {
              const next = new Set(ticked);
              if (next.has(i)) next.delete(i);
              else next.add(i);
              onTick(next);
            }}
            inputProps={{ "aria-label": clauseText(c) }}
          />
          <Box>
            <Typography typography="s2">{clauseText(c)}</Typography>
            <Typography typography="s3" color="text.secondary">
              {candidateFrom(c)}
            </Typography>
          </Box>
        </Stack>
      ))
    ) : (
      <Typography typography="s2" color="text.secondary">
        The drafting rows give no candidate edit: no reviewer note on a false
        pass, no clause behind a false fail, no shared wording.
      </Typography>
    )}
    {edits.length ? (
      <Box
        data-testid="w7-diff"
        sx={{
          p: 1,
          border: "1px solid",
          borderColor: "divider",
          borderRadius: 1,
        }}
      >
        {edits.map((e) => (
          <Typography
            key={e.clause}
            typography="s2"
            color={e.kind === "add" ? "success.main" : "error.main"}
            sx={{ fontFamily: "monospace" }}
          >
            {e.kind === "add" ? "+ " : "− "}
            {e.clause}
            <Typography
              component="span"
              typography="s3"
              color="text.secondary"
              sx={{ fontFamily: "inherit", ml: 1 }}
            >
              ({candidateFrom(e)})
            </Typography>
          </Typography>
        ))}
      </Box>
    ) : null}
  </>
);

ImproveCandidates.propTypes = {
  candidates: PropTypes.array.isRequired,
  ticked: PropTypes.instanceOf(Set).isRequired,
  onTick: PropTypes.func.isRequired,
  edits: PropTypes.array.isRequired,
};

export default ImproveCandidates;
