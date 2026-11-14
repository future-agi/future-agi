import React, { cloneElement, useState } from "react";
import PropTypes from "prop-types";
import CustomTooltip from "./CustomTooltip";

function isTruncated(el) {
  if (el.scrollWidth > el.clientWidth) return true;
  // scrollWidth rounds to whole pixels; a sub-pixel overflow still draws the
  // ellipsis, so compare the text's own width too.
  const range = document.createRange();
  range.selectNodeContents(el);
  const textWidth = range.getBoundingClientRect?.().width ?? 0;
  return textWidth > el.getBoundingClientRect().width;
}

/** Hover tooltip with the full text, shown only when the text is cut off. */
const OverflowTooltip = ({ title, children }) => {
  const [open, setOpen] = useState(false);
  return (
    <CustomTooltip
      show
      open={open}
      title={title}
      placement="top"
      arrow
      size="small"
      disableHoverListener
      disableFocusListener
      disableTouchListener
    >
      {cloneElement(children, {
        onMouseEnter: (event) => {
          children.props.onMouseEnter?.(event);
          // A chip truncates its inner label, not its root.
          const el =
            event.currentTarget.querySelector?.(".MuiChip-label") ||
            event.currentTarget;
          setOpen(isTruncated(el));
        },
        onMouseLeave: (event) => {
          children.props.onMouseLeave?.(event);
          setOpen(false);
        },
      })}
    </CustomTooltip>
  );
};

OverflowTooltip.propTypes = {
  title: PropTypes.node,
  children: PropTypes.element.isRequired,
};

export default OverflowTooltip;
