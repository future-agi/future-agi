// A code block for the drawer (persona fix P1): soft-wrap on by default
// (`Wrap`, remembered per user), auto height with no inner scroll (blocks
// over 40 lines collapse to `Show all 64 lines`), `CopyButton` with the
// in-place check. Masked blocks show one text and copy another.
import React, { useState } from "react";
import PropTypes from "prop-types";
import { Box, Button, IconButton, Stack } from "@mui/material";
// GS-06 (options-truth L3 G10): PrismLight with the languages our blocks
// print, so a page with a code block never pulls every Prism grammar (and
// refractor's full build). Another language renders as plain text.
import { PrismLight as SyntaxHighlighter } from "react-syntax-highlighter";
import bash from "react-syntax-highlighter/dist/esm/languages/prism/bash";
import javascript from "react-syntax-highlighter/dist/esm/languages/prism/javascript";
import json from "react-syntax-highlighter/dist/esm/languages/prism/json";
import python from "react-syntax-highlighter/dist/esm/languages/prism/python";
import toml from "react-syntax-highlighter/dist/esm/languages/prism/toml";
import typescript from "react-syntax-highlighter/dist/esm/languages/prism/typescript";
import yaml from "react-syntax-highlighter/dist/esm/languages/prism/yaml";
import CopyButton from "src/sections/common/ux/CopyButton";
import { useLayoutPrefs } from "src/sections/common/ux/oneview";
import Iconify from "src/components/iconify";

[
  ["bash", bash],
  ["sh", bash],
  ["shell", bash],
  ["javascript", javascript],
  ["js", javascript],
  ["json", json],
  ["python", python],
  ["py", python],
  ["toml", toml],
  ["typescript", typescript],
  ["ts", typescript],
  ["yaml", yaml],
  ["yml", yaml],
].forEach(([name, grammar]) =>
  SyntaxHighlighter.registerLanguage(name, grammar),
);

/** Save `text` as a file named `name` (the browser's download). */
export function downloadText(name, text, type = "text/plain") {
  const url = URL.createObjectURL(new Blob([text ?? ""], { type }));
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

const MONO =
  '"JetBrains Mono", "SFMono-Regular", Menlo, Consolas, "Liberation Mono", monospace';
export const COLLAPSE_OVER = 40;
const COLLAPSED_LINES = 24;
export const WRAP_SURFACE = "newExperiment";

/** `[wrap, setWrap]` remembered in the per-user layout store (default on). */
export function useCodeWrap() {
  const [prefs, patch] = useLayoutPrefs(WRAP_SURFACE);
  const wrap = prefs?.extras?.codeWrap !== false;
  return [wrap, (v) => patch({ extras: { codeWrap: v ? undefined : false } })];
}

export default function ScriptBlock({
  text,
  copyText,
  language = "python",
  copyTooltip = "Copy",
  wrap = true,
  testId,
  dense = false,
  onCopied,
  full = false,
  noCopy = false,
  fileName,
}) {
  const lines = String(text ?? "")
    .replace(/\n$/, "")
    .split("\n");
  // IT-R2 G1 P9: a full-height pane shows every line (it scrolls itself).
  const long = !full && lines.length > COLLAPSE_OVER;
  const [all, setAll] = useState(false);
  const shown = long && !all ? lines.slice(0, COLLAPSED_LINES) : lines;
  return (
    <Box
      data-testid={testId}
      data-lines={lines.length}
      sx={{ position: "relative", maxWidth: "100%", minWidth: 0 }}
    >
      {noCopy ? null : (
        <Box sx={{ position: "absolute", top: 2, right: 2, zIndex: 1 }}>
          {fileName ? (
            <IconButton
              size="small"
              aria-label={`Download ${fileName}`}
              data-testid="script-download"
              onClick={() => downloadText(fileName, copyText ?? text)}
              sx={{ bgcolor: "background.neutral", mr: 0.5 }}
            >
              <Iconify icon="mdi:download" width={16} />
            </IconButton>
          ) : null}
          <CopyButton
            value={copyText ?? text}
            tooltip={copyTooltip}
            onCopied={onCopied}
            sx={{ bgcolor: "background.neutral" }}
          />
        </Box>
      )}
      <SyntaxHighlighter
        useInlineStyles={false}
        language={language === "bash" ? "bash" : language}
        wrapLongLines={wrap}
        customStyle={{
          fontSize: "12px",
          lineHeight: 1.55,
          borderRadius: "8px",
          textAlign: "left",
          border: "1px solid var(--border-default)",
          padding: dense ? "6px 40px 6px 10px" : "10px 40px 10px 12px",
          backgroundColor: "var(--bg-neutral)",
          color: "var(--text-primary)",
          textShadow: "none",
          fontFamily: MONO,
          margin: 0,
          overflow: "hidden",
          maxWidth: "100%",
          minWidth: 0,
          whiteSpace: wrap ? "pre-wrap" : "pre",
          wordBreak: wrap ? "break-word" : "normal",
          overflowWrap: wrap ? "anywhere" : "normal",
          boxSizing: "border-box",
        }}
        codeTagProps={{
          style: {
            color: "var(--text-primary)",
            textShadow: "none",
            background: "transparent",
            fontFamily: MONO,
            whiteSpace: wrap ? "pre-wrap" : "pre",
          },
        }}
      >
        {shown.join("\n")}
      </SyntaxHighlighter>
      {long ? (
        <Stack direction="row" justifyContent="center" sx={{ mt: -0.5 }}>
          <Button
            size="small"
            onClick={() => setAll((v) => !v)}
            sx={{ typography: "s2", color: "text.secondary" }}
            aria-expanded={all}
          >
            {all ? "Show fewer lines" : `Show all ${lines.length} lines`}
          </Button>
        </Stack>
      ) : null}
    </Box>
  );
}

ScriptBlock.propTypes = {
  text: PropTypes.string.isRequired,
  copyText: PropTypes.string,
  language: PropTypes.string,
  copyTooltip: PropTypes.string,
  wrap: PropTypes.bool,
  testId: PropTypes.string,
  dense: PropTypes.bool,
  /** Additive (IT-R2 G1 P9): never collapse (the full-height pane). */
  full: PropTypes.bool,
  /** Additive (IT-R1 G1 P5): called after a copy (the waiting timer starts). */
  onCopied: PropTypes.func,
  /** Additive (IT-R3 G1 P2): no copy icon (a labelled Copy sits beside it). */
  noCopy: PropTypes.bool,
  /** Additive (ot-L1 G2): a download button that saves the text as this file. */
  fileName: PropTypes.string,
};
