import { Box, Typography, useTheme } from "@mui/material";
import PropTypes from "prop-types";
import PromptPanelHeader from "./PromptPanelHeader";

const LINE_TYPE_ROW_BG = {
  removed: "status.danger.bg",
  added: "status.success.bg",
  modified: "status.warning.bg",
  filler: "action.hover",
  unchanged: "transparent",
};

const DIFF_PART_BG = {
  removed: "status.danger.bg",
  added: "status.success.bg",
  default: "transparent",
};

const DIFF_PART_COLOR = {
  removed: "status.danger.fg",
  added: "status.success.fg",
};

const DiffParts = ({ parts, theme }) =>
  parts.map((part, index) => (
    <Typography
      key={`${part.text}-${index}`}
      component="span"
      typography="s1"
      sx={{
        backgroundColor: DIFF_PART_BG[part.status] || "transparent",
        color: DIFF_PART_COLOR[part.status] || theme.palette.text.primary,
        fontWeight:
          part.status !== "default" ? "fontWeightMedium" : "fontWeightRegular",
        whiteSpace: "pre-wrap",
        wordBreak: "break-word",
      }}
    >
      {part.text}
    </Typography>
  ));

DiffParts.propTypes = {
  parts: PropTypes.arrayOf(
    PropTypes.shape({
      text: PropTypes.string,
      status: PropTypes.oneOf(["added", "removed", "default"]),
    }),
  ).isRequired,
  theme: PropTypes.object.isRequired,
};

const LineRow = ({ line, theme }) => {
  const rowBg = LINE_TYPE_ROW_BG[line.type] || "transparent";
  const showLineNumber = line.lineNumber !== null;

  return (
    <Box
      sx={{
        display: "flex",
        minHeight: "24px",
        lineHeight: "1.5",
        backgroundColor: rowBg,
      }}
    >
      <Box
        sx={{
          width: "34px",
          minWidth: "34px",
          backgroundColor:
            line.type === "filler"
              ? theme.palette.action.hover
              : theme.palette.background.default,
          borderRight: `1px solid ${theme.palette.divider}`,
          userSelect: "none",
          padding: theme.spacing(1, 0.5),
          display: "flex",
          alignItems: "flex-start",
          justifyContent: "center",
        }}
      >
        {showLineNumber && (
          <Typography
            typography="s2"
            color="text.disabled"
            sx={{ lineHeight: "1.5" }}
          >
            {line.lineNumber}
          </Typography>
        )}
      </Box>

      <Box
        sx={{
          flex: 1,
          padding: theme.spacing(1, 2),
          display: "flex",
          alignItems: "flex-start",
        }}
      >
        <Box
          sx={{
            whiteSpace: "pre-wrap",
            wordBreak: "break-word",
            width: "100%",
          }}
        >
          {line.diffParts ? (
            <DiffParts parts={line.diffParts} theme={theme} />
          ) : (
            <Typography
              component="span"
              typography="s1"
              sx={{ whiteSpace: "pre-wrap", wordBreak: "break-word" }}
            >
              {line.text}
            </Typography>
          )}
        </Box>
      </Box>
    </Box>
  );
};

LineRow.propTypes = {
  line: PropTypes.shape({
    text: PropTypes.string,
    type: PropTypes.oneOf([
      "unchanged",
      "added",
      "removed",
      "modified",
      "filler",
    ]),
    lineNumber: PropTypes.number,
    diffParts: PropTypes.array,
  }).isRequired,
  theme: PropTypes.object.isRequired,
};

const PromptPanel = ({ title, prompt, diffLines, panelRef }) => {
  const theme = useTheme();

  const plainLines = (prompt || "").split("\n");

  return (
    <Box
      sx={{
        display: "flex",
        flexDirection: "column",
        border: "1px solid",
        borderColor: "divider",
        borderRadius: "4px",
        width: "100%",
        flex: 1,
        overflow: "hidden",
      }}
    >
      <PromptPanelHeader title={title} prompt={prompt} />
      <Box
        sx={{
          display: "flex",
          flexDirection: "column",
          flex: 1,
          minHeight: 0,
          backgroundColor: theme.palette.background.paper,
        }}
      >
        <Box
          ref={diffLines ? panelRef : undefined}
          sx={{
            display: "flex",
            flexDirection: "column",
            flex: 1,
            minHeight: 0,
            overflowY: "auto",
            overflowX: "auto",
            fontFamily: "monospace",
          }}
        >
          {diffLines
            ? diffLines.map((line, idx) => (
                <LineRow key={`diff-${idx}`} line={line} theme={theme} />
              ))
            : plainLines.map((text, idx) => (
                <Box
                  key={`plain-${idx}`}
                  sx={{
                    display: "flex",
                    minHeight: "24px",
                    lineHeight: "1.5",
                  }}
                >
                  <Box
                    sx={{
                      width: "34px",
                      minWidth: "34px",
                      backgroundColor: theme.palette.background.default,
                      borderRight: `1px solid ${theme.palette.divider}`,
                      userSelect: "none",
                      padding: theme.spacing(1, 0.5),
                      display: "flex",
                      alignItems: "flex-start",
                      justifyContent: "center",
                    }}
                  >
                    <Typography
                      typography="s2"
                      color="text.disabled"
                      sx={{ lineHeight: "1.5" }}
                    >
                      {idx + 1}
                    </Typography>
                  </Box>
                  <Box
                    sx={{
                      flex: 1,
                      padding: theme.spacing(1, 2),
                      display: "flex",
                      alignItems: "flex-start",
                    }}
                  >
                    <Typography
                      component="span"
                      typography="s1"
                      sx={{ whiteSpace: "pre-wrap", wordBreak: "break-word" }}
                    >
                      {text}
                    </Typography>
                  </Box>
                </Box>
              ))}
        </Box>
      </Box>
    </Box>
  );
};

PromptPanel.propTypes = {
  title: PropTypes.string,
  prompt: PropTypes.string,
  diffLines: PropTypes.array,
  panelRef: PropTypes.oneOfType([
    PropTypes.func,
    PropTypes.shape({ current: PropTypes.any }),
  ]),
};

export default PromptPanel;
