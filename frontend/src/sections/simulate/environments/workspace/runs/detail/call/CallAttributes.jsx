import PropTypes from "prop-types";
import { Stack, Typography } from "@mui/material";

import SoonChip from "../../../../components/SoonChip";
import { Attr } from "../chatDrawerCells";

const personaLine = (persona) =>
  [persona?.name, persona?.age, persona?.voice].filter(Boolean).join(" · ");

/**
 * Vel's Attributes: the scenario, the simulated caller and what the call had
 * to achieve. Whether the scenario is must-pass isn't in the API yet.
 * @param {{ scenario: ?string, status: ?string, persona: ?Object,
 *   personaName: ?string, expected: ?string }} props
 */
export default function CallAttributes({
  scenario,
  status,
  persona,
  personaName,
  expected,
}) {
  const traits = Array.isArray(persona?.traits) ? persona.traits : [];
  return (
    <Stack>
      <Attr label="scenario" value={scenario} />
      <Attr label="persona" value={personaLine(persona) || personaName} />
      <Attr label="traits" value={traits.join(", ")} />
      <Attr label="expected" value={expected} />
      <Attr label="status" value={status} />
      <Stack
        direction="row"
        alignItems="center"
        spacing={2}
        sx={{
          px: 2,
          py: 1.25,
          borderBottom: "1px solid",
          borderColor: "divider",
        }}
      >
        <Typography
          sx={{
            width: 96,
            flexShrink: 0,
            typography: "s3",
            color: "text.subtitle",
            fontFamily: "ui-monospace, Menlo, monospace",
          }}
        >
          must-pass
        </Typography>
        <SoonChip
          label="Must-pass"
          what="Whether a scenario is must-pass"
          size="tiny"
        />
      </Stack>
    </Stack>
  );
}
CallAttributes.propTypes = {
  scenario: PropTypes.string,
  status: PropTypes.string,
  persona: PropTypes.shape({
    name: PropTypes.string,
    age: PropTypes.string,
    voice: PropTypes.string,
    traits: PropTypes.arrayOf(PropTypes.string),
  }),
  personaName: PropTypes.string,
  expected: PropTypes.string,
};
