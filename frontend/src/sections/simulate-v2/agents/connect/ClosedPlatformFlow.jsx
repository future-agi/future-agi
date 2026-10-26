import PropTypes from "prop-types";
import { useState } from "react";
import {
  Box,
  MenuItem,
  Select,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import { CLOSED_PLATFORMS } from "../../_mock/rsiModel";
import { keyRefOf } from "../../_mock/secretRefs";
import {
  CLOSED_PLATFORM_REACH,
  CLOSED_REACH_LABELS,
  COUNTRY_BY_ISO,
  COUNTRY_OPTIONS,
} from "../../environments/connectOptions";
import { ChipCard, Field, Label } from "../../environments/StartEnvironment";
import ConnectSteps from "./ConnectSteps";
import SecretRefField from "./SecretRefField";
import { HintLine } from "./replicationBits";
import { useConnectSubmitLabel } from "./connectSubmit";

const MONO = "ui-monospace, Menlo, monospace";
const STEPS = ["Connect"];
const REACH_ICONS = {
  phone: "solar:phone-calling-rounded-linear",
  chat: "solar:chat-round-linear",
  api: "solar:code-square-linear",
};
const URL_OK = /^https?:\/\/\S+$/i;
/* Where the reply sits in the API's response — a JSON path. */
const REPLY_PATH = /^\$(\.[A-Za-z_][\w-]*|\[-?\d+\])+$/;

/**
 * An agent on a closed platform — Sierra, Decagon, Intercom Fin, Zendesk —
 * on one screen. Their configuration is closed, so nothing is imported:
 * we reach it the way a customer does (its phone line or chat channel) or
 * through its conversation API where one exists, with its instructions
 * pasted as the reference. Test only — an improvement ends in a diagnosis
 * and a suggested fix, proven by a re-run once it's applied.
 */
export default function ClosedPlatformFlow({
  title,
  onSave,
  initial = null,
  lockedType = null,
}) {
  const submitLabel = useConnectSubmitLabel();
  const [platformId, setPlatformId] = useState(
    initial?.closedPlatform || CLOSED_PLATFORMS[0].id,
  );
  const platform =
    CLOSED_PLATFORMS.find((p) => p.id === platformId) || CLOSED_PLATFORMS[0];
  const reach = CLOSED_PLATFORM_REACH[platform.id];
  /* Connecting for one environment: only the ways that bring that type. */
  const ways = reach.ways.filter(
    (w) =>
      !lockedType || (lockedType === "voice" ? w === "phone" : w !== "phone"),
  );
  const [wayPicked, setWayPicked] = useState(initial?.reach || null);
  const way = ways.includes(wayPicked) ? wayPicked : ways[0];

  const [countryIso, setCountryIso] = useState("US");
  const [number, setNumber] = useState("");
  const [url, setUrl] = useState("");
  const [tokenRef, setTokenRef] = useState("");
  const [replyPath, setReplyPath] = useState("$.messages[-1].text");
  const [instructions, setInstructions] = useState("");
  const [name, setName] = useState("");

  const dial = COUNTRY_BY_ISO[countryIso]?.dial || "+1";
  /* The token typed or referenced — recorded as where it's held. */
  const savedTokenRef = keyRefOf(
    tokenRef,
    `vault://acme/sim/${platform.id}_api_token`,
  );
  const digits = number.replace(/[^\d]/g, "");
  const reachOk =
    way === "phone"
      ? digits.length >= 6
      : way === "chat"
        ? URL_OK.test(url.trim())
        : URL_OK.test(url.trim()) &&
          !!savedTokenRef &&
          REPLY_PATH.test(replyPath.trim());
  const ready = !!way && reachOk && !!instructions.trim();
  const fallbackName = `${platform.label} ${way === "phone" ? "phone line" : way === "api" ? "agent" : "chat"}`;

  const pickPlatform = (id) => {
    setPlatformId(id);
    setWayPicked(null);
    setUrl("");
    setTokenRef("");
  };

  const save = () => {
    const common = {
      name: name.trim() || fallbackName,
      systemPrompt: instructions.trim(),
      closedPlatform: platform.id,
      versionNote: `Connected through ${platform.label}'s ${CLOSED_REACH_LABELS[way].toLowerCase()}.`,
    };
    if (way === "phone") {
      onSave({
        ...common,
        kind: "endpoint",
        phone: true,
        agentType: "voice",
        value: `tel:${dial}${digits}`,
        phoneNumber: number.trim(),
        countryCode: dial,
        callDirection: "inbound",
      });
      return;
    }
    onSave({
      ...common,
      kind: "endpoint",
      agentType: "chat",
      value: url.trim(),
      transport: "http",
      closedReach: way,
      ...(way === "api" && {
        tokenRef: savedTokenRef,
        replyPath: replyPath.trim(),
      }),
    });
  };

  const next = {
    label: submitLabel,
    icon: "solar:link-circle-linear",
    disabled: !ready,
    hint: !way
      ? "Pick how we reach it"
      : !reachOk
        ? way === "phone"
          ? "Add the number to call"
          : way === "chat"
            ? "Add the chat channel's address"
            : "Add the API address, token reference and reply path"
        : "Paste its instructions",
    onClick: save,
  };

  return (
    <ConnectSteps
      title={title}
      subtitle="Their configuration is closed, so we test the agent the way a customer reaches it. Improvements end in a suggested fix."
      steps={STEPS}
      active={0}
      next={next}
    >
      <Stack spacing={1.75}>
        {/* Picked on the directory (Sierra, Decagon…): nothing to switch. */}
        {!initial?.closedPlatform && (
          <Box>
            <Label>Platform</Label>
            <Box
              sx={{ display: "flex", flexWrap: "wrap", gap: 0.75, mt: 0.75 }}
            >
              {CLOSED_PLATFORMS.map((p) => (
                <ChipCard
                  key={p.id}
                  icon={CLOSED_PLATFORM_REACH[p.id]?.icon}
                  label={p.label}
                  on={platform.id === p.id}
                  onClick={() => pickPlatform(p.id)}
                />
              ))}
            </Box>
          </Box>
        )}

        <Box>
          <Label>How we reach it</Label>
          <Box sx={{ display: "flex", flexWrap: "wrap", gap: 0.75, mt: 0.75 }}>
            {ways.map((w) => (
              <ChipCard
                key={w}
                icon={REACH_ICONS[w]}
                label={w === "api" ? reach.api : CLOSED_REACH_LABELS[w]}
                on={way === w}
                onClick={() => setWayPicked(w)}
              />
            ))}
          </Box>
          {!ways.length && (
            <Typography
              sx={{ typography: "s3", color: "text.subtitle", mt: 0.75 }}
            >
              {`${platform.label} has no way in for this type of agent.`}
            </Typography>
          )}
        </Box>

        {way === "phone" && (
          <Box>
            <Label>Phone number</Label>
            <Stack direction="row" spacing={1} sx={{ mt: 0.75 }}>
              <Select
                size="small"
                value={countryIso}
                onChange={(e) => setCountryIso(e.target.value)}
                renderValue={(iso) =>
                  `${COUNTRY_BY_ISO[iso]?.flag || ""} ${COUNTRY_BY_ISO[iso]?.dial || ""}`
                }
                sx={{ width: 110, typography: "s2" }}
                MenuProps={{ PaperProps: { sx: { maxHeight: 320 } } }}
              >
                {COUNTRY_OPTIONS.map((c) => (
                  <MenuItem
                    key={c.iso}
                    value={c.iso}
                    sx={{ typography: "s2", gap: 1 }}
                  >
                    <Box component="span">{c.flag}</Box>
                    <Box component="span" sx={{ flex: 1 }}>
                      {c.name}
                    </Box>
                    <Box component="span" sx={{ color: "text.subtitle" }}>
                      {c.dial}
                    </Box>
                  </MenuItem>
                ))}
              </Select>
              <TextField
                size="small"
                fullWidth
                value={number}
                onChange={(e) => setNumber(e.target.value)}
                placeholder="415 555 0142"
                InputProps={{ sx: { typography: "s2", fontFamily: MONO } }}
              />
            </Stack>
          </Box>
        )}
        {way === "chat" && (
          <Field
            label="Chat channel address"
            required
            mono
            placeholder={reach.chatPlaceholder}
            value={url}
            onChange={setUrl}
            helper={
              <HintLine hint="Each simulated customer opens a new conversation there and writes to it, the way your customers do.">
                Where your customers chat with it.
              </HintLine>
            }
          />
        )}
        {way === "api" && (
          <Stack spacing={1.5}>
            <Field
              label={`${reach.api} address`}
              required
              mono
              placeholder={reach.apiPlaceholder}
              value={url}
              onChange={setUrl}
              helper="We start a conversation per scenario and send each simulated turn here."
            />
            <Stack direction={{ xs: "column", sm: "row" }} spacing={1.5}>
              <SecretRefField
                label="API token"
                required
                fullWidth
                placeholder={`vault://acme/sim/${platform.id}_api_token`}
                value={tokenRef}
                onChange={setTokenRef}
              />
              <Box sx={{ width: { sm: 220 }, flexShrink: 0 }}>
                <Field
                  label="Where the reply sits"
                  required
                  mono
                  value={replyPath}
                  onChange={setReplyPath}
                  helper="A JSON path in the response."
                  fullWidth
                />
              </Box>
            </Stack>
          </Stack>
        )}

        <Field
          label="Its instructions"
          required
          multiline
          minRows={4}
          placeholder="Paste the agent's instructions, or the guidance and procedures it follows."
          value={instructions}
          onChange={setInstructions}
          helper={
            <HintLine hint="Once you apply the fix and mark it released, a re-run proves it.">
              Scenarios and the suggested fix are written against these.
            </HintLine>
          }
        />
        <Field
          label="Name"
          placeholder={fallbackName}
          value={name}
          onChange={setName}
        />
        <Typography
          component="div"
          sx={{ typography: "s3", color: "text.subtitle" }}
        >
          <HintLine
            hint={`${platform.label} doesn't expose its agents' settings, so nothing is imported and nothing is changed there.`}
          >
            Test only — improvements end in a diagnosis and a suggested fix.
          </HintLine>
        </Typography>
      </Stack>
    </ConnectSteps>
  );
}

ClosedPlatformFlow.propTypes = {
  title: PropTypes.string,
  onSave: PropTypes.func.isRequired,
  /* { closedPlatform, reach } — opened from a directory row. */
  initial: PropTypes.object,
  /* Set when connecting for one environment — a voice one reaches it by phone only. */
  lockedType: PropTypes.string,
};
