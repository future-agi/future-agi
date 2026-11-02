import PropTypes from "prop-types";
import CustomTooltip from "src/components/tooltip";

export default function ViewerLock({ locked, title, children }) {
  if (!locked) return children;
  return (
    <CustomTooltip show title={title} size="small" arrow>
      <span>{children}</span>
    </CustomTooltip>
  );
}

ViewerLock.propTypes = {
  locked: PropTypes.bool,
  title: PropTypes.string.isRequired,
  children: PropTypes.node.isRequired,
};
