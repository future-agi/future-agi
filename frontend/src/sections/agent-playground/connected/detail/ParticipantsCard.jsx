import PropTypes from "prop-types";
import { Typography } from "@mui/material";
import DeclaredWorkerRows from "src/sections/simulate/environments/workspace/contract/DeclaredWorkerRows";
import { Card } from "./bits";
import { PARTICIPANTS_COPY } from "./participants.constants";

/** The agent version's workers, read-only, drawn as the Contract tab's Workers card draws them. */
export default function ParticipantsCard({ participants }) {
  if (!participants.length) return null;
  return (
    <Card title={PARTICIPANTS_COPY.title(participants.length)} action={null}>
      <DeclaredWorkerRows workers={participants} />
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
  participants: PropTypes.arrayOf(PropTypes.object).isRequired,
};
