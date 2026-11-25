import PropTypes from "prop-types";
import { useState } from "react";
import { Box, Link, Stack, Typography } from "@mui/material";
import { Section } from "./trialChangesParts";
import {
  QUIET as quiet,
  TRIAL_CHANGES_COPY as C,
  saidText,
} from "./trialChanges.constants";

/* What the actors wrote before the agent started, as a short chat: who
   and what they said (or did). Folded by default; the server's sentence
   stays on each row as its label. */
export function PreRunSection({ entries }) {
  const [open, setOpen] = useState(false);
  return (
    <Section
      title={C.preRunHeading}
      count={entries.length}
      caption={C.preRunCaption}
      hint={C.preRunAbout}
    >
      <Link
        component="button"
        type="button"
        underline="hover"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        sx={{ typography: "s3", fontWeight: "fontWeightMedium" }}
      >
        {C.preRunToggle(entries.length, open)}
      </Link>
      {open && (
        <Stack
          spacing={0.75}
          sx={{
            mt: 0.75,
            pl: 1.25,
            borderLeft: "2px solid",
            borderColor: "divider",
          }}
        >
          {entries.map((one, index) => {
            const said = saidText(one.arguments);
            const channel = one.arguments?.channel || one.channel;
            return (
              <Box key={`pre-${index}`} aria-label={one.line}>
                <Stack direction="row" spacing={0.75} alignItems="baseline">
                  <Typography
                    sx={{ typography: "s3", fontWeight: "fontWeightSemiBold" }}
                  >
                    {one.name}
                  </Typography>
                  {channel && <Typography sx={quiet}>{channel}</Typography>}
                </Stack>
                <Typography sx={{ ...quiet, color: "text.primary" }}>
                  {said || (one.action ? C.preRunDid(one.action) : one.line)}
                </Typography>
              </Box>
            );
          })}
        </Stack>
      )}
    </Section>
  );
}
PreRunSection.propTypes = { entries: PropTypes.array.isRequired };
