import React, { useState } from "react";
import PropTypes from "prop-types";
import { Box, Button, Typography } from "@mui/material";

function ConsentFrame({
  src,
  title,
  style,
  width,
  height,
  onRequestLoad,
  ...frameProps
}) {
  const [approved, setApproved] = useState(false);
  let provider = "third-party provider";
  try {
    provider = new URL(src).hostname;
  } catch {
    // Relative URLs still require an explicit click on this external-content surface.
  }
  if (approved) {
    return (
      <iframe
        {...frameProps}
        src={src}
        title={title}
        style={style}
        width={width}
        height={height}
        referrerPolicy="no-referrer"
      />
    );
  }
  // Do not mount an iframe (even hidden), thumbnail, preconnect or video SDK
  // until the user chooses to contact this provider. Loading=lazy is not consent.
  return (
    <Box
      style={{
        width: width || "100%",
        height,
        ...style,
        opacity: 1,
        visibility: "visible",
        display: "flex",
      }}
      sx={{
        minHeight: height || style?.height ? undefined : 180,
        bgcolor: "background.neutral",
        color: "text.primary",
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        gap: 1,
        p: 2,
        textAlign: "center",
      }}
      role="group"
      aria-label={title || "External content"}
    >
      <Typography variant="subtitle2">{title || "External content"}</Typography>
      <Typography variant="caption">
        External content from {provider}. Loading it connects to that provider.
      </Typography>
      <Button
        variant="outlined"
        onClick={() => {
          setApproved(true);
          onRequestLoad?.();
        }}
      >
        Load external content
      </Button>
    </Box>
  );
}
ConsentFrame.propTypes = {
  src: PropTypes.string.isRequired,
  onRequestLoad: PropTypes.func,
  title: PropTypes.string,
  style: PropTypes.object,
  width: PropTypes.oneOfType([PropTypes.string, PropTypes.number]),
  height: PropTypes.oneOfType([PropTypes.string, PropTypes.number]),
};

export default function ExternalContentFrame(props) {
  // Navigating to a different preview must not inherit a previous approval.
  return <ConsentFrame key={props.src} {...props} />;
}
ExternalContentFrame.propTypes = ConsentFrame.propTypes;
