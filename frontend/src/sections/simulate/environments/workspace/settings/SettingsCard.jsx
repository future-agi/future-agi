import PropTypes from "prop-types";
import { Box } from "@mui/material";
import SectionCard from "../../components/SectionCard";

// A Settings card as a named region, so each card can be found by its name.
export default function SettingsCard({ label, ...props }) {
  return (
    <Box component="section" aria-label={label}>
      <SectionCard {...props} />
    </Box>
  );
}
SettingsCard.propTypes = { label: PropTypes.string.isRequired };
