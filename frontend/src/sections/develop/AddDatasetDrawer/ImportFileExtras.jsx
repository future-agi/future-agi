import React, { useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  FormControlLabel,
  Link,
  Stack,
  Switch,
  Typography,
} from "@mui/material";
import { fmtCount } from "src/sections/common/format/fmt";
import { rawSourceLines, rejectedLinesText } from "./importFile";

const RAW_LINES = 40;

/**
 * W3-1, shared by both upload dialogs: `Flatten nested objects`, `Read JSON
 * inside text cells`, the raw source (first 40 lines, numbered, skipped
 * lines underlined) and `Download the skipped lines`.
 */
export default function ImportFileExtras({
  text,
  fileName,
  parsed,
  flatten,
  parseJson,
  onFlatten,
  onParseJson,
  optionsShown = true,
}) {
  const [showRaw, setShowRaw] = useState(false);
  const rejected = parsed?.rejected ?? [];
  const lines = showRaw ? rawSourceLines(text, rejected, RAW_LINES) : [];
  const skippedHref = rejected.length
    ? `data:text/plain;charset=utf-8,${encodeURIComponent(rejectedLinesText(rejected))}`
    : null;
  return (
    <Stack gap={0.75} data-testid="import-file-extras">
      <Stack direction="row" alignItems="center" gap={2} flexWrap="wrap">
        {/* L2 verify 2: where the upload route ignores import_options (dev)
            the two switches are not offered; every value lands as the file
            has it. */}
        {optionsShown ? (
          <>
            <FormControlLabel
              sx={{ m: 0 }}
              control={
                <Switch
                  size="small"
                  checked={Boolean(flatten)}
                  onChange={(e) => onFlatten(e.target.checked)}
                  inputProps={{ "aria-label": "Flatten nested objects" }}
                />
              }
              label={
                <Typography typography="s2">Flatten nested objects</Typography>
              }
            />
            <FormControlLabel
              sx={{ m: 0 }}
              control={
                <Switch
                  size="small"
                  checked={Boolean(parseJson)}
                  onChange={(e) => onParseJson(e.target.checked)}
                  inputProps={{ "aria-label": "Read JSON inside text cells" }}
                />
              }
              label={
                <Typography typography="s2">
                  Read JSON inside text cells
                </Typography>
              }
            />
          </>
        ) : null}
        <Link
          component="button"
          type="button"
          typography="s2"
          onClick={() => setShowRaw((v) => !v)}
          data-testid="import-raw-toggle"
        >
          {showRaw ? "Hide the file" : "Show the file"}
        </Link>
        {skippedHref && (
          <Link
            href={skippedHref}
            download={`${String(fileName ?? "file").replace(/\.[^.]+$/, "")}-skipped-lines.txt`}
            typography="s2"
            data-testid="import-skipped-download"
          >
            {`Download the skipped lines (${fmtCount(rejected.length, "line")})`}
          </Link>
        )}
      </Stack>
      {showRaw && (
        <Box
          data-testid="import-raw-source"
          sx={{
            maxHeight: 220,
            overflow: "auto",
            border: 1,
            borderColor: "divider",
            borderRadius: 1,
            fontFamily: "monospace",
            fontSize: 12,
            py: 0.5,
          }}
        >
          {lines.map((l) => (
            <Box
              key={l.no}
              sx={{ display: "flex", gap: 1.5, px: 1, whiteSpace: "pre" }}
            >
              <Box
                component="span"
                sx={{
                  color: "text.disabled",
                  minWidth: 28,
                  textAlign: "right",
                  userSelect: "none",
                }}
              >
                {l.no}
              </Box>
              <Box
                component="span"
                data-bad={l.bad ? "true" : undefined}
                title={
                  l.bad
                    ? rejected.find((r) => r.line === l.no)?.reason
                    : undefined
                }
                sx={
                  l.bad
                    ? {
                        textDecoration: "underline wavy",
                        textDecorationColor: (t) => t.palette.error.main,
                        color: "error.main",
                      }
                    : undefined
                }
              >
                {l.text || " "}
              </Box>
            </Box>
          ))}
        </Box>
      )}
    </Stack>
  );
}

ImportFileExtras.propTypes = {
  text: PropTypes.string,
  fileName: PropTypes.string,
  parsed: PropTypes.object,
  flatten: PropTypes.bool,
  parseJson: PropTypes.bool,
  onFlatten: PropTypes.func.isRequired,
  optionsShown: PropTypes.bool,
  onParseJson: PropTypes.func.isRequired,
};
