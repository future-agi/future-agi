import PropTypes from "prop-types";
import { Box, Stack, Typography } from "@mui/material";
import { Card, Pill } from "./bits";
import { MONO } from "./format";
import { PARTICIPANTS_COPY } from "./participants.constants";

/** The agent version's workers, read-only: name, how it reaches the world, and the tools it may use. */
export default function ParticipantsCard({ participants }) {
  if (!participants.length) return null;
  return (
    <Card title={PARTICIPANTS_COPY.title(participants.length)} action={null}>
      {participants.map((one) => {
        const tools = one.allowed_tools.length
          ? one.allowed_tools
          : one.tools || [];
        return (
          <Stack
            key={one.id}
            data-testid="participant-row"
            spacing={0.5}
            sx={{
              px: 1.5,
              py: 1,
              borderTop: "1px solid",
              borderColor: "divider",
              "&:first-of-type": { borderTop: "none" },
            }}
          >
            <Stack direction="row" alignItems="center" spacing={0.75}>
              <Typography noWrap sx={{ typography: "s1", fontWeight: 500 }}>
                {one.name || one.id}
              </Typography>
              {one.binding ? (
                <Pill>{PARTICIPANTS_COPY.binding[one.binding]}</Pill>
              ) : null}
              {one.process ? <Pill>{PARTICIPANTS_COPY.ownProcess}</Pill> : null}
            </Stack>
            {tools.length ? (
              <Stack direction="row" flexWrap="wrap" useFlexGap gap={0.75}>
                {tools.map((tool) => (
                  <Box
                    key={tool}
                    sx={{
                      typography: "s3",
                      fontFamily: MONO,
                      color: "text.secondary",
                    }}
                  >
                    {tool}
                  </Box>
                ))}
              </Stack>
            ) : null}
          </Stack>
        );
      })}
      <Typography
        sx={{
          px: 1.5,
          py: 1,
          borderTop: "1px solid",
          borderColor: "divider",
          typography: "s3",
          color: "text.subtitle",
        }}
      >
        {PARTICIPANTS_COPY.note}
      </Typography>
    </Card>
  );
}

ParticipantsCard.propTypes = {
  participants: PropTypes.arrayOf(
    PropTypes.shape({
      id: PropTypes.string.isRequired,
      name: PropTypes.string,
      tools: PropTypes.arrayOf(PropTypes.string),
      allowed_tools: PropTypes.arrayOf(PropTypes.string).isRequired,
      binding: PropTypes.oneOf(["separate", "shared"]),
      process: PropTypes.string,
    }),
  ).isRequired,
};
