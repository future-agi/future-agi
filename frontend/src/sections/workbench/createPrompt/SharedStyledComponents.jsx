import { Chip } from "@mui/material";
import PropTypes from "prop-types";

export const DraftBadge = ({ children }) => (
  <Chip size="small" color="warning" variant="soft" label={children} />
);

DraftBadge.propTypes = {
  children: PropTypes.node,
};
