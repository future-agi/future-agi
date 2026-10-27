import PropTypes from "prop-types";
import { useMemo, useState } from "react";
import { useTheme } from "@mui/material/styles";
import {
  Box,
  Button,
  ButtonBase,
  InputBase,
  Stack,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { Field } from "../forms/formBits";
import { BTN_SX, T12, T13, focusRing } from "../drawerStyles";
import AgentPicker, { useWorldAgents } from "./AgentPicker";
import { StartCard } from "./startBits";
import {
  defaultSandboxName,
  sandboxAppsByCategory,
  seedPromptExample,
} from "./sandboxApps";
import { uiOnlyToast } from "./startUtils";

/** An app's mark, swapped for a light one where the colour mark is near black. */
export function AppMark({ app, width = 20 }) {
  const dark = useTheme().palette.mode === "dark";
  const swap = dark && app.iconDark;
  return (
    <Iconify
      icon={swap ? app.iconDark : app.icon}
      width={width}
      sx={{ flexShrink: 0, ...(swap && { color: "text.primary" }) }}
    />
  );
}
AppMark.propTypes = {
  app: PropTypes.object.isRequired,
  width: PropTypes.number,
};

function AppTile({ app, on, onToggle }) {
  return (
    <ButtonBase
      role="checkbox"
      aria-checked={on}
      aria-label={app.name}
      onClick={onToggle}
      sx={{
        display: "block",
        p: 1.25,
        textAlign: "left",
        borderRadius: "8px",
        border: "1px solid",
        borderColor: on ? "text.primary" : "divider",
        bgcolor: on ? "action.selected" : "background.paper",
        "&.Mui-focusVisible": focusRing,
      }}
    >
      <Stack direction="row" alignItems="center" spacing={1}>
        <Iconify
          icon={on ? "solar:check-circle-bold" : "solar:circle-linear"}
          width={14}
          sx={{ color: on ? "text.primary" : "text.secondary", flexShrink: 0 }}
        />
        <AppMark app={app} />
        <Typography sx={{ ...T13, fontWeight: 500, flex: 1, minWidth: 0 }}>
          {app.name}
        </Typography>
        <Typography sx={{ ...T12, color: "text.secondary", fontWeight: 600 }}>
          {app.apiLevel === "api+ui" ? "API + UI" : "API"}
        </Typography>
      </Stack>
      <Typography sx={{ ...T12, color: "text.secondary", mt: 0.5 }}>
        {app.blurb}
      </Typography>
    </ButtonBase>
  );
}
AppTile.propTypes = {
  app: PropTypes.object.isRequired,
  on: PropTypes.bool,
  onToggle: PropTypes.func.isRequired,
};

/**
 * A SaaS sandbox for one connected agent: the apps it works in, each
 * answered by a sandbox seeded to the described starting state. UI only:
 * no sandbox is set up.
 */
export default function SandboxForm() {
  const [selected, setSelected] = useState([]);
  const [seedPrompt, setSeedPrompt] = useState("");
  const [name, setName] = useState("");
  const [q, setQ] = useState("");
  const { agents, isLoading } = useWorldAgents();
  const [agentId, setAgentId] = useState(null);
  const agent = agents.find((a) => a.id === agentId) || null;

  const cats = useMemo(() => sandboxAppsByCategory(), []);
  const shown = useMemo(() => {
    const query = q.trim().toLowerCase();
    return cats
      .map((c) => ({
        ...c,
        items: c.items.filter(
          (t) =>
            !query ||
            t.name.toLowerCase().includes(query) ||
            t.blurb.toLowerCase().includes(query),
        ),
      }))
      .filter((c) => c.items.length > 0);
  }, [cats, q]);

  const toggle = (id) =>
    setSelected((prev) =>
      prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id],
    );
  const canContinue = selected.length > 0 && !!agent;
  const n = selected.length;

  return (
    <Box>
      <Typography sx={{ ...T13, color: "text.secondary", mb: 2 }}>
        Your agent calls the real SDKs, but the calls land in a seeded sandbox
        that resets between runs.
      </Typography>
      <Box
        sx={{
          display: "grid",
          gap: 2,
          gridTemplateColumns: "minmax(0, 1.55fr) minmax(0, 1fr)",
        }}
      >
        <StartCard
          title="1 · Pick the apps"
          subtitle={`${n} selected. One sandbox per app. Pick several for scenarios that chain across apps.`}
          action={
            <Stack
              direction="row"
              alignItems="center"
              spacing={0.75}
              sx={{
                px: 1,
                height: 28,
                width: 180,
                flexShrink: 0,
                border: "1px solid",
                borderColor: "divider",
                borderRadius: "6px",
              }}
            >
              <Iconify
                icon="solar:magnifer-linear"
                width={14}
                sx={{ color: "text.secondary" }}
              />
              <InputBase
                value={q}
                onChange={(e) => setQ(e.target.value)}
                placeholder="Search services…"
                inputProps={{ "aria-label": "Search services" }}
                sx={{ ...T12, flex: 1 }}
              />
            </Stack>
          }
        >
          <Stack spacing={2.5} sx={{ p: 2 }}>
            {shown.map((cat) => (
              <Box key={cat.id}>
                <Typography
                  sx={{
                    ...T12,
                    fontWeight: 600,
                    color: "text.secondary",
                    letterSpacing: 0.5,
                    mb: 1,
                  }}
                >
                  {cat.label.toUpperCase()}
                </Typography>
                <Box
                  sx={{
                    display: "grid",
                    gap: 1,
                    gridTemplateColumns: "repeat(2, minmax(0, 1fr))",
                  }}
                >
                  {cat.items.map((t) => (
                    <AppTile
                      key={t.id}
                      app={t}
                      on={selected.includes(t.id)}
                      onToggle={() => toggle(t.id)}
                    />
                  ))}
                </Box>
              </Box>
            ))}
            {!shown.length && (
              <Typography sx={{ ...T13, color: "text.secondary" }}>
                No app matches &ldquo;{q.trim()}&rdquo;.
              </Typography>
            )}
          </Stack>
        </StartCard>

        <Box sx={{ position: "sticky", top: 0, alignSelf: "flex-start" }}>
          <Stack spacing={2}>
            <StartCard title="2 · Your agent">
              <Box sx={{ p: 2 }}>
                <AgentPicker
                  agents={agents}
                  isLoading={isLoading}
                  value={agentId}
                  onChange={setAgentId}
                  subtitle="It calls the sandboxed apps."
                />
              </Box>
            </StartCard>
            <StartCard
              title="3 · Describe the starting state"
              subtitle="What each run sees when it begins. Optional."
            >
              <Box sx={{ p: 2 }}>
                <Field
                  multiline
                  minRows={3}
                  value={seedPrompt}
                  onChange={setSeedPrompt}
                  inputProps={{ "aria-label": "Starting state" }}
                  placeholder={
                    n
                      ? seedPromptExample(selected)
                      : "Pick services first. The placeholder tunes to what's selected."
                  }
                />
              </Box>
            </StartCard>
            <StartCard title="4 · Name it (optional)">
              <Box sx={{ p: 2 }}>
                <Field
                  value={name}
                  onChange={setName}
                  inputProps={{ "aria-label": "Sandbox name" }}
                  placeholder={
                    n
                      ? defaultSandboxName(selected)
                      : "Support desk, RevOps sandbox, …"
                  }
                />
              </Box>
            </StartCard>
            <Box>
              <Button
                fullWidth
                variant="contained"
                color="primary"
                disabled={!canContinue}
                onClick={() => uiOnlyToast("setting up a SaaS sandbox")}
                startIcon={
                  <Iconify icon="solar:play-circle-linear" width={14} />
                }
                sx={{ ...BTN_SX, height: 36, ...T13, fontWeight: 500 }}
              >
                {canContinue
                  ? `Set up ${n} sandbox${n === 1 ? "" : "es"}`
                  : n === 0
                    ? "Pick at least one app"
                    : "Pick the agent"}
              </Button>
              <Typography
                sx={{
                  ...T12,
                  color: "text.secondary",
                  mt: 1,
                  textAlign: "center",
                }}
              >
                Takes about 30 seconds.
              </Typography>
            </Box>
          </Stack>
        </Box>
      </Box>
    </Box>
  );
}
