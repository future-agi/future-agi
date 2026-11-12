import React from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Skeleton from "@mui/material/Skeleton";
import Typography from "@mui/material/Typography";
import { alpha } from "@mui/material/styles";

const IMAGE = /\.(png|jpe?g|gif|webp)$/i;

// A plain-text file viewer with line numbers. It never renders HTML (SPEC
// §5.2): the text goes into the DOM as text. Lines with a finding or a
// reviewer note are marked, and the marks are listed under the line.
export default function FileViewer({ path, text, isLoading, error, marks = [], imageUrl }) {
  if (!path) {
    return (
      <Typography typography="s2" color="text.secondary" sx={{ p: 2 }}>
        Choose a file to read it.
      </Typography>
    );
  }
  if (IMAGE.test(path)) {
    return imageUrl ? (
      <Box sx={{ p: 2 }}>
        <Box component="img" src={imageUrl} alt={path} sx={{ maxWidth: "100%" }} />
      </Box>
    ) : (
      <Typography typography="s2" color="text.secondary" sx={{ p: 2 }}>
        An image file. It is not shown here.
      </Typography>
    );
  }
  if (isLoading) return <Skeleton variant="rounded" height={240} />;
  if (error) {
    return (
      <Typography typography="s2" color="error.main" sx={{ p: 2 }}>
        {error.message || "This file did not load."}
      </Typography>
    );
  }
  const byLine = {};
  for (const mark of marks) {
    const line = Number(mark.line) || 0;
    (byLine[line] = byLine[line] || []).push(mark);
  }
  const lines = String(text ?? "").replace(/\n$/, "").split("\n");
  return (
    <Box
      data-testid="hub-file-viewer"
      data-path={path}
      sx={{
        border: "1px solid",
        borderColor: "divider",
        borderRadius: 1,
        overflow: "auto",
        maxHeight: 560,
        bgcolor: "background.neutral",
      }}
    >
      {(byLine[0] || []).map((mark, i) => (
        <MarkRow key={`0-${i}`} mark={mark} />
      ))}
      <Box component="pre" sx={{ m: 0, py: 1, fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace", fontSize: 12.5 }}>
        {lines.map((line, index) => {
          const number = index + 1;
          const lineMarks = byLine[number] || [];
          return (
            <React.Fragment key={number}>
              <Box
                sx={{
                  display: "flex",
                  bgcolor: (t) => (lineMarks.length ? alpha(t.palette.warning.main, t.palette.mode === "dark" ? 0.16 : 0.3) : "transparent"),
                }}
              >
                <Box component="span" sx={{ width: 44, flexShrink: 0, textAlign: "right", pr: 1.5, color: "text.disabled", userSelect: "none" }}>
                  {number}
                </Box>
                <Box component="span" sx={{ whiteSpace: "pre", pr: 2 }}>
                  {line}
                </Box>
              </Box>
              {lineMarks.map((mark, i) => (
                <MarkRow key={`${number}-${i}`} mark={mark} />
              ))}
            </React.Fragment>
          );
        })}
      </Box>
    </Box>
  );
}

function MarkRow({ mark }) {
  const note = mark.kind === "note";
  return (
    <Box
      data-testid={note ? "hub-file-note" : "hub-file-finding"}
      sx={{
        mx: 1,
        my: 0.5,
        ml: 6,
        px: 1.25,
        py: 0.75,
        borderLeft: "3px solid",
        borderColor: note ? "info.main" : "warning.main",
        bgcolor: "background.paper",
        borderRadius: 0.5,
        whiteSpace: "normal",
        fontFamily: "inherit",
      }}
    >
      <Typography typography="s2" color="text.primary">
        {note ? "Reviewer note: " : `${mark.rule || "finding"}: `}
        {mark.text}
      </Typography>
    </Box>
  );
}

MarkRow.propTypes = { mark: PropTypes.object };

FileViewer.propTypes = {
  path: PropTypes.string,
  text: PropTypes.string,
  isLoading: PropTypes.bool,
  error: PropTypes.object,
  marks: PropTypes.array,
  imageUrl: PropTypes.string,
};
