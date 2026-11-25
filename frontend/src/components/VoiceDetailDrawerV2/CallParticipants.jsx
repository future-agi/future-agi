import PropTypes from "prop-types";
import { Stack, Typography } from "@mui/material";
import OtherPartyName from "./OtherPartyName";
import { CALL_PARTICIPANTS_HEADING, DEFAULT_ROLE_LABELS } from "./constants";

const customerParty = (customerName) =>
  customerName
    ? { speakerName: customerName, actorRole: DEFAULT_ROLE_LABELS.user }
    : { speakerName: DEFAULT_ROLE_LABELS.user };

// Who took part, as the server says; one who only acted in the world (no channel) was never on the call.
export default function CallParticipants({
  participants,
  customerName,
  withCustomer = true,
}) {
  const speakers = participants.filter((one) => one.channel);
  if (speakers.length === 0) return null;
  const line = [
    ...(withCustomer
      ? [{ key: "customer", party: customerParty(customerName) }]
      : []),
    ...speakers.map((one) => ({
      key: one.id,
      party: {
        speakerName: one.name,
        actorRole: one.role,
        channel: one.channel,
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
  // The call's `participants`: `{id, name, role, channel}` each.
  participants: PropTypes.arrayOf(PropTypes.object).isRequired,
  customerName: PropTypes.string,
  withCustomer: PropTypes.bool,
};
