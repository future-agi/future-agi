import PropTypes from "prop-types";
import { useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import {
  Box,
  Stack,
  Typography,
  Button,
  IconButton,
  TextField,
  Tooltip,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { paths } from "src/routes/paths";
import { SectionCard } from "../components/primitives";
import TwinLogo from "../components/TwinLogo";
import { useConnectedAgents } from "../store";
import { twinById, twinsByCategory } from "../_mock/twins";
import { useCreateTwinEnv, defaultEnvName } from "../twins/useCreateTwinEnv";
import { clickable, focusRing } from "../components/clickable";
import WorldAgentPicker from "./AgentChoice";
import { buildableAgentsOf } from "./worldSource";

/**
 * Create an agent's environment whose world is a sandbox of one or more
 * third-party services (Slack, Notion, Salesforce, etc.). Like every
 * environment it belongs to one agent in Agents — picked (or connected)
 * here — and the sandbox is the world its build starts from. Sandboxes are
 * kept until the environment is deleted; nothing expires.
 *
 * This is one of three entry points to `New environment` — equal peer
 * to "Build from your agent" and "Use a template". It exists because
 * a large class of agents (support desks, RevOps copilots, PM
 * assistants) operate not in a voice or chat surface but *inside*
 * SaaS apps. The world for those agents is Slack + Notion + Gmail,
 * not a seeded generic dataset.
 *
 * How this beats every twins competitor on the market:
 *
 *   · The twin backing is a first-class property of the environment.
 *     Everything the env framework provides — scenarios, personas,
 *     actors, evals, RL contract, agent versioning, run history —
 *     applies to twin-backed envs for free. No forked story.
 *
 *   · Seed prompts, not seed JSON. The user describes the sandbox in
 *     natural language ("Slack workspace with 3 overdue support
 *     tickets in #urgent and one satisfied customer DM"); we resolve
 *     it into the shape each service expects.
 *
 *   · Twin state versions with the env. When the user pins env v2,
 *     the twin seed pinned at v2 comes with it — a run three months
 *     later replays against the same world state, guaranteed.
 *
 *   · Twin end-state as an eval kind. Post-run, evals can inspect
 *     what actually landed in Slack / Notion — not just whether the
 *     agent decided to call the SDK. (Follow-up work on the Evals
 *     tab.)
 */
export default function NewTwinEnvironment() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const preselect = searchParams.get("service");
  return (
    /* Page gutter matches the other Environments screens; the composer adds none of its own. */
    <Box sx={{ p: 2 }}>
      <Stack direction="row" alignItems="center" spacing={1.5} sx={{ mb: 2 }}>
        <Tooltip arrow title="Back to environments">
          <IconButton
            size="small"
            onClick={() => navigate(paths.dashboard.simulate.environments)}
          >
            <Iconify icon="solar:alt-arrow-left-linear" width={20} />
          </IconButton>
        </Tooltip>
        <Box flex={1}>
          <Typography sx={{ typography: "m2", fontWeight: 600 }}>
            SaaS sandbox
          </Typography>
          <Typography noWrap sx={{ typography: "s2", color: "text.subtitle" }}>
            Pick the apps your agent works in and the agent itself. Each app
            gets a sandbox, seeded to your description.
          </Typography>
        </Box>
      </Stack>
      <TwinComposer preselect={preselect} />
    </Box>
  );
}

/**
 * The composer body — app picker + the agent + seed prompt + name +
 * set-up action. Extracted so it can be embedded without the page header.
 */
export function TwinComposer({ preselect }) {
  const createTwinEnv = useCreateTwinEnv();
  const [selected, setSelected] = useState(
    preselect && twinById(preselect) ? [preselect] : [],
  );
  const [seedPrompt, setSeedPrompt] = useState("");
  const [name, setName] = useState("");
  const [q, setQ] = useState("");
  /*
    provisioning here is only used to lock the button after click —
    the actual provisioning animation runs on the destination page
    (TwinProvisioningView), not in a modal on this one.
  */
  const [provisioning, setProvisioning] = useState(false);
  /* The agent this environment belongs to — one in Agents, never a raw
     endpoint typed here. Its versions and identity come with it. */
  const { agents: all } = useConnectedAgents();
  const [agentId, setAgentId] = useState(null);
  const agent = buildableAgentsOf(all).find((a) => a.id === agentId) || null;

  const cats = useMemo(() => twinsByCategory(), []);
  const filteredCats = useMemo(() => {
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

  /*
    Provision now: mint the env with status="provisioning" and navigate
    immediately to the review page. The provisioning animation and
    step timeline run inline there — no modal on this page.
  */
  const provision = () => {
    setProvisioning(true);
    createTwinEnv(selected, { name, seedPrompt, agent });
  };

  return (
    <Box sx={{ p: 0 }}>
      {/*
        One-liner explainer. Sits above the picker as prose rather than
        a component — the surrounding page already frames this as
        "twin-backed envs", so a short reminder is enough.
      */}
      <Typography sx={{ typography: "s2", color: "text.subtitle", mb: 2 }}>
        Your agent calls the real SDKs, but the calls land in a seeded sandbox
        that resets between runs.
      </Typography>

      <Box
        sx={{
          display: "grid",
          gap: 2,
          gridTemplateColumns: { xs: "1fr", lg: "1.55fr 1fr" },
        }}
      >
        <Stack spacing={2}>
          <SectionCard
            title="1 · Pick the apps"
            subtitle={`${selected.length} selected — one sandbox per app. Pick several for scenarios that chain across apps.`}
            action={
              <TextField
                size="small"
                value={q}
                onChange={(e) => setQ(e.target.value)}
                placeholder="Search services…"
                InputProps={{
                  startAdornment: (
                    <Iconify
                      icon="solar:magnifer-linear"
                      width={14}
                      sx={{ color: "text.subtitle", mr: 0.75 }}
                    />
                  ),
                }}
                sx={{
                  width: 220,
                  "& .MuiInputBase-input": { typography: "s2" },
                }}
              />
            }
          >
            <Box sx={{ p: 2.5 }}>
              <Stack spacing={2.5}>
                {filteredCats.map((cat) => (
                  <Box key={cat.id}>
                    <Typography
                      sx={{
                        typography: "s3",
                        fontWeight: 600,
                        color: "text.subtitle",
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
                        gridTemplateColumns: { xs: "1fr", sm: "1fr 1fr" },
                      }}
                    >
                      {cat.items.map((t) => {
                        const on = selected.includes(t.id);
                        return (
                          <Box
                            key={t.id}
                            {...clickable(() => toggle(t.id), {
                              role: "checkbox",
                              checked: on,
                            })}
                            sx={{
                              ...focusRing,
                              p: 1.25,
                              borderRadius: "8px",
                              cursor: "pointer",
                              border: "1px solid",
                              borderColor: on ? "primary.main" : "divider",
                              bgcolor: on
                                ? "action.selected"
                                : "background.paper",
                            }}
                          >
                            <Stack
                              direction="row"
                              alignItems="center"
                              spacing={1}
                            >
                              <Iconify
                                icon={
                                  on
                                    ? "solar:check-circle-bold"
                                    : "solar:circle-linear"
                                }
                                width={14}
                                sx={{
                                  color: on ? "primary.main" : "text.subtitle",
                                }}
                              />
                              <TwinLogo twin={t} width={20} />
                              <Typography
                                sx={{
                                  typography: "s2_1",
                                  fontWeight: 500,
                                  flex: 1,
                                }}
                              >
                                {t.name}
                              </Typography>
                              <Typography
                                sx={{
                                  typography: "s3",
                                  color: "text.subtitle",
                                  fontWeight: 600,
                                }}
                              >
                                {t.apiLevel === "api+ui" ? "API + UI" : "API"}
                              </Typography>
                            </Stack>
                            <Typography
                              sx={{
                                typography: "s3",
                                color: "text.subtitle",
                                mt: 0.5,
                              }}
                            >
                              {t.blurb}
                            </Typography>
                          </Box>
                        );
                      })}
                    </Box>
                  </Box>
                ))}
              </Stack>
            </Box>
          </SectionCard>
        </Stack>

        {/*
          Right rail — steps 2–4 + set up, sticky so they stay in
          view as the user scrolls the picker. Compact section cards so
          all three (agent, starting state, name) plus the CTA fit
          in one viewport at typical laptop heights.
        */}
        <Box
          sx={{
            position: { lg: "sticky" },
            top: { lg: 16 },
            alignSelf: "flex-start",
          }}
        >
          <Stack spacing={2}>
            <SectionCard title="2 · Your agent">
              <Box sx={{ p: 2 }}>
                <WorldAgentPicker
                  value={agentId}
                  onChange={setAgentId}
                  title="Agent"
                  subtitle="It calls the sandboxed apps."
                />
              </Box>
            </SectionCard>

            <SectionCard
              title="3 · Describe the starting state"
              subtitle="What each run sees when it begins. Optional."
            >
              <Box sx={{ p: 2 }}>
                <TextField
                  fullWidth
                  multiline
                  minRows={3}
                  value={seedPrompt}
                  onChange={(e) => setSeedPrompt(e.target.value)}
                  placeholder={
                    selected.length
                      ? seedPromptExample(selected)
                      : "Pick services first — the placeholder tunes to what's selected."
                  }
                  sx={{ "& .MuiInputBase-input": { typography: "s2" } }}
                />
              </Box>
            </SectionCard>

            <SectionCard title="4 · Name it (optional)">
              <Box sx={{ p: 2 }}>
                <TextField
                  fullWidth
                  size="small"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder={
                    selected.length
                      ? defaultEnvName(selected)
                      : "Support desk, RevOps sandbox, …"
                  }
                  sx={{ "& .MuiInputBase-input": { typography: "s2" } }}
                />
              </Box>
            </SectionCard>

            <Box>
              <Button
                fullWidth
                variant="contained"
                color="primary"
                disabled={!canContinue || provisioning}
                onClick={provision}
                startIcon={
                  provisioning ? (
                    <Iconify
                      icon="solar:refresh-circle-linear"
                      width={16}
                      sx={{
                        animation: "spin 1.2s linear infinite",
                        "@keyframes spin": {
                          to: { transform: "rotate(360deg)" },
                        },
                      }}
                    />
                  ) : (
                    <Iconify icon="solar:play-circle-linear" width={16} />
                  )
                }
                sx={{ py: 1.25 }}
              >
                {provisioning
                  ? "Setting up…"
                  : canContinue
                    ? `Set up ${selected.length} sandbox${selected.length === 1 ? "" : "es"}`
                    : selected.length === 0
                      ? "Pick at least one app"
                      : "Pick the agent"}
              </Button>
              <Typography
                sx={{
                  typography: "s3",
                  color: "text.subtitle",
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
TwinComposer.propTypes = {
  preselect: PropTypes.string,
};

/* ── helpers ──────────────────────────────────────────────────────────────── */

function ValueRow({ icon, title, body }) {
  return (
    <Stack direction="row" spacing={1.5} sx={{ p: 2 }} alignItems="flex-start">
      <Iconify
        icon={icon}
        width={16}
        sx={{ color: "primary.main", flexShrink: 0, mt: "2px" }}
      />
      <Box>
        <Typography sx={{ typography: "s2", fontWeight: 600 }}>
          {title}
        </Typography>
        <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
          {body}
        </Typography>
      </Box>
    </Stack>
  );
}
ValueRow.propTypes = {
  icon: PropTypes.string,
  title: PropTypes.string,
  body: PropTypes.string,
};

function seedPromptExample(services) {
  const first = twinById(services[0]);
  if (!first) return "Describe the state the sandbox should start with…";
  if (first.id === "slack") {
    return "Slack workspace with #support-urgent (3 overdue tickets), #general (5 recent messages), and a DM thread with a customer waiting for a refund status.";
  }
  if (first.id === "notion") {
    return "Notion workspace with a Roadmap database (three overdue launch tasks) and a Pricing QA page shared with the team.";
  }
  if (first.id === "gmail") {
    return "Inbox with 4 unread emails: two customer refund requests, one from legal, one meeting invite. Two labels: Support, Escalated.";
  }
  return `Describe the state ${first.name} should start with — what pages, channels, records, or messages should already be there when a run begins.`;
}
