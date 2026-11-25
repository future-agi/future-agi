import PropTypes from "prop-types";
import { Typography } from "@mui/material";
import SectionCard from "../../components/SectionCard";
import DeclaredWorkerRows from "./DeclaredWorkerRows";
import { WORKERS_COPY } from "./workers.constants";

export default function WorkersCard({ workers, sx }) {
  return (
    <SectionCard
      roomy
      title={WORKERS_COPY.title}
      subtitle={WORKERS_COPY.subtitle}
      sx={sx}
      action={
        <Typography
          sx={{
            typography: "s3",
            color: "text.subtitle",
            fontVariantNumeric: "tabular-nums",
          }}
        >
          {WORKERS_COPY.count(workers.length)}
        </Typography>
      }
    >
      <DeclaredWorkerRows workers={workers} />
      <Typography
        sx={{
          typography: "s3",
          color: "text.subtitle",
          px: 1.5,
          py: 1,
          borderTop: "1px solid",
          borderColor: "divider",
        }}
      >
        {WORKERS_COPY.footer}
      </Typography>
    </SectionCard>
  );
}

WorkersCard.propTypes = {
  workers: PropTypes.arrayOf(PropTypes.object).isRequired,
  sx: PropTypes.object,
};
