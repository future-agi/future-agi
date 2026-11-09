// Section `Run it`, code kind. IT-R3 G1 P2: one clear path from code to
// the first result. Top: the two ways (`Connect this machine` · Preview |
// `Run a script` · works today). The script is a numbered accordion — only
// the current step open, a done step one line (`1 Save the runner ·
// copied`), each step ends with `Next: <step>`: 1 Save the runner ·
// 2 Install and set keys · 3 Start the sandbox · 4 Point it at your agent
// (the contract in three lines) · 5 Run it (`1 row | All 24 rows`). When
// the run connects, the live status comes first and the script folds under
// `Run without connecting`. (Name kept; props are additive.)
import React, { useEffect, useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  ButtonBase,
  Collapse,
  Drawer,
  IconButton,
  Link,
  Stack,
  ToggleButton,
  ToggleButtonGroup,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import { VOCAB } from "src/sections/common/vocab";
import { copyText, useCopiedFlash } from "src/sections/common/ux/CopyButton";
import { notify } from "src/sections/common/ux/toast";
import { snippetFor } from "./snippets";
import {
  agentContract,
  dryRunNote,
  sampleRowsJsonl,
} from "./snippets/runLocal";
import ScriptBlock, { useCodeWrap } from "./ScriptBlock";
import { useWizardLayer } from "./pickerUi";
import { SimulatedTag } from "./TodayStatus";
import { STAND_IN_EVENTS } from "src/sections/common/Connect/connectCopy";
import { SANDBOX_HEALTH_URL } from "./snippets/sandbox";
import {
  agentDiff,
  copyAllSetup,
  orderLine,
  toolWrapper,
  tryNoKeysCommand,
} from "./setupRecipe";
import {
  AgentPointer,
  CopyAllSetupBlock,
  TryNoKeysLine,
} from "./SetupRecipeParts";

const TOP_CENTER = { vertical: "top", horizontal: "center" };

/** The one sentence under `Run on your machine` (P3, verbatim). */
export const RUN_LOCAL_SENTENCE =
  "What happens: your machine runs the agent on each row; the script sends each trace and result to Future AGI; this page fills in as rows finish.";
/** The panel's one status line (P3: never a tag per line). */
export const RUN_LOCAL_STATUS = VOCAB.PROTOTYPE_FOOTER;
export const LIVE_COMING_SOON = VOCAB.PROTOTYPE_FOOTER;

function StepHead({ step, right, bare = false }) {
  if (bare)
    return (
      <Stack
        direction="row"
        alignItems="center"
        gap={0.75}
        sx={{ minWidth: 0 }}
      >
        <Box sx={{ flex: 1 }} />
        {right}
      </Stack>
    );
  return (
    <Stack direction="row" alignItems="center" gap={0.75} sx={{ minWidth: 0 }}>
      <Box
        sx={{
          width: 20,
          height: 20,
          borderRadius: "50%",
          bgcolor: "background.neutral",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          typography: "s3",
          fontWeight: "fontWeightSemiBold",
          flexShrink: 0,
        }}
      >
        {step.n}
      </Box>
      <Typography typography="s1" fontWeight="fontWeightSemiBold" noWrap>
        {step.title}
      </Typography>
      {step.note ? (
        <Typography
          typography="s3"
          color="text.secondary"
          noWrap
          sx={{ minWidth: 0 }}
        >
          {`· ${step.note}`}
        </Typography>
      ) : null}
      <Box sx={{ flex: 1 }} />
      {right}
    </Stack>
  );
}
StepHead.propTypes = {
  step: PropTypes.object.isRequired,
  right: PropTypes.node,
  bare: PropTypes.bool,
};

/** Install and set keys: masked keys on screen (eye shows them), real keys
 * on Copy. `bare` (IT-R3 G1 P2): no heading (the accordion step has it). */
function InstallStep({ step, wrap, onCopied, bare = false }) {
  const [shown, setShown] = useState(false);
  return (
    <Stack spacing={0.5} data-step={bare ? undefined : step.id}>
      <StepHead
        step={bare ? { ...step, n: null, title: null } : step}
        bare={bare}
        right={
          <Stack direction="row" alignItems="center" gap={0.5}>
            <CustomTooltip
              show
              title={shown ? "Hide the keys" : "Show the keys"}
              size="small"
              placement="top"
            >
              <IconButton
                size="small"
                aria-label={shown ? "Hide the keys" : "Show the keys"}
                aria-pressed={shown}
                onClick={() => setShown((v) => !v)}
                data-testid="reveal-keys"
              >
                <Iconify
                  icon={shown ? "mdi:eye-off-outline" : "mdi:eye-outline"}
                  width={16}
                />
              </IconButton>
            </CustomTooltip>
            {step.keysLink ? (
              <Link
                href={step.keysLink}
                target="_blank"
                rel="noopener"
                typography="s2"
                sx={{
                  flexShrink: 0,
                  display: "inline-flex",
                  alignItems: "center",
                  gap: 0.25,
                }}
              >
                Get your keys
                <Iconify icon="mdi:open-in-new" width={12} />
              </Link>
            ) : null}
          </Stack>
        }
      />
      <Box sx={{ pl: bare ? 0 : 3.5 }}>
        <ScriptBlock
          text={shown ? step.revealText ?? step.code : step.code}
          copyText={step.copyText}
          language="bash"
          wrap={wrap}
          dense
          copyTooltip="Copies the full key"
          testId={`step-code-${step.id}`}
          onCopied={onCopied}
        />
      </Box>
    </Stack>
  );
}
InstallStep.propTypes = {
  step: PropTypes.object.isRequired,
  wrap: PropTypes.bool,
  onCopied: PropTypes.func,
  bare: PropTypes.bool,
};

/** `--dry-run` · what it does · the command (copyable). */
function DryLine({ command, flag, text }) {
  return (
    <Box>
      <Typography typography="s2" color="text.secondary">
        <Box component="code" sx={{ fontSize: 12, color: "text.primary" }}>
          {flag}
        </Box>{" "}
        {text}
      </Typography>
      {command ? (
        <Box sx={{ mt: 0.25 }}>
          <ScriptBlock
            text={command}
            language="bash"
            dense
            copyTooltip="Copy"
          />
        </Box>
      ) : null}
    </Box>
  );
}
DryLine.propTypes = {
  command: PropTypes.string,
  flag: PropTypes.string,
  text: PropTypes.string,
};

/** IT-R2 G1 P3 · `Start the sandbox`: what it is, then the commands. */
function SandboxStep({ step, wrap, onCopied, bare = false }) {
  return (
    <Stack spacing={0.5} data-step={bare ? undefined : step.id}>
      {bare ? null : <StepHead step={step} />}
      <Box sx={{ pl: bare ? 0 : 3.5 }}>
        <Typography
          typography="s2"
          color="text.secondary"
          sx={{ mb: 0.5 }}
          data-testid="sandbox-explain"
        >
          {step.explain}
        </Typography>
        <ScriptBlock
          text={step.code}
          language="bash"
          wrap={wrap}
          dense
          copyTooltip="Copy"
          testId={`step-code-${step.id}`}
          onCopied={onCopied}
        />
        {step.file ? (
          <Button
            size="small"
            onClick={() =>
              downloadText(step.fileName, step.file, "text/x-python")
            }
            startIcon={<Iconify icon="mdi:download-outline" width={16} />}
            data-testid="download-sandbox-server"
            sx={{ typography: "s2", mt: 0.5 }}
          >
            {`Download ${step.fileName}`}
          </Button>
        ) : null}
      </Box>
    </Stack>
  );
}
SandboxStep.propTypes = {
  step: PropTypes.object.isRequired,
  wrap: PropTypes.bool,
  onCopied: PropTypes.func,
  bare: PropTypes.bool,
};

/** Save `text` as a file (wrapped: blocked downloads say so, once). */
export function downloadText(name, text, type = "text/plain") {
  try {
    const url = URL.createObjectURL(new Blob([text ?? ""], { type }));
    const a = document.createElement("a");
    a.href = url;
    a.download = name;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    return true;
  } catch {
    notify("Couldn't save the file. Your browser blocked the download.", {
      variant: "error",
      key: `download-${name}`,
      anchorOrigin: TOP_CENTER,
    });
    return false;
  }
}

const lineCount = (text) =>
  String(text ?? "")
    .replace(/\n$/, "")
    .split("\n").length;

/**
 * IT-R3 G1 P2 (c): a Copy button whose own label confirms the copy for
 * 1.5 s (`Copied · 217 lines`); the toast is only the fallback for a
 * blocked clipboard.
 */
export function CopyLabelButton({
  text,
  label = "Copy",
  onCopied,
  testId,
  variant = "outlined",
  sx,
}) {
  const [copied, flash] = useCopiedFlash();
  const onClick = async () => {
    const ok = await copyText(text ?? "");
    if (ok) {
      flash();
      onCopied?.(text);
    } else
      notify("Couldn't copy. Your browser blocked the clipboard.", {
        variant: "error",
        key: `copy-${testId ?? label}`,
        anchorOrigin: TOP_CENTER,
      });
  };
  const n = lineCount(text);
  return (
    <Button
      size="small"
      variant={variant}
      onClick={onClick}
      startIcon={
        <Iconify
          icon={copied ? "mdi:check" : "mdi:content-copy"}
          width={14}
          sx={{ color: copied ? "green.600" : undefined }}
        />
      }
      data-testid={testId}
      aria-live="polite"
      sx={{ typography: "s2", whiteSpace: "nowrap", ...sx }}
    >
      {copied ? `Copied · ${n} ${n === 1 ? "line" : "lines"}` : label}
    </Button>
  );
}
CopyLabelButton.propTypes = {
  text: PropTypes.string,
  label: PropTypes.string,
  onCopied: PropTypes.func,
  testId: PropTypes.string,
  variant: PropTypes.string,
  sx: PropTypes.object,
};

/** A full-height pane over the drawer with one script (IT-R2 G1 P9);
 * IT-R3 G1 P2: the file name in the header, with Download and Copy. */
function ScriptPane({ open, onClose, title, text, language, wrap, onCopied }) {
  useWizardLayer(open);
  return (
    <Drawer
      anchor="right"
      open={open}
      onClose={onClose}
      PaperProps={{
        sx: { width: "min(820px, 92vw)", p: 2, gap: 1, display: "flex" },
      }}
      sx={{ zIndex: 1300 }}
      data-testid="script-pane"
    >
      <Stack direction="row" alignItems="center" gap={1}>
        <Iconify icon="mdi:file-code-outline" width={18} />
        <Typography typography="s1" fontWeight="fontWeightSemiBold">
          {title}
        </Typography>
        <Typography typography="s2" color="text.secondary" sx={{ flex: 1 }}>
          {`${lineCount(text)} lines`}
        </Typography>
        <Button
          size="small"
          variant="outlined"
          onClick={() => downloadText(title, text)}
          startIcon={<Iconify icon="mdi:download-outline" width={16} />}
          data-testid="script-pane-download"
          sx={{ typography: "s2" }}
        >
          Download
        </Button>
        <CopyLabelButton
          text={text}
          onCopied={onCopied}
          testId="script-pane-copy"
        />
        <IconButton size="small" onClick={onClose} aria-label="Close script">
          <Iconify icon="mingcute:close-line" width={18} />
        </IconButton>
      </Stack>
      <Box sx={{ flex: 1, minHeight: 0, overflow: "auto" }}>
        <ScriptBlock
          text={text}
          language={language}
          wrap={wrap}
          copyTooltip={`Copy ${title}`}
          testId="step-code-file"
          onCopied={onCopied}
          full
        />
      </Box>
    </Drawer>
  );
}
ScriptPane.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  title: PropTypes.string,
  text: PropTypes.string,
  language: PropTypes.string,
  wrap: PropTypes.bool,
  onCopied: PropTypes.func,
};

/**
 * IT-R3 G1 P2 (a): the way to run code, at the top of the card. dataset integration
 * FE-0: `Connect this machine` is removed (its command does not exist), so
 * `Run a script` is the only choice.
 */
// eslint-disable-next-line no-unused-vars
export function RunWayChoice({ form, onForm, connected = false }) {
  const options = [
    {
      value: "upload",
      title: "Run a script",
      sub: "Save a runner next to your agent and run it",
      tag: null,
    },
  ];
  const [othersOpen, setOthersOpen] = useState(false);
  const shown = othersOpen ? options : options.filter((o) => o.value === form);
  const others = options.length - shown.length;
  return (
    <Stack spacing={0.5}>
      <Stack
        direction="row"
        gap={1}
        role="radiogroup"
        aria-label="How to run your code"
        data-testid="run-way-choice"
      >
        {shown.map((o) => {
          const on = form === o.value;
          return (
            <ButtonBase
              key={o.value}
              role="radio"
              aria-checked={on}
              onClick={() => onForm(o.value)}
              data-testid={`run-way-${o.value}`}
              sx={{
                flex: 1,
                minWidth: 0,
                justifyContent: "flex-start",
                textAlign: "left",
                gap: 1,
                px: 1.25,
                py: 0.75,
                borderRadius: 0.75,
                border: "1px solid",
                borderColor: on ? "primary.main" : "divider",
                bgcolor: on ? "purple.o5" : "transparent",
              }}
            >
              <Iconify
                icon={on ? "mdi:radiobox-marked" : "mdi:radiobox-blank"}
                width={18}
                sx={{
                  color: on ? "primary.main" : "text.disabled",
                  flexShrink: 0,
                }}
              />
              <Box sx={{ minWidth: 0, flex: 1 }}>
                <Stack direction="row" alignItems="center" gap={0.75}>
                  <Typography
                    typography="s1"
                    fontWeight="fontWeightSemiBold"
                    noWrap
                  >
                    {o.title}
                  </Typography>
                </Stack>
                <Typography
                  typography="s2"
                  color="text.secondary"
                  noWrap
                  title={o.sub}
                >
                  {o.sub}
                </Typography>
              </Box>
            </ButtonBase>
          );
        })}
      </Stack>
      {others > 0 || othersOpen ? (
        <Box>
          <Link
            component="button"
            type="button"
            typography="s2"
            onClick={() => setOthersOpen((v) => !v)}
            aria-expanded={othersOpen}
            data-testid="other-ways-to-run"
            sx={{ color: "text.secondary" }}
          >
            {othersOpen ? "Fewer ways to run" : `Other ways to run (${others})`}
          </Link>
        </Box>
      ) : null}
    </Stack>
  );
}
RunWayChoice.propTypes = {
  form: PropTypes.string,
  onForm: PropTypes.func.isRequired,
  connected: PropTypes.bool,
};

/**
 * IT-R3 G1 P2 (b): one accordion step. Collapsed: `1 Save the runner ·
 * copied` (a check when done; click to reopen). Open: the body, then
 * `Next: <step>`.
 */
function AccordionStep({
  n,
  id,
  title,
  open,
  done,
  onOpen,
  onNext,
  nextTitle,
  right,
  children,
}) {
  return (
    <Box
      data-step={id}
      data-open={open ? "true" : "false"}
      sx={{
        borderRadius: 0.75,
        border: "1px solid",
        borderColor: open ? "divider" : "transparent",
        bgcolor: open ? "background.paper" : "transparent",
      }}
    >
      <Stack
        direction="row"
        alignItems="center"
        gap={0.5}
        sx={{ minWidth: 0, pr: 0.75 }}
      >
        <ButtonBase
          onClick={onOpen}
          aria-expanded={open}
          data-testid={`step-head-${id}`}
          sx={{
            flex: 1,
            minWidth: 0,
            justifyContent: "flex-start",
            gap: 0.75,
            px: 0.75,
            py: 0.6,
            borderRadius: 0.75,
          }}
        >
          <Box
            sx={{
              width: 20,
              height: 20,
              borderRadius: "50%",
              // IT-R4 G1 P3: a checklist (ticks), not a second numbered
              // stepper under the wizard's.
              position: "relative",
              border: "1.5px solid",
              borderColor: done
                ? "success.main"
                : open
                  ? "primary.main"
                  : "text.disabled",
              bgcolor: done ? "success.lighter" : "transparent",
              color: done ? "success.dark" : "text.primary",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              typography: "s3",
              fontWeight: "fontWeightSemiBold",
              flexShrink: 0,
            }}
          >
            {done ? (
              <Iconify icon="mdi:check" width={14} />
            ) : (
              <Box
                component="span"
                sx={{
                  position: "absolute",
                  width: "1px",
                  height: "1px",
                  overflow: "hidden",
                  clip: "rect(0 0 0 0)",
                  whiteSpace: "nowrap",
                }}
              >
                {`Step ${n}`}
              </Box>
            )}
          </Box>
          <Typography
            typography="s1"
            fontWeight="fontWeightSemiBold"
            noWrap
            sx={{ minWidth: 0 }}
          >
            {title}
          </Typography>
          {!open && done ? (
            <Typography typography="s2" color="text.secondary" noWrap>
              {`· ${done}`}
            </Typography>
          ) : null}
          <Box sx={{ flex: 1 }} />
          {!open ? (
            <Iconify
              icon="mdi:chevron-down"
              width={16}
              sx={{ color: "text.disabled", flexShrink: 0 }}
            />
          ) : null}
        </ButtonBase>
        {open && right ? right : null}
      </Stack>
      <Collapse in={open} unmountOnExit>
        <Stack spacing={1} sx={{ pl: 4, pr: 1.25, pb: 1.25, pt: 0.25 }}>
          {children}
          {nextTitle ? (
            <Box>
              <Button
                size="small"
                variant="outlined"
                onClick={onNext}
                endIcon={<Iconify icon="mdi:arrow-right" width={14} />}
                data-testid={`step-next-${id}`}
                sx={{ typography: "s2" }}
              >
                {`Next: ${nextTitle}`}
              </Button>
            </Box>
          ) : null}
        </Stack>
      </Collapse>
    </Box>
  );
}
AccordionStep.propTypes = {
  n: PropTypes.number,
  id: PropTypes.string,
  title: PropTypes.string,
  open: PropTypes.bool,
  done: PropTypes.string,
  onOpen: PropTypes.func,
  onNext: PropTypes.func,
  nextTitle: PropTypes.string,
  right: PropTypes.node,
  children: PropTypes.node,
};

/** `All 12 sandbox tools`: the full list (never `+ 9 more`). */
function SandboxToolsLink({ tools }) {
  const [open, setOpen] = useState(false);
  if (!tools?.length) return null;
  return (
    <Box>
      <Link
        component="button"
        type="button"
        typography="s2"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        data-testid="sandbox-tools-link"
      >
        {open ? "Hide the sandbox tools" : `All ${tools.length} sandbox tools`}
      </Link>
      <Collapse in={open} unmountOnExit>
        <Box
          component="ul"
          data-testid="sandbox-tools-list"
          sx={{
            m: 0,
            mt: 0.5,
            pl: 2.5,
            columns: 2,
            typography: "s2",
            color: "text.secondary",
          }}
        >
          {tools.map((t) => (
            <li key={t}>
              <Box
                component="code"
                sx={{ fontSize: 12, color: "text.primary" }}
              >
                {t}
              </Box>
            </li>
          ))}
        </Box>
      </Collapse>
    </Box>
  );
}
SandboxToolsLink.propTypes = { tools: PropTypes.array };

/**
 * @param {{entry, form, onForm, view, onView, lang, onLang, ids,
 *   status?, sample?, onCopied?, rowCount?, rowNoun?, connectPanel?}} props
 *   additive (IT-R1 G1): `status` renders under the run step (the
 *   checklist), `sample` `{rows, columns}` feeds `Download
 *   sample_rows.jsonl`, `onCopied` fires after any copy (the waiting timer
 *   starts there). Additive (IT-R3 G1 P2): `rowCount` / `rowNoun` label
 *   the `1 row | All 24 rows` toggle; `connectPanel` is the live connection
 *   status shown first when the run connects (the script folds under
 *   `Run without connecting`).
 */
export default function PathTabs({
  entry,
  form,
  onForm,
  lang,
  onLang,
  ids,
  status,
  sample,
  onCopied,
  rowCount,
  rowNoun = "row",
  connectPanel,
  scope: scopeProp,
  onScope,
  connected = false,
  reveal = null,
  arrived = false,
  onDone,
}) {
  const [wrap, setWrap] = useCodeWrap();
  const [fullOpen, setFullOpen] = useState(false);
  const [exampleOpen, setExampleOpen] = useState(false);
  const [scriptOpen, setScriptOpen] = useState(false);
  const [scopeOwn, setScopeOwn] = useState("one");
  // IT-R4 G1 P2 (S3): the run tab drives Simulate (`1 task` → rows: 1).
  const scope = scopeProp ?? scopeOwn;
  const setScope = (v) => {
    setScopeOwn(v);
    onScope?.(v);
  };
  const [noSandbox, setNoSandbox] = useState(false);
  const [sandboxCheck, setSandboxCheck] = useState(null);
  // `--no-sandbox` is a runner flag (S3): the commands are rebuilt with it.
  const withFlag = (cmd) =>
    cmd && noSandbox && !/--no-sandbox/.test(cmd)
      ? cmd.replace(/ --dry-run| --mock-tools/g, (f) =>
          f === " --mock-tools" ? "" : f,
        ) + " --no-sandbox"
      : cmd;
  const snippet = snippetFor({
    kind: "code",
    entryId: entry.id,
    form: "upload",
    lang,
    ids,
  });
  // options-truth L2 merge G54: one token per screen. When the host shows
  // the live connection panel, the panel prints the connect commands with
  // the session's own token (Terminal | In your code, Get a new command);
  // this block prints them only without a panel.
  const connect = null;
  const langs = entry.languages ?? ["python"];
  const fileLines = lineCount(snippet.file);
  const stepOf = (id) => snippet.steps.find((x) => x.id === id) ?? null;
  const install = stepOf("install");
  const sandbox = stepOf("sandbox");
  const agent = stepOf("agent");
  const run = stepOf("run");
  const note = dryRunNote({
    sandbox: ids?.sandbox,
    lang,
    entryId: entry.id,
    rows: Math.min(3, sample?.rows?.length || 3),
  });
  const contract = agentContract({
    entry,
    lang,
    ids,
    scriptName: snippet.fileName,
  });
  const steps = [
    { id: "save", title: `Save the runner: ${snippet.fileName}` },
    install ? { id: "install", title: "Install and set keys" } : null,
    sandbox ? { id: "sandbox", title: "Start the sandbox" } : null,
    agent ? { id: "agent", title: "Point it at your agent" } : null,
    run
      ? {
          id: "run",
          // the step follows the scope: `Run 1 task` / `Run all 30 tasks`
          title:
            scope === "one"
              ? `Run 1 ${rowNoun}`
              : rowCount
                ? `Run all ${rowCount} ${rowNoun}${rowCount === 1 ? "" : "s"}`
                : `Run all ${rowNoun}s`,
        }
      : null,
  ].filter(Boolean);
  const [openId, setOpenId] = useState(steps[0].id);
  const [done, setDone] = useState({});
  const mark = (id, word) => setDone((d) => ({ ...d, [id]: word }));
  // IT-R5 G1 P3 (d, e): the footer opens a step (`Simulate 1 task` → step
  // 5, `Open step 2`) by sending `reveal = {id, nonce}`.
  useEffect(() => {
    if (reveal?.id) setOpenId(reveal.id);
  }, [reveal?.id, reveal?.nonce]);
  // IT-R5 G1 P3 (c, rule 40): the run step ticks on the first result.
  useEffect(() => {
    if (arrived) setDone((d) => (d.run ? d : { ...d, run: "first result in" }));
  }, [arrived]);
  useEffect(() => {
    onDone?.(done, {
      noSandbox,
      steps: steps.map((st) => st.id),
      titles: Object.fromEntries(steps.map((st) => [st.id, st.title])),
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [JSON.stringify(done), noSandbox]);
  // IT-R4 G1 P2 (rule 29): a step ticks only on evidence: the runner
  // downloaded / copied, `Mark as done`, or the sandbox answering. Copying
  // a command and `Next` never tick.
  const copied = (id) => (text) => {
    if (id === "save") mark(id, "copied");
    onCopied?.(text);
  };
  const next = (i) => () => setOpenId(steps[i + 1]?.id ?? null);
  const MARKED = "Ticked in your checklist";
  const markDone = (id) => (
    <Box>
      <Button
        size="small"
        variant="text"
        onClick={() => mark(id, MARKED)}
        disabled={Boolean(done[id])}
        startIcon={<Iconify icon="mdi:check" width={14} />}
        data-testid={`mark-done-${id}`}
        sx={{ typography: "s2", px: 0.5 }}
      >
        {done[id] ? MARKED : "Tick in your checklist"}
      </Button>
    </Box>
  );
  const sbIds = ids?.sandbox ?? null;
  // options-truth L1 G2: Check sandbox really calls the local sandbox's
  // /health (sandbox_server.py answers with CORS); when nothing answers,
  // `Simulate the sandbox starting` stands in for it (S2).
  const checkSandbox = async () => {
    setSandboxCheck({ state: "checking" });
    const url = sandbox?.healthUrl ?? SANDBOX_HEALTH_URL;
    const host = new URL(url).host;
    try {
      const ctl = new AbortController();
      const t = setTimeout(() => ctl.abort(), 2500);
      const res = await fetch(url, { signal: ctl.signal });
      clearTimeout(t);
      const body = await res.json();
      if (!res.ok || !body?.ok) throw new Error(String(res.status));
      setSandboxCheck({
        state: "up",
        text: `Sandbox answering on ${host} · ${body.tools} tools`,
      });
      mark("sandbox", "sandbox answering");
    } catch {
      setSandboxCheck({ state: "down", text: `Not reachable on ${host}` });
    }
  };
  const simulateSandbox = () => {
    const host = new URL(sandbox?.healthUrl ?? SANDBOX_HEALTH_URL).host;
    setSandboxCheck({
      state: "up",
      simulated: true,
      text: `Sandbox answering on ${host} · ${(sbIds?.tools ?? []).length} tools`,
    });
    mark("sandbox", "sandbox answering (simulated)");
  };
  const sandboxCopied = React.useRef(false);
  const toggle = (id) => () => setOpenId((cur) => (cur === id ? null : id));
  const rows = rowCount ?? sample?.rows?.length ?? null;
  const allLabel = rows
    ? `All ${rows} ${rowNoun}${rows === 1 ? "" : "s"}`
    : `All ${rowNoun}s`;
  const runCommand = withFlag(scope === "one" ? snippet.tryCommand : run?.code);
  const sampleRows = sample?.rows ?? [];
  const nSample = Math.min(3, sampleRows.length);

  const body = (step, i) => {
    const common = {
      key: step.id,
      n: i + 1,
      id: step.id,
      title: step.title,
      open: openId === step.id,
      done: done[step.id],
      onOpen: toggle(step.id),
      onNext: next(i),
      nextTitle: steps[i + 1]?.title,
    };
    if (step.id === "save")
      return (
        <AccordionStep {...common}>
          <Typography typography="s2" color="text.secondary">
            {`The runner calls your agent once per ${rowNoun}, traces each call and sends the result to this experiment. Save it in your agent's folder.`}
          </Typography>
          <Typography
            typography="s2"
            color="text.secondary"
            data-testid="runner-order-line"
          >
            {orderLine({ runLabel: ids?.runLabel })}
          </Typography>
          {sbIds ? (
            <Typography
              typography="s2"
              color="text.secondary"
              data-testid="sandbox-why"
            >
              {`This suite runs against a local copy of ${sbIds.org ?? "your"}'s support system${
                sbIds.tables?.length
                  ? ` (${sbIds.tables.slice(0, 2).join(", ")})`
                  : ""
              }, so your agent's tool calls change nothing real.`}
            </Typography>
          ) : null}
          <Stack direction="row" alignItems="center" gap={1} flexWrap="wrap">
            <Button
              size="small"
              variant="outlined"
              onClick={() => {
                if (downloadText(snippet.fileName, snippet.file)) {
                  mark("save", "downloaded");
                  onCopied?.(snippet.file);
                }
              }}
              startIcon={<Iconify icon="mdi:download-outline" width={16} />}
              data-testid="download-runner"
              sx={{ typography: "s2" }}
            >
              {`Download ${snippet.fileName}`}
            </Button>
            <CopyLabelButton
              text={snippet.file ?? ""}
              onCopied={copied("save")}
              testId="copy-complete-script"
            />
            <Button
              size="small"
              onClick={() => setFullOpen(true)}
              aria-haspopup="dialog"
              startIcon={<Iconify icon="mdi:file-code-outline" width={16} />}
              data-testid="show-full-script"
              sx={{ typography: "s2", color: "text.secondary", px: 0.5 }}
            >
              {`Open (${fileLines} lines)`}
            </Button>
            <Typography typography="s3" color="text.secondary">
              Uploads results to POST
              /model-hub/experiments/v2/&#123;experiment_id&#125;/runs/&#123;run_id&#125;/results/
            </Typography>
          </Stack>
        </AccordionStep>
      );
    if (step.id === "install")
      return (
        <AccordionStep {...common}>
          <InstallStep
            step={{ ...install, title: null }}
            wrap={wrap}
            onCopied={copied("install")}
            bare
          />
          {markDone("install")}
        </AccordionStep>
      );
    if (step.id === "sandbox")
      return (
        <AccordionStep
          {...common}
          done={
            noSandbox ? "skipped · your agent uses its own tools" : common.done
          }
        >
          {noSandbox ? (
            <Typography
              typography="s2"
              color="text.secondary"
              data-testid="sandbox-skipped"
            >
              {`Skipped: the commands below run with --no-sandbox. Checks that read the sandbox state${
                sbIds?.stateChecks?.length
                  ? ` (${sbIds.stateChecks.join(", ")})`
                  : ""
              } are skipped.`}
            </Typography>
          ) : (
            <>
              <SandboxStep
                step={sandbox}
                wrap={wrap}
                onCopied={(t) => {
                  sandboxCopied.current = true;
                  copied("sandbox")(t);
                }}
                bare
              />
              <Stack
                direction="row"
                alignItems="center"
                gap={1}
                flexWrap="wrap"
              >
                <Button
                  size="small"
                  variant="outlined"
                  onClick={checkSandbox}
                  disabled={sandboxCheck?.state === "checking"}
                  startIcon={<Iconify icon="mdi:lan-check" width={14} />}
                  data-testid="check-sandbox"
                  sx={{ typography: "s2" }}
                >
                  Check sandbox
                </Button>
                {sandboxCheck && sandboxCheck.state !== "checking" ? (
                  <Typography
                    typography="s2"
                    data-testid="sandbox-check-result"
                    sx={{
                      color:
                        sandboxCheck.state === "up"
                          ? "success.dark"
                          : "warning.dark",
                    }}
                  >
                    {sandboxCheck.text}
                  </Typography>
                ) : null}
                {sandboxCheck?.simulated ? (
                  <SimulatedTag event={STAND_IN_EVENTS.sandbox} />
                ) : null}
                {sandboxCheck?.state === "down" ? (
                  <Stack direction="row" alignItems="center" gap={0.75}>
                    <Button
                      size="small"
                      variant="outlined"
                      onClick={simulateSandbox}
                      data-testid="simulate-sandbox"
                      sx={{ typography: "s2" }}
                    >
                      Simulate the sandbox starting
                    </Button>
                    <SimulatedTag event={STAND_IN_EVENTS.sandbox} />
                  </Stack>
                ) : null}
                {done.sandbox && done.sandbox !== MARKED
                  ? null
                  : markDone("sandbox")}
              </Stack>
            </>
          )}
          <Box>
            <Link
              component="button"
              type="button"
              typography="s2"
              onClick={() => setNoSandbox((v) => !v)}
              data-testid="skip-sandbox"
            >
              {noSandbox
                ? "Use the sandbox"
                : "My agent uses its own tools: skip the sandbox"}
            </Link>
          </Box>
        </AccordionStep>
      );
    if (step.id === "agent")
      return (
        <AccordionStep {...common}>
          <Box
            component="ul"
            data-testid="agent-contract"
            sx={{ m: 0, pl: 2.25, typography: "s2" }}
          >
            {contract.lines.map((l) => (
              <li key={l}>{l}</li>
            ))}
          </Box>
          <SandboxToolsLink tools={ids?.sandbox?.tools} />
          <AgentPointer
            diff={agentDiff(snippet.file, lang)}
            wrapper={
              sbIds
                ? toolWrapper({
                    envVar: sbIds.envVar,
                    tool: sbIds.tools?.[0],
                    lang,
                  })
                : null
            }
            fileName={snippet.fileName}
            envVar={sbIds?.envVar ?? null}
            wrap={wrap}
          />
          <Box>
            <Button
              size="small"
              onClick={() => setExampleOpen((v) => !v)}
              aria-expanded={exampleOpen}
              startIcon={
                <Iconify
                  icon={exampleOpen ? "mdi:chevron-down" : "mdi:chevron-right"}
                  width={16}
                />
              }
              data-testid="show-agent-example"
              sx={{ typography: "s2", color: "text.secondary", px: 0.5 }}
            >
              {exampleOpen
                ? `Hide the example ${agent.fileName}`
                : `Show a ${lineCount(agent.code)}-line example ${agent.fileName}`}
            </Button>
            <Collapse in={exampleOpen} unmountOnExit>
              <Stack spacing={0.75} sx={{ pt: 0.5 }}>
                <Typography
                  typography="s2"
                  fontWeight="fontWeightMedium"
                  data-testid="agent-must"
                >
                  {agent.must}
                </Typography>
                <ScriptBlock
                  text={agent.code}
                  language={agent.lang}
                  wrap={wrap}
                  copyTooltip={`Copy ${agent.fileName}`}
                  testId="step-code-agent"
                />
                <Stack
                  direction="row"
                  alignItems="center"
                  gap={1}
                  flexWrap="wrap"
                >
                  <Button
                    size="small"
                    variant="outlined"
                    disabled={!nSample}
                    onClick={() =>
                      downloadText(
                        "sample_rows.jsonl",
                        sampleRowsJsonl(sampleRows, sample?.columns ?? [], 3),
                        "application/jsonl",
                      )
                    }
                    startIcon={
                      <Iconify icon="mdi:download-outline" width={16} />
                    }
                    data-testid="download-sample-rows"
                    sx={{ typography: "s2" }}
                  >
                    {`Download sample_rows.jsonl (${nSample || 3} rows)`}
                  </Button>
                </Stack>
                <Stack spacing={0.5} data-testid="dry-run-note">
                  <DryLine
                    command={snippet.dryRunCommand}
                    flag="--dry-run"
                    text={note?.dry}
                  />
                  {snippet.mockCommand && note?.mock ? (
                    <DryLine
                      command={snippet.mockCommand}
                      flag="--dry-run --mock-tools"
                      text={note.mock}
                    />
                  ) : null}
                </Stack>
              </Stack>
            </Collapse>
          </Box>
          {markDone("agent")}
        </AccordionStep>
      );
    return (
      <AccordionStep {...common}>
        {/* IT-R5 G1 P3 (a): the no-keys try is the first line above the
            steps (TryNoKeysLine). */}
        <ToggleButtonGroup
          size="small"
          exclusive
          value={scope}
          onChange={(_, v) => v && setScope(v)}
          aria-label="Rows to run"
          data-testid="run-scope"
          sx={{
            alignSelf: "flex-start",
            "& .MuiToggleButton-root": {
              py: 0.25,
              px: 1,
              typography: "s2",
              textTransform: "none",
            },
          }}
        >
          <ToggleButton value="one">{`1 ${rowNoun}`}</ToggleButton>
          <ToggleButton value="all">{allLabel}</ToggleButton>
        </ToggleButtonGroup>
        <Stack direction="row" alignItems="flex-start" gap={1}>
          <Box sx={{ flex: 1, minWidth: 0 }}>
            <ScriptBlock
              text={runCommand ?? ""}
              language="bash"
              wrap={wrap}
              dense
              noCopy
              testId="step-code-run"
            />
          </Box>
          <CopyLabelButton
            text={runCommand ?? ""}
            onCopied={copied("run")}
            testId="copy-run-command"
          />
        </Stack>
        {status}
      </AccordionStep>
    );
  };

  const noKeys = tryNoKeysCommand(snippet, { noSandbox });
  // L3 G4 C4: the pasted setup names the files it needs, by step number
  const stepNo = (id) => steps.findIndex((st) => st.id === id) + 1 || null;
  const recipe = copyAllSetup({
    install,
    sandbox,
    tryCommand: withFlag(snippet.tryCommand),
    noSandbox,
    needs: [
      { file: snippet.fileName, n: stepNo("save") },
      sandbox && !noSandbox
        ? { file: sandbox.fileName, n: stepNo("sandbox") }
        : null,
      agent ? { file: agent.fileName, n: stepNo("agent") } : null,
    ].filter(Boolean),
    // the dataset's noun: `# 2. Run 1 row` on a row dataset
    rowNoun,
  });
  // ot-L1 G2: progress counts proof only (copied, downloaded, check
  // passed, first result in); your own ticks are a checklist beside it.
  const proven = (st) =>
    (done[st.id] && done[st.id] !== MARKED) ||
    (st.id === "sandbox" && noSandbox);
  const nDone = steps.filter(proven).length;
  const nMarked = steps.filter((st) => done[st.id] === MARKED).length;
  const script = (
    <Stack spacing={0.75} data-testid="run-script-steps">
      <TryNoKeysLine
        command={noKeys}
        onCopied={onCopied}
        nRows={nSample}
        onSampleRows={
          nSample
            ? () =>
                downloadText(
                  "sample_rows.jsonl",
                  sampleRowsJsonl(sampleRows, sample?.columns ?? [], 3),
                  "application/jsonl",
                )
            : null
        }
      />
      <CopyAllSetupBlock recipe={recipe} wrap={wrap} onCopied={onCopied} />
      <Typography
        typography="s2"
        fontWeight="fontWeightSemiBold"
        color="text.secondary"
        sx={{ px: 0.75 }}
        data-testid="setup-progress"
      >
        {`Set up ${nDone} of ${steps.length} done`}
        {nMarked ? (
          <Typography
            component="span"
            typography="s2"
            color="text.disabled"
            data-testid="your-checklist"
          >
            {` · Your checklist: ${nMarked} ticked`}
          </Typography>
        ) : null}
      </Typography>
      {steps.map((st, i) => body(st, i))}
    </Stack>
  );

  return (
    <Stack spacing={1.25} data-testid="path-tabs" data-form={form}>
      <RunWayChoice form={form} onForm={onForm} connected={connected} />
      <Stack direction="row" alignItems="center" gap={1} flexWrap="wrap">
        {langs.length > 1 ? (
          <ToggleButtonGroup
            size="small"
            exclusive
            value={lang}
            onChange={(_, v) => v && onLang(v)}
            aria-label="Language"
            sx={{
              "& .MuiToggleButton-root": {
                py: 0.25,
                px: 1,
                typography: "s2",
                textTransform: "none",
              },
            }}
          >
            {langs.map((l) => (
              <ToggleButton key={l} value={l}>
                {l === "python" ? "Python" : "TypeScript"}
              </ToggleButton>
            ))}
          </ToggleButtonGroup>
        ) : null}
        <Box sx={{ flex: 1 }} />
        <CustomTooltip
          show
          title="Wrap long lines in the code below"
          size="small"
          placement="top"
        >
          <Button
            size="small"
            onClick={() => setWrap(!wrap)}
            aria-pressed={wrap}
            startIcon={<Iconify icon="mdi:wrap" width={16} />}
            sx={{
              typography: "s2",
              color: wrap ? "primary.main" : "text.secondary",
            }}
          >
            Wrap lines
          </Button>
        </CustomTooltip>
      </Stack>
      {form === "connect" ? (
        <>
          {connect ? (
            <Stack spacing={0.75} data-testid="connect-commands">
              <Typography typography="s2" color="text.secondary">
                Run these once in your agent&apos;s folder. The connection is
                reused for every run you start from this page.
              </Typography>
              {(connect?.steps ?? []).map((st) => (
                <Stack
                  key={st.id ?? st.n}
                  direction="row"
                  alignItems="flex-start"
                  gap={1}
                >
                  <Typography
                    typography="s2"
                    fontWeight="fontWeightMedium"
                    sx={{ width: 64, flexShrink: 0, pt: 0.6 }}
                  >
                    {st.title}
                  </Typography>
                  <Box sx={{ flex: 1, minWidth: 0 }}>
                    <ScriptBlock
                      text={st.code ?? ""}
                      copyText={st.copyText}
                      language={st.lang ?? "bash"}
                      wrap={wrap}
                      dense
                      copyTooltip="Copy"
                      testId={`connect-code-${st.id ?? st.n}`}
                      onCopied={onCopied}
                    />
                  </Box>
                </Stack>
              ))}
            </Stack>
          ) : null}
          {connectPanel}
          <Box
            sx={{ borderTop: "1px solid", borderColor: "divider", pt: 0.75 }}
          >
            <ButtonBase
              onClick={() => setScriptOpen((v) => !v)}
              aria-expanded={scriptOpen}
              data-testid="run-without-connecting"
              sx={{
                gap: 0.5,
                px: 0.5,
                py: 0.25,
                borderRadius: 0.5,
                typography: "s2",
                color: "text.secondary",
              }}
            >
              <Iconify
                icon={scriptOpen ? "mdi:chevron-down" : "mdi:chevron-right"}
                width={16}
              />
              Run without connecting
            </ButtonBase>
            <Collapse in={scriptOpen} unmountOnExit>
              <Box sx={{ pt: 0.75 }}>{script}</Box>
            </Collapse>
          </Box>
        </>
      ) : (
        script
      )}
      {/* IT-R2 G1 P9: the whole script in a full-height pane (one
          scroller); Escape closes only the pane. */}
      <ScriptPane
        open={fullOpen}
        onClose={() => setFullOpen(false)}
        title={snippet.fileName}
        text={snippet.file ?? ""}
        language={lang === "typescript" ? "typescript" : "python"}
        wrap={wrap}
        onCopied={copied("save")}
      />
    </Stack>
  );
}

PathTabs.propTypes = {
  entry: PropTypes.object.isRequired,
  form: PropTypes.oneOf(["upload", "connect"]).isRequired,
  onForm: PropTypes.func.isRequired,
  view: PropTypes.oneOf(["cli", "code"]),
  onView: PropTypes.func,
  lang: PropTypes.string,
  onLang: PropTypes.func.isRequired,
  ids: PropTypes.object,
  status: PropTypes.node,
  sample: PropTypes.object,
  onCopied: PropTypes.func,
  rowCount: PropTypes.number,
  rowNoun: PropTypes.string,
  connectPanel: PropTypes.node,
  scope: PropTypes.oneOf(["one", "all"]),
  onScope: PropTypes.func,
  connected: PropTypes.bool,
  /** Additive (IT-R5 G1 P3): `{id, nonce}` opens that step. */
  reveal: PropTypes.object,
  /** Additive (IT-R5 G1 P3): the first result arrived (ticks the run step). */
  arrived: PropTypes.bool,
  /** Additive (IT-R5 G1 P3): `(done, {noSandbox})` on every tick. */
  onDone: PropTypes.func,
};
