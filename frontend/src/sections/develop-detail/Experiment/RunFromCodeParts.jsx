// Small pieces shared by the My code card, the Run from code panel, the
// harness card and the linked-traces card (R2 L3a): the `Script | SDK`
// toggle, numbered steps and a syntax-highlighted code block with Copy.
import React from "react";
import PropTypes from "prop-types";
import {
  Box,
  IconButton,
  Stack,
  ToggleButton,
  ToggleButtonGroup,
  Typography,
} from "@mui/material";
import { Prism as SyntaxHighlighter } from "react-syntax-highlighter";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import { enqueueSnackbar } from "src/components/snackbar";
import { copyToClipboard } from "src/utils/utils";

export const MONO =
  "ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace";

export const CODE_MODES = { upload: "upload", sdk: "sdk_preview" };

/** `Script | SDK` (value `upload` | `sdk_preview`). */
export const CodeModeToggle = ({ value, onChange, size = "small" }) => (
  <ToggleButtonGroup
    exclusive
    size={size}
    value={value === CODE_MODES.sdk ? CODE_MODES.sdk : CODE_MODES.upload}
    onChange={(e, v) => v && onChange(v)}
    aria-label="How your code runs"
    sx={{
      "& .MuiToggleButton-root": {
        px: 1.5,
        py: 0.25,
        typography: "s2",
        textTransform: "none",
        gap: 0.5,
      },
    }}
  >
    <ToggleButton value={CODE_MODES.upload}>Script</ToggleButton>
    <ToggleButton value={CODE_MODES.sdk}>SDK</ToggleButton>
  </ToggleButtonGroup>
);
CodeModeToggle.propTypes = {
  value: PropTypes.string,
  onChange: PropTypes.func.isRequired,
  size: PropTypes.string,
};

/** A numbered step: `①  Keys` + body. */
export const Step = ({ n, title, extra, children }) => (
  <Stack spacing={0.75}>
    <Stack direction="row" alignItems="center" gap={1}>
      <Box
        sx={{
          width: 20,
          height: 20,
          borderRadius: "50%",
          border: "1px solid",
          borderColor: "divider",
          bgcolor: "background.neutral",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          flexShrink: 0,
        }}
      >
        <Typography typography="s3" fontWeight="fontWeightSemiBold">
          {n}
        </Typography>
      </Box>
      <Typography typography="s1" fontWeight="fontWeightMedium">
        {title}
      </Typography>
      {extra}
    </Stack>
    <Box sx={{ pl: 3.5, minWidth: 0 }}>{children}</Box>
  </Stack>
);
Step.propTypes = {
  n: PropTypes.number.isRequired,
  title: PropTypes.string.isRequired,
  extra: PropTypes.node,
  children: PropTypes.node,
};

export const copyText = (text, message = "Copied") => {
  copyToClipboard(text);
  enqueueSnackbar(message, { variant: "success" });
};

/**
 * Syntax-highlighted code (the traceAI setup look: Prism token classes from
 * global.css), horizontal scroll, Copy in the corner.
 */
export const CodeBlock = ({
  text,
  language = "python",
  maxHeight = 320,
  testId,
}) => (
  <Box sx={{ position: "relative", maxWidth: "100%", minWidth: 0 }}>
    <CustomTooltip show title="Copy" size="small" type="black">
      <IconButton
        size="small"
        aria-label="Copy code"
        onClick={() => copyText(text)}
        sx={{
          position: "absolute",
          top: 4,
          right: 4,
          zIndex: 1,
          bgcolor: "background.neutral",
        }}
      >
        <Iconify
          icon="basil:copy-outline"
          width={16}
          sx={{ color: "text.secondary" }}
        />
      </IconButton>
    </CustomTooltip>
    <Box data-testid={testId} sx={{ maxHeight, overflow: "auto" }}>
      <SyntaxHighlighter
        useInlineStyles={false}
        language={language}
        customStyle={{
          fontSize: "12px",
          lineHeight: 1.55,
          borderRadius: "8px",
          textAlign: "left",
          border: "1px solid var(--border-default)",
          padding: "10px 40px 10px 12px",
          backgroundColor: "var(--bg-neutral)",
          color: "var(--text-primary)",
          textShadow: "none",
          fontFamily: MONO,
          margin: 0,
          overflowX: "auto",
          maxWidth: "100%",
          minWidth: 0,
          whiteSpace: "pre",
          wordWrap: "normal",
          boxSizing: "border-box",
        }}
        codeTagProps={{
          style: {
            color: "var(--text-primary)",
            textShadow: "none",
            background: "transparent",
            fontFamily: MONO,
          },
        }}
      >
        {text}
      </SyntaxHighlighter>
    </Box>
  </Box>
);
CodeBlock.propTypes = {
  text: PropTypes.string.isRequired,
  language: PropTypes.string,
  maxHeight: PropTypes.number,
  testId: PropTypes.string,
};

/** An ⓘ with a tooltip (field help as tooltips, not paragraphs). */
export const InfoTip = ({ title }) => (
  <CustomTooltip show title={title} placement="top" size="small" arrow>
    <Box
      component="span"
      tabIndex={0}
      aria-label={title}
      sx={{ display: "inline-flex", color: "text.disabled", cursor: "help" }}
    >
      <Iconify icon="mdi:information-outline" width={14} />
    </Box>
  </CustomTooltip>
);
InfoTip.propTypes = { title: PropTypes.string.isRequired };

/** How THIS run starts (howToRunOf): a title and its own command. */
export const ThisRunCommand = ({ way }) =>
  way ? (
    <Stack spacing={0.5} data-testid="this-run-command" data-way={way.way}>
      <Typography typography="s2" fontWeight="fontWeightMedium">
        {way.title}
      </Typography>
      {way.command ? (
        <CodeBlock
          text={way.command}
          language={way.lang ?? "bash"}
          testId="this-run-command-code"
        />
      ) : null}
      {way.note ? (
        <Typography typography="s3" color="text.secondary">
          {way.note}
        </Typography>
      ) : null}
    </Stack>
  ) : null;
ThisRunCommand.propTypes = { way: PropTypes.object };
