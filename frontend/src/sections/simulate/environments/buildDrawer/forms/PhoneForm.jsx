import PropTypes from "prop-types";
import { Box, Stack, Typography } from "@mui/material";
import { AGENT_TYPES } from "src/sections/agents/constants";
import usePanelBuild from "../../hooks/usePanelBuild";
import { DEFAULT_SCENARIOS } from "../../panels/scenarioCountRules";
import { COUNTRY_BY_ISO } from "../../components/countryCodes";
import CopyField from "../../components/CopyField";
import { INPUT_SX, T12 } from "../drawerStyles";
import useDrawerForm from "./useDrawerForm";
import { contactNumberError } from "./VoiceContact";
import CountryCodeSelect from "../../components/CountryCodeSelect";
import {
  Field,
  FormCard,
  Helper,
  HintLine,
  Label,
  SegmentedToggle,
  ToggleRow,
  useUiOnlyBuild,
} from "./formBits";
import PhoneSteps, { INITIAL_STEPS, stepsComplete } from "./extras/PhoneSteps";

/* The number simulated customers answer on when the agent places the call. */
const TEST_NUMBER = "+1 415 555 0186";

/**
 * Any voice agent reachable by phone (the build's "Others" connector): a
 * simulated customer dials its number. Calls from the agent (Outbound) and
 * instructions written as steps have no API yet: either one makes the form UI
 * only, on sample data, until it's switched back.
 */
export default function PhoneForm({ onBusyChange }) {
  const live = usePanelBuild();
  const [form, set, setForm] = useDrawerForm(
    {
      direction: "inbound",
      shape: "prompt",
      steps: INITIAL_STEPS,
      countryIso: "US",
      number: "",
      agentSpeaksFirst: false,
      instructions: "",
      repoUrl: "",
      scenarioCount: DEFAULT_SCENARIOS,
    },
    { resetPreflight: () => resetBoth() },
  );
  const {
    direction,
    shape,
    steps,
    countryIso,
    number,
    agentSpeaksFirst,
    instructions,
    repoUrl,
    scenarioCount,
  } = form;
  const outbound = direction === "outbound";
  const bySteps = shape === "steps";
  const uiOnly = outbound || bySteps;
  const numberLabel = outbound ? "Number it calls from" : "Number to call";
  const phoneError = contactNumberError(countryIso, number);
  const missing = phoneError
    ? `Add the ${numberLabel.toLowerCase()}`
    : bySteps
      ? stepsComplete(steps)
        ? null
        : "Name each step and say what it does"
      : !instructions.trim()
        ? "Paste its instructions"
        : null;

  const dialled = `${COUNTRY_BY_ISO[countryIso]?.dial || ""} ${number.trim()}`;
  const sample = useUiOnlyBuild([
    {
      label: numberLabel,
      detail: outbound
        ? `Calls from ${dialled} are answered on ${TEST_NUMBER}.`
        : `Dials ${dialled} over PSTN.`,
    },
    {
      label: bySteps ? "Steps" : "Its instructions",
      detail: bySteps
        ? `${steps.length} steps, each one of the agent's members`
        : "Scenarios are written against these",
    },
  ]);
  const build = uiOnly ? sample : live;
  const resetBoth = () => {
    live.resetPreflight();
    sample.resetPreflight();
  };
  // Placing the call, the agent opens it, so outbound starts with it speaking first.
  const pickDirection = (next) => {
    setForm((s) => ({
      ...s,
      direction: next,
      agentSpeaksFirst: next === "outbound",
    }));
    resetBoth();
  };

  const buildSource = () => ({
    kind: "platform",
    agentType: AGENT_TYPES.VOICE,
    provider: "other",
    scenarioCount: Number(scenarioCount) || undefined,
    agentMode: "prompt",
    prompt: instructions.trim(),
    ...(repoUrl.trim() ? { repoUrl: repoUrl.trim() } : {}),
    callDirection: "inbound",
    contact: {
      mode: "phone",
      inboundCalls: true,
      agentSpeaksFirst,
      countryIso,
      countryCode: COUNTRY_BY_ISO[countryIso]?.dial || "",
      number: number.trim(),
    },
  });

  return (
    <FormCard
      intro="Any voice agent you can reach by phone. We call it, or it calls our test number, and nothing in its account is read or changed."
      uiOnly={uiOnly}
      build={build}
      scenarioCount={scenarioCount}
      onScenarioCount={set("scenarioCount")}
      canRun={!missing}
      blockedReason={
        phoneError
          ? "Add a valid number, then preflight checks it."
          : bySteps
            ? "Name each step and say what it does, then preflight checks them."
            : "Paste its instructions, then preflight checks them."
      }
      onRun={() =>
        uiOnly ? sample.runPreflight() : live.runPreflight(buildSource())
      }
      hint={missing}
      onBusyChange={onBusyChange}
    >
      <Box>
        <Label>Direction</Label>
        <Stack
          direction="row"
          alignItems="center"
          spacing={1.25}
          sx={{ mt: 0.75 }}
        >
          <SegmentedToggle
            label="Direction"
            value={direction}
            onChange={pickDirection}
            options={[
              { value: "inbound", label: "Inbound" },
              { value: "outbound", label: "Outbound" },
            ]}
          />
          <Typography sx={{ ...T12, color: "text.secondary" }}>
            {outbound
              ? "Your agent places the call. It dials our test number."
              : "Your agent takes the call. A simulated customer dials its number."}
          </Typography>
        </Stack>
      </Box>
      <Box>
        <Label>{numberLabel}</Label>
        <Stack direction="row" spacing={1} sx={{ mt: 0.75 }}>
          <Box
            sx={{
              width: 110,
              flexShrink: 0,
              ...INPUT_SX,
              "& .MuiInputBase-root": { minHeight: 28, py: "0 !important" },
            }}
          >
            <CountryCodeSelect
              value={countryIso}
              onChange={set("countryIso")}
            />
          </Box>
          <Field
            mono
            value={number}
            onChange={set("number")}
            placeholder="415 555 0142"
            inputProps={{ "aria-label": numberLabel }}
            sx={{ flex: 1 }}
          />
        </Stack>
        <Helper error={!!number.trim() && !!phoneError}>
          {number.trim() && phoneError
            ? phoneError
            : outbound
              ? "Its caller ID, how we know a call on the test number is your agent."
              : "Its own number. Every scenario dials it."}
        </Helper>
      </Box>
      {outbound && (
        <Box>
          <Label>Test number your agent calls</Label>
          <Box sx={{ mt: 0.75 }}>
            <CopyField value={TEST_NUMBER} />
          </Box>
          <Typography
            component="div"
            sx={{ ...T12, color: "text.secondary", mt: 0.5 }}
          >
            <HintLine
              text="Point your agent's outbound calls here for a run."
              hint="A simulated customer from the scenario answers each call."
            />
          </Typography>
        </Box>
      )}
      <ToggleRow
        title="Agent speaks first"
        body={
          agentSpeaksFirst
            ? "The simulated customer waits for its greeting."
            : "The simulated customer speaks first."
        }
        checked={agentSpeaksFirst}
        onChange={set("agentSpeaksFirst")}
      />
      <Box>
        <Stack
          direction="row"
          alignItems="center"
          justifyContent="space-between"
          sx={{ mb: 0.75 }}
        >
          <Label>How it&apos;s written</Label>
          <SegmentedToggle
            label="How it's written"
            value={shape}
            onChange={set("shape")}
            options={[
              { value: "prompt", label: "One prompt" },
              { value: "steps", label: "Steps" },
            ]}
          />
        </Stack>
        {bySteps ? (
          <PhoneSteps steps={steps} onChange={set("steps")} />
        ) : (
          <Field
            label="Its instructions"
            required
            multiline
            minRows={4}
            value={instructions}
            onChange={set("instructions")}
            placeholder="Paste the agent's system prompt or the script it follows."
            helper={
              <HintLine
                text="Scenarios are written against these."
                hint="The prompt only seeds scenarios; it never changes the live agent."
              />
            }
          />
        )}
      </Box>
      <Field
        label="GitHub repo (optional)"
        mono
        value={repoUrl}
        onChange={set("repoUrl")}
        placeholder="https://github.com/your-org/your-agent"
        helper="Lets us read the agent's tools and prompts to seed matching scenarios."
      />
      <Typography component="div" sx={{ ...T12, color: "text.secondary" }}>
        <HintLine
          text="Test only. Improvements end in a suggested fix."
          hint="We only call it, so we can't change it ourselves."
        />
      </Typography>
    </FormCard>
  );
}
PhoneForm.propTypes = { onBusyChange: PropTypes.func };
