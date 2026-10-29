import React, { useCallback } from "react";
import PropTypes from "prop-types";
import { Box, IconButton, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { enqueueSnackbar } from "notistack";

const LINE_BG = {
  added: "rgba(34, 154, 22, 0.12)",
  removed: "rgba(183, 33, 54, 0.12)",
  unchanged: "transparent",
  filler: "transparent",
};

function DiffPane({ title, lines, testId }) {
  return (
    <Box
      data-testid={testId}
      sx={{
        flex: 1,
        minWidth: 0,
        border: "1px solid",
        borderColor: "divider",
        borderRadius: 1,
        overflow: "hidden",
        display: "flex",
        flexDirection: "column",
      }}
    >
      <Typography
        variant="caption"
        sx={{
          px: 1.5,
          py: 0.75,
          bgcolor: "action.hover",
          borderBottom: "1px solid",
          borderColor: "divider",
          fontWeight: 600,
        }}
      >
        {title}
      </Typography>
      <Box
        component="pre"
        sx={{
          m: 0,
          p: 0,
          flex: 1,
          overflow: "auto",
          fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
          fontSize: 11,
          lineHeight: 1.6,
          maxHeight: 260,
        }}
      >
        {(lines || []).map((line, index) => (
          <Box
            // aligned rows: index is stable across left/right
            // eslint-disable-next-line react/no-array-index-key
            key={`${line.lineNumber ?? "g"}-${index}`}
            sx={{
              display: "flex",
              bgcolor: LINE_BG[line.type] || "transparent",
              px: 1,
            }}
          >
            <Box
              component="span"
              sx={{
                width: 32,
                flexShrink: 0,
                color: "text.disabled",
                textAlign: "right",
                pr: 1,
                userSelect: "none",
              }}
            >
              {line.lineNumber ?? ""}
            </Box>
            <Box component="span" sx={{ whiteSpace: "pre" }}>
              {line.text || " "}
            </Box>
          </Box>
        ))}
      </Box>
    </Box>
  );
}

DiffPane.propTypes = {
  title: PropTypes.string.isRequired,
  lines: PropTypes.arrayOf(
    PropTypes.shape({
      text: PropTypes.string,
      type: PropTypes.string,
      lineNumber: PropTypes.number,
    }),
  ),
  testId: PropTypes.string,
};

export default function SaveAgentCodeTab({
  fileName,
  currentJson = "",
  aligned = { left: [], right: [] },
  totals = { added: 0, removed: 0 },
  perNode = [],
  hasBaseline = false,
}) {
  const handleCopy = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(currentJson);
      enqueueSnackbar("Definition copied", { variant: "success" });
    } catch (error) {
      enqueueSnackbar("Could not copy definition", { variant: "error" });
    }
  }, [currentJson]);

  const handleDownload = useCallback(() => {
    const blob = new Blob([currentJson], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = fileName || "untitled-agent.json";
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
  }, [currentJson, fileName]);

  return (
    <Stack spacing={1.5} data-testid="save-code-tab">
      <Stack direction="row" alignItems="center" spacing={0.5}>
        <Typography
          variant="body2"
          fontWeight={600}
          data-testid="save-code-filename"
        >
          {fileName}
        </Typography>
        <IconButton
          size="small"
          aria-label="Copy definition"
          onClick={handleCopy}
          data-testid="save-code-copy"
        >
          <Iconify icon="solar:copy-bold" width={16} />
        </IconButton>
        <IconButton
          size="small"
          aria-label="Download definition"
          onClick={handleDownload}
          data-testid="save-code-download"
        >
          <Iconify icon="solar:download-minimalistic-bold" width={16} />
        </IconButton>
      </Stack>

      {!hasBaseline ? (
        <Typography
          variant="body2"
          color="text.secondary"
          data-testid="save-code-empty"
        >
          Nothing to compare yet. The current draft is shown on the right.
        </Typography>
      ) : null}

      <Stack direction="row" spacing={1} alignItems="stretch">
        <DiffPane
          title="Original definition"
          lines={aligned.left}
          testId="save-code-original"
        />
        <DiffPane
          title="Modified"
          lines={aligned.right}
          testId="save-code-modified"
        />
        <Stack
          spacing={1}
          sx={{ width: 160, flexShrink: 0 }}
          data-testid="save-code-summary"
        >
          <Box
            sx={{
              p: 1.25,
              borderRadius: 1,
              bgcolor: "rgba(34, 154, 22, 0.12)",
              color: "success.dark",
            }}
          >
            <Typography variant="subtitle2" data-testid="save-code-added-total">
              +{totals.added} lines
            </Typography>
          </Box>
          <Box
            sx={{
              p: 1.25,
              borderRadius: 1,
              bgcolor: "rgba(183, 33, 54, 0.12)",
              color: "error.dark",
            }}
          >
            <Typography
              variant="subtitle2"
              data-testid="save-code-removed-total"
            >
              -{totals.removed} lines
            </Typography>
          </Box>
          <Stack spacing={0.75} sx={{ maxHeight: 180, overflowY: "auto" }}>
            {perNode.map((node) => (
              <Stack
                key={node.name}
                direction="row"
                justifyContent="space-between"
                data-testid={`save-code-node-stats-${node.name}`}
              >
                <Typography variant="caption" noWrap sx={{ maxWidth: 90 }}>
                  {node.name}
                </Typography>
                <Typography variant="caption">
                  {node.added ? (
                    <Box component="span" sx={{ color: "success.dark" }}>
                      +{node.added}
                    </Box>
                  ) : null}
                  {node.added && node.removed ? " " : null}
                  {node.removed ? (
                    <Box component="span" sx={{ color: "error.dark" }}>
                      -{node.removed}
                    </Box>
                  ) : null}
                  {!node.added && !node.removed ? (
                    <Box component="span" sx={{ color: "text.disabled" }}>
                      0
                    </Box>
                  ) : null}
                </Typography>
              </Stack>
            ))}
          </Stack>
        </Stack>
      </Stack>
    </Stack>
  );
}

SaveAgentCodeTab.propTypes = {
  fileName: PropTypes.string,
  currentJson: PropTypes.string,
  aligned: PropTypes.shape({
    left: PropTypes.array,
    right: PropTypes.array,
  }),
  totals: PropTypes.shape({
    added: PropTypes.number,
    removed: PropTypes.number,
  }),
  perNode: PropTypes.arrayOf(
    PropTypes.shape({
      name: PropTypes.string.isRequired,
      added: PropTypes.number,
      removed: PropTypes.number,
    }),
  ),
  hasBaseline: PropTypes.bool,
};
