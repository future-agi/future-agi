import React, { forwardRef, useLayoutEffect } from "react";
import PropTypes from "prop-types";
import {
  EXIT_TRANSITION_MS,
  SNACKBAR_AUTO_HIDE_MS,
  StyledNotistack,
} from "./styles";
import ClampedMessage from "./ClampedMessage";
import { syncToastPlacement } from "./toastPlacement";

/** How often a visible toast re-checks for bars appearing under it. */
const PLACE_EVERY_MS = 400;

const ClampedContent = forwardRef(function ClampedContent(props, ref) {
  const { message, style, className, autoHideDuration, persist, ...rest } =
    props;
  // The stack sits bottom-right of the content,
  // lifts over any bar or launcher at the bottom and moves
  // left of an open drawer; re-measured while this toast shows.
  useLayoutEffect(() => {
    syncToastPlacement();
    const t = setInterval(syncToastPlacement, PLACE_EVERY_MS);
    return () => clearInterval(t);
  }, []);
  const life =
    (Number.isFinite(autoHideDuration)
      ? autoHideDuration
      : SNACKBAR_AUTO_HIDE_MS) + EXIT_TRANSITION_MS;
  return (
    <StyledNotistack
      ref={ref}
      {...rest}
      className={[className, persist ? "fa-toast-persist" : null]
        .filter(Boolean)
        .join(" ")}
      style={{ ...style, "--fa-toast-life": `${life / 1000}s` }}
      message={
        React.isValidElement(message) ? (
          message
        ) : (
          <ClampedMessage>{message}</ClampedMessage>
        )
      }
    />
  );
});

ClampedContent.propTypes = {
  message: PropTypes.node,
  style: PropTypes.object,
  className: PropTypes.string,
  autoHideDuration: PropTypes.number,
  persist: PropTypes.bool,
};

export default ClampedContent;
