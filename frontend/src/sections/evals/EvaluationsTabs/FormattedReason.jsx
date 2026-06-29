import React from "react";
import { Box, Button, useTheme } from "@mui/material";
import PropTypes from "prop-types";
import { useEffect, useRef, useState } from "react";
import CellMarkdown from "src/sections/common/CellMarkdown";

const FormattedValueReason = (valueReason) => {
  const theme = useTheme();
  const [expanded, setExpanded] = useState(false);
  const [hasScrolled, setHasScrolled] = useState(false);
  const contentRef = useRef(null);
  const [isScrollable, setIsScrollable] = useState(false);

  const handleScroll = () => {
    if (!contentRef.current || hasScrolled) return;

    const { scrollTop } = contentRef.current;
    if (scrollTop > 8) {
      setHasScrolled(true);
    }
  };

  useEffect(() => {
    const checkScrollability = () => {
      if (contentRef.current) {
        const isContentTaller =
          contentRef.current.scrollHeight > contentRef.current.clientHeight;
        setIsScrollable(isContentTaller);
      }
    };

    checkScrollability();
  }, [expanded, valueReason]);

  useEffect(() => {
    if (!expanded) {
      setHasScrolled(false);
    }
  }, [expanded]);

  const valueReasonLength = valueReason && valueReason?.length;

  return (
    <Box
      sx={{
        minWidth: "fit-content",
        p: "4px",
      }}
    >
      <Box
        ref={contentRef}
        className="promptScroll"
        sx={{
          position: "relative",
          maxHeight: !expanded ? "150px" : "none",
          display: expanded ? "block" : "-webkit-box",
          WebkitLineClamp: expanded ? "none" : 5,
          WebkitBoxOrient: "vertical",
          overflowY: expanded ? "auto" : "hidden",
          textOverflow: "ellipsis",
        }}
        pb={expanded ? 1 : 0}
        onScroll={expanded ? handleScroll : undefined}
      >
        <CellMarkdown spacing={0} text={valueReason} />
        {expanded && isScrollable && !hasScrolled && (
          <Box
            sx={{
              position: "absolute",
              bottom: 0,
              left: 0,
              right: 0,
              height: "80px",
              opacity: "0.9",
              background: `linear-gradient(to bottom, transparent 0%, ${theme.palette.background.default} 100%)`,
              pointerEvents: "none",
            }}
          />
        )}
      </Box>

      {valueReasonLength > 300 && !expanded && (
        <Button
          onClick={() => setExpanded(!expanded)}
          sx={{
            textTransform: "none",
            p: 0,
            mt: 0.5,
            minWidth: "auto",
            color: "text.primary",
            textDecoration: "underline",
            fontSize: "14px",
            "&:hover": {
              backgroundColor: "transparent",
            },
          }}
        >
          {expanded ? "" : "Show more"}
        </Button>
      )}
    </Box>
  );
};

FormattedValueReason.propTypes = {
  valueReason: PropTypes.string,
};

export default FormattedValueReason;
