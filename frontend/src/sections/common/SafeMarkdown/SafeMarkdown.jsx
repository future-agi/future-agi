// W5-8 (EV-21; W5-SCOPE §7 C5): judge reasons and comment bodies as safe
// Markdown. react-markdown + remark-gfm + rehype-sanitize (all already
// dependencies):
// - raw HTML never renders: every HTML node becomes plain text, so a
//   `<script>` in a reason is shown as the characters `<script>`;
// - links open in a new tab with rel="noopener noreferrer nofollow"; unsafe
//   protocols are dropped by react-markdown's URL transform;
// - remote images are never loaded: an image shows as its alt text;
// - a `Raw` toggle shows the text exactly as stored.
import React, { useState } from "react";
import PropTypes from "prop-types";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeSanitize from "rehype-sanitize";
import { Box, ToggleButton, Typography } from "@mui/material";
import { remarkHtmlAsText } from "./markdownModel";

const components = {
  a: ({ node: _n, href, children }) => (
    <a href={href} target="_blank" rel="noopener noreferrer nofollow">
      {children}
    </a>
  ),
  img: ({ node: _n, alt }) => (
    <Box
      component="span"
      sx={{ color: "text.secondary" }}
      title="Image not loaded"
    >
      {alt ? `[image: ${alt}]` : "[image]"}
    </Box>
  ),
};
components.a.propTypes = {
  node: PropTypes.any,
  href: PropTypes.string,
  children: PropTypes.node,
};
components.img.propTypes = { node: PropTypes.any, alt: PropTypes.string };

const REMARK = [remarkGfm, remarkHtmlAsText];
const REHYPE = [rehypeSanitize];

/** The Markdown body only (no toggle). */
export function MarkdownBody({ text }) {
  return (
    <Box
      data-testid="safe-markdown"
      sx={{
        fontSize: 13,
        lineHeight: 1.55,
        wordBreak: "break-word",
        "& p": { m: 0, mb: 0.75 },
        "& p:last-child": { mb: 0 },
        "& ul, & ol": { my: 0.5, pl: 2.5 },
        "& code": {
          fontFamily: "monospace",
          fontSize: 12,
          bgcolor: "action.hover",
          px: 0.5,
          borderRadius: 0.5,
        },
        "& pre": {
          bgcolor: "action.hover",
          p: 1,
          borderRadius: 1,
          overflow: "auto",
        },
        "& table": { borderCollapse: "collapse" },
        "& th, & td": {
          border: "1px solid",
          borderColor: "divider",
          px: 0.75,
          py: 0.25,
        },
        "& a": { color: "primary.main" },
      }}
    >
      <Markdown
        remarkPlugins={REMARK}
        rehypePlugins={REHYPE}
        components={components}
      >
        {String(text ?? "")}
      </Markdown>
    </Box>
  );
}

MarkdownBody.propTypes = { text: PropTypes.string };

/** Markdown with a `Raw` toggle. */
export default function SafeMarkdown({
  text,
  defaultRaw = false,
  toggle = true,
}) {
  const [raw, setRaw] = useState(defaultRaw);
  return (
    <Box sx={{ position: "relative" }}>
      {toggle && (
        <ToggleButton
          value="raw"
          size="small"
          selected={raw}
          onChange={() => setRaw((r) => !r)}
          aria-label="Raw"
          data-testid="safe-markdown-toggle"
          sx={{
            float: "right",
            ml: 1,
            py: 0,
            px: 0.75,
            fontSize: 11,
            lineHeight: "18px",
            textTransform: "none",
          }}
        >
          Raw
        </ToggleButton>
      )}
      {raw ? (
        <Typography
          data-testid="safe-markdown-raw"
          component="pre"
          sx={{
            m: 0,
            fontFamily: "inherit",
            fontSize: 13,
            whiteSpace: "pre-wrap",
            wordBreak: "break-word",
          }}
        >
          {String(text ?? "")}
        </Typography>
      ) : (
        <MarkdownBody text={text} />
      )}
    </Box>
  );
}

SafeMarkdown.propTypes = {
  text: PropTypes.string,
  defaultRaw: PropTypes.bool,
  toggle: PropTypes.bool,
};
