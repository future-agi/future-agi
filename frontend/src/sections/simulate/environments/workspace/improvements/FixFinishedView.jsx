import PropTypes from "prop-types";
import { Box } from "@mui/material";

export default function FixFinishedView({ fix }) {
  return <Box data-testid="fix-finished-view" data-fix={fix.id} />;
}

FixFinishedView.propTypes = { fix: PropTypes.object.isRequired };
