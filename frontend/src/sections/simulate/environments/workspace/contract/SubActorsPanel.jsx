import PropTypes from "prop-types";
import { Box, Stack, Typography } from "@mui/material";
import SectionCard from "../../components/SectionCard";
import { genderLabel } from "../scenarios/personaLabels";
import { CONTRACT_COPY } from "./contract.constants";

const C = CONTRACT_COPY.subActors;

/**
 * The environment's sub-actors: people on the person's side who are not the
 * person. Read-only; they are added, changed and removed through the builder chat.
 */
export default function SubActorsPanel({ subActors }) {
  return (
    <SectionCard title={C.title(subActors.length)} subtitle={C.subtitle}>
      <Stack
        divider={
          <Box sx={{ borderBottom: "1px solid", borderColor: "divider" }} />
        }
      >
        {subActors.map((one) => (
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
            <Typography sx={{ typography: "s2", color: "text.secondary" }}>
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

SubActorsPanel.propTypes = {
  subActors: PropTypes.arrayOf(
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
