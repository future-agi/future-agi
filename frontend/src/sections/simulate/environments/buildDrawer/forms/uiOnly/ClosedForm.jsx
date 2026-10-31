import PropTypes from "prop-types";
import { Box, Stack, Typography } from "@mui/material";
import { DEFAULT_SCENARIOS } from "../../../panels/scenarioCountRules";
import { T12 } from "../../drawerStyles";
import useDrawerForm from "../useDrawerForm";
import { CountryCode, contactNumberError } from "../VoiceContact";
import {
  ChipToggle,
  Field,
  FormCard,
  HintLine,
  Label,
  useUiOnlyBuild,
} from "../formBits";
import SecretField from "../shared/SecretField";
import { CLOSED_PLATFORM_REACH, CLOSED_REACH_LABELS } from "./sampleData";

const REACH_ICONS = {
  phone: "solar:phone-calling-rounded-linear",
  chat: "solar:chat-round-linear",
  api: "solar:code-square-linear",
};
const URL_OK = /^https?:\/\/\S+$/i;
/* Where the reply sits in the API's response: a JSON path. */
const REPLY_PATH = /^\$(\.[A-Za-z_][\w-]*|\[-?\d+\])+$/;

const sampleChecksFor = (label) => [
  { label: "Reachable", detail: `${label} answered a test conversation` },
  { label: "Instructions", detail: "Read as the reference for scenarios" },
];

/**
 * An agent on a closed platform (Sierra, Decagon, Intercom Fin, Zendesk).
 * Their configuration is closed, so nothing is imported: we reach it the way
 * a customer does, by its phone line or chat channel, or through its
 * conversation API where one exists, with its instructions pasted as the
 * reference. UI only: preflight is a sample and nothing builds.
 */
export default function ClosedForm({ item, onBusyChange }) {
  const reach = CLOSED_PLATFORM_REACH[item.setup.platform];
  const label = item.name;
  const build = useUiOnlyBuild(sampleChecksFor(label));
  const [form, set, setForm] = useDrawerForm(
    {
      way: reach.ways[0],
      countryIso: "US",
      number: "",
      url: "",
      token: "",
      replyPath: "$.messages[-1].text",
      instructions: "",
      name: "",
      scenarioCount: DEFAULT_SCENARIOS,
    },
    build,
  );
  const { way, url, token, replyPath, instructions, name, scenarioCount } =
    form;

  // Another way in asks for another address.
  const pickWay = (next) => {
    setForm((s) => ({ ...s, way: next, url: "", token: "" }));
    build.resetPreflight();
  };

  const phoneError =
    way === "phone" ? contactNumberError(form.countryIso, form.number) : null;
  const urlTyped = url.trim();
  const urlError = urlTyped && !URL_OK.test(urlTyped);
  const pathError = replyPath.trim() && !REPLY_PATH.test(replyPath.trim());
  const reachOk =
    way === "phone"
      ? !phoneError
      : way === "chat"
        ? URL_OK.test(urlTyped)
        : URL_OK.test(urlTyped) &&
          !!token.trim() &&
          REPLY_PATH.test(replyPath.trim());
  const missing = !reachOk
    ? way === "phone"
      ? phoneError
      : way === "chat"
        ? "Add the chat channel's address"
        : "Add the API address, token and reply path"
    : !instructions.trim()
      ? "Paste its instructions"
      : null;
  const fallbackName = `${label} ${way === "phone" ? "phone line" : way === "api" ? "agent" : "chat"}`;

  return (
    <FormCard
      uiOnly
      intro="Their configuration is closed, so we test the agent the way a customer reaches it. Improvements end in a suggested fix."
      build={build}
      scenarioCount={scenarioCount}
      onScenarioCount={set("scenarioCount")}
      canRun={!missing}
      blockedReason="Add how we reach it and its instructions, then preflight checks them."
      onRun={build.runPreflight}
      hint={missing}
      onBusyChange={onBusyChange}
    >
      <Box>
        <Label>How we reach it</Label>
        <Box sx={{ display: "flex", flexWrap: "wrap", gap: 0.75, mt: 0.75 }}>
          {reach.ways.map((w) => (
            <ChipToggle
              key={w}
              icon={REACH_ICONS[w]}
              label={w === "api" ? reach.api : CLOSED_REACH_LABELS[w]}
              on={way === w}
              onClick={() => pickWay(w)}
            />
          ))}
        </Box>
      </Box>

      {way === "phone" && (
        <Stack direction="row" spacing={1}>
          <CountryCode value={form.countryIso} onChange={set("countryIso")} />
          <Field
            label="Phone number"
            required
            mono
            value={form.number}
            onChange={set("number")}
            placeholder="415 555 0142"
            error={form.number.trim() ? phoneError : null}
            sx={{ flex: 1 }}
          />
        </Stack>
      )}
      {way === "chat" && (
        <Field
          label="Chat channel address"
          required
          mono
          value={url}
          onChange={set("url")}
          placeholder={reach.chatPlaceholder}
          error={urlError ? "An address starts with https:// or http://" : null}
          helper={
            <HintLine
              text="Where your customers chat with it."
              hint="Each simulated customer opens a new conversation there and writes to it, the way your customers do."
            />
          }
        />
      )}
      {way === "api" && (
        <Stack spacing={1.5}>
          <Field
            label={`${reach.api} address`}
            required
            mono
            value={url}
            onChange={set("url")}
            placeholder={reach.apiPlaceholder}
            error={
              urlError ? "An address starts with https:// or http://" : null
            }
            helper="We start a conversation per scenario and send each simulated turn here."
          />
          <Stack direction="row" spacing={1.5} alignItems="flex-start">
            <SecretField
              label="API token"
              required
              value={token}
              onChange={set("token")}
              placeholder="Paste the token"
              sx={{ flex: 1 }}
            />
            <Field
              label="Where the reply sits"
              required
              mono
              value={replyPath}
              onChange={set("replyPath")}
              error={pathError ? "A JSON path, like $.messages[-1].text" : null}
              helper="A JSON path in the response."
              sx={{ width: 220, flexShrink: 0 }}
            />
          </Stack>
        </Stack>
      )}

      <Field
        label="Its instructions"
        required
        multiline
        minRows={4}
        value={instructions}
        onChange={set("instructions")}
        placeholder="Paste the agent's instructions, or the guidance and procedures it follows."
        helper={
          <HintLine
            text="Scenarios and the suggested fix are written against these."
            hint="Once you apply the fix and mark it released, a re-run proves it."
          />
        }
      />
      <Field
        label="Name"
        value={name}
        onChange={set("name")}
        placeholder={fallbackName}
      />
      <Typography component="div" sx={{ ...T12, color: "text.secondary" }}>
        <HintLine
          text="Test only. Improvements end in a diagnosis and a suggested fix."
          hint={`${label} doesn't expose its agents' settings, so nothing is imported and nothing is changed there.`}
        />
      </Typography>
    </FormCard>
  );
}
ClosedForm.propTypes = {
  item: PropTypes.object.isRequired,
  onBusyChange: PropTypes.func,
};
