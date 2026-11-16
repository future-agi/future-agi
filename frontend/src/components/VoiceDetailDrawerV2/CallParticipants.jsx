import PropTypes from "prop-types";
import { Stack, Typography } from "@mui/material";
import OtherPartyName from "./OtherPartyName";
import { CALL_PARTICIPANTS_HEADING, DEFAULT_ROLE_LABELS } from "./constants";

const customerParty = (customerName) =>
  customerName
    ? { speakerName: customerName, actorRole: DEFAULT_ROLE_LABELS.user }
    : { speakerName: DEFAULT_ROLE_LABELS.user };

// Actors with no channel act only in the world, so they are left out.
export default function CallParticipants({
  actors,
  customerName,
  withCustomer = true,
}) {
  const speakers = actors.filter((actor) => actor.channel);
  if (speakers.length === 0) return null;
  const line = [
    ...(withCustomer
      ? [{ key: "customer", party: customerParty(customerName) }]
      : []),
    ...speakers.map((actor) => ({
      key: actor.id,
      party: {
        speakerName: actor.name || actor.id,
        actorRole: actor.role,
        channel: actor.channel,
      },
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
  actors: PropTypes.arrayOf(PropTypes.object).isRequired,
  customerName: PropTypes.string,
  withCustomer: PropTypes.bool,
};
