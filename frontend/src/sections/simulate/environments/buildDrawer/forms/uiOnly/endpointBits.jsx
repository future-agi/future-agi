import PropTypes from "prop-types";
import { useState } from "react";
import { alpha } from "@mui/material/styles";
import {
  Box,
  Button,
  ButtonBase,
  Checkbox,
  Stack,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import CopyField from "../../../components/CopyField";
import {
  BTN_SX,
  OUTLINED_BTN_SX,
  ROW_BOX_SX,
  T12,
  T13,
  focusRing,
} from "../../drawerStyles";
import { HintLine } from "../formBits";
import SampleTag from "../shared/SampleTag";
import { WEBHOOK_PROVIDERS } from "./sampleData";

const HEAD = {
  ...T12,
  fontWeight: 600,
  letterSpacing: 0.4,
  color: "text.secondary",
};

function FactLine({ label, first = false, children }) {
  return (
    <Stack
      direction="row"
      spacing={2}
      alignItems="center"
      sx={{
        px: 1.75,
        py: 0.875,
        borderTop: first ? "none" : "1px solid",
        borderColor: "divider",
      }}
    >
      <Typography
        sx={{ ...T12, color: "text.secondary", width: 120, flexShrink: 0 }}
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
  first: PropTypes.bool,
  children: PropTypes.node,
};

const factSx = { ...T12, fontWeight: 600 };

/**
 * "How we'll test it" for an opaque endpoint: it can't be copied, so the
 * real agent answers, the proof is Live, and fidelity is Observed only until
 * a secondary webhook mirrors its events.
 */
export function HowWeTestIt({ webhook }) {
  const fidelity = webhook
    ? {
        label: "Observed + webhook",
        note: "Call and tool events are mirrored to us by the webhook you added.",
      }
    : {
        label: "Observed only",
        note: "We see the conversation, not the tool calls behind it.",
      };
  return (
    <Box sx={{ ...ROW_BOX_SX, overflow: "hidden" }}>
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
        <Typography sx={{ ...HEAD, flex: 1 }}>
          How we&apos;ll test it
        </Typography>
        <Stack
          direction="row"
          alignItems="center"
          spacing={0.5}
          sx={{
            px: 0.75,
            height: 22,
            borderRadius: "11px",
            border: "1px solid",
            borderColor: "divider",
            ...T12,
            fontWeight: 500,
          }}
        >
          <Iconify icon="solar:bolt-linear" width={12} />
          <span>Live</span>
        </Stack>
      </Stack>
      <FactLine label="Copy" first>
        <Typography component="div" sx={factSx}>
          <HintLine
            text="Wrapped endpoint"
            hint="We only see a URL, so it can't be copied. Simulator traffic drives your real agent, and its real tools execute."
          />
        </Typography>
      </FactLine>
      <FactLine label="Left untouched">
        <Typography sx={T12}>
          Nothing is copied. Your real agent answers every call.
        </Typography>
      </FactLine>
      <FactLine label="Proof">
        <Typography component="div" sx={factSx}>
          <HintLine
            text="Live · Deployed by you"
            hint="The agent ran in your account and its tools reached your real systems. Only the starting state was checked, and a model judged sub-goals from the conversation. Weaker proof."
          />
        </Typography>
      </FactLine>
      <FactLine label="Before the first run">
        <Typography component="div" sx={T12}>
          <HintLine
            text="You confirm it may drive your real agent."
            hint="Acknowledge below that simulations drive your real agent."
          />
        </Typography>
      </FactLine>
      <FactLine label="Run fidelity">
        <Typography component="div" sx={factSx}>
          <HintLine text={fidelity.label} hint={fidelity.note} />
        </Typography>
      </FactLine>
    </Box>
  );
}
HowWeTestIt.propTypes = { webhook: PropTypes.bool };

/**
 * An opaque endpoint can't be copied, so a simulation drives the real agent.
 * Nothing runs until someone says so; after that, an optional secondary
 * webhook mirrors its call and tool events to us. Skipping keeps fidelity at
 * Observed only, and says so. `value` is `{ acknowledgedAt, webhookAt }` or null.
 */
export function EndpointConsent({ value, onChange, webhookUrl }) {
  const [provider, setProvider] = useState("server");
  const [skipped, setSkipped] = useState(false);
  const acknowledged = !!value?.acknowledgedAt;
  const webhookAt = value?.webhookAt || null;
  const steps = WEBHOOK_PROVIDERS.find((p) => p.id === provider)?.steps || "";
  // Where to add it stays in view; the rest sits on hover.
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
    <Box sx={{ ...ROW_BOX_SX, overflow: "hidden" }}>
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
            color: acknowledged ? "text.secondary" : "error.main",
            mt: 0.25,
            flexShrink: 0,
          }}
        />
        <Box flex={1} minWidth={0}>
          <Typography component="div" sx={{ ...T13, fontWeight: 600 }}>
            <HintLine
              text="Simulations drive your real agent and its tools"
              hint="Its real tools run. Point it at staging if they shouldn't."
            />
          </Typography>
          <Stack
            component="label"
            direction="row"
            alignItems="center"
            spacing={0.5}
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
              sx={{ p: 0.5 }}
            />
            <Typography sx={{ ...T12, fontWeight: 600 }}>
              I understand. Simulations may drive this agent
            </Typography>
          </Stack>
        </Box>
      </Stack>

      {acknowledged && (
        <Box
          sx={{
            px: 1.75,
            py: 1.25,
            borderTop: "1px solid",
            borderColor: "divider",
          }}
        >
          <Stack direction="row" alignItems="center" spacing={1}>
            <Typography sx={{ ...T12, fontWeight: 600 }}>
              Secondary webhook
            </Typography>
            <Typography sx={{ ...T12, color: "text.secondary" }}>
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
              <Typography component="div" sx={{ ...T12, flex: 1, minWidth: 0 }}>
                <HintLine
                  text="Added · fidelity Observed + webhook"
                  hint="Call and tool events are mirrored to us."
                />
              </Typography>
              <Button
                onClick={() => onChange({ ...value, webhookAt: null })}
                sx={{ ...BTN_SX, color: "text.secondary" }}
              >
                Remove
              </Button>
            </Stack>
          ) : (
            <>
              <Typography
                component="div"
                sx={{ ...T12, color: "text.secondary", mt: 0.25, mb: 1 }}
              >
                <HintLine
                  text="Next to your own webhook, not instead of it."
                  hint="Add it on your production agent as a second webhook. Runs then show the tool calls behind each turn."
                />
              </Typography>
              <Stack direction="row" alignItems="center" spacing={1}>
                <Box flex={1} minWidth={0}>
                  <CopyField value={webhookUrl} />
                </Box>
                <SampleTag />
              </Stack>
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
                    <ButtonBase
                      key={p.id}
                      aria-pressed={on}
                      onClick={() => setProvider(p.id)}
                      sx={{
                        px: 1,
                        height: 24,
                        borderRadius: "4px",
                        border: "1px solid",
                        borderColor: on ? "text.primary" : "divider",
                        bgcolor: (t) =>
                          on
                            ? alpha(t.palette.text.primary, 0.08)
                            : "transparent",
                        ...T12,
                        fontWeight: 600,
                        color: on ? "text.primary" : "text.secondary",
                        "&.Mui-focusVisible": focusRing,
                      }}
                    >
                      {p.label}
                    </ButtonBase>
                  );
                })}
              </Stack>
              <Typography
                component="div"
                sx={{ ...T12, color: "text.secondary", mt: 0.75 }}
              >
                {stepsRest ? (
                  <HintLine text={stepsFirst} hint={stepsRest} />
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
                  variant="outlined"
                  onClick={() => {
                    setSkipped(false);
                    onChange({ ...value, webhookAt: new Date().toISOString() });
                  }}
                  sx={OUTLINED_BTN_SX}
                >
                  I&apos;ve added it
                </Button>
                {!skipped && (
                  <Button
                    onClick={() => setSkipped(true)}
                    sx={{ ...BTN_SX, color: "text.secondary" }}
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
                    borderRadius: "6px",
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
                    sx={{ ...T12, color: "text.secondary" }}
                  >
                    <HintLine
                      text="Skipped. Fidelity stays Observed only."
                      hint="Runs show the conversation, not the tool calls behind it. Add the webhook any time from the agent's Connections tab."
                    />
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
  webhookUrl: PropTypes.string.isRequired,
};
