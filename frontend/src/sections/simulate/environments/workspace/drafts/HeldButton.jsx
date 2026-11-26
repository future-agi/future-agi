import PropTypes from "prop-types";
import { forwardRef } from "react";
import { Button } from "@mui/material";
import CustomTooltip from "src/components/tooltip";

/**
 * A button that can be held with a reason. Held, not disabled: a disabled button
 * can't take focus, and the reason would be out of reach by keyboard.
 */
const HeldButton = forwardRef(function HeldButton(
  { reason = null, children, onClick, ...props },
  ref,
) {
  const held = Boolean(reason);
  return (
    <CustomTooltip show={held} size="small" arrow title={reason || ""}>
      <span>
        <Button
          ref={ref}
          {...props}
          className={held ? "Mui-disabled" : undefined}
          aria-disabled={held ? true : undefined}
          aria-label={held ? `${children}. ${reason}` : undefined}
          onClick={held ? undefined : onClick}
        >
          {children}
        </Button>
      </span>
    </CustomTooltip>
  );
});

HeldButton.propTypes = {
  // Why it can't be pressed now; none means it can.
  reason: PropTypes.string,
  children: PropTypes.string.isRequired,
  onClick: PropTypes.func,
};

export default HeldButton;
