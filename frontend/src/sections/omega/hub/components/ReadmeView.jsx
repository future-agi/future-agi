/* eslint-disable react-refresh/only-export-components */
import React from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Link from "@mui/material/Link";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import CopyCommand from "./CopyCommand";

// A remote image (any scheme, or protocol-relative) is shown as a link, never
// loaded (SPEC §2.4 threats: tracking through README images). A relative
// image inside the approved bundle may be shown when the page can resolve it
// to the registry's own file endpoint.
export function isRemoteSrc(src) {
  return /^([a-z][a-z0-9+.-]*:|\/\/)/i.test(String(src || "").trim());
}

const safeHref = (href) =>
  /^(https?:|mailto:|#)/i.test(String(href || "")) ? href : undefined;

const WHAT_IT_DOES = /^what (it|this \w+) does$/i;
const HEADING = /^(#{1,6})[ \t]+(.+?)[ \t]*#*[ \t]*$/;
const FENCE = /^\s*(```|~~~)/;

// The README's "## What it does" section, apart from the rest, so a page can
// show it first and leave it out of the README below. Headings inside code
// fences are not headings. With no such section, `what` is null and `rest`
// is the README as given.
export function splitReadme(markdown) {
  const text = String(markdown || "");
  const lines = text.split("\n");
  let fenced = false;
  let start = -1;
  let end = lines.length;
  for (let index = 0; index < lines.length; index += 1) {
    if (FENCE.test(lines[index])) {
      fenced = !fenced;
      continue;
    }
    const heading = fenced ? null : HEADING.exec(lines[index]);
    if (!heading) continue;
    if (start === -1) {
      if (heading[1].length === 2 && WHAT_IT_DOES.test(heading[2].trim()))
        start = index;
    } else if (heading[1].length <= 2) {
      end = index;
      break;
    }
  }
  if (start === -1) return { what: null, rest: text };
  const section = lines.slice(start + 1, end);
  const what = section.join("\n").trim();
  if (!what) return { what: null, rest: text };
  return {
    what,
    rest: [...lines.slice(0, start), ...lines.slice(end)].join("\n").trim(),
  };
}

const SHORT_COMMAND =
  /^(\s*)omega hub (run|add) ([a-z][a-z0-9-]*)(?=\s|$)(.*)$/gm;

// A README written for its own repository runs the entry by its short id,
// which works only inside that checkout. On the Hub, a line that runs or
// adds this entry by its id becomes the pinned command of the version shown
// (`commands.run` or `commands.add`, which carry the registry when it is not
// the default); the rest of the line, a `--live` model or `-- --serve`,
// follows it. Lines about anything else are left as written.
export function pinReadmeCommands(markdown, commands) {
  if (!markdown || !commands) return markdown;
  const names = commands.names || [];
  return markdown.replace(SHORT_COMMAND, (line, indent, verb, id, rest) =>
    names.includes(id) ? `${indent}${commands[verb]}${rest}` : line,
  );
}

const SHELL = new Set(["bash", "sh", "shell", "zsh"]);

function textOf(node) {
  if (!node) return "";
  if (node.type === "text") return node.value || "";
  return (node.children || []).map(textOf).join("");
}

const isLanguage = (name) => name.startsWith("language-");

function languageOf(node) {
  const classes = node?.properties?.className || [];
  const match = classes.map(String).find(isLanguage);
  return match ? match.slice("language-".length) : null;
}

// A README heading moved down `offset` levels, so a README shown under a
// page's own h2 starts its sections at h3. Its size follows the README's
// own level (the class), not the moved one.
function MarkdownHeading({ level, offset, children }) {
  const Tag = `h${Math.min(6, level + offset)}`;
  return <Tag className={`hub-md-h${level}`}>{children}</Tag>;
}

MarkdownHeading.propTypes = {
  level: PropTypes.number.isRequired,
  offset: PropTypes.number.isRequired,
  children: PropTypes.node,
};

export default function ReadmeView({
  markdown,
  resolveImage,
  testId = "hub-readme",
  commands,
  headingOffset = 0,
}) {
  if (!markdown) return null;
  const headings = Object.fromEntries(
    [1, 2, 3, 4, 5, 6].map((level) => [
      `h${level}`,
      ({ children }) => (
        <MarkdownHeading level={level} offset={headingOffset}>
          {children}
        </MarkdownHeading>
      ),
    ]),
  );
  const components = {
    ...headings,
    img: ({ src, alt }) => {
      const local =
        !isRemoteSrc(src) && resolveImage ? resolveImage(src) : null;
      if (local) {
        return (
          <Box
            component="img"
            src={local}
            alt={alt || ""}
            sx={{ maxWidth: "100%" }}
          />
        );
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
      <Link
        href={safeHref(href)}
        target="_blank"
        rel="nofollow noopener noreferrer"
        underline="hover"
      >
        {children}
      </Link>
    ),
    // A shell block is a command to copy, shown whole with a copy button;
    // any other block stays preformatted text.
    pre: ({ node, children }) => {
      const code = (node?.children || []).find(
        (child) => child.tagName === "code",
      );
      const text = SHELL.has(languageOf(code))
        ? textOf(code).replace(/\n+$/, "")
        : "";
      if (text) {
        return (
          <Box className="hub-md-command" sx={{ my: 1.5 }}>
            <CopyCommand text={text} />
          </Box>
        );
      }
      return <pre>{children}</pre>;
    },
  };
  return (
    <Box
      data-testid={testId}
      sx={{
        typography: "s1_2",
        color: "text.primary",
        overflowWrap: "anywhere",
        "& > :first-child": { mt: 0 },
        "& .hub-md-h1, & .hub-md-h2, & .hub-md-h3, & .hub-md-h4": {
          fontWeight: 600,
          color: "text.primary",
          mt: 3,
          mb: 1,
        },
        "& .hub-md-h1": { typography: "l3", fontWeight: 600 },
        "& .hub-md-h2": { typography: "m2", fontWeight: 600 },
        "& .hub-md-h3": { typography: "s1_2", fontWeight: 600 },
        "& .hub-md-h4, & .hub-md-h5, & .hub-md-h6": {
          typography: "s1",
          fontWeight: 600,
        },
        "& p, & ul, & ol": { my: 1 },
        "& ul, & ol": { pl: 2.5 },
        "& li": { my: 0.5 },
        "& code:not([data-command])": {
          typography: "code_md",
          px: 0.5,
          py: 0.125,
          borderRadius: 0.5,
          bgcolor: "background.neutral",
          border: "1px solid",
          borderColor: "divider",
        },
        "& pre": {
          my: 1.5,
          p: 1.5,
          borderRadius: 1,
          overflowX: "auto",
          bgcolor: "background.neutral",
          border: "1px solid",
          borderColor: "divider",
          typography: "code_md",
          "& code:not([data-command])": {
            p: 0,
            border: 0,
            bgcolor: "transparent",
          },
        },
        "& table": {
          borderCollapse: "collapse",
          display: "block",
          overflowX: "auto",
          my: 1.5,
        },
        "& th, & td": {
          border: "1px solid",
          borderColor: "divider",
          px: 1,
          py: 0.5,
          textAlign: "left",
        },
        "& th": { fontWeight: 600, bgcolor: "background.neutral" },
        "& blockquote": {
          m: 0,
          pl: 1.5,
          borderLeft: "3px solid",
          borderColor: "divider",
          color: "text.secondary",
        },
      }}
    >
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        skipHtml
        components={components}
      >
        {pinReadmeCommands(markdown, commands)}
      </ReactMarkdown>
    </Box>
  );
}

ReadmeView.propTypes = {
  markdown: PropTypes.string,
  resolveImage: PropTypes.func,
  testId: PropTypes.string,
  commands: PropTypes.shape({
    names: PropTypes.arrayOf(PropTypes.string),
    run: PropTypes.string,
    add: PropTypes.string,
  }),
  headingOffset: PropTypes.number,
};
