import { JsonViewer } from "@textea/json-viewer";
import React, { useState } from "react";
import PropTypes from "prop-types";
import { useTheme, Dialog, DialogContent } from "@mui/material";
import { defineDataType } from "@textea/json-viewer";
import { InlineAudio } from "src/components/inline-audio/inline-row-audio";
import { sanitizeSrc, isImageValue, isAudioValue } from "src/components/custom-json-viewer/media-utils";

const MiniImageRender = ({ value }) => {
  const [open, setOpen] = useState(false);
  const safeSrc = sanitizeSrc(value);

  if (!safeSrc) return <span>{value}</span>;

  return (
    <>
      <img
        width={150}
        src={safeSrc}
        alt="Preview"
        style={{ display: "inline-block", cursor: "pointer" }}
        onClick={(e) => {
          e.stopPropagation();
          setOpen(true);
        }}
      />
      <Dialog open={open} onClose={() => setOpen(false)} maxWidth="lg">
        <DialogContent
          sx={{
            bgcolor: "background.paper",
          }}
        >
          <img src={safeSrc} alt="full" style={{ width: "100%" }} />
        </DialogContent>
      </Dialog>
    </>
  );
};

MiniImageRender.propTypes = {
  value: PropTypes.string,
};

const MiniAudioRender = ({ value }) => {
  const safeSrc = sanitizeSrc(value);

  if (!safeSrc) return <span>{value}</span>;

  return (
    <div onClick={(e) => e.stopPropagation()}>
      <InlineAudio src={safeSrc} />
    </div>
  );
};

MiniAudioRender.propTypes = {
  value: PropTypes.string,
};

const imageType = defineDataType({
  is: (value) => isImageValue(value) && !!sanitizeSrc(value),
  Component: MiniImageRender,
});

const audioType = defineDataType({
  is: (value) => isAudioValue(value) && !!sanitizeSrc(value),
  Component: MiniAudioRender,
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
        valueTypes={[imageType, audioType]}
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
  object: PropTypes.object,
};

export default CustomJsonViewer;
