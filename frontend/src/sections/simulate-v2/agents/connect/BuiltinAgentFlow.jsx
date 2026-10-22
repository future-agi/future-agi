import PropTypes from "prop-types";
import { useState } from "react";
import { Box, Button, IconButton, MenuItem, Select, Stack, TextField, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { BUILTIN_MODELS, BUILTIN_PROMPT_PLACEHOLDER, BUILTIN_TOOL_LIBRARY } from "../../_mock/connectAgent";
import { ChipCard, Field, Label, SegmentedToggle } from "../../environments/StartEnvironment";
import ConnectSteps from "./ConnectSteps";
import { HintLine } from "./replicationBits";
import { paths } from "src/routes/paths";

const MONO = "ui-monospace, Menlo, monospace";
/* One screen: what it is (name, prompt, model) and, if it has any, its
   tools — then Create. Observe and constraints start at the defaults and are
   set on the agent's page, in Finish setting up. */
const STEPS = ["Create"];
const TOOL_NAME = /^[a-z][a-z0-9_]*$/;

/**
 * An agent built in Future AGI: a system prompt, the tools it may call and
 * the model it runs on — defined here, run here, nothing to deploy. Saved
 * as v1; improvements ship as new agent or prompt versions.
 */
export default function BuiltinAgentFlow({ title, onSave, initial = null, lockedType = null }) {
  const [name, setName] = useState("");
  const [agentType, setAgentType] = useState(lockedType || "chat");
  const [prompt, setPrompt] = useState(initial?.prompt || "");
  const [model, setModel] = useState("gpt-4o");
  const [tools, setTools] = useState([]);
  const [draft, setDraft] = useState({ name: "", desc: "" });

  const has = (n) => tools.some((t) => t.name === n);
  const addTool = (t) => { if (!has(t.name)) setTools((list) => [...list, { name: t.name, desc: t.desc, args: t.args || [] }]); };
  const draftOk = TOOL_NAME.test(draft.name.trim()) && !has(draft.name.trim()) && !!draft.desc.trim();

  const source = {
    kind: "builtin",
    agentType,
    name: name.trim(),
    prompt: prompt.trim(),
    model,
    tools,
    versionNote: "Created in Future AGI.",
  };

  const next = {
    label: "Create agent · v1",
    icon: "solar:widget-add-linear",
    disabled: !name.trim() || !prompt.trim(),
    hint: !name.trim() ? "Name the agent" : "Write its instructions",
    onClick: () => onSave(source),
  };
  /* Your own tool, written out — behind a link, since most start from the list. */
  const [ownTool, setOwnTool] = useState(false);

  return (
    <ConnectSteps
      title={title}
      subtitle="One prompt, one model and its tools. It runs in Future AGI — nothing to deploy."
      steps={STEPS}
      active={0}
      next={next}
    >
        <Stack spacing={1.75}>
          {/* More than one prompt is a graph — Agent Playground builds those, not this form. */}
          <Stack
            direction="row" alignItems="center" spacing={1.25}
            sx={{ px: 1.5, py: 1, border: "1px solid", borderColor: "divider", borderRadius: 1 }}
          >
            <Iconify icon="solar:routing-3-linear" width={16} sx={{ color: "text.secondary", flexShrink: 0 }} />
            <Box flex={1} minWidth={0}>
              <Typography sx={{ typography: "s2", fontWeight: 600 }}>Steps, branches, loops or several agents?</Typography>
              <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
                Build it as a graph in Agent Playground — prompt nodes chained together, agents nested inside agents.
                A long-running or background agent is connected where it runs instead — its repository, our SDK or its endpoint.
              </Typography>
            </Box>
            <Button
              size="small" variant="outlined" href={`${paths.dashboard.agents}?tab=playground`}
              endIcon={<Iconify icon="solar:arrow-right-up-linear" width={13} />}
              sx={{ typography: "s3", fontWeight: 700, height: 26, color: "text.primary", borderColor: "divider", flexShrink: 0 }}
            >
              Open Agent Playground
            </Button>
          </Stack>
          <Stack direction={{ xs: "column", sm: "row" }} spacing={1.5} alignItems={{ sm: "flex-end" }}>
            <Field label="Agent name" required fullWidth placeholder="e.g. Returns assistant" value={name} onChange={setName} />
            {!lockedType && <Box sx={{ flexShrink: 0 }}>
              <Typography sx={{ typography: "s3", fontWeight: 600, mb: 0.5 }}>Talks over</Typography>
              <SegmentedToggle
                value={agentType}
                onChange={setAgentType}
                options={[{ value: "chat", label: "Chat" }, { value: "voice", label: "Voice" }]}
              />
            </Box>}
          </Stack>
          <Field
            label="System prompt" required multiline minRows={7}
            placeholder={BUILTIN_PROMPT_PLACEHOLDER}
            value={prompt} onChange={setPrompt}
            helper={<HintLine hint="Never straight to production.">Improvements ship as new prompt versions you review.</HintLine>}
          />
          <Box sx={{ maxWidth: 320 }}>
            <Typography sx={{ typography: "s3", fontWeight: 600, mb: 0.5 }}>Model</Typography>
            <Select
              size="small" fullWidth value={model} onChange={(e) => setModel(e.target.value)} sx={{ typography: "s2" }}
              renderValue={(id) => {
                const m = BUILTIN_MODELS.find((x) => x.id === id);
                return (
                  <Stack direction="row" spacing={1} alignItems="baseline">
                    <Box component="span" sx={{ fontFamily: MONO }}>{m?.label || id}</Box>
                    {m?.vendor && <Box component="span" sx={{ color: "text.subtitle" }}>{m.vendor}</Box>}
                  </Stack>
                );
              }}
            >
              {BUILTIN_MODELS.map((m) => (
                <MenuItem key={m.id} value={m.id} sx={{ typography: "s2", gap: 1 }}>
                  <Box component="span" sx={{ fontFamily: MONO, flex: 1 }}>{m.label}</Box>
                  <Box component="span" sx={{ color: "text.subtitle" }}>{m.vendor}</Box>
                </MenuItem>
              ))}
            </Select>
          </Box>

          <Stack direction="row" alignItems="baseline" spacing={1} sx={{ pt: 0.5 }}>
            <Typography sx={{ typography: "s2", fontWeight: 700 }}>Tools</Typography>
            <Typography sx={{ typography: "s3", color: "text.subtitle" }}>Optional · in a simulation the environment answers these calls</Typography>
          </Stack>

          <Box sx={{ border: "1px solid", borderColor: "divider", borderRadius: 1, overflow: "hidden" }}>
            {tools.length === 0 ? (
              <Typography sx={{ typography: "s2", color: "text.subtitle", px: 1.75, py: 1.5 }}>
                No tools yet — the agent can only talk.
              </Typography>
            ) : tools.map((t, i) => (
              <Stack
                key={t.name}
                direction="row" alignItems="center" spacing={1.5}
                sx={{ px: 1.75, py: 1, borderTop: i ? "1px solid" : "none", borderColor: "divider" }}
              >
                <Box flex={1} minWidth={0}>
                  <Typography noWrap sx={{ typography: "s2", fontWeight: 600, fontFamily: MONO }}>
                    {`${t.name}(${(t.args || []).join(", ")})`}
                  </Typography>
                  <Typography noWrap sx={{ typography: "s3", color: "text.subtitle" }}>{t.desc}</Typography>
                </Box>
                <IconButton size="small" onClick={() => setTools((list) => list.filter((x) => x.name !== t.name))}>
                  <Iconify icon="solar:trash-bin-minimalistic-linear" width={14} sx={{ color: "text.subtitle" }} />
                </IconButton>
              </Stack>
            ))}
          </Box>

          {BUILTIN_TOOL_LIBRARY.some((t) => !has(t.name)) && (
            <Box>
              <Label>Start from a common tool</Label>
              <Stack direction="row" spacing={0.75} useFlexGap flexWrap="wrap" sx={{ mt: 0.75 }}>
                {BUILTIN_TOOL_LIBRARY.filter((t) => !has(t.name)).map((t) => (
                  <ChipCard key={t.name} icon="solar:add-circle-linear" label={t.name} onClick={() => addTool(t)} />
                ))}
              </Stack>
            </Box>
          )}

          {!ownTool ? (
            /* A button, so it reads as something you can do — not a footnote. */
            <Box>
              <Button
                size="small" variant="outlined" onClick={() => setOwnTool(true)}
                startIcon={<Iconify icon="mingcute:add-line" width={14} />}
                sx={{ typography: "s3", fontWeight: 700, height: 26, color: "text.primary", borderColor: "divider" }}
              >
                Create a tool
              </Button>
            </Box>
          ) : (
          <Box>
            <Label>Describe your own</Label>
            <Stack direction={{ xs: "column", sm: "row" }} spacing={1} sx={{ mt: 0.75 }}>
              <TextField
                size="small" placeholder="tool_name"
                value={draft.name} onChange={(e) => setDraft((d) => ({ ...d, name: e.target.value.toLowerCase().replace(/\s+/g, "_") }))}
                sx={{ width: { sm: 200 }, "& .MuiInputBase-input": { typography: "s2", fontFamily: MONO } }}
              />
              <TextField
                size="small" fullWidth placeholder="When the agent should call it, and what it returns"
                value={draft.desc} onChange={(e) => setDraft((d) => ({ ...d, desc: e.target.value }))}
                sx={{ "& .MuiInputBase-input": { typography: "s2" } }}
              />
              <Button
                variant="outlined" size="small" disabled={!draftOk}
                onClick={() => { addTool({ name: draft.name.trim(), desc: draft.desc.trim() }); setDraft({ name: "", desc: "" }); }}
                sx={{ typography: "s2", fontWeight: 700, flexShrink: 0 }}
              >
                Add tool
              </Button>
            </Stack>
          </Box>
          )}
        </Stack>
    </ConnectSteps>
  );
}

BuiltinAgentFlow.propTypes = {
  title: PropTypes.string,
  /* Set when connecting for one environment — the type is fixed, not a choice. */
  lockedType: PropTypes.string,
  initial: PropTypes.object,
  limits: PropTypes.object.isRequired,
  onLimits: PropTypes.func.isRequired,
  onSave: PropTypes.func.isRequired,
  profile: PropTypes.object.isRequired,
  onProfile: PropTypes.func.isRequired,
  observe: PropTypes.object,
  onObserve: PropTypes.func.isRequired,
  prefill: PropTypes.object,
};
