import React, { forwardRef } from "react";
import PropTypes from "prop-types";
import { StyledNotistack } from "./styles";
import ClampedMessage from "./ClampedMessage";

const ClampedContent = forwardRef(function ClampedContent(props, ref) {
  const { message, ...rest } = props;
  return (
    <StyledNotistack
      ref={ref}
      {...rest}
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
};

export default ClampedContent;
