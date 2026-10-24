import PropTypes from "prop-types";
import { useEffect, useState } from "react";
import {
  Box,
  Button,
  Chip,
  MenuItem,
  Select,
  Stack,
  TextField,
  ToggleButton,
  ToggleButtonGroup,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import {
  CHANNEL_LABELS,
  agentChannelsOf,
  channelEnvsOf,
} from "../_mock/rsiModel";
import { ActionText, InfoTip, PanelCard } from "./AgentBits";
import {
  DIRECTIONS,
  LANGUAGES,
  MAX_CONCURRENT_STEPS,
  TURN_LIMIT_STEPS,
  contactNumbersOf,
  contactNumbersPatch,
  identityOf,
  languageName,
  phoneLabelOf,
  reachOf,
  reachPatch,
  turnLimitLabel,
} from "./agentProfile";
import { MONO, testedInLine } from "./agentViewUtils";
import { segmentedToggleSx } from "./segmentedToggleSx";

const CHANNEL_ICON = {
  voice: "solar:phone-calling-rounded-linear",
  chat: "solar:chat-round-linear",
};
const selectSx = {
  height: 30,
  minWidth: 130,
  typography: "s2",
  "& .MuiSelect-select": { typography: "s2", py: 0.5 },
};
/* The product's segmented control (Evals: Single / Composite). */
const toggleSx = segmentedToggleSx;

/**
 * Reach and chat settings — how simulations reach the agent on each channel
 * it serves, the languages it speaks, and adding a second channel. Set when
 * it was connected, changeable here because it does change: a new test
 * number, an agent that now greets first, a chat window next to the phone
 * line. Same for every version, so editing it doesn't make one.
 *   values.reach = { maxConcurrent, whoSpeaksFirst, direction, turnLimit, whoWritesFirst }
 *   values.languages, values.channels, values.contact (call over, numbers —
 *   the first is the one simulations dial)
 */
export default function AgentCallSettings({
  agent,
  state,
  onSave,
  onBuildEnv,
  callOver = true,
  bare = false,
}) {
  const c = agent.values?.contact || {};
  const mode = c.mode || "web";
  const reach = reachOf(agent);
  const { languages } = identityOf(agent);
  const channels = agentChannelsOf(agent);
  const tested = channelEnvsOf(agent, state);
  /* Every number it answers on; a new one is typed in the add row. */
  const numbers = contactNumbersOf(agent);
  /* Only the ones saved here are a list to edit. */
  const saved = numbers
    .filter((n) => !n.connected)
    .map(({ countryCode, number: num }) => ({ countryCode, number: num }));
  const [number, setNumber] = useState("");
  const [code, setCode] = useState(
    numbers[0]?.countryCode || c.countryCode || "+1",
  );
  useEffect(() => {
    setNumber("");
  }, [numbers.length]);

  const saveContact = (patch) =>
    onSave({
      contact: {
        ...c,
        mode,
        inboundCalls: reach.direction !== "outbound",
        ...patch,
      },
    });
  const saveNumbers = (next, msg) =>
    onSave(
      {
        contact: {
          ...contactNumbersPatch(c, next),
          mode,
          inboundCalls: reach.direction !== "outbound",
        },
      },
      msg,
    );
  const addNumber = () => {
    const n = { countryCode: code.trim(), number: number.trim() };
    saveNumbers([...saved, n], `${phoneLabelOf(n)} added.`);
  };
  const saveReach = (patch, msg) => onSave(reachPatch(agent, patch), msg);
  const voice = channels.includes("voice");
  const chat = channels.includes("chat");
  const missing = !voice ? "voice" : !chat ? "chat" : null;
  const envsOf = (ch) => tested.find((t) => t.channel === ch)?.envs || [];
  const removable = (ch) => channels.length > 1 && envsOf(ch).length === 0;

  const concurrentRow = (
    <SettingRow
      title={voice ? "Calls at once" : "Conversations at once"}
      body="Started at the same time by a run"
      hint="Kept under what your account allows."
    >
      <Select
        size="small"
        value={reach.maxConcurrent}
        onChange={(e) => saveReach({ maxConcurrent: e.target.value })}
        sx={{ ...selectSx, fontVariantNumeric: "tabular-nums" }}
      >
        {[...new Set([...MAX_CONCURRENT_STEPS, reach.maxConcurrent])]
          .sort((a, b) => a - b)
          .map((n) => (
            <MenuItem
              key={n}
              value={n}
              sx={{ typography: "s2", fontVariantNumeric: "tabular-nums" }}
            >{`Up to ${n}`}</MenuItem>
          ))}
      </Select>
    </SettingRow>
  );

  const channelHead = (ch) => {
    const envs = envsOf(ch);
    return (
      <Stack
        direction="row"
        alignItems="center"
        spacing={1}
        sx={{ px: 2, py: 1, borderTop: "1px solid", borderColor: "divider" }}
      >
        <Iconify
          icon={CHANNEL_ICON[ch]}
          width={14}
          sx={{ color: "text.secondary", flexShrink: 0 }}
        />
        <Typography sx={{ typography: "s2", fontWeight: 600, flex: 1 }}>
          {CHANNEL_LABELS[ch]}
        </Typography>
        <Typography
          noWrap
          sx={{ typography: "s3", color: "text.subtitle", minWidth: 0 }}
        >
          {testedInLine(envs) || "No environment yet"}
        </Typography>
        {removable(ch) && (
          <ActionText
            onClick={() =>
              onSave(
                { channels: channels.filter((x) => x !== ch) },
                `${CHANNEL_LABELS[ch]} removed from ${agent.name}.`,
              )
            }
          >
            Remove
          </ActionText>
        )}
      </Stack>
    );
  };

  /* A channel with nothing testing it yet — the next step is its environment. */
  const buildPrompt = (ch) =>
    envsOf(ch).length || !onBuildEnv ? null : (
      <Stack
        direction="row"
        alignItems="center"
        spacing={1.5}
        sx={{ px: 2, py: 1.25, borderTop: "1px solid", borderColor: "divider" }}
      >
        <Box flex={1} minWidth={0}>
          <Typography
            sx={{ typography: "s2", fontWeight: 600 }}
          >{`Build a ${CHANNEL_LABELS[ch].toLowerCase()} environment`}</Typography>
          <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
            Each channel gets its own
            <InfoTip
              text={`A ${ch === "chat" ? "chat window" : "phone line"} environment — simulated tools and data, scenarios and evaluations for the same agent.`}
            />
          </Typography>
        </Box>
        <Button
          size="small"
          variant="outlined"
          onClick={() => onBuildEnv(ch)}
          startIcon={<Iconify icon="solar:add-circle-linear" width={14} />}
          sx={{ color: "text.primary", borderColor: "divider", flexShrink: 0 }}
        >
          Build environment
        </Button>
      </Stack>
    );

  return (
    <PanelCard
      title="Reach and chat settings"
      note="Same for every version"
      bare={bare}
    >
      <Box sx={{ px: 2, py: 1.25 }}>
        <Typography sx={{ typography: "s2", fontWeight: 600, mb: 0.75 }}>
          Languages
          <InfoTip text="What it speaks on every channel — scenarios are written in these." />
        </Typography>
        <LanguagePicker
          value={languages}
          onChange={(next, msg) => onSave({ languages: next }, msg)}
        />
      </Box>

      {voice && channelHead("voice")}
      {voice && callOver && (
        <SettingRow
          title="Call over"
          body={mode === "web" ? "A web call (WebRTC)" : "A real phone call"}
          hint={
            mode === "web"
              ? "No phone line or telephony provider needed."
              : null
          }
        >
          <ToggleButtonGroup
            size="small"
            exclusive
            value={mode}
            onChange={(_, v) => v && saveContact({ mode: v })}
            sx={toggleSx}
          >
            <ToggleButton value="web">Web</ToggleButton>
            <ToggleButton value="phone">Phone</ToggleButton>
          </ToggleButtonGroup>
        </SettingRow>
      )}
      {voice && callOver && mode === "phone" && (
        <Box
          sx={{
            px: 2,
            py: 1.25,
            borderTop: "1px solid",
            borderColor: "divider",
          }}
        >
          <Typography sx={{ typography: "s2", fontWeight: 600, mb: 0.75 }}>
            Phone numbers
            {numbers.length > 1 && (
              <InfoTip text="Simulations dial the first one." />
            )}
          </Typography>
          {numbers.length ? (
            <Box
              sx={{
                border: "1px solid",
                borderColor: "divider",
                borderRadius: 1,
                mb: 1,
              }}
            >
              {numbers.map((n, i) => (
                <Stack
                  key={`${n.countryCode}${n.number}`}
                  direction="row"
                  alignItems="center"
                  spacing={1}
                  sx={{
                    px: 1.25,
                    py: 0.625,
                    borderTop: i ? "1px solid" : "none",
                    borderColor: "divider",
                  }}
                >
                  <Typography
                    sx={{
                      typography: "s2",
                      fontFamily: MONO,
                      flex: 1,
                      minWidth: 0,
                    }}
                  >
                    {phoneLabelOf(n)}
                  </Typography>
                  {!n.connected && (
                    <ActionText
                      onClick={() =>
                        saveNumbers(
                          saved.filter(
                            (x) => phoneLabelOf(x) !== phoneLabelOf(n),
                          ),
                          `${phoneLabelOf(n)} removed.`,
                        )
                      }
                    >
                      Remove
                    </ActionText>
                  )}
                </Stack>
              ))}
            </Box>
          ) : (
            <Typography
              sx={{ typography: "s3", color: "text.secondary", mb: 0.75 }}
            >
              Simulations can&apos;t reach the agent by phone until a number is
              saved.
            </Typography>
          )}
          <Stack direction="row" spacing={1}>
            <TextField
              size="small"
              value={code}
              onChange={(e) => setCode(e.target.value)}
              sx={{ width: 76 }}
              inputProps={{ "aria-label": "Country code" }}
              InputProps={{ sx: { typography: "s2", fontFamily: MONO } }}
            />
            <TextField
              size="small"
              fullWidth
              value={number}
              onChange={(e) => setNumber(e.target.value)}
              placeholder="415 555 0182"
              inputProps={{ "aria-label": "Phone number" }}
              InputProps={{ sx: { typography: "s2", fontFamily: MONO } }}
            />
            <Button
              size="small"
              variant="contained"
              color="primary"
              disabled={!number.trim()}
              onClick={addNumber}
              sx={{ flexShrink: 0 }}
            >
              {numbers.length ? "Add another" : "Add"}
            </Button>
          </Stack>
        </Box>
      )}
      {voice && (
        <SettingRow
          title="Direction"
          body={DIRECTIONS.find((d) => d.id === reach.direction)?.body.replace(
            /\.$/,
            "",
          )}
        >
          <ToggleButtonGroup
            size="small"
            exclusive
            value={reach.direction}
            onChange={(_, v) => v && saveReach({ direction: v })}
            sx={toggleSx}
          >
            {DIRECTIONS.map((d) => (
              <ToggleButton key={d.id} value={d.id}>
                {d.label}
              </ToggleButton>
            ))}
          </ToggleButtonGroup>
        </SettingRow>
      )}
      {voice && (
        <SettingRow
          title="Who speaks first"
          body={
            reach.whoSpeaksFirst === "agent"
              ? "The caller waits for the greeting"
              : "The caller talks first"
          }
        >
          <ToggleButtonGroup
            size="small"
            exclusive
            value={reach.whoSpeaksFirst}
            onChange={(_, v) => v && saveReach({ whoSpeaksFirst: v })}
            sx={toggleSx}
          >
            <ToggleButton value="agent">Agent</ToggleButton>
            <ToggleButton value="user">Caller</ToggleButton>
          </ToggleButtonGroup>
        </SettingRow>
      )}
      {voice && concurrentRow}
      {voice && buildPrompt("voice")}

      {chat && channelHead("chat")}
      {chat && (
        <SettingRow
          title="Who writes first"
          body={
            reach.whoWritesFirst === "agent"
              ? "The agent opens with its greeting"
              : "The user writes first"
          }
        >
          <ToggleButtonGroup
            size="small"
            exclusive
            value={reach.whoWritesFirst}
            onChange={(_, v) => v && saveReach({ whoWritesFirst: v })}
            sx={toggleSx}
          >
            <ToggleButton value="agent">Agent</ToggleButton>
            <ToggleButton value="user">User</ToggleButton>
          </ToggleButtonGroup>
        </SettingRow>
      )}
      {chat && (
        <SettingRow
          title="Turn limit"
          body="Counted as not resolved past this"
          hint="A simulated chat ends here if it hasn't finished."
        >
          <Select
            size="small"
            value={reach.turnLimit}
            onChange={(e) => saveReach({ turnLimit: e.target.value })}
            sx={selectSx}
          >
            {[...new Set([...TURN_LIMIT_STEPS, reach.turnLimit])].map((n) => (
              <MenuItem key={n} value={n} sx={{ typography: "s2" }}>
                {turnLimitLabel(n)}
              </MenuItem>
            ))}
          </Select>
        </SettingRow>
      )}
      {chat && !voice && concurrentRow}
      {chat && buildPrompt("chat")}

      {missing && (
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
          <Iconify
            icon={CHANNEL_ICON[missing]}
            width={16}
            sx={{ color: "text.subtitle", flexShrink: 0 }}
          />
          <Box flex={1} minWidth={0}>
            <Typography
              sx={{ typography: "s2", fontWeight: 600 }}
            >{`Add ${CHANNEL_LABELS[missing].toLowerCase()}`}</Typography>
            <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
              {`The same agent ${missing === "chat" ? "in a chat window" : "on a phone line"}`}
              <InfoTip text="One version history, its own environment." />
            </Typography>
          </Box>
          <Button
            size="small"
            variant="outlined"
            onClick={() =>
              onSave(
                { channels: [...channels, missing] },
                `${CHANNEL_LABELS[missing]} added — build a ${missing} environment to test it.`,
              )
            }
            sx={{
              color: "text.primary",
              borderColor: "divider",
              flexShrink: 0,
            }}
          >
            {`Add ${CHANNEL_LABELS[missing].toLowerCase()}`}
          </Button>
        </Stack>
      )}
    </PanelCard>
  );
}
AgentCallSettings.propTypes = {
  agent: PropTypes.object.isRequired,
  state: PropTypes.object,
  onSave: PropTypes.func.isRequired,
  onBuildEnv: PropTypes.func,
  callOver: PropTypes.bool,
  bare: PropTypes.bool,
};

function SettingRow({ title, body, hint, children }) {
  return (
    <Stack
      direction="row"
      alignItems="center"
      spacing={1.5}
      sx={{ px: 2, py: 1.125, borderTop: "1px solid", borderColor: "divider" }}
    >
      <Box flex={1} minWidth={0}>
        <Typography sx={{ typography: "s2", fontWeight: 600 }}>
          {title}
        </Typography>
        <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
          {body}
          <InfoTip text={hint} />
        </Typography>
      </Box>
      {children}
    </Stack>
  );
}
SettingRow.propTypes = {
  title: PropTypes.node,
  body: PropTypes.node,
  hint: PropTypes.node,
  children: PropTypes.node,
};

/* Chips for the languages it speaks; the last one can't be removed. */
function LanguagePicker({ value, onChange }) {
  const rest = LANGUAGES.filter((l) => !value.includes(l));
  return (
    <Stack direction="row" flexWrap="wrap" gap={0.75} alignItems="center">
      {value.map((l) => (
        <Chip
          key={l}
          size="small"
          label={languageName(l)}
          onDelete={
            value.length > 1
              ? () =>
                  onChange(
                    value.filter((x) => x !== l),
                    `${languageName(l)} removed.`,
                  )
              : undefined
          }
          sx={{
            color: "text.primary",
            bgcolor: "transparent",
            border: "1px solid",
            borderColor: "text.primary",
          }}
        />
      ))}
      {rest.length > 0 && (
        <Select
          size="small"
          value=""
          displayEmpty
          onChange={(e) =>
            e.target.value &&
            onChange([...value, e.target.value], `${e.target.value} added.`)
          }
          renderValue={() => (
            <Stack
              direction="row"
              alignItems="center"
              spacing={0.5}
              component="span"
            >
              <Iconify icon="solar:add-linear" width={14} />
              <span>Add language</span>
            </Stack>
          )}
          MenuProps={{ PaperProps: { sx: { maxHeight: 280 } } }}
          /* Outlined like the page's other buttons, so it reads as one. */
          sx={{
            height: 24,
            color: "text.primary",
            "& .MuiSelect-select": {
              typography: "s3",
              fontWeight: 600,
              py: 0,
              pl: 1,
            },
            "& .MuiOutlinedInput-notchedOutline": {
              borderColor: "text.disabled",
            },
            "&:hover .MuiOutlinedInput-notchedOutline": {
              borderColor: "text.secondary",
            },
          }}
        >
          {rest.map((l) => (
            <MenuItem key={l} value={l} sx={{ typography: "s2" }}>
              {l}
            </MenuItem>
          ))}
        </Select>
      )}
    </Stack>
  );
}
LanguagePicker.propTypes = {
  value: PropTypes.array.isRequired,
  onChange: PropTypes.func.isRequired,
};
