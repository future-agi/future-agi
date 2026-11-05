import PropTypes from "prop-types";
import { Box, Stack, Typography } from "@mui/material";
import SectionCard from "../../components/SectionCard";
import { genderLabel } from "../scenarios/personaLabels";
import { ACTORS_COPY } from "./actors.constants";

const C = ACTORS_COPY.callActors;

/**
 * The environment's actors: people on the person's side who are not the
 * person. Read-only; they are added, changed and removed through the builder chat.
 */
export default function CallActorsPanel({ callActors }) {
  return (
    <SectionCard title={C.title(callActors.length)} subtitle={C.subtitle}>
      <Stack
        divider={
          <Box sx={{ borderBottom: "1px solid", borderColor: "divider" }} />
        }
      >
        {callActors.map((one) => (
          <Stack key={one.id} spacing={0.25} sx={{ px: 2.5, py: 1.25 }}>
            <Typography sx={{ typography: "s1", fontWeight: 600 }}>
              {one.name}
              {one.role ? (
                <Box
                  component="span"
                  sx={{ color: "text.secondary", fontWeight: 400 }}
                >
                  {` · ${one.role}`}
                </Box>
              ) : null}
            </Typography>
            <Typography sx={{ typography: "s3", color: "text.secondary" }}>
              {[
                C.channels[one.channel] || one.channel,
                genderLabel(one.persona?.gender),
                one.persona?.age_group,
                one.persona?.accent,
                C.scenarios(one.scenarios_count ?? 0),
              ]
                .filter(Boolean)
                .join(" · ")}
            </Typography>
          </Stack>
        ))}
      </Stack>
    </SectionCard>
  );
}

CallActorsPanel.propTypes = {
  callActors: PropTypes.arrayOf(
    PropTypes.shape({
      id: PropTypes.string.isRequired,
      name: PropTypes.string,
      role: PropTypes.string,
      channel: PropTypes.string,
      persona: PropTypes.object,
      scenarios_count: PropTypes.number,
    }),
  ).isRequired,
};
