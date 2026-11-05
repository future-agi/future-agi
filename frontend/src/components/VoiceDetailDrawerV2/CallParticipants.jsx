import PropTypes from "prop-types";
import { Stack, Typography } from "@mui/material";
import OtherPartyName from "./OtherPartyName";
import { CALL_PARTICIPANTS_HEADING, DEFAULT_ROLE_LABELS } from "./constants";

const customerParty = (customerName) =>
  customerName
    ? { speakerName: customerName, actorRole: DEFAULT_ROLE_LABELS.user }
    : { speakerName: DEFAULT_ROLE_LABELS.user };

// Who was on the call, shown only when someone besides the agent and the customer was.
// Each person is one unit: they wrap whole, and a long role is cut rather than broken.
export default function CallParticipants({ others, customerName }) {
  if (others.length === 0) return null;
  const line = [
    { key: "customer", party: customerParty(customerName) },
    ...others.map((party) => ({
      key: `${party.role}:${party.speakerName}`,
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
};
