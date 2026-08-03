import { JsonViewer } from "@textea/json-viewer";
import React from "react";
import PropTypes from "prop-types";
import { useTheme } from "@mui/material";
import { defineDataType } from "@textea/json-viewer";

const MiniImageRender = ({ value }) => (
  <img
    width={150}
    src={value}
    alt="Base64 Preview"
    style={{ display: "inline-block" }}
  />
);

MiniImageRender.propTypes = {
  value: PropTypes.string,
};

const imageType = defineDataType({
  is: (value) => {
    return typeof value === "string" && value.startsWith("data:image");
  },
  Component: MiniImageRender,
});

const CustomJsonViewer = ({ object, ...rest }) => {
  const theme = useTheme();
  // Create a custom component for the copy button that renders nothing
  const EmptyCopyButton = () => null;

  // Create a ref for the JsonViewer container
  const jsonViewerRef = React.useRef(null);

  React.useEffect(() => {
    if (!jsonViewerRef.current) return;

    const container = jsonViewerRef.current;

    const handleContainerClick = (e) => {
      // Check if the click was on a toggle button or icon using closest
      // This covers both the button and any child elements like SVGs or paths
      const isToggleElement = e.target.closest(
        '.tj-toggle-button, [class*="toggle"], .tj-toggle-icon, .tj-expandable > svg, .tj-arrow, [class*="arrow"], [class*="caret"], [class*="chevron"]',
      );

      if (isToggleElement) {
        window.__jsonViewerClick = true;
      }
    };

    // Add the handler
    container.addEventListener("click", handleContainerClick, true); // Use capture phase

    // Clean up
    return () => {
      container.removeEventListener("click", handleContainerClick, true);
    };
  }, [object]); // Re-run when object changes

  return (
    <div ref={jsonViewerRef}>
      <JsonViewer
        value={object}
        theme={theme.palette.mode}
        displayDataTypes={false}
        displaySize={false}
        indentWidth={3}
        rootName={false}
        groupArraysAfterLength={10}
        highlightUpdates={false}
        editable={false}
        valueTypes={[imageType]}
        // Disable copy functionality
        enableClipboard={false}
        quotesOnKeys={false}
        components={{
          CopyButton: EmptyCopyButton,
        }}
        // Style overrides to hide any copy buttons
        sx={{
          fontSize: "14px",
          lineHeight: "22px",
          "& [data-testid='copy-icon-button'], & .tj-copy-button, & [class*='copy-button'], & [class*='copyButton']":
            {
              display: "none !important",
              visibility: "hidden !important",
              opacity: "0 !important",
            },
        }}
        {...rest}
      />
    </div>
  );
};

CustomJsonViewer.propTypes = {
  object: PropTypes.any,
};

export default CustomJsonViewer;
