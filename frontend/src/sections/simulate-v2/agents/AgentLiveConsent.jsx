import PropTypes from "prop-types";
import { useEffect, useState } from "react";
import { alpha } from "@mui/material/styles";
import {
  Box,
  Button,
  Checkbox,
  IconButton,
  Radio,
  Stack,
  Tooltip,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { PROVIDER_LABELS } from "../_mock/agentRegistry";
import {
  PLATFORM_TESTING,
  proofKindOf,
  retellPinStateOf,
} from "../_mock/rsiModel";
import { replicationOf } from "../_mock/replication";
import { ActionText, InfoTip, KvRow, PanelCard } from "./AgentBits";
import { MONO, ago } from "./agentViewUtils";

/* One set of words for every layout (card, bare, oneLine). */
const copiesLabelFor = (provider) =>
  `Create test copies or drafts in my ${provider === "your" ? "" : `${provider} `}account`;
const TOOLS_SAFE_LABEL = "Safe to call";
const TOOLS_REPLAY_LABEL = "Stubbed on the replay flag";
const NOT_READY_COPIES = "Agree to test copies first";
const NOT_READY_TOOLS = "Choose how its tools are handled";
const SAFE_HINT =
  "They reach staging systems, or what they do is fine in a test.";

const selectedBg = "action.selected";

/* Copies or drafts only where we make them — a phone number, an endpoint or a
   project connection is called as it is. */
const makesCopies = (mode) =>
  [
    "provider_clone",
    "platform_draft",
    "platform_branch",
    "exported_mocked",
  ].includes(mode);

function Choice({ on, title, body, hint, onClick, children, radio }) {
  return (
    <Stack
      direction="row"
      spacing={1}
      alignItems="flex-start"
      role={radio ? "radio" : "checkbox"}
      aria-checked={on}
      tabIndex={0}
      onClick={onClick}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onClick();
        }
      }}
      sx={{
        px: 1.25,
        py: 0.875,
        borderRadius: 1,
        cursor: "pointer",
        border: "1px solid",
        borderColor: on ? "text.primary" : "divider",
        bgcolor: on ? selectedBg : "transparent",
      }}
    >
      {radio ? (
        <Iconify
          icon={on ? "solar:check-circle-bold" : "solar:circle-linear"}
          width={16}
          sx={{
            color: on ? "text.primary" : "text.disabled",
            mt: 0.125,
            flexShrink: 0,
          }}
        />
      ) : (
        <Checkbox
          size="small"
          checked={on}
          tabIndex={-1}
          sx={{ p: 0, mt: "1px" }}
        />
      )}
      <Box flex={1} minWidth={0}>
        <Typography sx={{ typography: "s2", fontWeight: 600 }}>
          {title}
        </Typography>
        {(body || hint) && (
          <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
            {body || hint}
            {body && <InfoTip text={hint} />}
          </Typography>
        )}
        {children}
      </Box>
    </Stack>
  );
}
Choice.propTypes = {
  on: PropTypes.bool,
  title: PropTypes.node,
  body: PropTypes.node,
  hint: PropTypes.node,
  onClick: PropTypes.func,
  children: PropTypes.node,
  radio: PropTypes.bool,
};

/**
 * Live-test consent, before the first run on an agent that runs in the
 * customer's account. Two acknowledgments: we create test copies or drafts
 * there and call the agent, and its tools are safe to call — or the agent
 * reads the replay flag and stubs side-effecting tools.
 *   values.liveTestConsent = { at, replayFlag }
 *
 * `bare` drops the card and the bordered choices — a few plain lines, for
 * when it sits inside something that already says why (a Needs input row).
 * `oneLine` (with `bare`) puts the choices and the button on one row, the
 * lead left to whatever holds it.
 */
export function LiveConsentCard({
  agent,
  env,
  onSave,
  bare = false,
  oneLine = false,
  onCancel = null,
  onReady = null,
}) {
  const r = replicationOf(agent, env);
  const proof = proofKindOf(agent);
  const given = agent.values?.liveTestConsent || null;
  const provider = PROVIDER_LABELS[agent.values?.provider] || "your";
  const copies = makesCopies(r.mode);
  /* Ticked by default — the button (Give consent, or Start fix in Fix) is the act of consenting, not the tick. */
  const [copiesOk, setCopiesOk] = useState(true);
  const [tools, setTools] = useState(null); // "safe" | "replay"
  const readyNow = (!makesCopies(r.mode) || copiesOk) && !!tools;
  /* With `onReady` the caller's own button gives consent (Fix's Start fix):
     it hears the consent while it's complete, null while it isn't. */
  useEffect(() => {
    if (!onReady) return;
    onReady(
      readyNow
        ? {
            liveTestConsent: {
              at: new Date().toISOString(),
              replayFlag: tools === "replay",
            },
          }
        : null,
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [readyNow, tools]);
  /* Not live, or an endpoint that has its own acknowledgment. */
  if (
    proof.kind !== "live" ||
    (!given?.at && agent.values?.endpointConsent?.acknowledgedAt)
  )
    return null;

  const lead = copies
    ? `We'll make test copies or drafts of your agent in your ${provider === "your" ? "" : `${provider} `}account and call them. Anything the agent does — like issuing a refund — really happens.`
    : "We'll call your agent with simulated callers. Anything it does — like issuing a refund — really happens.";

  const withdraw = () =>
    onSave(
      { liveTestConsent: null },
      "Consent withdrawn — runs of this agent wait until it's given again.",
    );
  const ready = (!copies || copiesOk) && !!tools;
  const give = () =>
    onSave(
      {
        liveTestConsent: {
          at: new Date().toISOString(),
          replayFlag: tools === "replay",
        },
      },
      tools === "replay"
        ? "Consent given — simulated calls set the replay flag."
        : "Consent given — simulations can call this agent.",
    );
  const copiesLabel = copiesLabelFor(provider);
  const copiesHint =
    r.mode === "provider_clone"
      ? "Labelled fagi-sim-<environment>-<build>. Removed if the agent is archived."
      : r.mode === "platform_draft"
        ? "Unpublished draft versions — only once your live numbers are pinned."
        : "Removed if the agent is archived. Nothing is published or merged.";
  const notReady = copies && !copiesOk ? NOT_READY_COPIES : NOT_READY_TOOLS;
  const replayHint =
    "Every simulated call carries metadata.fagi_replay = true. Tools that write data or call outside services return a canned answer when it's set.";

  if (bare) {
    if (given?.at) {
      return (
        <Typography sx={{ typography: "s3", color: "text.secondary" }}>
          {`Given ${ago(given.at)} · tools ${(given.replayFlag ? TOOLS_REPLAY_LABEL : TOOLS_SAFE_LABEL).toLowerCase()} · `}
          <ActionText onClick={withdraw}>Withdraw</ActionText>
        </Typography>
      );
    }
    if (oneLine) {
      return (
        <Stack
          direction="row"
          alignItems="center"
          flexWrap="wrap"
          sx={{ columnGap: 3, rowGap: 0.75 }}
        >
          {copies && (
            <Pick
              control={
                <Checkbox size="small" checked={copiesOk} tabIndex={-1} />
              }
              on={copiesOk}
              onClick={() => setCopiesOk((x) => !x)}
              label={copiesLabel}
              hint={copiesHint}
            />
          )}
          <Stack direction="row" alignItems="center" sx={{ columnGap: 2 }}>
            <Typography sx={{ typography: "s2", color: "text.subtitle" }}>
              Its tools
            </Typography>
            <Pick
              control={
                <Radio size="small" checked={tools === "safe"} tabIndex={-1} />
              }
              on={tools === "safe"}
              onClick={() => setTools("safe")}
              label={TOOLS_SAFE_LABEL}
              hint={SAFE_HINT}
            />
            <Pick
              control={
                <Radio
                  size="small"
                  checked={tools === "replay"}
                  tabIndex={-1}
                />
              }
              on={tools === "replay"}
              onClick={() => setTools("replay")}
              label={TOOLS_REPLAY_LABEL}
              hint={replayHint}
            />
          </Stack>
          <Tooltip arrow title={ready ? "" : notReady}>
            <Box component="span" sx={{ ml: "auto" }}>
              <Button
                size="small"
                variant="contained"
                color="primary"
                disabled={!ready}
                onClick={give}
              >
                Give consent
              </Button>
            </Box>
          </Tooltip>
        </Stack>
      );
    }
    return (
      <Stack spacing={1}>
        <Typography sx={{ typography: "s2", color: "text.secondary" }}>
          {lead}
          <InfoTip text={`${proof.label} proof — ${proof.note}`} />
        </Typography>
        {copies && (
          <Pick
            control={<Checkbox size="small" checked={copiesOk} tabIndex={-1} />}
            on={copiesOk}
            onClick={() => setCopiesOk((x) => !x)}
            label={copiesLabel}
            hint={copiesHint}
          />
        )}
        <Stack
          direction="row"
          alignItems="center"
          flexWrap="wrap"
          sx={{ columnGap: 2.5, rowGap: 0.5 }}
        >
          <Typography sx={{ typography: "s2", color: "text.subtitle" }}>
            Its tools
          </Typography>
          <Pick
            control={
              <Radio size="small" checked={tools === "safe"} tabIndex={-1} />
            }
            on={tools === "safe"}
            onClick={() => setTools("safe")}
            label={TOOLS_SAFE_LABEL}
            hint={SAFE_HINT}
          />
          <Pick
            control={
              <Radio size="small" checked={tools === "replay"} tabIndex={-1} />
            }
            on={tools === "replay"}
            onClick={() => setTools("replay")}
            label={TOOLS_REPLAY_LABEL}
            hint={replayHint}
          />
        </Stack>
        {!onReady && (
          <Stack
            direction="row"
            alignItems="center"
            spacing={1.5}
            sx={{ pt: 0.5 }}
          >
            <Button
              size="small"
              variant="contained"
              color="primary"
              disabled={!ready}
              onClick={give}
            >
              Give consent
            </Button>
            {!ready && (
              <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
                {notReady}
              </Typography>
            )}
          </Stack>
        )}
      </Stack>
    );
  }

  if (given?.at) {
    return (
      <PanelCard
        title="Consent"
        note={`Given ${ago(given.at)}`}
        action={<ActionText onClick={withdraw}>Withdraw</ActionText>}
      >
        <KvRow label="In your account" first>
          {copies
            ? `Test copies or drafts in ${provider === "your" ? "your" : `the ${provider}`} account`
            : "Called as it runs — nothing copied"}
        </KvRow>
        <KvRow
          label="Its tools"
          hint={
            given.replayFlag
              ? "Every simulated call sets the replay flag; the agent stubs tools that write data or call outside services."
              : null
          }
        >
          {given.replayFlag ? TOOLS_REPLAY_LABEL : TOOLS_SAFE_LABEL}
        </KvRow>
      </PanelCard>
    );
  }

  /* The choices, the same in the card and the dialog. */
  const choices = (
    <Stack spacing={0.75}>
      {copies && (
        <Choice
          on={copiesOk}
          onClick={() => setCopiesOk((x) => !x)}
          title={copiesLabel}
          hint={copiesHint}
        />
      )}
      <Typography sx={{ typography: "s2", fontWeight: 600, pt: 0.75 }}>
        Its tools
      </Typography>
      <Choice
        radio
        on={tools === "safe"}
        onClick={() => setTools("safe")}
        title={TOOLS_SAFE_LABEL}
        hint={SAFE_HINT}
      />
      <Choice
        radio
        on={tools === "replay"}
        onClick={() => setTools("replay")}
        title={TOOLS_REPLAY_LABEL}
        hint="Tools that write data or call outside services return a canned answer when the flag is set."
      >
        {tools === "replay" && (
          <Box
            sx={{
              mt: 0.75,
              px: 1,
              py: 0.5,
              borderRadius: "4px",
              bgcolor: "background.neutral",
              width: "fit-content",
            }}
          >
            <Typography sx={{ typography: "s3", fontFamily: MONO }}>
              metadata.fagi_replay = true
            </Typography>
          </Box>
        )}
      </Choice>
    </Stack>
  );

  /* In a dialog: its own title, a close icon, and the actions in the footer. */
  if (onCancel) {
    return (
      <>
        <Stack
          direction="row"
          alignItems="flex-start"
          spacing={2}
          sx={{ px: 2.5, pt: 2, pb: 1.5 }}
        >
          <Box flex={1} minWidth={0}>
            <Typography sx={{ typography: "m2", fontWeight: 600 }}>
              Let us call your agent for tests
            </Typography>
            <Typography
              sx={{ typography: "s2", color: "text.secondary", mt: 0.5 }}
            >
              {lead}
              <InfoTip text={`${proof.label} proof — ${proof.note}`} />
            </Typography>
          </Box>
          <IconButton
            size="small"
            onClick={onCancel}
            aria-label="Close"
            sx={{ mt: -0.25 }}
          >
            <Iconify
              icon="solar:close-circle-linear"
              width={20}
              sx={{ color: "text.subtitle" }}
            />
          </IconButton>
        </Stack>
        <Box sx={{ px: 2.5, pb: 2 }}>{choices}</Box>
        <Stack
          direction="row"
          alignItems="center"
          spacing={1}
          sx={{
            px: 2.5,
            py: 1.5,
            borderTop: "1px solid",
            borderColor: "divider",
          }}
        >
          <Typography
            sx={{
              typography: "s3",
              color: "text.subtitle",
              flex: 1,
              minWidth: 0,
            }}
          >
            {ready ? "Needed once, before the first run." : notReady}
          </Typography>
          <Button
            size="small"
            onClick={onCancel}
            sx={{ color: "text.secondary" }}
          >
            Cancel
          </Button>
          <Button
            size="small"
            variant="contained"
            color="primary"
            disabled={!ready}
            onClick={give}
          >
            Give consent
          </Button>
        </Stack>
      </>
    );
  }

  return (
    <PanelCard
      title="Consent"
      note="Needed before the first run"
      sx={{ borderColor: (t) => alpha(t.palette.text.primary, 0.32) }}
    >
      <Box sx={{ px: 2, py: 1.5 }}>
        <Typography sx={{ typography: "s2", fontWeight: 600 }}>
          {lead}
          <InfoTip text={`${proof.label} proof — ${proof.note}`} />
        </Typography>
        <Box sx={{ mt: 1.25 }}>{choices}</Box>
        <Stack
          direction="row"
          alignItems="center"
          spacing={1.5}
          sx={{ mt: 1.5 }}
        >
          <Button
            size="small"
            variant="contained"
            color="primary"
            disabled={!ready}
            onClick={give}
          >
            Give consent
          </Button>
          {!ready && (
            <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
              {notReady}
            </Typography>
          )}
        </Stack>
      </Box>
    </PanelCard>
  );
}
LiveConsentCard.propTypes = {
  agent: PropTypes.object.isRequired,
  env: PropTypes.object,
  onSave: PropTypes.func.isRequired,
  bare: PropTypes.bool,
  oneLine: PropTypes.bool,
  onCancel: PropTypes.func,
  onReady: PropTypes.func,
};

/* One plain checkbox or radio with its label — no box around it; the longer note behind an ⓘ. */
function Pick({ control, on, onClick, label, hint }) {
  return (
    <Stack
      direction="row"
      alignItems="center"
      spacing={0.75}
      role={control.type === Radio ? "radio" : "checkbox"}
      aria-checked={on}
      tabIndex={0}
      onClick={onClick}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onClick();
        }
      }}
      sx={{
        cursor: "pointer",
        width: "fit-content",
        "& .MuiCheckbox-root, & .MuiRadio-root": { p: 0 },
      }}
    >
      <Box sx={{ display: "inline-flex", pointerEvents: "none" }}>
        {control}
      </Box>
      <Typography sx={{ typography: "s2", fontWeight: on ? 600 : 500 }}>
        {label}
        <InfoTip text={hint} />
      </Typography>
    </Stack>
  );
}
Pick.propTypes = {
  control: PropTypes.element.isRequired,
  on: PropTypes.bool,
  onClick: PropTypes.func,
  label: PropTypes.node,
  hint: PropTypes.node,
};

/**
 * Retell: before we create a draft, the live numbers are pinned to a
 * released version — a new draft becomes the newest version, and a number
 * that follows the newest would start taking real calls on it. Until then a
 * test runs with settings overridden for one call.
 *   values.retellNumbersPinned = { at, version }
 */
export function RetellPinCard({ agent, onSave }) {
  const pins = retellPinStateOf(agent);
  if (!pins) return null;
  const retell = PLATFORM_TESTING.retell;
  return (
    <PanelCard
      title={
        <>
          Live numbers
          <InfoTip text={retell.released} />
        </>
      }
      note={pins.pinned ? `Pinned ${ago(pins.at)}` : "Pin before drafts"}
      action={
        pins.pinned ? (
          <ActionText
            onClick={() =>
              onSave(
                { retellNumbersPinned: null },
                "Marked unpinned — tests go back to settings overridden for one call.",
              )
            }
          >
            Undo
          </ActionText>
        ) : null
      }
    >
      <Stack
        direction="row"
        spacing={1}
        alignItems="flex-start"
        sx={{ px: 2, py: 1.25 }}
      >
        <Iconify
          icon={
            pins.pinned ? "solar:pin-linear" : "solar:danger-triangle-linear"
          }
          width={16}
          sx={{ color: "text.secondary", mt: 0.25, flexShrink: 0 }}
        />
        <Box flex={1} minWidth={0}>
          <Typography sx={{ typography: "s2", fontWeight: 600 }}>
            {pins.line}
            <InfoTip
              text={
                pins.pinned
                  ? "Each test creates a draft version and calls it with that version pinned. Your numbers keep answering on the released version."
                  : retell.note
              }
            />
          </Typography>
        </Box>
      </Stack>
      {pins.numbers.map((n) => (
        <Stack
          key={n.number}
          direction="row"
          alignItems="center"
          spacing={2}
          sx={{
            px: 2,
            py: 0.75,
            borderTop: "1px solid",
            borderColor: "divider",
          }}
        >
          <Typography sx={{ typography: "s2", fontFamily: MONO, flex: 1 }}>
            {n.number}
          </Typography>
          <Typography sx={{ typography: "s3", color: "text.secondary" }}>
            {n.follows === "newest"
              ? "Follows the newest version"
              : `Pinned to ${n.follows}`}
          </Typography>
        </Stack>
      ))}
      {!pins.pinned && (
        <Stack
          direction="row"
          alignItems="center"
          spacing={1.5}
          sx={{
            px: 2,
            py: 1.25,
            borderTop: "1px solid",
            borderColor: "divider",
          }}
        >
          <Typography
            sx={{ typography: "s3", color: "text.subtitle", flex: 1 }}
          >
            {`In Retell: Phone numbers → each number → Agent version → ${pins.version}.`}
          </Typography>
          <Button
            size="small"
            variant="outlined"
            onClick={() =>
              onSave(
                {
                  retellNumbersPinned: {
                    at: new Date().toISOString(),
                    version: pins.version,
                  },
                },
                `Numbers pinned to ${pins.version} — tests now run on draft versions.`,
              )
            }
            sx={{
              color: "text.primary",
              borderColor: "divider",
              flexShrink: 0,
            }}
          >
            {`I've pinned them to ${pins.version}`}
          </Button>
        </Stack>
      )}
    </PanelCard>
  );
}
RetellPinCard.propTypes = {
  agent: PropTypes.object.isRequired,
  onSave: PropTypes.func.isRequired,
};
