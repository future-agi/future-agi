import { cloneElement } from "react";
import PropTypes from "prop-types";
import CustomTooltip from "src/components/tooltip";

export default function ViewerLock({ locked, title, children, ...forwarded }) {
  if (!locked) return cloneElement(children, forwarded);
  return (
    <CustomTooltip show title={title} size="small" arrow>
      <span>{children}</span>
    </CustomTooltip>
  );
}

ViewerLock.propTypes = {
  locked: PropTypes.bool,
  title: PropTypes.string.isRequired,
  children: PropTypes.element.isRequired,
};
