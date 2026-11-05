import PropTypes from "prop-types";
import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { paths } from "src/routes/paths";
import {
  Box,
  Button,
  IconButton,
  MenuItem,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import PageHeader from "src/components/page-header";
import { ConfirmDialog } from "src/components/custom-dialog";
import SideDrawer from "../components/SideDrawer";
import { useConnectedAgents, useSimStore } from "../store";
import { skillRuntimeOf } from "../_mock/skills";
import { connectedAgentPath } from "../improvements/improvementNav";
import {
  agentContextOf,
  attach,
  createSkill,
  detach,
  latestV,
  libraryOf,
  promote,
  recordTest,
  refLabel,
  relevantScenariosOf,
  resolveRef,
  testLineOf,
  usedByOf,
} from "../_mock/skillLibrary";

const head = {
  typography: "s3",
  color: "text.tertiary",
  fontWeight: 500,
  py: 1,
  px: 1.5,
  textAlign: "left",
};
const cell = {
  typography: "s2",
  py: 1,
  px: 1.5,
  borderTop: "1px solid",
  borderColor: "divider",
};
const ago = (iso) => {
  const d = Math.round((Date.now() - new Date(iso)) / 864e5);
  return d < 1 ? "today" : d < 45 ? `${d}d ago` : `${Math.round(d / 30)}mo ago`;
};
const LOADED = { observed: "Observed", unknown: "Unknown" };

/**
 * Build → Skills (clarified plan, skills.md): reusable skills in one
 * versioned place. Create or import one, test a version against an agent's
 * environment (did it load: Observed or Unknown), promote a version to
 * production — with a reason when it's untested — and attach it to agents.
 */
export default function SkillsLibrary() {
  const { state, dispatch } = useSimStore();
  const { agents } = useConnectedAgents();
  const settings = state.workspaceSettings;
  const library = useMemo(() => libraryOf(settings), [settings]);
  const [openId, setOpenId] = useState(null);
  const [creating, setCreating] = useState(false);
  const [query, setQuery] = useState("");
  const save = (patch) =>
    Object.entries(patch).forEach(([key, value]) => {
      if (key === "skill") return;
      dispatch({ type: "setWorkspaceSetting", key, value });
    });
  const nameOf = (key) => agents.find((a) => a.key === key)?.name || "An agent";
  const shown = library.filter((s) =>
    `${s.name} ${s.description} ${s.owner}`
      .toLowerCase()
      .includes(query.trim().toLowerCase()),
  );
  const open = library.find((s) => s.id === openId) || null;
  /* Always-on instructions of each agent that has code (skills.md, Agent context). */
  const [allContexts, setAllContexts] = useState(false);
  const contexts = useMemo(
    () =>
      agents
        .map((a) => agentContextOf(a, settings))
        .filter(Boolean)
        .filter((c) =>
          `${c.agentName} ${c.file} ${c.skills.join(" ")}`
            .toLowerCase()
            .includes(query.trim().toLowerCase()),
        ),
    [agents, settings, query],
  );
  const navigate = useNavigate();

  return (
    <Box
      sx={{
        p: 2,
        display: "flex",
        flexDirection: "column",
        gap: 2,
        height: "100%",
        minHeight: 0,
      }}
    >
      <PageHeader
        sx={{ mb: 0 }}
        title="Skills"
        description="Keep reusable skills in one place, test a version on your agents, then promote it."
        actions={
          <Button
            size="small"
            variant="contained"
            color="primary"
            startIcon={<Iconify icon="solar:add-circle-linear" width={16} />}
            onClick={() => setCreating(true)}
          >
            New skill
          </Button>
        }
      />
      <TextField
        size="small"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        placeholder="Search skills"
        sx={{ width: 300 }}
      />
      <Box
        sx={{
          border: "1px solid",
          borderColor: "divider",
          borderRadius: "8px",
          overflow: "auto",
        }}
      >
        <Box component="table" sx={{ width: 1, borderCollapse: "collapse" }}>
          <Box component="thead">
            <Box component="tr">
              {[
                "Skill",
                "Latest",
                "Production",
                "Last test",
                "Used by",
                "Updated",
              ].map((h) => (
                <Box component="th" key={h} sx={head}>
                  {h}
                </Box>
              ))}
            </Box>
          </Box>
          <Box component="tbody">
            {shown.map((s) => {
              const v = latestV(s);
              const test = s.tests?.[v];
              const used = usedByOf(s.id, settings);
              return (
                <Box
                  component="tr"
                  key={s.id}
                  onClick={() => setOpenId(s.id)}
                  sx={{
                    cursor: "pointer",
                    "&:hover": { bgcolor: "action.hover" },
                  }}
                >
                  <Box component="td" sx={{ ...cell, maxWidth: 420 }}>
                    <Typography sx={{ typography: "s2", fontWeight: 500 }}>
                      {s.name}
                    </Typography>
                    <Typography
                      noWrap
                      sx={{ typography: "s3", color: "text.secondary" }}
                    >
                      {s.description}
                    </Typography>
                  </Box>
                  <Box component="td" sx={cell}>{`v${v}`}</Box>
                  <Box
                    component="td"
                    sx={{
                      ...cell,
                      color: s.tags?.production
                        ? "text.primary"
                        : "text.tertiary",
                    }}
                  >
                    {s.tags?.production ? `v${s.tags.production}` : "None yet"}
                  </Box>
                  <Box
                    component="td"
                    sx={{
                      ...cell,
                      color: test ? "text.primary" : "text.tertiary",
                    }}
                  >
                    {test
                      ? `v${v} · loaded: ${LOADED[test.loaded]}`
                      : `v${v} not tested`}
                  </Box>
                  <Box
                    component="td"
                    sx={{
                      ...cell,
                      color: used.length ? "text.primary" : "text.tertiary",
                    }}
                  >
                    {used.length
                      ? `${used.length} agent${used.length === 1 ? "" : "s"}`
                      : "No agents"}
                  </Box>
                  <Box component="td" sx={{ ...cell, color: "text.secondary" }}>
                    {ago(s.versions[s.versions.length - 1].at)}
                  </Box>
                </Box>
              );
            })}
          </Box>
        </Box>
      </Box>

      {contexts.length > 0 && (
        <Box>
          <Typography sx={{ typography: "s2", fontWeight: 600, mb: 0.5 }}>
            Agent context
          </Typography>
          <Typography sx={{ typography: "s3", color: "text.secondary", mb: 1 }}>
            The always-on instructions each agent starts with.
          </Typography>
          <Box
            sx={{
              border: "1px solid",
              borderColor: "divider",
              borderRadius: "8px",
              overflow: "auto",
            }}
          >
            <Box
              component="table"
              sx={{ width: 1, borderCollapse: "collapse" }}
            >
              <Box component="thead">
                <Box component="tr">
                  {[
                    "Agent",
                    "File",
                    "Its own skills",
                    "Where it lives",
                    "",
                  ].map((h) => (
                    <Box component="th" key={h || "open"} sx={head}>
                      {h}
                    </Box>
                  ))}
                </Box>
              </Box>
              <Box component="tbody">
                {(allContexts ? contexts : contexts.slice(0, 5)).map((c) => {
                  const inRepo = c.location === "repo";
                  const openSource = () =>
                    navigate(connectedAgentPath(c.agentId, "definition"));
                  return (
                    <Box
                      component="tr"
                      key={c.agentKey}
                      onClick={inRepo ? openSource : undefined}
                      sx={
                        inRepo
                          ? {
                              cursor: "pointer",
                              "&:hover": { bgcolor: "action.hover" },
                            }
                          : undefined
                      }
                    >
                      <Box
                        component="td"
                        sx={{ ...cell, fontWeight: 500, maxWidth: 260 }}
                      >
                        <Typography
                          noWrap
                          sx={{ typography: "s2", fontWeight: 500 }}
                        >
                          {c.agentName}
                        </Typography>
                      </Box>
                      <Box
                        component="td"
                        sx={{
                          ...cell,
                          fontFamily: "ui-monospace, Menlo, monospace",
                        }}
                      >
                        {c.file}
                      </Box>
                      <Box
                        component="td"
                        sx={{
                          ...cell,
                          maxWidth: 280,
                          color: c.skills.length
                            ? "text.primary"
                            : "text.tertiary",
                        }}
                      >
                        <Typography
                          noWrap
                          sx={{ typography: "s2", color: "inherit" }}
                        >
                          {c.skills.length
                            ? c.skills.join(", ")
                            : "None linked"}
                        </Typography>
                      </Box>
                      <Box
                        component="td"
                        sx={{ ...cell, color: "text.secondary" }}
                      >
                        {inRepo
                          ? "In the repo · read-only here"
                          : `In the library · v${c.v}`}
                      </Box>
                      <Box component="td" sx={{ ...cell, textAlign: "right" }}>
                        {inRepo && (
                          <Button
                            size="small"
                            variant="outlined"
                            onClick={(e) => {
                              e.stopPropagation();
                              openSource();
                            }}
                            sx={{
                              color: "text.primary",
                              borderColor: "divider",
                            }}
                          >
                            Open source
                          </Button>
                        )}
                      </Box>
                    </Box>
                  );
                })}
              </Box>
            </Box>
          </Box>
          {contexts.length > 5 && (
            <Button
              variant="outlined"
              size="small"
              onClick={() => setAllContexts((v) => !v)}
              sx={{ mt: 1 }}
            >
              {allContexts ? "Show fewer" : `Show all ${contexts.length}`}
            </Button>
          )}
        </Box>
      )}

      <SideDrawer open={!!open} onClose={() => setOpenId(null)} width={640}>
        {open && (
          <SkillDetail
            key={open.id}
            skill={open}
            settings={settings}
            agents={agents}
            nameOf={nameOf}
            envs={state.myEnvironments || []}
            byEnv={state.byEnv || {}}
            onSave={save}
            onClose={() => setOpenId(null)}
          />
        )}
      </SideDrawer>

      {creating && (
        <CreateSkillDialog
          onClose={() => setCreating(false)}
          onCreate={(fields) => {
            const out = createSkill(settings, fields);
            save(out);
            setCreating(false);
            setOpenId(out.skill.id);
          }}
        />
      )}
    </Box>
  );
}

function SkillDetail({
  skill,
  settings,
  agents,
  nameOf,
  envs,
  byEnv,
  onSave,
  onClose,
}) {
  const navigate = useNavigate();
  const [setup, setSetup] = useState(null);
  const [promoting, setPromoting] = useState(null);
  const [reason, setReason] = useState("");
  const [testing, setTesting] = useState(null);
  const [attachKey, setAttachKey] = useState("");
  const [attachRef, setAttachRef] = useState(() =>
    skill.tags?.production ? "production" : latestV(skill),
  );
  const used = usedByOf(skill.id, settings);
  /* Agents a code skill can be attached to; the rest say why not. */
  const attachable = agents.filter(
    (a) => !used.some((u) => u.agentKey === a.key),
  );
  const eligible = attachable.filter((a) => skillRuntimeOf(a).canAdd);
  useEffect(() => {
    if (!testing) return undefined;
    const t = setTimeout(() => {
      const agent = agents.find((a) => a.key === testing.agentKey);
      onSave(
        recordTest(settings, skill.id, testing.v, {
          ...testing,
          observed: !!skillRuntimeOf(agent).observed,
        }),
      );
      setTesting(null);
    }, 1800);
    return () => clearTimeout(t);
  }, [testing]); // eslint-disable-line react-hooks/exhaustive-deps
  const tester = used[0]?.agentKey || null;
  const versions = [...skill.versions].reverse();

  return (
    <Stack sx={{ height: "100%", minHeight: 0 }}>
      <Stack
        direction="row"
        alignItems="center"
        spacing={1}
        sx={{
          px: 2.5,
          py: 2,
          borderBottom: "1px solid",
          borderColor: "divider",
        }}
      >
        <Box sx={{ flex: 1, minWidth: 0 }}>
          <Typography sx={{ typography: "s1", fontWeight: 600 }}>
            {skill.name}
          </Typography>
          <Typography noWrap sx={{ typography: "s3", color: "text.tertiary" }}>
            {`${skill.owner} · ${skill.description}`}
          </Typography>
        </Box>
        <IconButton size="small" onClick={onClose} aria-label="Close">
          <Iconify icon="solar:close-linear" width={16} />
        </IconButton>
      </Stack>
      <Box sx={{ flex: 1, overflow: "auto", px: 2.5, py: 2 }}>
        <Typography sx={{ typography: "s2", fontWeight: 600, mb: 0.75 }}>
          Versions
        </Typography>
        <Box
          sx={{
            border: "1px solid",
            borderColor: "divider",
            borderRadius: "8px",
            mb: 3,
          }}
        >
          <Box component="table" sx={{ width: 1, borderCollapse: "collapse" }}>
            <Box component="thead">
              <Box component="tr">
                {["Version", "Change", "Test", "Tag", ""].map((h) => (
                  <Box component="th" key={h} sx={head}>
                    {h}
                  </Box>
                ))}
              </Box>
            </Box>
            <Box component="tbody">
              {versions.map((ver) => {
                const t = skill.tests?.[ver.v];
                const tags = Object.entries(skill.tags || {})
                  .filter(([, v]) => v === ver.v)
                  .map(([k]) => k);
                const busy = testing?.v === ver.v;
                return (
                  <Box component="tr" key={ver.v}>
                    <Box component="td" sx={cell}>{`v${ver.v}`}</Box>
                    <Box
                      component="td"
                      sx={{ ...cell, color: "text.secondary" }}
                    >
                      {`${ver.note} · ${ver.by} · ${ago(ver.at)}`}
                    </Box>
                    <Box
                      component="td"
                      sx={{
                        ...cell,
                        color: t ? "text.primary" : "text.tertiary",
                      }}
                    >
                      {busy
                        ? "Testing…"
                        : t
                          ? `Loaded: ${LOADED[t.loaded]} · ${testLineOf(t)}`
                          : "Not tested"}
                    </Box>
                    <Box
                      component="td"
                      sx={{ ...cell, color: "text.secondary" }}
                    >
                      {tags.join(", ") || "—"}
                    </Box>
                    <Box
                      component="td"
                      sx={{ ...cell, textAlign: "right", whiteSpace: "nowrap" }}
                    >
                      <Button
                        variant="outlined"
                        size="small"
                        disabled={!tester || !!testing}
                        onClick={() =>
                          setSetup({ v: ver.v, agentKey: tester, envId: "" })
                        }
                      >
                        Test
                      </Button>
                      {!tags.includes("production") && (
                        <Button
                          variant="outlined"
                          size="small"
                          onClick={() => {
                            setReason("");
                            setPromoting(ver.v);
                          }}
                          sx={{ color: "text.primary" }}
                        >
                          Promote
                        </Button>
                      )}
                    </Box>
                  </Box>
                );
              })}
            </Box>
          </Box>
        </Box>
        <Typography
          sx={{ typography: "s3", color: "text.tertiary", mt: -2.25, mb: 3 }}
        >
          {tester
            ? "A test runs an attached agent and compares the version with the one it loads now. Loaded shows Observed when the agent reports each skill it loads, Unknown when it doesn't."
            : "Attach it to an agent to test a version in that agent's environment."}
        </Typography>

        <Typography sx={{ typography: "s2", fontWeight: 600, mb: 0.75 }}>
          Used by
        </Typography>
        <Box
          sx={{
            border: "1px solid",
            borderColor: "divider",
            borderRadius: "8px",
          }}
        >
          {used.length === 0 && (
            <Typography
              sx={{ typography: "s3", color: "text.tertiary", px: 1.5, py: 1 }}
            >
              Not attached to any agent yet.
            </Typography>
          )}
          {used.map((u) => (
            <Stack
              key={u.agentKey}
              direction="row"
              alignItems="center"
              spacing={1}
              sx={{
                px: 1.5,
                py: 1,
                borderBottom: "1px solid",
                borderColor: "divider",
              }}
            >
              <Typography sx={{ typography: "s2", flex: 1 }}>
                {nameOf(u.agentKey)}
              </Typography>
              <Typography sx={{ typography: "s3", color: "text.secondary" }}>
                {refLabel(skill, u.ref)}
              </Typography>
              <Button
                variant="outlined"
                size="small"
                onClick={() => onSave(detach(settings, u.agentKey, skill.id))}
              >
                Detach
              </Button>
            </Stack>
          ))}
          <Stack
            direction="row"
            alignItems="center"
            spacing={1}
            sx={{ px: 1.5, py: 1 }}
          >
            <TextField
              select
              size="small"
              value={attachKey}
              onChange={(e) => setAttachKey(e.target.value)}
              sx={{ minWidth: 220 }}
              SelectProps={{ displayEmpty: true }}
            >
              <MenuItem value="" disabled>
                {eligible.length
                  ? "Attach to an agent…"
                  : "No other agent can load skills"}
              </MenuItem>
              {attachable.map((a) => {
                const rt = skillRuntimeOf(a);
                return (
                  <MenuItem key={a.key} value={a.key} disabled={!rt.canAdd}>
                    {rt.canAdd
                      ? a.name
                      : `${a.name} · Unsupported — ${rt.short || "can't load skills"}`}
                  </MenuItem>
                );
              })}
            </TextField>
            <TextField
              select
              size="small"
              value={attachRef}
              onChange={(e) => setAttachRef(e.target.value)}
              sx={{ minWidth: 150 }}
            >
              {Object.keys(skill.tags || {}).map((k) => (
                <MenuItem
                  key={k}
                  value={k}
                >{`${k} (v${skill.tags[k]})`}</MenuItem>
              ))}
              {skill.versions.map((ver) => (
                <MenuItem key={ver.v} value={ver.v}>{`v${ver.v}`}</MenuItem>
              ))}
            </TextField>
            <Button
              size="small"
              variant="outlined"
              disabled={!attachKey}
              onClick={() => {
                onSave(attach(settings, attachKey, skill.id, attachRef));
                setAttachKey("");
              }}
              sx={{ color: "text.primary", borderColor: "divider" }}
            >
              Attach
            </Button>
          </Stack>
        </Box>
        <Typography sx={{ typography: "s3", color: "text.tertiary", mt: 0.75 }}>
          Attached to a tag, an agent gets whichever version that tag points to.
          Runs record the exact version it used.
        </Typography>
      </Box>

      {setup && (
        <SkillTestDialog
          skill={skill}
          setup={setup}
          used={used}
          agents={agents}
          nameOf={nameOf}
          envs={envs}
          byEnv={byEnv}
          onChange={setSetup}
          onClose={() => setSetup(null)}
          onAddScenarios={(envId) =>
            navigate(
              `${paths.dashboard.simulate.environmentStep(envId, "scenarios")}?add=1`,
            )
          }
          onStart={(run) => {
            setTesting(run);
            setSetup(null);
          }}
        />
      )}
      {promoting != null && (
        <ConfirmDialog
          open
          onClose={() => setPromoting(null)}
          PaperProps={{ sx: { maxWidth: 480 } }}
          title={`Promote v${promoting} to production?`}
          content={
            <Stack spacing={1}>
              <Typography sx={{ typography: "s2", color: "text.secondary" }}>
                {skill.tests?.[promoting]
                  ? "Agents attached to production get this version on their next run."
                  : "This version hasn't been tested. Say why it's going out anyway — the reason is kept with the promotion."}
              </Typography>
              {!skill.tests?.[promoting] && (
                <TextField
                  size="small"
                  fullWidth
                  multiline
                  minRows={2}
                  value={reason}
                  onChange={(e) => setReason(e.target.value)}
                  placeholder="Why it ships untested"
                />
              )}
            </Stack>
          }
          action={
            <Button
              size="small"
              variant="contained"
              color="primary"
              disabled={!skill.tests?.[promoting] && !reason.trim()}
              onClick={() => {
                onSave(
                  promote(
                    settings,
                    skill.id,
                    promoting,
                    "production",
                    reason.trim(),
                  ),
                );
                setPromoting(null);
              }}
            >
              Promote
            </Button>
          }
        />
      )}
    </Stack>
  );
}
SkillDetail.propTypes = {
  skill: PropTypes.object.isRequired,
  settings: PropTypes.object,
  agents: PropTypes.array.isRequired,
  nameOf: PropTypes.func.isRequired,
  envs: PropTypes.array,
  byEnv: PropTypes.object,
  onSave: PropTypes.func.isRequired,
  onClose: PropTypes.func.isRequired,
};

/*
  Skill → Test in environment (skills.md): pick an attached agent and one of
  its environments; the version is compared with the one the agent loads now
  on the same scenarios. No scenario about the skill → add some first.
*/
function SkillTestDialog({
  skill,
  setup,
  used,
  agents,
  nameOf,
  envs,
  byEnv,
  onChange,
  onClose,
  onAddScenarios,
  onStart,
}) {
  const agent = agents.find((a) => a.key === setup.agentKey);
  const agentEnvs = (agent?.usedIn || [])
    .map((u) => envs.find((e) => e.id === u.envId))
    .filter((e, i, arr) => e && arr.indexOf(e) === i);
  const envId = setup.envId || agentEnvs[0]?.id || "";
  const env = agentEnvs.find((e) => e.id === envId);
  const relevant = relevantScenariosOf(skill, byEnv[envId]?.scenarios || []);
  const ref = used.find((u) => u.agentKey === setup.agentKey)?.ref;
  /* Testing the version the agent already loads compares with running
     without the skill (0) — comparing a version with itself proves nothing. */
  const current = ref != null ? resolveRef(skill, ref) : null;
  const against = current != null && current !== setup.v ? current : 0;
  return (
    <ConfirmDialog
      open
      onClose={onClose}
      PaperProps={{ sx: { maxWidth: 520 } }}
      title={`Test v${setup.v} in an environment`}
      content={
        <Stack spacing={1.5} sx={{ pt: 0.5 }}>
          <TextField
            select
            size="small"
            label="Agent"
            value={setup.agentKey}
            onChange={(e) =>
              onChange({ ...setup, agentKey: e.target.value, envId: "" })
            }
          >
            {used.map((u) => (
              <MenuItem key={u.agentKey} value={u.agentKey}>
                {nameOf(u.agentKey)}
              </MenuItem>
            ))}
          </TextField>
          <TextField
            select
            size="small"
            label="Environment"
            value={envId}
            disabled={!agentEnvs.length}
            onChange={(e) => onChange({ ...setup, envId: e.target.value })}
            SelectProps={{ displayEmpty: true }}
          >
            {!agentEnvs.length && (
              <MenuItem value="">
                This agent isn&apos;t in an environment
              </MenuItem>
            )}
            {agentEnvs.map((e) => (
              <MenuItem key={e.id} value={e.id}>
                {e.name}
              </MenuItem>
            ))}
          </TextField>
          {env && (
            <Typography sx={{ typography: "s2", color: "text.secondary" }}>
              {relevant.length
                ? `Compares v${setup.v} with ${against ? `v${against}, what ${nameOf(setup.agentKey)} loads now` : `${nameOf(setup.agentKey)} running without this skill`}, on the same ${relevant.length} ${relevant.length === 1 ? "scenario" : "scenarios"} about this skill.`
                : `No scenario in ${env.name} is about this skill. A pass on unrelated scenarios proves nothing — add some first.`}
            </Typography>
          )}
        </Stack>
      }
      action={
        env && !relevant.length ? (
          <Button
            size="small"
            variant="contained"
            color="primary"
            onClick={() => onAddScenarios(envId)}
          >
            Add scenarios
          </Button>
        ) : (
          <Button
            size="small"
            variant="contained"
            color="primary"
            disabled={!env}
            onClick={() =>
              onStart({
                v: setup.v,
                agentKey: setup.agentKey,
                envId,
                envName: env.name,
                against,
                scenarios: relevant.length,
              })
            }
          >
            Run test
          </Button>
        )
      }
    />
  );
}
SkillTestDialog.propTypes = {
  skill: PropTypes.object.isRequired,
  setup: PropTypes.object.isRequired,
  used: PropTypes.array.isRequired,
  agents: PropTypes.array.isRequired,
  nameOf: PropTypes.func.isRequired,
  envs: PropTypes.array.isRequired,
  byEnv: PropTypes.object.isRequired,
  onChange: PropTypes.func.isRequired,
  onClose: PropTypes.func.isRequired,
  onAddScenarios: PropTypes.func.isRequired,
  onStart: PropTypes.func.isRequired,
};

function CreateSkillDialog({ onClose, onCreate }) {
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [body, setBody] = useState("");
  const missing = !!name.trim() && !body.trim();
  return (
    <ConfirmDialog
      open
      onClose={onClose}
      PaperProps={{ sx: { maxWidth: 560 } }}
      title="New skill"
      content={
        <Stack spacing={1.5} sx={{ pt: 0.5 }}>
          <TextField
            size="small"
            label="Name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="refund-policy"
          />
          <TextField
            size="small"
            label="When it's used"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            placeholder="Use before offering or issuing a refund."
            helperText="The description decides when an agent reaches for it."
          />
          <TextField
            size="small"
            label="Instructions (SKILL.md)"
            value={body}
            onChange={(e) => setBody(e.target.value)}
            multiline
            minRows={4}
            error={missing}
            helperText={
              missing ? "SKILL.md is missing" : "Paste it, or upload the file."
            }
          />
          <Box>
            <Button
              size="small"
              variant="outlined"
              component="label"
              sx={{ color: "text.primary", borderColor: "divider" }}
            >
              Upload SKILL.md
              <input
                hidden
                type="file"
                accept=".md,text/markdown,text/plain"
                onChange={(e) => {
                  const f = e.target.files?.[0];
                  if (f) f.text().then((t) => setBody(t));
                  e.target.value = "";
                }}
              />
            </Button>
          </Box>
        </Stack>
      }
      action={
        <Button
          size="small"
          variant="contained"
          color="primary"
          disabled={!name.trim() || !body.trim()}
          onClick={() =>
            onCreate({
              name,
              description,
              text: body.trimStart().startsWith("---")
                ? body
                : `---\nname: ${name.trim()}\ndescription: ${description.trim()}\n---\n\n${body.trim()}\n`,
            })
          }
        >
          Create v1
        </Button>
      }
    />
  );
}
CreateSkillDialog.propTypes = {
  onClose: PropTypes.func.isRequired,
  onCreate: PropTypes.func.isRequired,
};
