import PropTypes from "prop-types";
import { useState } from "react";
import {
  Box,
  Button,
  Checkbox,
  Chip,
  Stack,
  Tooltip,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { CopyField } from "../../components/primitives";
import {
  FIDELITY_ICONS,
  MODE_ICONS,
  RUN_LOCATIONS,
  WEBHOOK_PROVIDERS,
  replicationOf,
  runLocationReason,
} from "../../_mock/replication";

/*
  The copy a simulation drives (PRD v6 Story 1): how the platform makes it,
  what stays untouched, and what results from it can prove. Shared by the
  New agent flows, the environment builder's panels and the agent page.
  Nothing here asks the person to make a copy — it says how we make ours.
*/

const HEAD = {
  typography: "s3",
  fontWeight: 600,
  letterSpacing: 0.4,
  textTransform: "none",
  color: "text.subtitle",
};
const selectedBg = "action.selected";

/* Run fidelity as a neutral pill — the label does the work, not a colour. */
export function FidelityBadge({ fidelity, label }) {
  return (
    <Chip
      size="small"
      variant="outlined"
      label={label}
      icon={
        <Iconify
          icon={FIDELITY_ICONS[fidelity] || "solar:database-linear"}
          width={12}
        />
      }
      sx={{ flexShrink: 0 }}
    />
  );
}
FidelityBadge.propTypes = {
  fidelity: PropTypes.string,
  label: PropTypes.string,
};

/* The explanation behind a fact, on hover — the row itself stays one line. */
export function Hint({ text }) {
  if (!text) return null;
  return (
    <Tooltip arrow title={text}>
      <Box
        component="span"
        sx={{
          display: "inline-flex",
          color: "text.disabled",
          cursor: "help",
          flexShrink: 0,
        }}
      >
        <Iconify icon="solar:info-circle-linear" width={14} />
      </Box>
    </Tooltip>
  );
}
Hint.propTypes = { text: PropTypes.node };

/* One short helper line with the full sentence behind an ⓘ — the shape every
   setup drawer's helpers take. */
export function HintLine({ children, hint }) {
  return (
    <Box
      component="span"
      sx={{
        display: "inline-flex",
        alignItems: "center",
        gap: 0.5,
        maxWidth: "100%",
      }}
    >
      <span>{children}</span>
      <Hint text={hint} />
    </Box>
  );
}
HintLine.propTypes = { children: PropTypes.node, hint: PropTypes.node };

function FactLine({ label, children, first }) {
  return (
    <Stack
      direction={{ xs: "column", sm: "row" }}
      spacing={{ xs: 0.25, sm: 2 }}
      alignItems={{ sm: "center" }}
      sx={{
        px: 1.75,
        py: 0.875,
        borderTop: first ? "none" : "1px solid",
        borderColor: "divider",
      }}
    >
      <Typography
        sx={{
          typography: "s2",
          color: "text.subtitle",
          width: { sm: 120 },
          flexShrink: 0,
        }}
      >
        {label}
      </Typography>
      <Box flex={1} minWidth={0}>
        {children}
      </Box>
    </Stack>
  );
}
FactLine.propTypes = {
  label: PropTypes.string,
  children: PropTypes.node,
  first: PropTypes.bool,
};

/**
 * "How we'll copy it" — read-only. The mode and what it means, what stays
 * untouched, and the run fidelity results will carry. `aside` is a quiet
 * line underneath (e.g. pointing at a staging agent instead).
 */
export function HowWeCopyIt({
  agent,
  env,
  title = "How we'll test it",
  aside,
}) {
  const r = replicationOf(agent, env);
  /* A phone number is only called — nothing is copied, so nothing to agree to. */
  const phone = !!agent?.values?.phoneNumber;
  return (
    <Box
      sx={{
        border: "1px solid",
        borderColor: "divider",
        borderRadius: 1,
        overflow: "hidden",
      }}
    >
      <Stack
        direction="row"
        alignItems="center"
        spacing={1}
        sx={{
          px: 1.75,
          py: 0.875,
          bgcolor: "background.neutral",
          borderBottom: "1px solid",
          borderColor: "divider",
        }}
      >
        <Typography sx={{ ...HEAD, flex: 1 }}>{title}</Typography>
        <FidelityBadge
          fidelity={r.proofKind === "live" ? "live" : r.fidelity}
          label={r.proofLabel}
        />
      </Stack>
      <FactLine label="Copy" first>
        <Stack direction="row" alignItems="center" spacing={0.75}>
          <Iconify
            icon={MODE_ICONS[r.mode]}
            width={14}
            sx={{ color: "text.secondary", flexShrink: 0 }}
          />
          <Typography sx={{ typography: "s2", fontWeight: 600 }}>
            {r.label}
          </Typography>
          <Hint text={r.how} />
        </Stack>
      </FactLine>
      <FactLine label="Left untouched">
        <Typography sx={{ typography: "s2" }}>{r.untouched}</Typography>
      </FactLine>
      <FactLine label="Proof">
        <Stack direction="row" alignItems="center" spacing={0.75}>
          <Typography
            sx={{ typography: "s2", fontWeight: 600 }}
          >{`${r.proofLabel} · ${r.tierLabel}`}</Typography>
          <Hint text={r.proofNote} />
        </Stack>
      </FactLine>
      {r.proofKind === "live" && !phone && (
        <FactLine label="Before the first run">
          <Stack direction="row" alignItems="center" spacing={0.75}>
            <Typography sx={{ typography: "s2" }}>
              {r.mode === "wrapped_endpoint"
                ? "You confirm it may drive your real agent."
                : "You agree to test copies in your account."}
            </Typography>
            <Hint
              text={
                r.mode === "wrapped_endpoint"
                  ? "Acknowledge below that simulations drive your real agent."
                  : "You'll be asked to agree that we create test copies or drafts in your account and call the agent — its tools reach your real systems."
              }
            />
          </Stack>
          {r.pins && !r.pins.pinned && (
            <Typography
              sx={{ typography: "s3", color: "text.subtitle", mt: 0.25 }}
            >{`${r.pins.line}.`}</Typography>
          )}
        </FactLine>
      )}
      <FactLine label="Run fidelity">
        <Stack direction="row" alignItems="center" spacing={0.75}>
          <Typography sx={{ typography: "s2", fontWeight: 600 }}>
            {r.fidelityLabel}
          </Typography>
          <Hint text={r.fidelityNote} />
        </Stack>
      </FactLine>
      {aside && (
        <Typography
          sx={{
            px: 1.75,
            py: 0.875,
            typography: "s3",
            color: "text.subtitle",
            borderTop: "1px solid",
            borderColor: "divider",
          }}
        >
          {aside}
        </Typography>
      )}
    </Box>
  );
}
HowWeCopyIt.propTypes = {
  agent: PropTypes.object,
  env: PropTypes.object,
  title: PropTypes.string,
  aside: PropTypes.node,
};

/**
 * Where a code agent runs (Story 6): our hosted sandbox, where we build it
 * fresh from its code, or your own machine through our SDK. Running on your
 * machine is set up by connecting through the SDK — the agent checks in from
 * there — so it shows here only for an agent that did; code we build offers
 * the hosted sandbox. The cost note is part of each option because it is
 * part of the choice.
 */
/* Each option's note, cut to one line — the full note sits behind the ⓘ. */
const RUN_LOCATION_SHORT = {
  hosted: "Built from your code · metered by the minute",
  sdk: "Local code through our SDK · identified by commit",
};

export function RunLocationChoice({ value = "hosted", onChange, dense }) {
  return (
    <Box>
      <Box sx={{ display: "grid", gap: 1 }}>
        {[value === "sdk" ? "sdk" : "hosted"].map((id) => {
          const loc = RUN_LOCATIONS[id];
          const on = value === id;
          const pick = () => {
            if (value !== id) onChange?.(id);
          };
          return (
            <Stack
              key={id}
              direction="row"
              spacing={1.25}
              alignItems="flex-start"
              role="radio"
              aria-checked={on}
              tabIndex={0}
              onClick={pick}
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === " ") {
                  e.preventDefault();
                  pick();
                }
              }}
              sx={{
                px: 1.5,
                py: dense ? 1 : 1.25,
                borderRadius: 1,
                border: "1px solid",
                cursor: onChange ? "pointer" : "default",
                borderColor: on ? "text.primary" : "divider",
                bgcolor: on ? selectedBg : "transparent",
              }}
            >
              <Iconify
                icon={
                  id === "sdk"
                    ? "solar:laptop-2-linear"
                    : "solar:server-square-linear"
                }
                width={16}
                sx={{ color: "text.secondary", mt: 0.25, flexShrink: 0 }}
              />
              <Box flex={1} minWidth={0}>
                <Typography sx={{ typography: "s2", fontWeight: 600 }}>
                  {loc.label}
                </Typography>
                <Typography
                  component="div"
                  sx={{ typography: "s3", color: "text.subtitle", mt: 0.25 }}
                >
                  <HintLine hint={loc.note}>{RUN_LOCATION_SHORT[id]}</HintLine>
                </Typography>
              </Box>
              <Iconify
                icon={on ? "solar:check-circle-bold" : "solar:circle-linear"}
                width={16}
                sx={{
                  color: on ? "text.primary" : "text.disabled",
                  flexShrink: 0,
                }}
              />
            </Stack>
          );
        })}
      </Box>
      <Typography
        component="div"
        sx={{ typography: "s3", color: "text.subtitle", mt: 0.75 }}
      >
        <HintLine hint={`${runLocationReason(value)}.`}>
          {value === "sdk"
            ? "It runs on your machine and checks in with its commit."
            : "We build it from the code for every run."}
        </HintLine>
      </Typography>
    </Box>
  );
}
RunLocationChoice.propTypes = {
  agent: PropTypes.object,
  env: PropTypes.object,
  value: PropTypes.oneOf(["sdk", "hosted"]),
  onChange: PropTypes.func,
  dense: PropTypes.bool,
};

/**
 * An opaque endpoint can't be copied, so a simulation drives the real agent.
 * Nothing runs until someone says so (AC-1.12); after that, an optional
 * secondary webhook mirrors its call and tool events to us. Skipping keeps
 * fidelity at Observed only, and says so.
 *   value — { acknowledgedAt, webhookAt|null } | null
 */
export function EndpointConsent({
  value,
  onChange,
  webhookUrl,
  showWebhook = true,
  lead,
}) {
  const [provider, setProvider] = useState("server");
  const [skipped, setSkipped] = useState(false);
  const acknowledged = !!value?.acknowledgedAt;
  const webhookAt = value?.webhookAt || null;
  const steps = WEBHOOK_PROVIDERS.find((p) => p.id === provider)?.steps || "";
  /* Where to add it stays in view; the rest of the instructions sit on hover. */
  const cut = steps.indexOf(". ");
  const stepsFirst = cut > 0 ? steps.slice(0, cut + 1) : steps;
  const stepsRest = cut > 0 ? steps.slice(cut + 2) : "";
  const toggle = () =>
    onChange(
      acknowledged
        ? null
        : { acknowledgedAt: new Date().toISOString(), webhookAt: null },
    );

  return (
    <Box
      sx={{
        border: "1px solid",
        borderColor: "divider",
        borderRadius: 1,
        overflow: "hidden",
      }}
    >
      <Stack
        direction="row"
        spacing={1.25}
        alignItems="flex-start"
        sx={{ px: 1.75, py: 1.25 }}
      >
        <Iconify
          icon={
            acknowledged
              ? "solar:check-circle-linear"
              : "solar:danger-triangle-linear"
          }
          width={16}
          sx={{
            color: acknowledged ? "text.secondary" : "status.danger.fg",
            mt: 0.25,
            flexShrink: 0,
          }}
        />
        <Box flex={1} minWidth={0}>
          {lead && !acknowledged && (
            <Typography sx={{ ...HEAD, mb: 0.25 }}>{lead}</Typography>
          )}
          <Typography
            component="div"
            sx={{ typography: "s2", fontWeight: 600 }}
          >
            <HintLine hint="Its real tools run — point it at staging if they shouldn't.">
              Simulations drive your real agent and its tools
            </HintLine>
          </Typography>
          <Stack
            direction="row"
            alignItems="center"
            spacing={0.5}
            onClick={toggle}
            sx={{
              mt: 0.75,
              ml: -0.75,
              cursor: "pointer",
              width: "fit-content",
            }}
          >
            <Checkbox
              size="small"
              checked={acknowledged}
              onChange={toggle}
              onClick={(e) => e.stopPropagation()}
              sx={{ p: 0.5 }}
            />
            <Typography sx={{ typography: "s2", fontWeight: 600 }}>
              I understand — simulations may drive this agent
            </Typography>
          </Stack>
        </Box>
      </Stack>

      {showWebhook && acknowledged && (
        <Box
          sx={{
            px: 1.75,
            py: 1.25,
            borderTop: "1px solid",
            borderColor: "divider",
          }}
        >
          <Stack direction="row" alignItems="baseline" spacing={1}>
            <Typography sx={{ typography: "s2", fontWeight: 600 }}>
              Secondary webhook
            </Typography>
            <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
              Optional
            </Typography>
          </Stack>
          {webhookAt ? (
            <Stack
              direction="row"
              alignItems="center"
              spacing={1}
              sx={{ mt: 0.5 }}
            >
              <Iconify
                icon="solar:check-read-linear"
                width={14}
                sx={{ color: "text.secondary" }}
              />
              <Typography
                component="div"
                sx={{ typography: "s2", flex: 1, minWidth: 0 }}
              >
                <HintLine hint="Call and tool events are mirrored to us.">
                  Added · fidelity Observed + webhook
                </HintLine>
              </Typography>
              <Button
                size="small"
                onClick={() => onChange({ ...value, webhookAt: null })}
                sx={{ color: "text.secondary", minWidth: 0 }}
              >
                Remove
              </Button>
            </Stack>
          ) : (
            <>
              <Typography
                component="div"
                sx={{
                  typography: "s3",
                  color: "text.subtitle",
                  mt: 0.25,
                  mb: 1,
                }}
              >
                <HintLine hint="Add it on your production agent as a second webhook. Runs then show the tool calls behind each turn.">
                  Next to your own webhook, not instead of it.
                </HintLine>
              </Typography>
              <CopyField value={webhookUrl} />
              <Stack
                direction="row"
                spacing={0.5}
                useFlexGap
                flexWrap="wrap"
                sx={{ mt: 1 }}
              >
                {WEBHOOK_PROVIDERS.map((p) => {
                  const on = p.id === provider;
                  return (
                    <Box
                      key={p.id}
                      role="button"
                      tabIndex={0}
                      onClick={() => setProvider(p.id)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter" || e.key === " ") {
                          e.preventDefault();
                          setProvider(p.id);
                        }
                      }}
                      sx={{
                        px: 1,
                        py: 0.375,
                        borderRadius: "4px",
                        cursor: "pointer",
                        border: "1px solid",
                        borderColor: on ? "text.primary" : "divider",
                        bgcolor: on ? selectedBg : "transparent",
                        typography: "s3",
                        fontWeight: 600,
                        color: on ? "text.primary" : "text.secondary",
                      }}
                    >
                      {p.label}
                    </Box>
                  );
                })}
              </Stack>
              <Typography
                component="div"
                sx={{ typography: "s3", color: "text.secondary", mt: 0.75 }}
              >
                {stepsRest ? (
                  <HintLine hint={stepsRest}>{stepsFirst}</HintLine>
                ) : (
                  stepsFirst
                )}
              </Typography>
              <Stack
                direction="row"
                alignItems="center"
                spacing={1}
                sx={{ mt: 1.25 }}
              >
                <Button
                  size="small"
                  variant="outlined"
                  onClick={() => {
                    setSkipped(false);
                    onChange({ ...value, webhookAt: new Date().toISOString() });
                  }}
                  sx={{ color: "text.primary", borderColor: "divider" }}
                >
                  I&apos;ve added it
                </Button>
                {!skipped && (
                  <Button
                    size="small"
                    onClick={() => setSkipped(true)}
                    sx={{ color: "text.secondary" }}
                  >
                    Skip for now
                  </Button>
                )}
              </Stack>
              {skipped && (
                <Stack
                  direction="row"
                  spacing={0.75}
                  alignItems="flex-start"
                  sx={{
                    mt: 1,
                    px: 1.25,
                    py: 0.875,
                    borderRadius: 1,
                    bgcolor: "background.neutral",
                  }}
                >
                  <Iconify
                    icon="solar:eye-linear"
                    width={14}
                    sx={{ color: "text.secondary", mt: 0.25, flexShrink: 0 }}
                  />
                  <Typography
                    component="div"
                    sx={{ typography: "s3", color: "text.secondary" }}
                  >
                    <HintLine hint="Runs show the conversation, not the tool calls behind it. Add the webhook any time from the agent's Connections tab.">
                      Skipped — fidelity stays Observed only.
                    </HintLine>
                  </Typography>
                </Stack>
              )}
            </>
          )}
        </Box>
      )}
    </Box>
  );
}
EndpointConsent.propTypes = {
  value: PropTypes.object,
  onChange: PropTypes.func.isRequired,
  webhookUrl: PropTypes.string,
  showWebhook: PropTypes.bool,
  /* A line above the statement while it's unacknowledged — e.g. "runs are blocked". */
  lead: PropTypes.node,
};

/**
 * Vapi is tested on a test copy we create, which needs a key with create
 * scope. Without it: re-issue the key, or test with the live assistant's
 * settings overridden for one call. Both are live.
 *   choice — null | "mocked" (kept as the stored value for the override)
 */
export function CloneScopeChoice({
  providerName,
  choice,
  onReissue,
  onMocked,
}) {
  const options = [
    {
      id: "reissue",
      title: "Re-issue the key with create scope",
      body: "A labelled test copy in your account · Proof: Live",
      hint: `We create a labelled test copy in your ${providerName} account and leave the live assistant as it is.`,
      onClick: onReissue,
    },
    {
      id: "mocked",
      title: "Override its settings for one call",
      body: "Live assistant, new settings per call · Proof: Live",
      hint: "Each test call runs your live assistant with the proposed change's settings for that call only. Its real tools run.",
      onClick: onMocked,
    },
  ];
  return (
    <Box
      sx={{
        border: "1px solid",
        borderColor: "divider",
        borderRadius: 1,
        overflow: "hidden",
      }}
    >
      <Stack
        direction="row"
        spacing={1.25}
        alignItems="flex-start"
        sx={{ px: 1.75, py: 1.25 }}
      >
        <Iconify
          icon="solar:lock-keyhole-minimalistic-linear"
          width={16}
          sx={{ color: "text.secondary", mt: 0.25, flexShrink: 0 }}
        />
        <Box flex={1} minWidth={0}>
          <Typography
            component="div"
            sx={{ typography: "s2", fontWeight: 600 }}
          >
            <HintLine
              hint={`We test ${providerName} assistants on a test copy in your account, which needs create scope.`}
            >
              This key can read assistants but can&apos;t create them
            </HintLine>
          </Typography>
        </Box>
      </Stack>
      <Stack spacing={0.75} sx={{ px: 1.75, pb: 1.25 }}>
        {options.map((o) => {
          const on = choice === o.id;
          return (
            <Stack
              key={o.id}
              direction="row"
              spacing={1.25}
              alignItems="flex-start"
              role="button"
              tabIndex={0}
              onClick={o.onClick}
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === " ") {
                  e.preventDefault();
                  o.onClick();
                }
              }}
              sx={{
                px: 1.5,
                py: 1,
                borderRadius: 1,
                cursor: "pointer",
                border: "1px solid",
                borderColor: on ? "text.primary" : "divider",
                bgcolor: on ? selectedBg : "transparent",
              }}
            >
              <Iconify
                icon={on ? "solar:check-circle-bold" : "solar:circle-linear"}
                width={16}
                sx={{
                  color: on ? "text.primary" : "text.disabled",
                  mt: 0.125,
                  flexShrink: 0,
                }}
              />
              <Box flex={1} minWidth={0}>
                <Typography sx={{ typography: "s2", fontWeight: 600 }}>
                  {o.title}
                </Typography>
                <Typography
                  component="div"
                  sx={{ typography: "s3", color: "text.subtitle" }}
                >
                  <HintLine hint={o.hint}>{o.body}</HintLine>
                </Typography>
              </Box>
            </Stack>
          );
        })}
      </Stack>
    </Box>
  );
}
CloneScopeChoice.propTypes = {
  providerName: PropTypes.string,
  choice: PropTypes.string,
  onReissue: PropTypes.func.isRequired,
  onMocked: PropTypes.func.isRequired,
};
