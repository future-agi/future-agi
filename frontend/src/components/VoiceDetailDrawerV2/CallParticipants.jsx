import PropTypes from "prop-types";
import { Box, Stack, Typography } from "@mui/material";
import OtherPartyName from "./OtherPartyName";
import { CALL_PARTICIPANTS_HEADING, DEFAULT_ROLE_LABELS } from "./constants";

const Person = ({ children }) => (
  <Typography component="span" variant="s3" sx={{ fontWeight: 600 }}>
    {children}
  </Typography>
);
Person.propTypes = { children: PropTypes.node };

// Who was on the call, shown only when someone besides the agent and the customer was.
export default function CallParticipants({ others, customerName }) {
  if (others.length === 0) return null;
  const entries = [
    <Person key="customer">
      {customerName
        ? `${customerName} · ${DEFAULT_ROLE_LABELS.user}`
        : DEFAULT_ROLE_LABELS.user}
    </Person>,
    ...others.map((party) => (
      <OtherPartyName
        key={`${party.role}:${party.speakerName}`}
        party={party}
      />
    )),
    <Person key="agent">{DEFAULT_ROLE_LABELS.assistant}</Person>,
  ];
  return (
    <Stack direction="row" alignItems="center" flexWrap="wrap" gap={0.5}>
      <Typography variant="s3" sx={{ color: "text.secondary" }}>
        {`${CALL_PARTICIPANTS_HEADING}:`}
      </Typography>
      {entries.map((entry, i) => (
        <Box
          key={entry.key}
          component="span"
          sx={{ display: "inline-flex", minWidth: 0, maxWidth: "100%" }}
        >
          {entry}
          {i < entries.length - 1 && (
            <Typography
              component="span"
              variant="s3"
              sx={{ color: "text.secondary" }}
            >
              ,
            </Typography>
          )}
        </Box>
      ))}
    </Stack>
  );
}

CallParticipants.propTypes = {
  others: PropTypes.arrayOf(PropTypes.object).isRequired,
  customerName: PropTypes.string,
};
