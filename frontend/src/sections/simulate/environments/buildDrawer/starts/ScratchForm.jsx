import { Box } from "@mui/material";
import PropTypes from "prop-types";
import DesignForm from "src/pages/dashboard/harness/DesignForm";

export default function ScratchForm({ onBusyChange }) {
  return (
    <Box sx={{ p: 2.5 }}>
      <DesignForm onBusyChange={onBusyChange} />
    </Box>
  );
}

ScratchForm.propTypes = { onBusyChange: PropTypes.func };
