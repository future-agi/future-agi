import PropTypes from "prop-types";
import { useEffect, useRef, useState } from "react";
import { enqueueSnackbar } from "notistack";
import { alpha } from "@mui/material/styles";
import {
  Box,
  Button,
  ButtonBase,
  CircularProgress,
  Stack,
  Switch,
  TextField,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import { errorMessage } from "src/pages/dashboard/harness/harnessShared";
import { CreditExhaustionBanner } from "src/components/CreditExhaustionBanner";
import SoonTag from "../../components/SoonTag";
import {
  MAX_SCENARIOS,
  isValidScenarioCount,
} from "../../panels/scenarioCountRules";
import { BUILD_TONES } from "../../buildEnvironment/buildTones";
import { PARALLELISM_COPY } from "../../parallelism.constants";
import { sampleAgentFor, useConnectMode } from "../connectNewAgent";
import {
  BTN_SX,
  INPUT_SX,
  OUTLINED_BTN_SX,
  ROW_BOX_SX,
  T12,
  T13,
  chipSx,
  focusRing,
} from "../drawerStyles";

/*
  The pieces every setup form in the Build environment drawer is made of,
  sized and worded as in Vel's connect forms.
*/

export const MONO = "ui-monospace, SFMono-Regular, Menlo, monospace";

/** A section label above a control: 12px semibold, quiet. */
export function Label({ children }) {
  return (
    <Typography
      sx={{
        ...T12,
        fontWeight: 600,
        color: "text.secondary",
        letterSpacing: 0.4,
      }}
    >
      {children}
    </Typography>
  );
}
Label.propTypes = { children: PropTypes.node };

/** Text with a trailing info icon whose tooltip says the rest. */
export function HintLine({ text, hint }) {
  return (
    <Box
      component="span"
      sx={{ display: "inline-flex", alignItems: "center", gap: 0.5 }}
    >
      {text}
      {hint && (
        <CustomTooltip show title={hint} arrow size="small">
          <Iconify
            icon="solar:info-circle-linear"
            width={14}
            aria-label={hint}
            sx={{ color: "text.disabled", cursor: "help", flexShrink: 0 }}
          />
        </CustomTooltip>
      )}
    </Box>
  );
}
HintLine.propTypes = { text: PropTypes.node, hint: PropTypes.string };

export function Helper({ children, error = false }) {
  if (!children) return null;
  return (
    <Typography
      role={error ? "alert" : undefined}
      sx={{ ...T12, color: error ? "error.main" : "text.secondary", mt: 0.5 }}
    >
      {children}
    </Typography>
  );
}
Helper.propTypes = { children: PropTypes.node, error: PropTypes.bool };

/** A labelled 28px input with its helper line. */
export function Field({
  label,
  required,
  helper,
  error,
  mono,
  multiline,
  minRows = 2,
  value,
  onChange,
  placeholder,
  type,
  inputProps,
  sx,
}) {
  return (
    <Box sx={{ minWidth: 0, ...sx }}>
      {label && (
        <Typography
          component="label"
          sx={{ ...T12, fontWeight: 600, display: "block", mb: 0.5 }}
        >
          {label}
          {required && (
            <Box component="span" sx={{ color: "error.main", ml: 0.5 }}>
              *
            </Box>
          )}
        </Typography>
      )}
      <TextField
        fullWidth
        size="small"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        type={type}
        multiline={multiline}
        minRows={multiline ? minRows : undefined}
        error={!!error}
        autoComplete={type === "password" ? "new-password" : "off"}
        inputProps={{
          "aria-label": typeof label === "string" ? label : undefined,
          spellCheck: false,
          ...inputProps,
        }}
        sx={{
          ...INPUT_SX,
          ...(mono
            ? {}
            : {
                "& .MuiInputBase-input": {
                  fontFamily: "inherit",
                  py: 0.75,
                  px: 1,
                  ...T12,
                },
              }),
        }}
      />
      <Helper error={!!error}>{error || helper}</Helper>
    </Box>
  );
}
Field.propTypes = {
  label: PropTypes.node,
  required: PropTypes.bool,
  helper: PropTypes.node,
  error: PropTypes.node,
  mono: PropTypes.bool,
  multiline: PropTypes.bool,
  minRows: PropTypes.number,
  value: PropTypes.string,
  onChange: PropTypes.func.isRequired,
  placeholder: PropTypes.string,
  type: PropTypes.string,
  inputProps: PropTypes.object,
  sx: PropTypes.object,
};

/** A chip that picks one value: Voice or Chat, Public or Private. */
export function ChipToggle({
  label,
  icon,
  on,
  onClick,
  soon = false,
  fullWidth = false,
}) {
  return (
    <ButtonBase
      aria-pressed={on}
      disabled={soon}
      onClick={onClick}
      sx={{
        ...chipSx(on),
        justifyContent: "flex-start",
        ...(fullWidth && { width: "100%" }),
        "&.Mui-focusVisible": focusRing,
      }}
    >
      {icon && (
        <Iconify
          icon={icon}
          width={14}
          sx={{ color: on ? "text.primary" : "text.secondary" }}
        />
      )}
      <Box component="span" sx={{ color: soon ? "text.disabled" : "inherit" }}>
        {label}
      </Box>
      {soon && <SoonTag />}
    </ButtonBase>
  );
}
ChipToggle.propTypes = {
  label: PropTypes.node,
  icon: PropTypes.string,
  on: PropTypes.bool,
  onClick: PropTypes.func,
  soon: PropTypes.bool,
  fullWidth: PropTypes.bool,
};

/** Two or more short options in a 28px track, as Web or Phone. */
export function SegmentedToggle({ value, onChange, options, label }) {
  return (
    <Stack
      direction="row"
      role="group"
      aria-label={label}
      spacing={0.25}
      sx={{
        p: "2px",
        borderRadius: "6px",
        bgcolor: "action.hover",
        width: "fit-content",
        flexShrink: 0,
        alignSelf: "flex-start",
      }}
    >
      {options.map((o) => {
        const on = value === o.value;
        return (
          <ButtonBase
            key={o.value}
            aria-pressed={on}
            disabled={o.soon}
            onClick={() => onChange(o.value)}
            sx={{
              height: 24,
              px: 1.25,
              gap: 0.75,
              borderRadius: "4px",
              fontSize: 12,
              lineHeight: "24px",
              fontWeight: 500,
              color: on ? "text.primary" : "text.secondary",
              bgcolor: on ? "background.paper" : "transparent",
              boxShadow: (t) =>
                on ? `0 0 0 1px ${t.palette.divider}` : "none",
              "&:hover": { color: "text.primary" },
              "&.Mui-disabled": { color: "text.disabled" },
              "&.Mui-focusVisible": focusRing,
            }}
          >
            {o.label}
            {o.soon && <SoonTag sx={{ height: 18 }} />}
          </ButtonBase>
        );
      })}
    </Stack>
  );
}
SegmentedToggle.propTypes = {
  value: PropTypes.string,
  onChange: PropTypes.func.isRequired,
  options: PropTypes.arrayOf(
    PropTypes.shape({
      value: PropTypes.string,
      label: PropTypes.node,
      soon: PropTypes.bool,
    }),
  ).isRequired,
  label: PropTypes.string,
};

/** A bordered row with a title, a line and a switch at the end. */
export function ToggleRow({
  title,
  body,
  hint,
  checked,
  onChange,
  disabled = false,
  soon = false,
}) {
  return (
    <Stack
      direction="row"
      alignItems="center"
      spacing={1.5}
      sx={{ ...ROW_BOX_SX, px: 1.75, py: 1.25 }}
    >
      <Box flex={1} minWidth={0}>
        <Typography sx={{ ...T12, fontWeight: 600 }}>{title}</Typography>
        {body && (
          <Typography sx={{ ...T12, color: "text.secondary", mt: 0.25 }}>
            <HintLine text={body} hint={hint} />
          </Typography>
        )}
      </Box>
      {soon && <SoonTag />}
      <Switch
        size="small"
        checked={!!checked}
        disabled={disabled || soon}
        onChange={(e) => onChange?.(e.target.checked)}
        inputProps={{ "aria-label": title }}
      />
    </Stack>
  );
}
ToggleRow.propTypes = {
  title: PropTypes.node,
  body: PropTypes.node,
  hint: PropTypes.string,
  checked: PropTypes.bool,
  onChange: PropTypes.func,
  disabled: PropTypes.bool,
  soon: PropTypes.bool,
};

/** A radio card: an icon, a label, a short line, and a check when picked. */
export function WhereOption({
  icon,
  label,
  short,
  hint,
  on,
  onClick,
  soon = false,
}) {
  return (
    <ButtonBase
      role="radio"
      aria-checked={on}
      aria-label={label}
      disabled={soon}
      onClick={onClick}
      sx={{
        display: "flex",
        alignItems: "flex-start",
        gap: 1.25,
        px: 1.5,
        py: 1.25,
        textAlign: "left",
        borderRadius: "6px",
        border: "1px solid",
        borderColor: on ? "text.primary" : "divider",
        bgcolor: (t) =>
          on ? alpha(t.palette.text.primary, 0.08) : "transparent",
        "&.Mui-focusVisible": focusRing,
      }}
    >
      <Iconify
        icon={icon}
        width={16}
        sx={{ color: "text.secondary", mt: 0.25, flexShrink: 0 }}
      />
      <Box flex={1} minWidth={0}>
        <Typography sx={{ ...T12, fontWeight: 600 }}>{label}</Typography>
        <Typography
          component="div"
          sx={{ ...T12, color: "text.secondary", mt: 0.25 }}
        >
          <HintLine text={short} hint={hint} />
        </Typography>
      </Box>
      {soon ? (
        <SoonTag />
      ) : (
        <Iconify
          icon={on ? "solar:check-circle-bold" : "solar:circle-linear"}
          width={16}
          sx={{ color: on ? "text.primary" : "text.disabled", flexShrink: 0 }}
        />
      )}
    </ButtonBase>
  );
}
WhereOption.propTypes = {
  icon: PropTypes.string,
  label: PropTypes.string,
  short: PropTypes.node,
  hint: PropTypes.string,
  on: PropTypes.bool,
  onClick: PropTypes.func,
  soon: PropTypes.bool,
};

/** A count row: title and line on the left, a small number box on the right. */
export function NumberRow({
  title,
  line,
  error,
  value,
  onChange,
  ariaLabel,
  disabled,
}) {
  return (
    <Stack
      direction="row"
      alignItems="center"
      spacing={1.5}
      sx={{ ...ROW_BOX_SX, px: 1.5, py: 1 }}
    >
      <Box flex={1} minWidth={0}>
        <Typography sx={{ ...T12, fontWeight: 600 }}>{title}</Typography>
        <Typography
          sx={{ ...T12, color: error ? "error.main" : "text.secondary" }}
        >
          {error || line}
        </Typography>
      </Box>
      <TextField
        size="small"
        value={value}
        disabled={disabled}
        onChange={(e) => onChange(e.target.value)}
        error={!!error}
        inputProps={{ inputMode: "numeric", "aria-label": ariaLabel || title }}
        sx={{
          ...INPUT_SX,
          width: 96,
          flexShrink: 0,
          "& .MuiInputBase-root": { minHeight: 32 },
        }}
      />
    </Stack>
  );
}
NumberRow.propTypes = {
  title: PropTypes.node,
  line: PropTypes.node,
  error: PropTypes.node,
  value: PropTypes.string,
  onChange: PropTypes.func.isRequired,
  ariaLabel: PropTypes.string,
  disabled: PropTypes.bool,
};

/** How many scenarios the build generates, 1 to 1,000. Digits only. */
export function ScenarioRow({ value, onChange }) {
  const n = Number(value) || 0;
  const ok = isValidScenarioCount(value);
  const onInput = (raw) => {
    const digits = raw.replace(/\D/g, "").replace(/^0+/, "").slice(0, 4);
    onChange(
      digits && Number(digits) > MAX_SCENARIOS ? String(MAX_SCENARIOS) : digits,
    );
  };
  return (
    <NumberRow
      title="Scenarios to generate"
      line={`We'll generate ${n.toLocaleString()} scenario${n === 1 ? "" : "s"} when it builds. Set any number up to ${MAX_SCENARIOS.toLocaleString()}.`}
      error={ok ? null : `Between 1 and ${MAX_SCENARIOS.toLocaleString()}.`}
      value={value}
      onChange={onInput}
    />
  );
}
ScenarioRow.propTypes = {
  value: PropTypes.string,
  onChange: PropTypes.func.isRequired,
};

/** How many scenarios run at once in one sandbox (the build's parallelism). */
export function ParallelRow({ build }) {
  const {
    parallelismEnabled: enabled,
    parallelism,
    parallelismInput,
    admittedParallelism,
  } = build;
  // The notes the old Build tab showed: what a typed-over-the-limit value becomes, and
  // what preflight admits once it has run.
  const notes = [
    enabled &&
      String(parallelismInput ?? "") !== String(parallelism) &&
      `Will use ${parallelism}.`,
    enabled &&
      typeof admittedParallelism === "number" &&
      PARALLELISM_COPY.admitted(admittedParallelism, parallelism),
  ].filter(Boolean);
  return (
    <NumberRow
      title={PARALLELISM_COPY.label}
      line={
        <>
          {enabled
            ? PARALLELISM_COPY.enabledHint
            : PARALLELISM_COPY.disabledHint}
          {notes.map((n) => (
            <Box
              key={n}
              component="span"
              sx={{ display: "block", color: "text.primary" }}
            >
              {n}
            </Box>
          ))}
        </>
      }
      value={enabled ? parallelismInput : "1"}
      onChange={build.setParallelism}
      disabled={!enabled}
    />
  );
}
ParallelRow.propTypes = { build: PropTypes.object.isRequired };

const IDLE_TEXT =
  "Preflight checks the source and credentials before building.";

// The rows a finished preflight lists, from the hosted preflight report.
function checksOf(result) {
  const credentials = result?.credentials;
  if (!credentials) {
    return [
      {
        key: "report",
        label: "Credential report",
        detail: "Preflight did not return one. Re-run before building.",
        ok: false,
      },
    ];
  }
  const requirements = (credentials.requirements || [])
    .filter((r) => r.required || r.status === "configured")
    .map((r) => ({
      key: `req:${r.environment_name}:${r.purpose}`,
      label: r.environment_name,
      detail: `${r.purpose}${r.kind === "file" ? " · Credential file" : ""}`,
      ok: r.status !== "missing",
      word: r.status === "missing" ? "Missing" : "Verified",
    }));
  const choices = (credentials.credential_choices || [])
    .filter((c) => !c.satisfied)
    .map((c) => ({
      key: `choice:${c.id}`,
      label: "Credential set",
      detail: `Provide one: ${c.options.map((o) => o.join(" + ")).join(" or ")}`,
      ok: false,
      word: "Missing",
    }));
  const probes = (credentials.probe || []).map((p) => ({
    key: `probe:${p.provider}:${(p.aliases || []).join(",")}`,
    label: p.label,
    detail: p.message,
    ok: !!p.ok,
  }));
  return [...requirements, ...choices, ...probes];
}

/** The preflight row: a prompt to run it, then what it found. */
export function PreflightRow({
  build,
  canRun,
  blockedReason,
  onRun,
  readyTitle = "Ready to build",
}) {
  const { status } = build;
  if (status === "idle" || status === "running") {
    const running = status === "running";
    return (
      <Stack
        direction="row"
        alignItems="center"
        spacing={1.25}
        sx={{ ...ROW_BOX_SX, px: 1.5, py: 1.125 }}
      >
        {running ? (
          <CircularProgress
            size={14}
            thickness={5}
            sx={{ color: "text.secondary", flexShrink: 0 }}
          />
        ) : (
          <Iconify
            icon="solar:shield-check-linear"
            width={16}
            sx={{ color: "text.secondary", flexShrink: 0 }}
          />
        )}
        <Typography
          sx={{
            ...T12,
            flex: 1,
            color: running ? "text.primary" : "text.secondary",
          }}
        >
          {running
            ? "Checking the source and credentials…"
            : (!canRun && blockedReason) || IDLE_TEXT}
        </Typography>
        <Button
          variant="outlined"
          disabled={!canRun || running}
          onClick={onRun}
          sx={OUTLINED_BTN_SX}
        >
          Run preflight
        </Button>
      </Stack>
    );
  }
  const failedToRun = status === "error";
  const checks = failedToRun ? [] : checksOf(build.result);
  const failed = checks.filter((c) => !c.ok).length;
  const ready = build.readyToSubmit;
  const credentials = build.result?.credentials;
  const title = failedToRun
    ? "Preflight couldn't run"
    : ready
      ? readyTitle
      : failed
        ? `Needs attention: ${failed} of ${checks.length} check${checks.length === 1 ? "" : "s"} failed`
        : "Needs attention";
  const summary = failedToRun
    ? errorMessage(build.error)
    : credentials
      ? `${credentials.scanned_files ?? 0} source files scanned${credentials.detected_connectors?.length ? ` · Detected: ${credentials.detected_connectors.join(", ")}` : ""}`
      : null;
  return (
    <Box sx={{ ...ROW_BOX_SX, overflow: "hidden" }}>
      <Stack
        direction="row"
        alignItems="center"
        spacing={1}
        sx={{
          px: 1.5,
          py: 1,
          borderBottom: checks.length ? "1px solid" : "none",
          borderColor: "divider",
        }}
      >
        <Iconify
          icon={
            ready ? "solar:check-circle-bold" : "solar:danger-triangle-bold"
          }
          width={16}
          sx={{
            color: ready ? BUILD_TONES.green : "error.main",
            flexShrink: 0,
          }}
        />
        <Box flex={1} minWidth={0}>
          <Typography sx={{ ...T12, fontWeight: 600 }}>{title}</Typography>
          {summary && (
            <Typography sx={{ ...T12, color: "text.secondary" }}>
              {summary}
            </Typography>
          )}
        </Box>
        <Button
          onClick={onRun}
          startIcon={<Iconify icon="solar:refresh-linear" width={14} />}
          sx={{ ...BTN_SX, color: "text.secondary", flexShrink: 0 }}
        >
          Re-run
        </Button>
      </Stack>
      {checks.map((c, i) => (
        <Stack
          key={c.key}
          direction="row"
          spacing={1.5}
          sx={{
            px: 1.5,
            py: 0.875,
            borderTop: i ? "1px solid" : "none",
            borderColor: "divider",
          }}
        >
          <Box flex={1} minWidth={0}>
            <Typography
              sx={{ ...T12, fontWeight: 600, overflowWrap: "anywhere" }}
            >
              {c.label}
            </Typography>
            {c.detail && (
              <Typography
                sx={{
                  ...T12,
                  color: "text.secondary",
                  overflowWrap: "anywhere",
                }}
              >
                {c.detail}
              </Typography>
            )}
          </Box>
          <Typography
            sx={{
              ...T12,
              fontWeight: 600,
              pt: 0.25,
              flexShrink: 0,
              color: c.ok ? BUILD_TONES.green : "error.main",
            }}
          >
            {c.word || (c.ok ? "Verified" : "Failed")}
          </Typography>
        </Stack>
      ))}
    </Box>
  );
}
PreflightRow.propTypes = {
  build: PropTypes.object.isRequired,
  canRun: PropTypes.bool,
  blockedReason: PropTypes.string,
  onRun: PropTypes.func.isRequired,
  readyTitle: PropTypes.string,
};

const UI_ONLY_NOTICE =
  "This flow isn't wired to the API yet. Preflight shows sample checks and building is off.";
const CONNECT_NOTICE =
  "Connecting an agent here isn't wired to the API yet. Connect agent adds a sample agent to finish this setup.";

/** The top line of a UI-only form (the header carries its "UI only" pill). */
export function UiOnlyNotice({ text = UI_ONLY_NOTICE }) {
  return (
    <Stack
      direction="row"
      alignItems="center"
      spacing={1.25}
      sx={{ ...ROW_BOX_SX, px: 1.5, py: 1, mb: 1.75, bgcolor: "action.hover" }}
    >
      <Iconify
        icon="solar:info-circle-linear"
        width={16}
        sx={{ color: "text.secondary", flexShrink: 0 }}
      />
      <Typography sx={{ ...T12, color: "text.secondary" }}>{text}</Typography>
    </Stack>
  );
}
UiOnlyNotice.propTypes = { text: PropTypes.string };

const SAMPLE_PREFLIGHT_MS = 900;

/**
 * The build state of a UI-only form, shaped like usePanelBuild so the form
 * switches to the real hook the day its API lands. Preflight returns
 * `sampleChecks` as passed checks; "building" only says it isn't wired.
 */
export function useUiOnlyBuild(sampleChecks = []) {
  const [status, setStatus] = useState("idle");
  const [parallelismInput, setParallelismInput] = useState("1");
  const timer = useRef(null);
  useEffect(() => () => clearTimeout(timer.current), []);
  const resetPreflight = () => {
    clearTimeout(timer.current);
    setStatus("idle");
  };
  const done = status === "done";
  return {
    status,
    result: done
      ? {
          ready_to_submit: true,
          credentials: {
            scanned_files: 0,
            detected_connectors: [],
            requirements: sampleChecks.map((c) => ({
              environment_name: c.label,
              purpose: c.detail,
              required: true,
              status: "configured",
            })),
            credential_choices: [],
            probe: [],
          },
        }
      : null,
    error: null,
    readyToSubmit: done,
    committing: false,
    parallelism: Math.min(
      4,
      Math.max(1, Math.trunc(Number(parallelismInput) || 1)),
    ),
    parallelismInput,
    parallelismEnabled: true,
    admittedParallelism: undefined,
    setParallelism: (v) => {
      setParallelismInput(String(v ?? ""));
      resetPreflight();
    },
    runPreflight: () => {
      setStatus("running");
      timer.current = setTimeout(() => setStatus("done"), SAMPLE_PREFLIGHT_MS);
    },
    resetPreflight,
    commitBuild: () =>
      enqueueSnackbar(
        "UI only: building from this flow isn't wired to the API yet.",
        { variant: "info" },
      ),
    creditExhaustion: { error: null, onUpgrade: () => {}, onDismiss: () => {} },
  };
}

/**
 * The card every setup form sits in: its intro, its fields, the scenario
 * count, parallel worlds and preflight, then a footer that stays at the
 * drawer's bottom edge. `hint` says why the build can't start yet; preflight's
 * own state is added here. While the build is being created the drawer is told
 * (`onBusyChange`) so nothing closes it mid-request.
 *
 * Opened to connect an agent (in connect mode), the form only connects: no
 * scenario count or parallel worlds, a sample preflight that's optional, and
 * "Connect agent" adds a sample agent once the fields are filled. Nothing
 * reaches the build API.
 */
export function FormCard({
  intro,
  children,
  build,
  scenarioCount,
  onScenarioCount,
  canRun,
  blockedReason,
  onRun,
  hint,
  onBusyChange,
  uiOnly = false,
}) {
  const connect = useConnectMode();
  const sample = useUiOnlyBuild(
    connect
      ? [{ label: connect.item.name, detail: "Reached with these details" }]
      : [],
  );
  const shown = connect ? sample : build;
  useEffect(() => {
    onBusyChange?.(shown.committing);
  }, [shown.committing, onBusyChange]);
  useEffect(() => () => onBusyChange?.(false), [onBusyChange]);

  const countOk = isValidScenarioCount(scenarioCount);
  const footerHint = connect
    ? hint || null
    : hint ||
      (!countOk
        ? `Scenarios to generate: 1 to ${MAX_SCENARIOS.toLocaleString()}`
        : !build.readyToSubmit
          ? build.status === "done" || build.status === "error"
            ? "Fix what preflight found, then run it again"
            : "Run preflight first"
          : null);
  const disabled = !!footerHint;

  return (
    <Box
      sx={{
        border: "1px solid",
        borderColor: "divider",
        borderRadius: "8px",
        bgcolor: "background.paper",
        overflow: "clip",
      }}
    >
      <Box sx={{ p: 2.5 }}>
        {connect ? (
          <UiOnlyNotice text={CONNECT_NOTICE} />
        ) : (
          uiOnly && <UiOnlyNotice />
        )}
        {intro && (
          <Typography sx={{ ...T12, color: "text.secondary", mb: 1.75 }}>
            {intro}
          </Typography>
        )}
        <Stack spacing={1.75}>{children}</Stack>
        <Stack spacing={1.25} sx={{ mt: 1.75 }}>
          {!connect && (
            <>
              <ScenarioRow value={scenarioCount} onChange={onScenarioCount} />
              <ParallelRow build={build} />
            </>
          )}
          <PreflightRow
            build={shown}
            canRun={canRun}
            blockedReason={blockedReason}
            onRun={connect ? sample.runPreflight : onRun}
            readyTitle={connect ? "Ready to connect" : undefined}
          />
          <CreditExhaustionBanner
            error={shown.creditExhaustion.error}
            onUpgrade={shown.creditExhaustion.onUpgrade}
            onDismiss={shown.creditExhaustion.onDismiss}
          />
        </Stack>
      </Box>
      <Stack
        direction="row"
        alignItems="center"
        spacing={2}
        sx={{
          px: 2.5,
          py: 1.5,
          borderTop: "1px solid",
          borderColor: "divider",
          position: "sticky",
          bottom: 0,
          zIndex: 2,
          bgcolor: "background.paper",
        }}
      >
        <Box flex={1} />
        {shown.committing ? (
          <Stack direction="row" alignItems="center" spacing={1}>
            <CircularProgress
              size={13}
              thickness={5}
              sx={{ color: "text.secondary" }}
            />
            <Typography sx={{ ...T12, color: "text.secondary" }}>
              Building the environment…
            </Typography>
          </Stack>
        ) : (
          disabled && (
            <Typography sx={{ ...T12, color: "text.secondary" }}>
              {footerHint}
            </Typography>
          )
        )}
        <Button
          variant="contained"
          color="primary"
          disabled={disabled || shown.committing}
          onClick={
            connect
              ? () =>
                  connect.onConnected(
                    sampleAgentFor(connect.item, connect.modality),
                  )
              : build.commitBuild
          }
          startIcon={<Iconify icon="solar:link-circle-linear" width={14} />}
          sx={{
            ...BTN_SX,
            height: 32,
            px: 1.5,
            ...T13,
            fontWeight: 500,
            flexShrink: 0,
          }}
        >
          {connect ? "Connect agent" : "Connect and build environment"}
        </Button>
      </Stack>
    </Box>
  );
}
FormCard.propTypes = {
  // The design without its API yet: sample preflight, no real build.
  uiOnly: PropTypes.bool,
  intro: PropTypes.node,
  children: PropTypes.node,
  build: PropTypes.object.isRequired,
  scenarioCount: PropTypes.string,
  onScenarioCount: PropTypes.func.isRequired,
  canRun: PropTypes.bool,
  blockedReason: PropTypes.string,
  onRun: PropTypes.func.isRequired,
  hint: PropTypes.string,
  onBusyChange: PropTypes.func,
};
