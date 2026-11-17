// IT-R5 G1 P3: the code path's recipe parts (PathTabs renders them).
// TryNoKeysLine: the 30 s line above the steps. CopyAllSetupBlock: ONE
// `Copy all setup commands` block (keys masked on screen, real on Copy).
// AgentPointer: step 4's run_rows() diff, the sandbox tool wrapper and what
// skipping the sandbox costs.
import React, { useState } from "react";
import PropTypes from "prop-types";
import { Box, Button, IconButton, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import Term from "src/sections/common/ux/Term";
import ScriptBlock from "./ScriptBlock";
import { CopyLabelButton } from "./PathTabs";

const codeSx = {
  fontFamily: "monospace",
  fontSize: 12,
  px: 0.5,
  borderRadius: 0.5,
  bgcolor: "background.neutral",
  wordBreak: "break-all",
};

/**
 * `Try it without Future AGI keys (your agent still needs its model key):
 * <cmd> [Copy]` (+ the sample rows file).
 */
export function TryNoKeysLine({ command, onCopied, onSampleRows, nRows }) {
  if (!command) return null;
  return (
    <Stack
      direction="row"
      alignItems="center"
      gap={0.75}
      flexWrap="wrap"
      data-testid="try-no-keys"
      sx={{ px: 0.75 }}
    >
      <Typography typography="s2" fontWeight="fontWeightMedium">
        Try it without Future AGI keys (your agent still needs its model key):
      </Typography>
      <Box component="code" sx={codeSx}>
        {command}
      </Box>
      <CopyLabelButton
        text={command}
        variant="text"
        onCopied={onCopied}
        testId="copy-try-no-keys"
        sx={{ px: 0.5, minWidth: 0 }}
      />
      {onSampleRows ? (
        <Button
          size="small"
          onClick={onSampleRows}
          startIcon={<Iconify icon="mdi:download-outline" width={14} />}
          data-testid="download-sample-rows-top"
          sx={{ typography: "s2", px: 0.5, color: "text.secondary" }}
        >
          {`needs sample_rows.jsonl (${nRows || 3} rows)`}
        </Button>
      ) : null}
    </Stack>
  );
}
TryNoKeysLine.propTypes = {
  command: PropTypes.string,
  onCopied: PropTypes.func,
  onSampleRows: PropTypes.func,
  nRows: PropTypes.number,
};

/** ONE block with every setup command; keys masked until revealed. */
export function CopyAllSetupBlock({ recipe, wrap, onCopied }) {
  const [shown, setShown] = useState(false);
  const [open, setOpen] = useState(false);
  if (!recipe?.shown) return null;
  return (
    <Box
      data-testid="copy-all-setup"
      sx={{
        mx: 0.75,
        px: 1,
        py: 0.75,
        borderRadius: 0.75,
        border: "1px solid",
        borderColor: "divider",
      }}
    >
      <Stack direction="row" alignItems="center" gap={0.75} flexWrap="wrap">
        <CopyLabelButton
          text={recipe.copy}
          label="Copy all setup commands"
          onCopied={onCopied}
          testId="copy-all-setup-commands"
        />
        {recipe.hasKeys ? (
          <Typography typography="s2" color="text.secondary">
            Copy includes your real keys
          </Typography>
        ) : null}
        <Box sx={{ flex: 1 }} />
        {open && recipe.hasKeys ? (
          <CustomTooltip
            show
            title={shown ? "Hide the keys" : "Show the keys"}
            size="small"
            placement="top"
          >
            <IconButton
              onClick={() => setShown((v) => !v)}
              aria-label={shown ? "Hide the keys" : "Show the keys"}
              aria-pressed={shown}
              data-testid="copy-all-reveal-keys"
              sx={{ width: 28, height: 28 }}
            >
              <Iconify
                icon={shown ? "mdi:eye-off-outline" : "mdi:eye-outline"}
                width={16}
              />
            </IconButton>
          </CustomTooltip>
        ) : null}
        <Button
          size="small"
          onClick={() => setOpen((v) => !v)}
          aria-expanded={open}
          data-testid="copy-all-setup-toggle"
          sx={{ typography: "s2", color: "text.secondary", px: 0.5 }}
        >
          {open ? "Hide" : "Show"}
        </Button>
      </Stack>
      {open ? (
        <Box sx={{ mt: 0.5 }}>
          <ScriptBlock
            text={shown ? recipe.copy.replace(/\n$/, "") : recipe.shown}
            copyText={recipe.copy}
            language="bash"
            wrap={wrap}
            dense
            noCopy
            testId="copy-all-setup-script"
          />
        </Box>
      ) : null}
    </Box>
  );
}
CopyAllSetupBlock.propTypes = {
  recipe: PropTypes.object,
  wrap: PropTypes.bool,
  onCopied: PropTypes.func,
};

/** Step 4: the two lines to change, the tool wrapper, the skip cost. */
export function AgentPointer({ diff, wrapper, fileName, envVar, wrap }) {
  return (
    <Stack spacing={0.75} data-testid="agent-pointer">
      {diff ? (
        <Box>
          <Typography typography="s2" color="text.secondary">
            {`Your agent takes other input or answers in another field? Change these two lines of ${diff.fn} in ${fileName}:`}
          </Typography>
          <Box
            component="pre"
            data-testid="agent-diff"
            sx={{
              m: 0,
              mt: 0.25,
              px: 1,
              py: 0.5,
              borderRadius: 0.5,
              bgcolor: "background.neutral",
              fontSize: 12,
              whiteSpace: wrap ? "pre-wrap" : "pre",
              overflowX: "auto",
            }}
          >
            {diff.before.map((l) => (
              <Box key={`b-${l}`} sx={{ color: "error.dark" }}>{`- ${l}`}</Box>
            ))}
            {diff.after.map((l) => (
              <Box
                key={`a-${l}`}
                sx={{ color: "success.dark" }}
              >{`+ ${l}`}</Box>
            ))}
          </Box>
        </Box>
      ) : null}
      {wrapper ? (
        <Box>
          <Typography typography="s2" color="text.secondary">
            {`Route each tool to the sandbox, e.g.:`}
          </Typography>
          <Box sx={{ mt: 0.25 }}>
            <ScriptBlock
              text={wrapper}
              language={/^import os/.test(wrapper) ? "python" : "typescript"}
              wrap={wrap}
              dense
              copyTooltip="Copy the tool wrapper"
              testId="agent-tool-wrapper"
            />
          </Box>
        </Box>
      ) : null}
      {envVar ? (
        <Typography
          typography="s2"
          color="text.secondary"
          data-testid="skip-sandbox-cost"
        >
          Skip the sandbox and your tools run against your own systems;{" "}
          <Term
            k="rowChecks"
            extra="They read the sandbox after the run (did the order really change?)."
          >
            end-state checks
          </Term>{" "}
          (did the order really change?) are not scored, only the reply checks
          are.
        </Typography>
      ) : null}
    </Stack>
  );
}
AgentPointer.propTypes = {
  diff: PropTypes.object,
  wrapper: PropTypes.string,
  fileName: PropTypes.string,
  envVar: PropTypes.string,
  wrap: PropTypes.bool,
};
