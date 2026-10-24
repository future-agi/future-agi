import PropTypes from "prop-types";
import { useState } from "react";
import {
  Box,
  Button,
  IconButton,
  MenuItem,
  Select,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import {
  COUNTRY_BY_ISO,
  COUNTRY_OPTIONS,
} from "../../environments/connectOptions";
import {
  Field,
  Label,
  SegmentedToggle,
  ToggleRow,
} from "../../environments/StartEnvironment";
import { CopyField } from "../../components/primitives";
import ConnectSteps from "./ConnectSteps";
import { HintLine } from "./replicationBits";
import { useConnectSubmitLabel } from "./connectSubmit";
import { e164Of, factCheck, phoneNumberError, usePreflight } from "./preflight";

const MONO = "ui-monospace, Menlo, monospace";
const STEPS = ["Connect"];
/* The number simulated customers answer on when the agent places the call —
   reserved for this agent while it's connected. */
const TEST_NUMBER = "+1 415 555 0186";

/**
 * Only a number to call: on one screen, the direction (we call it, or it
 * calls our test number), its number, who speaks first and its
 * instructions as the reference for what it should do. Test only — nothing
 * in its account is read, copied or changed, so improvements end in a
 * suggested fix.
 */
export default function PhoneAgentFlow({ title, onSave, initial = null }) {
  const submitLabel = useConnectSubmitLabel();
  /* Inbound: a simulated caller dials its number. Outbound: it dials ours. */
  const [direction, setDirection] = useState(
    initial?.callDirection === "outbound" ? "outbound" : "inbound",
  );
  const [countryIso, setCountryIso] = useState("US");
  const [number, setNumber] = useState(initial?.number || "");
  /* Placing a call, the agent opens it — so outbound starts with it speaking first. */
  const [agentSpeaksFirst, setAgentSpeaksFirst] = useState(
    direction === "outbound",
  );
  const [instructions, setInstructions] = useState("");
  /* One prompt, or a series of steps the way Bland, Retell and Vapi
     workflows are written — each step its own instructions and where it
     goes next. Steps come in as the agent's members. */
  const [shape, setShape] = useState("prompt");
  const [steps, setSteps] = useState(() => [
    { id: "s1", name: "Greet and identify", instructions: "", next: "" },
    { id: "s2", name: "", instructions: "", next: "" },
  ]);
  const editStep = (id, patch) =>
    setSteps((list) =>
      list.map((st) => (st.id === id ? { ...st, ...patch } : st)),
    );
  const stepsOk =
    steps.length >= 2 &&
    steps.every((st) => st.name.trim() && st.instructions.trim());
  const scriptOf = () =>
    steps
      .map(
        (st, i) =>
          `## Step ${i + 1} · ${st.name.trim()}\n${st.instructions.trim()}${st.next.trim() ? `\nThen: ${st.next.trim()}` : ""}`,
      )
      .join("\n\n");
  const written = shape === "steps" ? stepsOk : !!instructions.trim();
  const [name, setName] = useState("");
  const outbound = direction === "outbound";
  const dial = COUNTRY_BY_ISO[countryIso]?.dial || "+1";
  const numberError = phoneNumberError(dial, number);
  const ready = !numberError && written;

  const pickDirection = (d) => {
    setDirection(d);
    setAgentSpeaksFirst(d === "outbound");
  };

  const preflight = usePreflight(
    [
      factCheck({
        id: "phone",
        label: outbound ? "Number it calls from" : "Number to call",
        ok: !numberError,
        okText: outbound
          ? `Calls from ${e164Of(dial, number)} are answered on ${TEST_NUMBER}.`
          : `Dials ${e164Of(dial, number)} over PSTN.`,
        failText: numberError || "",
      }),
    ],
    [direction, countryIso, number],
  );

  const save = () =>
    onSave({
      kind: "endpoint",
      phone: true,
      agentType: "voice",
      value: `tel:${e164Of(dial, number)}`,
      phoneNumber: number.trim(),
      countryCode: dial,
      callDirection: direction,
      agentSpeaksFirst,
      ...(outbound && { testNumber: TEST_NUMBER }),
      systemPrompt: shape === "steps" ? scriptOf() : instructions.trim(),
      ...(shape === "steps" && {
        members: steps.map((st, i) => ({
          id: `step-${i + 1}`,
          name: st.name.trim(),
          role: `Step ${i + 1}${st.next.trim() ? ` · then ${st.next.trim()}` : ""}`,
          instructions: st.instructions.trim(),
        })),
      }),
      name: name.trim() || `Phone agent ${e164Of(dial, number)}`,
      versionNote: "Connected by phone number.",
      /* What the confirm step would ask — already answered here. */
      flowReach: {
        direction,
        whoSpeaksFirst: agentSpeaksFirst ? "agent" : "caller",
      },
    });

  const next = {
    label: submitLabel,
    icon: "solar:link-circle-linear",
    disabled: !ready,
    hint: numberError
      ? outbound
        ? "Add the number it calls from"
        : "Add the number to call"
      : shape === "steps"
        ? "Name each step and say what it does"
        : "Paste its instructions",
    onClick: save,
  };

  return (
    <ConnectSteps
      title={title}
      subtitle="Any voice agent you can reach by phone. We call it, or it calls our test number — nothing in its account is read or changed."
      steps={STEPS}
      active={0}
      next={next}
      preflight={{
        state: preflight,
        canRun: !numberError,
        blockedReason: numberError
          ? "Add a valid number — then preflight checks it."
          : null,
      }}
    >
      <Stack spacing={1.75}>
        <Box>
          <Label>Direction</Label>
          <Stack
            direction={{ xs: "column", sm: "row" }}
            alignItems={{ sm: "center" }}
            spacing={1.25}
            sx={{ mt: 0.75 }}
          >
            <SegmentedToggle
              value={direction}
              onChange={pickDirection}
              options={[
                { value: "inbound", label: "Inbound" },
                { value: "outbound", label: "Outbound" },
              ]}
            />
            <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
              {outbound
                ? "Your agent places the call — it dials our test number."
                : "Your agent takes the call — a simulated customer dials its number."}
            </Typography>
          </Stack>
        </Box>

        <Box>
          <Label>{outbound ? "Number it calls from" : "Number to call"}</Label>
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
              error={!!number.trim() && !!numberError}
              InputProps={{ sx: { typography: "s2", fontFamily: MONO } }}
            />
          </Stack>
          <Typography
            sx={{
              typography: "s3",
              color:
                number.trim() && numberError ? "error.main" : "text.subtitle",
              mt: 0.5,
            }}
          >
            {number.trim() && numberError
              ? numberError
              : outbound
                ? "Its caller ID — how we know a call on the test number is your agent."
                : "Its own number — every scenario dials it."}
          </Typography>
        </Box>

        {/* Outbound: where its calls should go during a run. */}
        {outbound && (
          <Box>
            <CopyField
              label="Test number your agent calls"
              value={TEST_NUMBER}
            />
            <Typography
              component="div"
              sx={{ typography: "s3", color: "text.subtitle", mt: 0.5 }}
            >
              <HintLine hint="Each call is answered by a simulated customer from the scenario being run. The number stays this agent's while it's connected — change it on the agent's page.">
                Point your agent&apos;s outbound calls here for a run.
              </HintLine>
            </Typography>
          </Box>
        )}

        <ToggleRow
          checked={agentSpeaksFirst}
          onChange={setAgentSpeaksFirst}
          title="Agent speaks first"
          body={
            agentSpeaksFirst
              ? "The simulated customer waits for its greeting."
              : "The simulated customer speaks first."
          }
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
              value={shape}
              onChange={setShape}
              options={[
                { value: "prompt", label: "One prompt" },
                { value: "steps", label: "Steps" },
              ]}
            />
          </Stack>
          {shape === "prompt" ? (
            <Field
              label="Its instructions"
              required
              multiline
              minRows={4}
              placeholder="Paste the agent's system prompt or the script it follows."
              value={instructions}
              onChange={setInstructions}
              helper={
                <HintLine hint="We can't read or change it. Scenarios and the suggested fix are written against these; once you apply the fix, a re-run on the same environment version proves it.">
                  Scenarios and the suggested fix are written against these.
                </HintLine>
              }
            />
          ) : (
            <Stack spacing={1}>
              {steps.map((st, i) => (
                <Box
                  key={st.id}
                  sx={{
                    border: "1px solid",
                    borderColor: "divider",
                    borderRadius: 1,
                    p: 1.25,
                  }}
                >
                  <Stack
                    direction="row"
                    alignItems="center"
                    spacing={1}
                    sx={{ mb: 1 }}
                  >
                    <Typography
                      sx={{
                        typography: "s3",
                        fontWeight: 600,
                        color: "text.subtitle",
                        flexShrink: 0,
                      }}
                    >{`Step ${i + 1}`}</Typography>
                    <TextField
                      size="small"
                      fullWidth
                      value={st.name}
                      placeholder={
                        i === 0
                          ? "Greet and identify"
                          : "e.g. Book the appointment"
                      }
                      onChange={(e) =>
                        editStep(st.id, { name: e.target.value })
                      }
                      InputProps={{ sx: { typography: "s2" } }}
                    />
                    {steps.length > 2 && (
                      <IconButton
                        size="small"
                        aria-label={`Remove step ${i + 1}`}
                        onClick={() =>
                          setSteps((list) => list.filter((x) => x.id !== st.id))
                        }
                      >
                        <Iconify
                          icon="solar:trash-bin-minimalistic-linear"
                          width={16}
                        />
                      </IconButton>
                    )}
                  </Stack>
                  <TextField
                    size="small"
                    fullWidth
                    multiline
                    minRows={2}
                    value={st.instructions}
                    placeholder="What the agent says and does in this step."
                    onChange={(e) =>
                      editStep(st.id, { instructions: e.target.value })
                    }
                    InputProps={{ sx: { typography: "s2" } }}
                  />
                  <TextField
                    size="small"
                    fullWidth
                    value={st.next}
                    sx={{ mt: 1 }}
                    placeholder={
                      i === steps.length - 1
                        ? "Then — e.g. end the call"
                        : `Then — e.g. go to step ${i + 2} once the caller is verified; otherwise transfer`
                    }
                    onChange={(e) => editStep(st.id, { next: e.target.value })}
                    InputProps={{ sx: { typography: "s3" } }}
                  />
                </Box>
              ))}
              <Box>
                <Button
                  size="small"
                  variant="outlined"
                  startIcon={<Iconify icon="solar:add-linear" width={14} />}
                  onClick={() =>
                    setSteps((list) => [
                      ...list,
                      {
                        id: `s${Date.now()}`,
                        name: "",
                        instructions: "",
                        next: "",
                      },
                    ])
                  }
                  sx={{ color: "text.primary", borderColor: "divider" }}
                >
                  Add step
                </Button>
              </Box>
              <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
                Each step becomes one of the agent&apos;s members, so a
                suggested fix names the step it changes.
              </Typography>
            </Stack>
          )}
        </Box>
        <Field
          label="Name"
          placeholder="e.g. Pharmacy refill line"
          value={name}
          onChange={setName}
        />
        <Typography
          component="div"
          sx={{ typography: "s3", color: "text.subtitle" }}
        >
          <HintLine hint="We only call it (or answer it), so we can't change it ourselves.">
            Test only — improvements end in a suggested fix.
          </HintLine>
        </Typography>
      </Stack>
    </ConnectSteps>
  );
}

PhoneAgentFlow.propTypes = {
  title: PropTypes.string,
  onSave: PropTypes.func.isRequired,
  /* { number, callDirection } — what the page already knew. */
  initial: PropTypes.object,
};
