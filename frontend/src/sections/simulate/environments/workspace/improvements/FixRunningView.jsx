import PropTypes from "prop-types";
import { Box } from "@mui/material";

export default function FixRunningView({ fix }) {
  return <Box data-testid="fix-running-view" data-fix={fix.id} />;
}

FixRunningView.propTypes = { fix: PropTypes.object.isRequired };
