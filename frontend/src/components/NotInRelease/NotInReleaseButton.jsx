import PropTypes from "prop-types";
import { useId } from "react";
import { Box, Button } from "@mui/material";
import CustomTooltip from "src/components/tooltip";
import { NOT_IN_RELEASE, visuallyHidden } from "./notInRelease";

// A small action not built in this release: shown, disabled and out of the
// tab order, with the shared line in its tooltip and as its accessible
// description. Wrapped in a span so the tooltip still fires over the disabled
// button. Any click handler passed in is dropped, so it can't submit anything.
// `reason` replaces the line only where a more specific, truthful reason
// applies (a locked template says how to make it editable).
export default function NotInReleaseButton({
  children,
  reason = NOT_IN_RELEASE,
  ...buttonProps
}) {
  const descriptionId = useId();
  return (
    <CustomTooltip show arrow size="small" title={reason}>
      <Box component="span" sx={{ display: "inline-flex", flexShrink: 0 }}>
        <Button
          {...buttonProps}
          onClick={undefined}
          disabled
          aria-disabled="true"
          aria-describedby={descriptionId}
        >
          {children}
        </Button>
        <Box component="span" id={descriptionId} sx={visuallyHidden}>
          {reason}
        </Box>
      </Box>
    </CustomTooltip>
  );
}
NotInReleaseButton.propTypes = {
  children: PropTypes.node,
  reason: PropTypes.string,
};
