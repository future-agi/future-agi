import PropTypes from "prop-types";
import { Box, Typography } from "@mui/material";
import { ALK_MONO } from "../alkTokens";
import CodeBlock from "./CodeBlock";

/**
 * The stages emit markdown — headings, lists, fenced code and tables. Rendered as plain text
 * a generated table arrives as a wall of pipes, so it is parsed here rather than shown raw.
 *
 * Deliberately small: only the subset the harness actually emits, no library. A streaming
 * paragraph stays plain text and is promoted once the turn ends.
 */
const inline = (text) => {
  const parts = [];
  const pattern = /(\*\*[^*]+\*\*|\*[^*]+\*|`[^`]+`)/g;
  let last = 0;
  let match = pattern.exec(text);
  while (match) {
    if (match.index > last) parts.push(text.slice(last, match.index));
    const token = match[0];
    if (token.startsWith("**")) {
      parts.push(<strong key={parts.length}>{token.slice(2, -2)}</strong>);
    } else if (token.startsWith("`")) {
      parts.push(
        <Box
          key={parts.length}
          component="code"
          sx={{ fontFamily: ALK_MONO, fontSize: "0.9em", bgcolor: "action.hover", px: 0.4, borderRadius: "3px" }}
        >
          {token.slice(1, -1)}
        </Box>
      );
    } else {
      parts.push(<em key={parts.length}>{token.slice(1, -1)}</em>);
    }
    last = match.index + token.length;
    match = pattern.exec(text);
  }
  if (last < text.length) parts.push(text.slice(last));
  return parts;
};

const splitRow = (line) =>
  line
    .trim()
    .replace(/^\||\|$/g, "")
    .split("|")
    .map((cell) => cell.trim());

const Markdown = ({ text }) => {
  const lines = String(text ?? "").split("\n");
  const blocks = [];
  let index = 0;

  while (index < lines.length) {
    const line = lines[index];

    if (line.trimStart().startsWith("```")) {
      const body = [];
      index += 1;
      while (index < lines.length && !lines[index].trimStart().startsWith("```")) {
        body.push(lines[index]);
        index += 1;
      }
      index += 1;
      blocks.push(<CodeBlock key={blocks.length}>{body.join("\n")}</CodeBlock>);
      continue;
    }

    // A table needs its separator row to be a table at all.
    if (line.includes("|") && /^\s*\|?[\s:-]*-[-\s:|]*\|?\s*$/.test(lines[index + 1] || "")) {
      const head = splitRow(line);
      index += 2;
      const rows = [];
      while (index < lines.length && lines[index].includes("|")) {
        rows.push(splitRow(lines[index]));
        index += 1;
      }
      blocks.push(
        <Box key={blocks.length} sx={{ overflowX: "auto" }}>
          <Box component="table" sx={{ borderCollapse: "collapse", fontSize: 13, width: "100%" }}>
            <thead>
              <tr>
                {head.map((cell) => (
                  <Box
                    key={cell}
                    component="th"
                    sx={{ textAlign: "left", px: 1, py: 0.5, bgcolor: "action.hover", border: "1px solid", borderColor: "divider" }}
                  >
                    {cell}
                  </Box>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((row, r) => (
                // Markdown rows have no identity beyond their position.
                // eslint-disable-next-line react/no-array-index-key
                <tr key={r}>
                  {row.map((cell, c) => (
                    // eslint-disable-next-line react/no-array-index-key
                    <Box key={c} component="td" sx={{ px: 1, py: 0.5, border: "1px solid", borderColor: "divider" }}>
                      {inline(cell)}
                    </Box>
                  ))}
                </tr>
              ))}
            </tbody>
          </Box>
        </Box>
      );
      continue;
    }

    const heading = line.match(/^(#{1,4})\s+(.*)$/);
    if (heading) {
      blocks.push(
        <Typography key={blocks.length} sx={{ fontWeight: 650, fontSize: 14.5 }}>
          {inline(heading[2])}
        </Typography>
      );
      index += 1;
      continue;
    }

    if (/^\s*[-*]\s+/.test(line) || /^\s*\d+\.\s+/.test(line)) {
      const ordered = /^\s*\d+\.\s+/.test(line);
      const items = [];
      while (index < lines.length && (/^\s*[-*]\s+/.test(lines[index]) || /^\s*\d+\.\s+/.test(lines[index]))) {
        items.push(lines[index].replace(/^\s*(?:[-*]|\d+\.)\s+/, ""));
        index += 1;
      }
      blocks.push(
        <Box
          key={blocks.length}
          component={ordered ? "ol" : "ul"}
          // Same size as the paragraphs around it — a bare li inherits the root 16px
          // and reads as a different document.
          sx={{ m: 0, pl: 2.5, typography: "body2" }}
        >
          {items.map((item) => (
            <li key={item}>{inline(item)}</li>
          ))}
        </Box>
      );
      continue;
    }

    if (line.trim() === "---") {
      blocks.push(<Box key={blocks.length} sx={{ borderTop: "1px solid", borderColor: "divider" }} />);
      index += 1;
      continue;
    }

    if (line.trim() === "") {
      index += 1;
      continue;
    }

    const paragraph = [];
    while (index < lines.length && lines[index].trim() !== "" && !lines[index].trimStart().startsWith("```")) {
      paragraph.push(lines[index]);
      index += 1;
    }
    blocks.push(
      <Typography key={blocks.length} variant="body2" sx={{ whiteSpace: "pre-wrap" }}>
        {inline(paragraph.join("\n"))}
      </Typography>
    );
  }

  return <Box sx={{ display: "flex", flexDirection: "column", gap: 1 }}>{blocks}</Box>;
};

Markdown.propTypes = { text: PropTypes.string };

export default Markdown;
