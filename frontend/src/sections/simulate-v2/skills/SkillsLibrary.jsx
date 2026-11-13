import PropTypes from "prop-types";
import { useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { paths } from "src/routes/paths";
import {
  Box,
  Button,
  IconButton,
  Stack,
  Tab,
  Tabs,
  TextField,
  Tooltip,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import PageHeader from "src/components/page-header";
import { ConfirmDialog } from "src/components/custom-dialog";
import SideDrawer from "../components/SideDrawer";
import { useConnectedAgents, useSimStore } from "../store";
import { skillSetOf } from "../_mock/skills";
import { connectedAgentPath } from "../improvements/improvementNav";
import {
  addFromRepo,
  agentContextOf,
  createSkill,
  latestV,
  libraryOf,
  usedByOf,
  verdictOf,
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
const VERDICT_TONE = {
  good: "success.main",
  bad: "error.main",
  warn: "warning.main",
  neutral: "text.primary",
};
const LOADED_HINT =
  "Loaded: Observed means the test run's trace shows the agent reading the skill. Unknown means nothing in the run says either way.";
const tableBox = {
  border: "1px solid",
  borderColor: "divider",
  borderRadius: "8px",
  overflow: "auto",
  flexShrink: 0,
};
const rowSx = { cursor: "pointer", "&:hover": { bgcolor: "action.hover" } };
const mono = { fontFamily: "ui-monospace, Menlo, monospace" };
/* Same tab rail as the Environments landing page. */
const TAB_SX = {
  minWidth: 0,
  px: 0.75,
  "&:not(:last-of-type)": { mr: 2.5 },
  color: "text.secondary",
  "&.Mui-selected": { color: "text.primary" },
};
const descriptionOf = (skill) => {
  const v = skill.versions?.[skill.versions.length - 1];
  const text = v?.files?.[0]?.text || "";
  return (text.match(/description:\s*(.*)/) || [])[1] || "";
};
const whereOf = (c) =>
  c.location === "repo"
    ? `In the agent's repo${c.agentVersion ? ` · with agent ${c.agentVersion}` : ""} · read-only here`
    : `In the library · v${c.v} · tagged ${c.tag}`;

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
  const navigate = useNavigate();
  /* A skill opens on its own page (files, versions, tests, results). */
  const setOpenId = (id) => navigate(paths.dashboard.simulate.skill(id));
  const [openContext, setOpenContext] = useState(null);
  const [creating, setCreating] = useState(false);
  const [query, setQuery] = useState("");
  /* The tab lives in the URL so Back from an agent returns to it. */
  const [params, setParams] = useSearchParams();
  const tab = params.get("tab") === "contexts" ? "contexts" : "skills";
  const setTab = (v) => {
    const next = new URLSearchParams(params);
    if (v === "contexts") next.set("tab", "contexts");
    else next.delete("tab");
    setParams(next, { replace: true });
    setQuery("");
  };
  const save = (patch) =>
    Object.entries(patch).forEach(([key, value]) => {
      if (key === "skill") return;
      dispatch({ type: "setWorkspaceSetting", key, value });
    });
  const nameOf = (key) => agents.find((a) => a.key === key)?.name || "An agent";
  const q = query.trim().toLowerCase();
  const shown = library.filter((s) =>
    `${s.name} ${s.description} ${s.owner}`.toLowerCase().includes(q),
  );
  /* skills.md: an agent context is an agent's always-on instructions
     (AGENTS.md or similar) and the skills it links. */
  const contexts = useMemo(
    () => agents.map((a) => agentContextOf(a, settings)).filter(Boolean),
    [agents, settings],
  );
  const shownContexts = contexts.filter((c) =>
    `${c.agentName} ${c.file} ${c.skills.join(" ")}`.toLowerCase().includes(q),
  );
  /* skills.md: "Skills found in an agent's source can be imported into the
     library." One row per skill name found in agents' code and not yet in
     the library; a library skill with the same name says the code copy is
     separate. */
  const inCode = useMemo(() => {
    const byName = {};
    agents.forEach((a) => {
      if (!agentContextOf(a, settings)) return;
      skillSetOf(a, settings)
        .skills.filter((s) => s.state !== "library")
        .forEach((s) => {
          const row = (byName[s.id] ||= { skill: s, agents: [] });
          row.agents.push(a);
        });
    });
    return byName;
  }, [agents, settings]);
  const found = Object.values(inCode)
    .filter((r) => !library.some((s) => s.name === r.skill.id))
    .filter((r) => r.skill.id.toLowerCase().includes(q));
  const importSkill = (row) => {
    const out = addFromRepo(settings, row.skill, row.agents[0].name);
    save(out);
    setOpenId(out.skill.id);
  };
  const skillCount =
    library.length +
    Object.keys(inCode).filter((n) => !library.some((s) => s.name === n))
      .length;

  return (
    <Box
      sx={{
        display: "flex",
        flexDirection: "column",
        height: "100%",
        minHeight: 0,
      }}
    >
      <Box sx={{ px: 2, pt: 2 }}>
        <PageHeader
          sx={{ mb: 1 }}
          title="Skills"
          description="Reusable skills and each agent's always-on instructions, versioned in one place. Test a version on your agents, then promote it."
          actions={
            tab === "skills" && (
              <Button
                size="small"
                variant="contained"
                color="primary"
                startIcon={
                  <Iconify icon="solar:add-circle-linear" width={16} />
                }
                onClick={() => setCreating(true)}
              >
                New skill
              </Button>
            )
          }
        />
      </Box>
      <Box
        sx={{
          px: 2,
          borderBottom: "1px solid",
          borderColor: "divider",
          flexShrink: 0,
        }}
      >
        <Tabs
          sx={{ boxShadow: "none" }}
          value={tab}
          onChange={(_, v) => setTab(v)}
        >
          <Tab
            value="skills"
            disableRipple
            label={<TabLabel text="Skills" count={skillCount} />}
            sx={TAB_SX}
          />
          <Tab
            value="contexts"
            disableRipple
            label={<TabLabel text="Agent contexts" count={contexts.length} />}
            sx={TAB_SX}
          />
        </Tabs>
      </Box>

      <Box
        sx={{
          flex: 1,
          minHeight: 0,
          overflow: "auto",
          p: 2,
          display: "flex",
          flexDirection: "column",
          gap: 1.5,
        }}
      >
        <Stack direction="row" alignItems="center" spacing={1.5}>
          <TextField
            size="small"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder={tab === "skills" ? "Search skills" : "Search agents"}
            sx={{ width: 300 }}
          />
          <Typography sx={{ typography: "s3", color: "text.secondary" }}>
            {tab === "skills"
              ? "A skill is a SKILL.md any agent can attach, at a version or a tag."
              : "An agent's own instructions file and the skills it links. Change it where it lives."}
          </Typography>
        </Stack>

        {tab === "skills" && (
          <Box sx={tableBox}>
            <Box
              component="table"
              sx={{ width: 1, borderCollapse: "collapse" }}
            >
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
                      {h === "Last test" ? (
                        <Tooltip arrow title={LOADED_HINT}>
                          <span>{h}</span>
                        </Tooltip>
                      ) : (
                        h
                      )}
                    </Box>
                  ))}
                </Box>
              </Box>
              <Box component="tbody">
                {shown.map((s) => {
                  const v = latestV(s);
                  const test = s.tests?.[v];
                  const used = usedByOf(s.id, settings);
                  const codeCopies = inCode[s.name]?.agents.length || 0;
                  return (
                    <Box
                      component="tr"
                      key={s.id}
                      onClick={() => setOpenId(s.id)}
                      sx={rowSx}
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
                        {s.tags?.production
                          ? `v${s.tags.production}`
                          : "None yet"}
                      </Box>
                      <Box
                        component="td"
                        sx={{
                          ...cell,
                          color: test ? "text.primary" : "text.tertiary",
                        }}
                      >
                        {test ? (
                          <>
                            {`v${v} · `}
                            <Box
                              component="span"
                              sx={{ color: VERDICT_TONE[verdictOf(test).tone] }}
                            >
                              {verdictOf(test).word}
                            </Box>
                            <Box
                              component="span"
                              sx={{ color: "text.tertiary" }}
                            >
                              {` · ${LOADED[test.loaded]}`}
                            </Box>
                          </>
                        ) : (
                          `v${v} not tested`
                        )}
                      </Box>
                      <Box component="td" sx={cell}>
                        <Typography
                          sx={{
                            typography: "s2",
                            color: used.length
                              ? "text.primary"
                              : "text.tertiary",
                          }}
                        >
                          {used.length
                            ? `${used.length} agent${used.length === 1 ? "" : "s"}`
                            : "No agents"}
                        </Typography>
                        {codeCopies > 0 && (
                          <Typography
                            sx={{ typography: "s3", color: "text.tertiary" }}
                          >
                            {`A separate copy is in ${codeCopies} agent${codeCopies === 1 ? "'s" : "s'"} code`}
                          </Typography>
                        )}
                      </Box>
                      <Box
                        component="td"
                        sx={{ ...cell, color: "text.secondary" }}
                      >
                        {ago(s.versions[s.versions.length - 1].at)}
                      </Box>
                    </Box>
                  );
                })}
                {found.map((r) => (
                  <Box component="tr" key={`code-${r.skill.id}`}>
                    <Box component="td" sx={{ ...cell, maxWidth: 420 }}>
                      <Typography sx={{ typography: "s2", fontWeight: 500 }}>
                        {r.skill.id}
                      </Typography>
                      <Typography
                        noWrap
                        sx={{ typography: "s3", color: "text.secondary" }}
                      >
                        {descriptionOf(r.skill)}
                      </Typography>
                    </Box>
                    <Box
                      component="td"
                      colSpan={3}
                      sx={{ ...cell, color: "text.tertiary" }}
                    >
                      Found in code · import it to version, test and promote it
                      here
                    </Box>
                    <Box
                      component="td"
                      sx={{ ...cell, color: "text.tertiary" }}
                    >
                      {`In ${r.agents.length} agent${r.agents.length === 1 ? "'s" : "s'"} code`}
                    </Box>
                    <Box component="td" sx={cell}>
                      <Button
                        size="small"
                        variant="outlined"
                        onClick={() => importSkill(r)}
                        sx={{ color: "text.primary", borderColor: "divider" }}
                      >
                        Import
                      </Button>
                    </Box>
                  </Box>
                ))}
                {!shown.length && !found.length && (
                  <Box component="tr">
                    <Box
                      component="td"
                      colSpan={6}
                      sx={{ ...cell, color: "text.tertiary" }}
                    >
                      No skills match.
                    </Box>
                  </Box>
                )}
              </Box>
            </Box>
          </Box>
        )}

        {tab === "contexts" && (
          <Box sx={tableBox}>
            <Box
              component="table"
              sx={{ width: 1, borderCollapse: "collapse" }}
            >
              <Box component="thead">
                <Box component="tr">
                  {["Agent", "File", "Linked skills", "Where it lives"].map(
                    (h) => (
                      <Box component="th" key={h} sx={head}>
                        {h}
                      </Box>
                    ),
                  )}
                </Box>
              </Box>
              <Box component="tbody">
                {shownContexts.map((c) => (
                  <Box
                    component="tr"
                    key={c.agentKey}
                    onClick={() => setOpenContext(c.agentKey)}
                    sx={rowSx}
                  >
                    <Box component="td" sx={{ ...cell, maxWidth: 260 }}>
                      <Typography
                        noWrap
                        sx={{ typography: "s2", fontWeight: 500 }}
                      >
                        {c.agentName}
                      </Typography>
                    </Box>
                    <Box component="td" sx={{ ...cell, ...mono }}>
                      {c.file}
                    </Box>
                    <Box
                      component="td"
                      sx={{
                        ...cell,
                        maxWidth: 320,
                        color: c.skills.length
                          ? "text.primary"
                          : "text.tertiary",
                      }}
                    >
                      <Typography
                        noWrap
                        sx={{ typography: "s2", color: "inherit" }}
                      >
                        {c.skills.length ? c.skills.join(", ") : "None linked"}
                      </Typography>
                    </Box>
                    <Box
                      component="td"
                      sx={{ ...cell, color: "text.secondary" }}
                    >
                      {whereOf(c)}
                    </Box>
                  </Box>
                ))}
                {!shownContexts.length && (
                  <Box component="tr">
                    <Box
                      component="td"
                      colSpan={4}
                      sx={{ ...cell, color: "text.tertiary" }}
                    >
                      {contexts.length
                        ? "No agents match."
                        : "No agent with code is connected yet."}
                    </Box>
                  </Box>
                )}
              </Box>
            </Box>
          </Box>
        )}
      </Box>

      <SideDrawer
        open={!!openContext}
        onClose={() => setOpenContext(null)}
        width={520}
      >
        {openContext && (
          <ContextDetail
            context={contexts.find((c) => c.agentKey === openContext)}
            library={library}
            onOpenSkill={(id) => setOpenId(id)}
            onClose={() => setOpenContext(null)}
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

function TabLabel({ text, count }) {
  return (
    <Stack direction="row" alignItems="center" spacing={0.75} component="span">
      <span>{text}</span>
      <Box
        component="span"
        sx={{ typography: "s2", fontVariantNumeric: "tabular-nums" }}
      >
        {count}
      </Box>
    </Stack>
  );
}
TabLabel.propTypes = { text: PropTypes.string, count: PropTypes.number };

/* One agent's context: its instructions file, where it lives and the skills
   it links. Opens in place; leaving for the agent is a button, not a row click. */
function ContextDetail({ context, library, onOpenSkill, onClose }) {
  const navigate = useNavigate();
  if (!context) return null;
  const inRepo = context.location === "repo";
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
          <Typography noWrap sx={{ typography: "s1", fontWeight: 600 }}>
            {context.agentName}
          </Typography>
          <Typography noWrap sx={{ typography: "s3", color: "text.tertiary" }}>
            Agent context
          </Typography>
        </Box>
        <IconButton size="small" onClick={onClose} aria-label="Close">
          <Iconify icon="solar:close-linear" width={16} />
        </IconButton>
      </Stack>
      <Box sx={{ flex: 1, overflow: "auto", px: 2.5, py: 2 }}>
        <Box sx={{ ...tableBox, mb: 2 }}>
          <Box component="table" sx={{ width: 1, borderCollapse: "collapse" }}>
            <Box component="tbody">
              {[
                [
                  "File",
                  <Box key="f" component="span" sx={mono}>
                    {context.file}
                  </Box>,
                ],
                ["Where it lives", whereOf(context)],
                [
                  "Changes",
                  inRepo
                    ? "Through a pull request in the agent's repository"
                    : "Save a new version here; agents pulling its tag get it",
                ],
              ].map(([k, v], i) => (
                <Box component="tr" key={k}>
                  <Box
                    component="td"
                    sx={{
                      ...cell,
                      ...(i === 0 && { borderTop: 0 }),
                      width: 140,
                      color: "text.secondary",
                    }}
                  >
                    {k}
                  </Box>
                  <Box
                    component="td"
                    sx={{ ...cell, ...(i === 0 && { borderTop: 0 }) }}
                  >
                    {v}
                  </Box>
                </Box>
              ))}
            </Box>
          </Box>
        </Box>
        <Typography sx={{ typography: "s2", fontWeight: 600, mb: 0.75 }}>
          Linked skills
        </Typography>
        {context.skills.length ? (
          <Box sx={tableBox}>
            <Box
              component="table"
              sx={{ width: 1, borderCollapse: "collapse" }}
            >
              <Box component="tbody">
                {context.skills.map((name, i) => {
                  const lib = library.find((s) => s.name === name);
                  return (
                    <Box component="tr" key={name}>
                      <Box
                        component="td"
                        sx={{ ...cell, ...(i === 0 && { borderTop: 0 }) }}
                      >
                        {name}
                      </Box>
                      <Box
                        component="td"
                        sx={{
                          ...cell,
                          ...(i === 0 && { borderTop: 0 }),
                          color: "text.secondary",
                        }}
                      >
                        {lib ? "In the library" : "In the agent's code"}
                      </Box>
                      <Box
                        component="td"
                        sx={{
                          ...cell,
                          ...(i === 0 && { borderTop: 0 }),
                          textAlign: "right",
                        }}
                      >
                        {lib && (
                          <Button
                            size="small"
                            variant="outlined"
                            onClick={() => onOpenSkill(lib.id)}
                            sx={{
                              color: "text.primary",
                              borderColor: "divider",
                            }}
                          >
                            Open skill
                          </Button>
                        )}
                      </Box>
                    </Box>
                  );
                })}
              </Box>
            </Box>
          </Box>
        ) : (
          <Typography sx={{ typography: "s2", color: "text.tertiary" }}>
            None linked
          </Typography>
        )}
      </Box>
      <Stack
        direction="row"
        justifyContent="flex-end"
        sx={{
          px: 2.5,
          py: 1.5,
          borderTop: "1px solid",
          borderColor: "divider",
        }}
      >
        <Button
          size="small"
          variant="outlined"
          endIcon={<Iconify icon="solar:arrow-right-up-linear" width={14} />}
          onClick={() =>
            navigate(connectedAgentPath(context.agentId, "definition"))
          }
          sx={{ color: "text.primary", borderColor: "divider" }}
        >
          {inRepo ? "Open source on the agent" : "Open agent"}
        </Button>
      </Stack>
    </Stack>
  );
}
ContextDetail.propTypes = {
  context: PropTypes.object,
  library: PropTypes.array,
  onOpenSkill: PropTypes.func,
  onClose: PropTypes.func,
};

function CreateSkillDialog({ onClose, onCreate }) {
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [body, setBody] = useState("");
  /* Supporting files — scripts, references, templates — beside SKILL.md. */
  const [files, setFiles] = useState([]);
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
            <Button
              size="small"
              variant="outlined"
              component="label"
              sx={{ color: "text.primary", borderColor: "divider", ml: 1 }}
            >
              Add supporting files
              <input
                hidden
                multiple
                type="file"
                onChange={(e) => {
                  const picked = [...(e.target.files || [])];
                  Promise.all(
                    picked.map((f) =>
                      f.text().then((t) => ({ path: f.name, text: t })),
                    ),
                  ).then((next) =>
                    setFiles((cur) => [
                      ...cur.filter(
                        (x) => !next.some((n) => n.path === x.path),
                      ),
                      ...next,
                    ]),
                  );
                  e.target.value = "";
                }}
              />
            </Button>
            {files.length > 0 && (
              <Typography
                sx={{ typography: "s3", color: "text.secondary", mt: 0.75 }}
              >
                {`Supporting files: ${files.map((f) => f.path).join(", ")}`}
              </Typography>
            )}
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
              files,
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
