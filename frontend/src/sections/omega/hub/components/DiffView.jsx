import React from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Typography from "@mui/material/Typography";
import ToneChip from "./ToneChip";

// The diff against the previous approved version (SPEC §4.2 row 9, §5.2):
// highlights first (new keys, new live models, a changed disclosure), then the
// file lists, the manifest field changes and the unified text diff.
export default function DiffView({ diff, previous }) {
  if (!diff) {
    return (
      <Typography typography="s2" color="text.secondary" data-testid="hub-diff-none">
        {previous ? "No changes were recorded against the previous version." : "A new entry: there is no previous version to compare with."}
      </Typography>
    );
  }
  const files = diff.files || {};
  const lines = String(diff.text || "").split("\n");
  return (
    <Box data-testid="hub-diff" sx={{ display: "flex", flexDirection: "column", gap: 1.5 }}>
      <Typography typography="s2" color="text.secondary">
        {`Compared with the approved version ${diff.previous || previous}.`}
      </Typography>
      {(diff.highlights || []).length > 0 && (
        <Box sx={{ display: "flex", gap: 0.75, flexWrap: "wrap" }}>
          {diff.highlights.map((text) => (
            <ToneChip key={text} tone="attention" label={text} />
          ))}
        </Box>
      )}
      <Box sx={{ display: "grid", gap: 0.5 }}>
        {["added", "removed", "changed"].map((kind) =>
          (files[kind] || []).length ? (
            <Typography key={kind} typography="s2" color="text.secondary" sx={{ overflowWrap: "anywhere" }}>
              <Box component="span" sx={{ fontWeight: 600, color: "text.primary", textTransform: "capitalize" }}>
                {`${kind}: `}
              </Box>
              {files[kind].join(", ")}
            </Typography>
          ) : null,
        )}
      </Box>
      {(diff.manifest || []).length > 0 && (
        <Box component="ul" sx={{ m: 0, pl: 2.5 }}>
          {diff.manifest.map((change) => (
            <li key={change.field}>
              <Typography typography="s2" sx={{ overflowWrap: "anywhere" }}>
                <Box component="code" sx={{ fontWeight: 600 }}>
                  {change.field}
                </Box>
                {`: ${JSON.stringify(change.before)} → ${JSON.stringify(change.after)}`}
              </Typography>
            </li>
          ))}
        </Box>
      )}
      {diff.text && (
        <Box
          component="pre"
          sx={{
            m: 0,
            p: 1.5,
            maxHeight: 520,
            overflow: "auto",
            borderRadius: 1,
            border: "1px solid",
            borderColor: "divider",
            bgcolor: "background.neutral",
            fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
            fontSize: 12.5,
          }}
        >
          {lines.map((line, index) => (
            <Box
              key={index}
              component="span"
              sx={{
                display: "block",
                whiteSpace: "pre",
                color: line.startsWith("+") && !line.startsWith("+++")
                  ? "success.dark"
                  : line.startsWith("-") && !line.startsWith("---")
                    ? "error.dark"
                    : line.startsWith("@@")
                      ? "info.main"
                      : "text.primary",
              }}
            >
              {line || " "}
            </Box>
          ))}
        </Box>
      )}
    </Box>
  );
}

DiffView.propTypes = { diff: PropTypes.object, previous: PropTypes.string };
