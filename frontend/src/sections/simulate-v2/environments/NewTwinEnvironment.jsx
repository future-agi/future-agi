import PropTypes from "prop-types";
import { useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { alpha } from "@mui/material/styles";
import {
  Box, Stack, Typography, Button, IconButton, TextField, Tooltip,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { paths } from "src/routes/paths";
import { SectionCard } from "../components/primitives";
import TwinLogo from "../components/TwinLogo";
import { twinById, twinsByCategory } from "../_mock/twins";
import { useCreateTwinEnv, defaultEnvName } from "../twins/useCreateTwinEnv";

/**
 * Create an environment whose world is a live sandbox of one or more
 * third-party services (Slack, Notion, Salesforce, etc.).
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
    <Box sx={{ p: 3 }}>
      <Stack direction="row" alignItems="center" spacing={1.5} sx={{ mb: 2 }}>
        <Tooltip arrow title="Back to environments">
          <IconButton size="small" onClick={() => navigate(paths.dashboard.simulate.environments)}>
            <Iconify icon="solar:alt-arrow-left-linear" width={18} />
          </IconButton>
        </Tooltip>
        <Box flex={1}>
          <Typography sx={{ typography: "m2", fontWeight: 600 }}>
            Compose a multi-clone environment
          </Typography>
          <Typography noWrap sx={{ typography: "s2", color: "text.subtitle" }}>
            Pick the services your agent operates across. We provision a live sandbox for each, seeded to your prompt.
          </Typography>
        </Box>
      </Stack>
      <TwinComposer preselect={preselect} />
    </Box>
  );
}

/**
 * The composer body — twin picker + seed prompt + lifetime + name +
 * provision action. Extracted so the Environments → Templates → Twins
 * tab can embed it inline. When embedded there is no back-arrow header;
 * the gallery tabs provide navigation context.
 */
export function TwinComposer({ preselect, embedded = false }) {
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
  /*
    Lifetime: "persistent" keeps the env until the user deletes it;
    a number of minutes (10, 60, 240) makes it short-lived — the env
    self-expires and the workspace shows a countdown. Short-lived is
    the primary Arga model because it guarantees clean-slate
    reproducibility, but Future AGI's evals-first users typically
    want persistent so they can compare runs over time — that's why
    persistent is the default here.
  */
  const [ttl, setTtl] = useState("persistent");
  /*
    Agent-connection fields — SDK endpoint is required to hand off to
    the review layout; auth token is optional (many local/preview
    agents run without one). Kept here rather than on a separate page
    so the whole compose flow is one form.
  */
  const [sdkEndpoint, setSdkEndpoint] = useState("");
  const [authToken, setAuthToken] = useState("");
  const [showAuth, setShowAuth] = useState(false);

  const cats = useMemo(() => twinsByCategory(), []);
  const filteredCats = useMemo(() => {
    const query = q.trim().toLowerCase();
    return cats.map((c) => ({
      ...c,
      items: c.items.filter((t) => !query || t.name.toLowerCase().includes(query) || t.blurb.toLowerCase().includes(query)),
    })).filter((c) => c.items.length > 0);
  }, [cats, q]);

  const toggle = (id) => setSelected((prev) => prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]);

  const canContinue = selected.length > 0 && !!sdkEndpoint.trim();

  /*
    Provision now: mint the env with status="provisioning" and navigate
    immediately to the review page. The provisioning animation and
    step timeline run inline there — no modal on this page.
  */
  const provision = () => {
    setProvisioning(true);
    createTwinEnv(selected, {
      name,
      seedPrompt,
      ttlMinutes: ttl === "persistent" ? null : Number(ttl),
      agent: {
        sdkEndpoint: sdkEndpoint.trim(),
        authToken: authToken.trim() || null,
      },
    });
  };

  return (
    <Box sx={{ p: embedded ? 0 : 3 }}>
      {/*
        One-liner explainer. Sits above the picker as prose rather than
        a component — the surrounding page already frames this as
        "twin-backed envs", so a short reminder is enough.
      */}
      <Typography sx={{ typography: "s2", color: "text.subtitle", mb: 2 }}>
        Your agent calls the real SDKs — Slack, Notion, Salesforce — but the calls land in a sandbox we own, seeded to your prompt and torn down between runs. Evals inspect what actually landed, not just which tools were called.
      </Typography>

      <Box sx={{ display: "grid", gap: 2, gridTemplateColumns: { xs: "1fr", lg: "1.55fr 1fr" } }}>
        <Stack spacing={2}>
          <SectionCard
            title="1 · Pick the clones"
            subtitle={`${selected.length} selected — one live sandbox per service. Multi-select for scenarios that chain across services.`}
            action={
              <TextField
                size="small" value={q} onChange={(e) => setQ(e.target.value)}
                placeholder="Search services…"
                InputProps={{ startAdornment: <Iconify icon="solar:magnifer-linear" width={13} sx={{ color: "text.subtitle", mr: 0.75 }} /> }}
                sx={{ width: 220, "& .MuiInputBase-input": { typography: "s2" } }}
              />
            }
          >
            <Box sx={{ p: 2.5 }}>
              <Stack spacing={2.5}>
                {filteredCats.map((cat) => (
                  <Box key={cat.id}>
                    <Typography sx={{ typography: "s3", fontWeight: 700, color: "text.subtitle", letterSpacing: 0.5, mb: 1 }}>
                      {cat.label.toUpperCase()}
                    </Typography>
                    <Box sx={{ display: "grid", gap: 1, gridTemplateColumns: { xs: "1fr", sm: "1fr 1fr" } }}>
                      {cat.items.map((t) => {
                        const on = selected.includes(t.id);
                        return (
                          <Box key={t.id} onClick={() => toggle(t.id)}
                            sx={{
                              p: 1.25, borderRadius: 1.25, cursor: "pointer",
                              border: "1px solid",
                              borderColor: (th) => on
                                ? (th.palette.mode === "dark" ? alpha(th.palette.text.primary, 0.35) : th.palette.primary.main)
                                : th.palette.divider,
                              bgcolor: (th) => on
                                ? (th.palette.mode === "dark" ? alpha(th.palette.text.primary, 0.06) : alpha(th.palette.primary.main, 0.05))
                                : "background.paper",
                            }}>
                            <Stack direction="row" alignItems="center" spacing={1}>
                              <Iconify icon={on ? "solar:check-circle-bold" : "solar:circle-linear"} width={14}
                                sx={{ color: on ? "primary.main" : "text.subtitle" }} />
                              <TwinLogo twin={t} width={20} />
                              <Typography sx={{ typography: "s2", fontWeight: 600, flex: 1 }}>{t.name}</Typography>
                              <Typography sx={{ typography: "s3", color: "text.subtitle", fontWeight: 700 }}>
                                {t.apiLevel === "api+ui" ? "API + UI" : "API"}
                              </Typography>
                            </Stack>
                            <Typography sx={{ typography: "s3", color: "text.subtitle", mt: 0.5 }}>{t.blurb}</Typography>
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
          Right rail — steps 2–4 + provision, sticky so they stay in
          view as the user scrolls the picker. Compact section cards so
          all three (starting state, lifetime, name) plus the CTA fit
          in one viewport at typical laptop heights.
        */}
        <Box sx={{ position: { lg: "sticky" }, top: { lg: 16 }, alignSelf: "flex-start" }}>
          <Stack spacing={2}>
            <SectionCard
              title="2 · Connect your agent"
              subtitle="Where the sandbox should call your agent."
            >
              <Stack spacing={1.5} sx={{ p: 2 }}>
                <Box>
                  <Typography sx={{ typography: "s3", fontWeight: 600, mb: 0.5 }}>
                    Agent SDK endpoint <Box component="span" sx={{ color: "error.main" }}>*</Box>
                  </Typography>
                  <TextField
                    fullWidth size="small"
                    value={sdkEndpoint}
                    onChange={(e) => setSdkEndpoint(e.target.value)}
                    placeholder="https://api.yourapp.com/agent/step"
                    sx={{ "& .MuiInputBase-input": { typography: "s2", fontFamily: "ui-monospace, Menlo, monospace" } }}
                  />
                </Box>
                <Box>
                  <Button
                    size="small"
                    onClick={() => setShowAuth((o) => !o)}
                    startIcon={<Iconify icon={showAuth ? "solar:alt-arrow-up-linear" : "solar:alt-arrow-down-linear"} width={12} />}
                    sx={{ typography: "s3", fontWeight: 600, color: "text.secondary", px: 0.5 }}
                  >
                    {showAuth ? "Hide auth token" : "Auth token (optional)"}
                  </Button>
                  {showAuth && (
                    <TextField
                      fullWidth size="small" type="password"
                      value={authToken}
                      onChange={(e) => setAuthToken(e.target.value)}
                      placeholder="Bearer …"
                      sx={{ mt: 1, "& .MuiInputBase-input": { typography: "s2", fontFamily: "ui-monospace, Menlo, monospace" } }}
                      helperText="Only if your endpoint requires an Authorization header."
                      FormHelperTextProps={{ sx: { typography: "s3", mx: 0 } }}
                    />
                  )}
                </Box>
              </Stack>
            </SectionCard>

            <SectionCard
              title="3 · Describe the starting state"
              subtitle="What each run sees when it begins. Optional."
            >
              <Box sx={{ p: 2 }}>
                <TextField
                  fullWidth multiline minRows={3}
                  value={seedPrompt}
                  onChange={(e) => setSeedPrompt(e.target.value)}
                  placeholder={selected.length
                    ? seedPromptExample(selected)
                    : "Pick services first — the placeholder tunes to what's selected."}
                  sx={{ "& .MuiInputBase-input": { typography: "s2" } }}
                />
              </Box>
            </SectionCard>

            <SectionCard
              title="4 · Lifetime"
              subtitle="How long the sandbox stays alive. Short-lived envs auto-expire."
            >
              <Box sx={{ p: 2 }}>
                <Stack spacing={0.5}>
                  {TTL_OPTIONS.map((opt) => (
                    <TtlChip
                      key={opt.value}
                      label={opt.label}
                      sub={opt.sub}
                      icon={opt.icon}
                      on={ttl === opt.value}
                      onClick={() => setTtl(opt.value)}
                    />
                  ))}
                </Stack>
              </Box>
            </SectionCard>

            <SectionCard title="5 · Name it (optional)">
              <Box sx={{ p: 2 }}>
                <TextField
                  fullWidth size="small"
                  value={name} onChange={(e) => setName(e.target.value)}
                  placeholder={selected.length ? defaultEnvName(selected) : "Support desk, RevOps sandbox, …"}
                  sx={{ "& .MuiInputBase-input": { typography: "s2" } }}
                />
              </Box>
            </SectionCard>

            <Box>
              <Button
                fullWidth variant="contained" color="primary"
                disabled={!canContinue || provisioning}
                onClick={provision}
                startIcon={
                  provisioning
                    ? <Iconify icon="solar:refresh-circle-linear" width={16} sx={{ animation: "spin 1.2s linear infinite", "@keyframes spin": { to: { transform: "rotate(360deg)" } } }} />
                    : <Iconify icon="solar:play-circle-linear" width={16} />
                }
                sx={{ typography: "s1", fontWeight: 700, py: 1.25 }}
              >
                {provisioning
                  ? "Provisioning sandbox…"
                  : canContinue
                    ? `Provision ${selected.length} clone${selected.length === 1 ? "" : "s"}`
                    : selected.length === 0
                      ? "Pick at least one clone"
                      : "Add your agent endpoint"}
              </Button>
              <Typography sx={{ typography: "s3", color: "text.subtitle", mt: 1, textAlign: "center" }}>
                Takes ~30s in production. Prototype resolves quickly.
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
  embedded: PropTypes.bool,
};

/* ── helpers ──────────────────────────────────────────────────────────────── */

function ValueRow({ icon, title, body }) {
  return (
    <Stack direction="row" spacing={1.5} sx={{ p: 2 }} alignItems="flex-start">
      <Iconify icon={icon} width={15} sx={{ color: "primary.main", flexShrink: 0, mt: "2px" }} />
      <Box>
        <Typography sx={{ typography: "s2", fontWeight: 700 }}>{title}</Typography>
        <Typography sx={{ typography: "s3", color: "text.subtitle" }}>{body}</Typography>
      </Box>
    </Stack>
  );
}
ValueRow.propTypes = {
  icon: PropTypes.string,
  title: PropTypes.string,
  body: PropTypes.string,
};

/*
  TTL choices. Persistent is first because it matches how Future AGI
  users track env-run history over time; the ephemeral tiers below
  cover Arga-style one-off testing.
*/
const TTL_OPTIONS = [
  { value: "persistent", label: "Persistent", sub: "Keeps state until you delete", icon: "solar:bookmark-linear" },
  { value: "10", label: "10 minutes", sub: "Quick throwaway test", icon: "solar:clock-circle-linear" },
  { value: "60", label: "1 hour", sub: "One session", icon: "solar:clock-circle-linear" },
  { value: "240", label: "4 hours", sub: "A working session", icon: "solar:clock-circle-linear" },
];

function TtlChip({ label, sub, icon, on, onClick }) {
  return (
    <Stack direction="row" alignItems="center" spacing={1}
      onClick={onClick}
      sx={{
        px: 1.25, py: 1, borderRadius: 1, cursor: "pointer",
        border: "1px solid",
        borderColor: (th) => on
          ? (th.palette.mode === "dark" ? alpha(th.palette.text.primary, 0.4) : th.palette.primary.main)
          : th.palette.divider,
        bgcolor: (th) => on
          ? (th.palette.mode === "dark" ? alpha(th.palette.text.primary, 0.06) : alpha(th.palette.primary.main, 0.05))
          : "background.paper",
        "&:hover": { borderColor: (th) => on ? undefined : th.palette.text.disabled },
        transition: "border-color .12s ease",
      }}>
      <Iconify icon={on ? "solar:check-circle-bold" : icon} width={14}
        sx={{ color: on ? "primary.main" : "text.subtitle", flexShrink: 0 }} />
      <Typography sx={{ typography: "s2", fontWeight: 600, flexShrink: 0 }}>{label}</Typography>
      <Typography noWrap sx={{ typography: "s3", color: "text.subtitle", flex: 1, minWidth: 0 }}>
        {sub}
      </Typography>
    </Stack>
  );
}
TtlChip.propTypes = {
  label: PropTypes.string, sub: PropTypes.string, icon: PropTypes.string,
  on: PropTypes.bool, onClick: PropTypes.func,
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
