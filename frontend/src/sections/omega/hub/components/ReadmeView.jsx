/* eslint-disable react-refresh/only-export-components */
import React from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Link from "@mui/material/Link";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

// A remote image (any scheme, or protocol-relative) is shown as a link, never
// loaded (SPEC §2.4 threats: tracking through README images). A relative
// image inside the approved bundle may be shown when the page can resolve it
// to the registry's own file endpoint.
export function isRemoteSrc(src) {
  return /^([a-z][a-z0-9+.-]*:|\/\/)/i.test(String(src || "").trim());
}

const safeHref = (href) => (/^(https?:|mailto:|#)/i.test(String(href || "")) ? href : undefined);

export default function ReadmeView({ markdown, resolveImage, testId = "hub-readme" }) {
  if (!markdown) return null;
  const components = {
    img: ({ src, alt }) => {
      const local = !isRemoteSrc(src) && resolveImage ? resolveImage(src) : null;
      if (local) {
        return <Box component="img" src={local} alt={alt || ""} sx={{ maxWidth: "100%" }} />;
      }
      const href = safeHref(src);
      return (
        <Link
          href={href}
          target="_blank"
          rel="nofollow noopener noreferrer"
          data-testid="hub-readme-image-link"
          typography="s2"
        >
          {`Image: ${alt || src}`}
        </Link>
      );
    },
    a: ({ href, children }) => (
      <Link href={safeHref(href)} target="_blank" rel="nofollow noopener noreferrer" underline="hover">
        {children}
      </Link>
    ),
  };
  return (
    <Box
      data-testid={testId}
      sx={{
        typography: "body2",
        color: "text.primary",
        overflowWrap: "anywhere",
        "& h1, & h2, & h3": { fontWeight: 600, lineHeight: 1.3, mt: 2.5, mb: 1 },
        "& h1": { fontSize: 22, mt: 0 },
        "& h2": { fontSize: 18, pb: 0.5, borderBottom: "1px solid", borderColor: "divider" },
        "& h3": { fontSize: 15 },
        "& p, & ul, & ol": { my: 1 },
        "& li": { my: 0.25 },
        "& code": {
          fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
          fontSize: 12.5,
          px: 0.5,
          borderRadius: 0.5,
          bgcolor: "background.neutral",
        },
        "& pre": {
          p: 1.5,
          borderRadius: 1,
          overflowX: "auto",
          bgcolor: "background.neutral",
          border: "1px solid",
          borderColor: "divider",
          "& code": { p: 0, bgcolor: "transparent" },
        },
        "& table": { borderCollapse: "collapse", display: "block", overflowX: "auto" },
        "& th, & td": { border: "1px solid", borderColor: "divider", px: 1, py: 0.5 },
        "& blockquote": { m: 0, pl: 1.5, borderLeft: "3px solid", borderColor: "divider", color: "text.secondary" },
      }}
    >
      <ReactMarkdown remarkPlugins={[remarkGfm]} skipHtml components={components}>
        {markdown}
      </ReactMarkdown>
    </Box>
  );
}

ReadmeView.propTypes = {
  markdown: PropTypes.string,
  resolveImage: PropTypes.func,
  testId: PropTypes.string,
};
