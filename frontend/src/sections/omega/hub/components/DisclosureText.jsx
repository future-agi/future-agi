import React, { useId, useState } from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";

// Preserve complete confirmed text; only its presentation is collapsed.
export default function DisclosureText({ text, label }) {
  const id = useId();
  const [expanded, setExpanded] = useState(false);
  if (text.length <= 300) return text;
  const preview = `${text.slice(0, 300).replace(/\s+\S*$/, "")}…`;
  return (
    <>
      {!expanded && <span>{preview}</span>}
      <Box component="span" id={id} hidden={!expanded} sx={{ whiteSpace: "pre-wrap" }}>
        {text}
      </Box>
      <Button
        type="button"
        size="small"
        aria-expanded={expanded}
        aria-controls={id}
        aria-label={`${expanded ? "Show less" : "Show all"} ${label.toLowerCase()} disclosure`}
        onClick={() => setExpanded((value) => !value)}
        sx={{ display: "block", mt: 0.5, "&:focus-visible": { outline: "2px solid", outlineOffset: 2 } }}
      >
        {expanded ? "Show less" : "Show all"}
      </Button>
    </>
  );
}

DisclosureText.propTypes = {
  text: PropTypes.string.isRequired,
  label: PropTypes.string.isRequired,
};
