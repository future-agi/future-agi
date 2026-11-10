import PropTypes from "prop-types";
import { Stack, Typography } from "@mui/material";
import OtherPartyName from "./OtherPartyName";
import { CALL_PARTICIPANTS_HEADING, DEFAULT_ROLE_LABELS } from "./constants";
import { otherPartyKey } from "./transcriptUtils";

const customerParty = (customerName) =>
  customerName
    ? { speakerName: customerName, actorRole: DEFAULT_ROLE_LABELS.user }
    : { speakerName: DEFAULT_ROLE_LABELS.user };

// Who was on the call, shown only when someone besides the agent and the customer was. A world actor acts in the
// world and never joins the call, so it is left out here; its actions are in the transcript.
// Each person is one unit: they wrap whole, and a long role is cut rather than broken.
export default function CallParticipants({
  others,
  customerName,
  withCustomer = true,
}) {
  const speakers = others.filter((party) => party.role !== "world_actor");
  if (speakers.length === 0) return null;
  const line = [
    // A run started by someone's message has no customer; they are listed only when one took part.
    ...(withCustomer
      ? [{ key: "customer", party: customerParty(customerName) }]
      : []),
    ...speakers.map((party) => ({
      key: otherPartyKey(party),
      party,
    })),
    { key: "agent", party: { speakerName: DEFAULT_ROLE_LABELS.assistant } },
  ];
  return (
    <Stack
      direction="row"
      alignItems="center"
      flexWrap="wrap"
      columnGap={1.5}
      rowGap={0.5}
    >
      <Typography variant="s3" sx={{ color: "text.secondary" }}>
        {`${CALL_PARTICIPANTS_HEADING}:`}
      </Typography>
      {line.map(({ key, party }) => (
        <OtherPartyName
          key={key}
          party={party}
          fontWeight="fontWeightRegular"
        />
      ))}
    </Stack>
  );
}

CallParticipants.propTypes = {
  others: PropTypes.arrayOf(PropTypes.object).isRequired,
  customerName: PropTypes.string,
  withCustomer: PropTypes.bool,
};
