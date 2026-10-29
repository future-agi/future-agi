import PropTypes from "prop-types";
import { useEffect, useMemo, useState } from "react";
import { useSnackbar } from "notistack";
import { useNavigate } from "react-router-dom";
import { paths } from "src/routes/paths";
import {
  Box,
  Button,
  IconButton,
  Menu,
  MenuItem,
  Stack,
  Switch,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { EmptyState } from "../../components/primitives";
import CopyCommandButton from "../../components/CopyCommandButton";
import { useSimStore } from "../../store";
import { nextMajorLabel } from "../../_mock/versions";
import {
  draftChangesOf,
  draftStateOf,
  frontmatterOf,
  importSkill,
  latestOf,
  loadedVOf,
  skillImportCli,
  skillRuntimeOf,
  skillSetOf,
  skillEvidenceOf,
  startTestPatch,
  testStatusOf,
  testSummaryOf,
  VERDICTS,
} from "../../_mock/skills";
import { currentUser } from "../../_mock/scenarioProvenance";
import { agentIdOf } from "../../_mock/agentRegistry";
import { MONO } from "../agentViewUtils";
import { FactBox, FactRow } from "./SkillFacts";
import SkillDrawer from "./SkillDrawer";
import SkillImportDrawer from "./SkillImportDrawer";
import SkillTestDrawer from "./SkillTestDrawer";

const STATE_TEXT = { on: "Loaded", off: "Off", library: "Not loaded" };
const head = {
  typography: "s3",
  color: "text.tertiary",
  fontWeight: 500,
  py: 1,
  px: 1.5,
  bgcolor: "transparent",
};
const cell = {
  py: 0.75,
  px: 1.5,
  borderColor: "divider",
  whiteSpace: "nowrap",
};
const ago = (iso) => {
  const d = Math.round((Date.now() - new Date(iso)) / 864e5);
  return d < 1 ? "today" : d < 45 ? `${d}d ago` : `${Math.round(d / 30)}mo ago`;
};

/**
 * Agent → Skills (roadmap skills.md): the skills the agent loads, each a
 * versioned folder. Import reads a folder and shows what it asks for; turning
 * skills on or off makes a draft skill set — the agent's current version
 * keeps its skills until the draft is saved as a new version.
 */
export default function AgentSkillsTab({ agent, agentTools, onRecordVersion }) {
  const { state, dispatch } = useSimStore();
  const { enqueueSnackbar } = useSnackbar();
  const navigate = useNavigate();
  const runtime = useMemo(() => skillRuntimeOf(agent), [agent]);
  const set = useMemo(
    () => skillSetOf(agent, state.workspaceSettings),
    [agent, state.workspaceSettings],
  );
  const [importing, setImporting] = useState(null);
  const [openId, setOpenId] = useState(null);
  const [menu, setMenu] = useState(null);
  /* { skillId } to plan a test, { skillId, testId } to view one. */
  const [testing, setTesting] = useState(null);
  /* Re-read running tests' status while one is running. */
  const [, tick] = useState(0);
  const running = (set.tests || []).some((t) => testStatusOf(t) === "running");
  useEffect(() => {
    if (!running) return undefined;
    const t = setInterval(() => tick((n) => n + 1), 2000);
    return () => clearInterval(t);
  }, [running]);
  /* The environments testing this agent — where a skill test runs. */
  const envs = useMemo(
    () =>
      (agent.usedIn || [])
        .map((u) => ({
          env: state.myEnvironments.find((e) => e.id === u.envId),
          envState: state.byEnv?.[u.envId],
        }))
        .filter((e) => e.env && e.envState),
    [agent, state.myEnvironments, state.byEnv],
  );

  const save = (next) =>
    dispatch({
      type: "setWorkspaceSetting",
      key: "agentSkills",
      value: {
        ...(state.workspaceSettings?.agentSkills || {}),
        [agent.key]: {
          skills: next.skills,
          draft: next.draft || {},
          tests: next.tests || set.tests || [],
        },
      },
    });
  const toggle = (skill) => {
    const now = draftStateOf(skill, set.draft);
    const to = now === "on" ? "off" : "on";
    const draft = { ...set.draft };
    if (to === skill.state) delete draft[skill.id];
    else draft[skill.id] = to;
    save({ ...set, draft });
  };
  const changes = draftChangesOf(set);
  const scenariosOf = (envId) => state.byEnv?.[envId]?.scenarios || [];
  /* What to do with a finished test: keep, revise, leave it, or improve. */
  const decide = (kind, { test, text, summary }) => {
    const skill = set.skills.find((s) => s.id === test.skillId);
    const at = new Date().toISOString();
    const mark = (decision, base) => ({
      ...base,
      tests: base.tests.map((t) =>
        t.id === test.id
          ? { ...t, decision: { ...decision, at, by: currentUser().name } }
          : t,
      ),
    });
    const draft = { ...set.draft };
    delete draft[skill.id];
    if (kind === "improve") {
      navigate(
        `${paths.dashboard.simulate.improvements}?new=1&agent=${agentIdOf(agent.key)}`,
      );
      return;
    }
    if (kind === "remove") {
      const line =
        skill.state === "on"
          ? `Kept ${agent.latest} as it was. ${skill.id} v${test.v} wasn't adopted; every version stays here.`
          : `${skill.id} wasn't added. It stays imported, not loaded.`;
      save(mark({ kind, line }, { ...set, draft }));
      setTesting(null);
      enqueueSnackbar(line);
      return;
    }
    if (kind === "revise") {
      const v = latestOf(skill).v + 1;
      const version = {
        v,
        at,
        by: currentUser().name,
        source: { kind: "edit", from: test.id },
        files: latestOf(skill).files.map((f) =>
          f.path === "SKILL.md" ? { ...f, text } : f,
        ),
        note: `Revised after ${test.id}`,
      };
      const skills = set.skills.map((s) =>
        s.id === skill.id
          ? {
              ...s,
              ...(s.state === "on" && { loadedV: loadedVOf(s) }),
              versions: [...s.versions, version],
            }
          : s,
      );
      save(
        mark(
          {
            kind,
            v,
            line: `Revised as v${v} and tested again on the same cases.`,
          },
          { ...set, skills },
        ),
      );
      setTesting({ skillId: skill.id, prefill: test });
      return;
    }
    /* Keep: the candidate's skill set becomes a new agent version. Runs
       already made keep the skill set they ran with. */
    const cand = test.configs.find((c) => c.candidate);
    if (cand.asIs) {
      save(
        mark(
          {
            kind,
            line: `Kept as it is. ${agent.latest} already loads ${skill.id} v${test.v}.`,
          },
          set,
        ),
      );
      setTesting(null);
      return;
    }
    const label = nextMajorLabel(agent.versions.map((v) => v.label));
    onRecordVersion({
      id: `agent-${label}`,
      label,
      note: `Skills: ${skill.id} v${test.v}, tested in ${test.id}.`,
      createdAt: at,
      connectedAt: at,
      origin: "manual",
      basedOnVersion: agent.latest,
      changes: [
        {
          kind: "skills",
          title: `Skill ${skill.id} v${test.v} ${skill.state === "on" ? "updated" : "added"} · tested in ${test.id}`,
          detail: VERDICTS[summary.verdict].label,
        },
      ],
      skillSet: cand.skills,
    });
    const skills = set.skills.map((s) => {
      if (s.id !== skill.id) return s;
      const { loadedV, ...rest } = s;
      return {
        ...rest,
        state: "on",
        ...(test.v !== latestOf(s).v && { loadedV: test.v }),
      };
    });
    save(
      mark(
        {
          kind,
          label,
          line: `Kept as ${label}: it loads ${skill.id} v${test.v}. Earlier runs keep the skill set they ran with.`,
        },
        { ...set, skills, draft },
      ),
    );
    setTesting(null);
  };
  const saveDraft = () => {
    const label = nextMajorLabel(agent.versions.map((v) => v.label));
    const now = new Date().toISOString();
    const loaded = set.skills.filter(
      (s) => draftStateOf(s, set.draft) === "on",
    );
    onRecordVersion({
      id: `agent-${label}`,
      label,
      note: `Skills: ${changes.map((c) => `${c.skill.id} ${c.to === "on" ? "on" : "off"}`).join(", ")}.`,
      createdAt: now,
      connectedAt: now,
      origin: "manual",
      basedOnVersion: agent.latest,
      changes: changes.map((c) => ({
        kind: "skills",
        title: `Skill ${c.skill.id} ${c.to === "on" ? `turned on · v${latestOf(c.skill).v}` : "turned off"}`,
        detail:
          frontmatterOf(
            latestOf(c.skill).files.find((f) => f.path === "SKILL.md")?.text,
          )?.description || "",
      })),
      skillSet: loaded.map((s) => ({
        id: s.id,
        v: set.draft[s.id] ? latestOf(s).v : loadedVOf(s),
      })),
    });
    save({
      skills: set.skills.map(({ loadedV, ...s }) => ({
        ...s,
        /* Turned on now: it loads its latest version. */
        ...(loadedV != null && !set.draft[s.id] && { loadedV }),
        state:
          draftStateOf(s, set.draft) === "on"
            ? "on"
            : s.state === "library" && !set.draft[s.id]
              ? "library"
              : "off",
      })),
      draft: {},
    });
  };

  if (!runtime.canAdd)
    return (
      <EmptyState
        icon="solar:book-bookmark-linear"
        title="No skills on this agent"
        body={runtime.why}
      />
    );

  const open = set.skills.find((s) => s.id === openId);
  const onCount = set.skills.filter(
    (s) => draftStateOf(s, set.draft) === "on",
  ).length;

  return (
    <Stack spacing={2}>
      <Stack direction="row" alignItems="flex-end" spacing={1.5}>
        <Box flex={1} minWidth={0}>
          <Typography sx={{ typography: "s1", fontWeight: 600 }}>
            {`Skills · ${onCount} loaded of ${set.skills.length}`}
          </Typography>
          <Typography sx={{ typography: "s2", color: "text.secondary" }}>
            A skill is a folder of instructions, scripts and references the
            agent loads when its description matches the request.
          </Typography>
        </Box>
        <CopyCommandButton command={skillImportCli(agent)} label="Copy CLI" />
        <Button
          size="small"
          variant="contained"
          color="primary"
          startIcon={<Iconify icon="mingcute:add-line" width={16} />}
          onClick={() => setImporting({ path: "" })}
        >
          Import skill
        </Button>
      </Stack>

      <FactBox>
        <FactRow k="Loaded by">{runtime.mechanism}</FactRow>
        <FactRow k="Activation">
          {runtime.observed ? "Observed" : "Unknown"}
          <Box
            component="span"
            sx={{ color: "text.tertiary" }}
          >{` · ${runtime.line}`}</Box>
        </FactRow>
      </FactBox>

      {changes.length > 0 && (
        <Stack
          direction="row"
          alignItems="center"
          spacing={1.5}
          sx={{
            border: "1px solid",
            borderColor: "divider",
            borderRadius: "8px",
            px: 1.5,
            py: 1,
            bgcolor: "background.neutral",
          }}
        >
          <Box flex={1} minWidth={0}>
            <Typography sx={{ typography: "s2", fontWeight: 600 }}>
              {`Draft skill set · ${changes.length} change${changes.length === 1 ? "" : "s"}`}
            </Typography>
            <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
              {`${changes.map((c) => `${c.skill.id} ${c.to === "on" ? "on" : "off"}`).join(" · ")}. ${agent.latest} keeps its skills until you save this as a new version.`}
            </Typography>
          </Box>
          <Button
            size="small"
            color="inherit"
            onClick={() => save({ ...set, draft: {} })}
          >
            Discard
          </Button>
          {changes.some((c) => c.to === "on") && (
            <Button
              size="small"
              variant="outlined"
              color="inherit"
              onClick={() =>
                setTesting({
                  skillId: changes.find((c) => c.to === "on").skill.id,
                })
              }
            >
              Test the change
            </Button>
          )}
          <Button
            size="small"
            variant="contained"
            color="primary"
            onClick={saveDraft}
          >
            Save as new version
          </Button>
        </Stack>
      )}

      <Table size="small">
        <TableHead>
          <TableRow>
            <TableCell sx={head}>Skill</TableCell>
            <TableCell sx={{ ...head, width: 96 }}>Version</TableCell>
            <TableCell sx={{ ...head, width: 72, textAlign: "right" }}>
              Files
            </TableCell>
            <TableCell sx={{ ...head, width: 110 }}>Updated</TableCell>
            <TableCell sx={{ ...head, width: 180 }}>Loaded</TableCell>
            <TableCell sx={{ ...head, width: 44 }} />
          </TableRow>
        </TableHead>
        <TableBody>
          {set.skills.map((s) => {
            const v = latestOf(s);
            const desc = frontmatterOf(
              v.files.find((f) => f.path === "SKILL.md")?.text,
            )?.description;
            const st = draftStateOf(s, set.draft);
            const changed = st !== s.state;
            return (
              <TableRow
                key={s.id}
                hover
                onClick={() => setOpenId(s.id)}
                sx={{ cursor: "pointer", height: 44 }}
              >
                <TableCell
                  sx={{
                    ...cell,
                    maxWidth: 0,
                    width: "100%",
                    whiteSpace: "normal",
                  }}
                >
                  <Typography noWrap sx={{ fontFamily: MONO, fontSize: 12 }}>
                    {s.id}
                  </Typography>
                  <Typography
                    noWrap
                    title={desc}
                    sx={{ typography: "s3", color: "text.tertiary" }}
                  >
                    {desc || "—"}
                  </Typography>
                </TableCell>
                <TableCell
                  sx={{ ...cell, typography: "s3" }}
                  title={
                    s.state === "on" && loadedVOf(s) !== v.v
                      ? `Loads v${loadedVOf(s)}. v${v.v} is imported, not loaded yet.`
                      : undefined
                  }
                >
                  {s.state === "on" && loadedVOf(s) !== v.v
                    ? `v${loadedVOf(s)} · v${v.v} new`
                    : `v${v.v}`}
                </TableCell>
                <TableCell
                  sx={{
                    ...cell,
                    typography: "s3",
                    textAlign: "right",
                    fontVariantNumeric: "tabular-nums",
                  }}
                >
                  {v.files.length}
                </TableCell>
                <TableCell
                  sx={{ ...cell, typography: "s3", color: "text.secondary" }}
                >
                  {ago(v.at)}
                </TableCell>
                <TableCell sx={cell} onClick={(e) => e.stopPropagation()}>
                  <Stack direction="row" alignItems="center" spacing={0.5}>
                    <Switch
                      size="small"
                      checked={st === "on"}
                      onChange={() => toggle(s)}
                    />
                    <Typography
                      sx={{
                        typography: "s3",
                        color: changed ? "text.primary" : "text.tertiary",
                      }}
                    >
                      {changed
                        ? `${STATE_TEXT[s.state]} → ${STATE_TEXT[st]}`
                        : STATE_TEXT[st]}
                    </Typography>
                  </Stack>
                </TableCell>
                <TableCell sx={cell} onClick={(e) => e.stopPropagation()}>
                  <IconButton
                    size="small"
                    aria-label="More"
                    onClick={(e) => setMenu({ el: e.currentTarget, skill: s })}
                  >
                    <Iconify icon="solar:menu-dots-bold" width={16} />
                  </IconButton>
                </TableCell>
              </TableRow>
            );
          })}
        </TableBody>
      </Table>
      {!set.skills.length && (
        <Typography sx={{ typography: "s2", color: "text.secondary" }}>
          No skills yet. Import a folder from the repository or upload one.
        </Typography>
      )}

      {(set.tests || []).length > 0 && (
        <Box>
          <Typography sx={{ typography: "s1", fontWeight: 600, mb: 0.5 }}>
            {`Skill tests · ${set.tests.length}`}
          </Typography>
          <Table size="small">
            <TableHead>
              <TableRow>
                <TableCell sx={{ ...head, width: 72 }}>Test</TableCell>
                <TableCell sx={head}>Skill</TableCell>
                <TableCell sx={head}>Environment</TableCell>
                <TableCell sx={{ ...head, textAlign: "right" }}>Runs</TableCell>
                <TableCell sx={head}>Status</TableCell>
                <TableCell sx={head}>Started</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {[...set.tests].reverse().map((t) => {
                const st = testStatusOf(t);
                return (
                  <TableRow
                    key={t.id}
                    hover
                    onClick={() =>
                      setTesting({ skillId: t.skillId, testId: t.id })
                    }
                    sx={{ cursor: "pointer", height: 36 }}
                  >
                    <TableCell sx={{ ...cell, typography: "s3" }}>
                      {t.id}
                    </TableCell>
                    <TableCell sx={{ ...cell, fontFamily: MONO, fontSize: 12 }}>
                      {`${t.skillId} v${t.v}`}
                    </TableCell>
                    <TableCell sx={{ ...cell, typography: "s3" }}>
                      {t.envName}
                    </TableCell>
                    <TableCell
                      sx={{
                        ...cell,
                        typography: "s3",
                        textAlign: "right",
                        fontVariantNumeric: "tabular-nums",
                      }}
                    >
                      {`${t.cases.length} × ${t.repeats} × ${t.configs.length} = ${t.runs}`}
                    </TableCell>
                    <TableCell sx={{ ...cell, typography: "s3" }}>
                      {st === "running"
                        ? "Running"
                        : st === "cancelled"
                          ? "Cancelled"
                          : t.decision
                            ? {
                                keep: t.decision.label
                                  ? `Kept · ${t.decision.label}`
                                  : "Kept as it is",
                                remove: "Not adopted",
                                revise: `Revised → v${t.decision.v}`,
                              }[t.decision.kind]
                            : VERDICTS[
                                testSummaryOf(
                                  t,
                                  set.skills.find((x) => x.id === t.skillId),
                                  scenariosOf(t.envId),
                                ).verdict
                              ].label}
                    </TableCell>
                    <TableCell
                      sx={{
                        ...cell,
                        typography: "s3",
                        color: "text.secondary",
                      }}
                    >
                      {`${ago(t.startedAt)} · ${t.by}`}
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        </Box>
      )}

      <Menu anchorEl={menu?.el} open={!!menu} onClose={() => setMenu(null)}>
        <MenuItem
          onClick={() => {
            setOpenId(menu.skill.id);
            setMenu(null);
          }}
        >
          Open
        </MenuItem>
        <MenuItem
          onClick={() => {
            setImporting({
              path:
                latestOf(menu.skill).source?.kind === "repo"
                  ? latestOf(menu.skill).source.path
                  : "",
            });
            setMenu(null);
          }}
        >
          Import a new revision
        </MenuItem>
        <MenuItem
          onClick={() => {
            setTesting({ skillId: menu.skill.id });
            setMenu(null);
          }}
        >
          Test this skill
        </MenuItem>
        {menu?.skill.state === "library" && (
          <MenuItem
            onClick={() => {
              const id = menu.skill.id;
              const draft = { ...set.draft };
              delete draft[id];
              save({ skills: set.skills.filter((s) => s.id !== id), draft });
              setMenu(null);
              enqueueSnackbar(
                `Removed ${id}. It was never loaded, so no version used it.`,
              );
            }}
          >
            Remove
          </MenuItem>
        )}
      </Menu>

      <SkillImportDrawer
        open={!!importing}
        onClose={() => setImporting(null)}
        agent={agent}
        set={set}
        runtime={runtime}
        agentTools={agentTools}
        startPath={importing?.path}
        onImport={(folder) => {
          const next = importSkill(set, folder);
          save(next);
          setImporting(null);
          enqueueSnackbar(
            next.imported.isNewVersion
              ? `Imported ${next.imported.id} v${next.imported.v}. The agent still loads the version it had.`
              : `Imported ${next.imported.id}. It isn't loaded yet. Turn it on to make a draft.`,
            { variant: "success" },
          );
        }}
      />
      <SkillDrawer
        open={!!open}
        onClose={() => setOpenId(null)}
        skill={open}
        draft={set.draft}
        runtime={runtime}
        agentTools={agentTools}
        evidence={
          open ? skillEvidenceOf(open, set.tests, runtime, scenariosOf) : null
        }
        onImportRevision={() => {
          const src = latestOf(open).source;
          setOpenId(null);
          setImporting({ path: src?.kind === "repo" ? src.path : "" });
        }}
        onTest={() => {
          setTesting({ skillId: open.id });
          setOpenId(null);
        }}
      />
      <SkillTestDrawer
        open={!!testing}
        onClose={() => setTesting(null)}
        agent={agent}
        skill={set.skills.find((s) => s.id === testing?.skillId)}
        set={set}
        runtime={runtime}
        envs={envs}
        test={(set.tests || []).find((t) => t.id === testing?.testId)}
        prefill={testing?.prefill}
        onDecide={decide}
        onStart={(t) => {
          const next = startTestPatch(set, t);
          save(next);
          const id = next.tests[next.tests.length - 1].id;
          setTesting({ skillId: t.skillId, testId: id });
          enqueueSnackbar(`${id} started · ${t.runs} runs on ${t.envName}.`, {
            variant: "success",
          });
        }}
      />
    </Stack>
  );
}

AgentSkillsTab.propTypes = {
  agent: PropTypes.object.isRequired,
  agentTools: PropTypes.array,
  onRecordVersion: PropTypes.func.isRequired,
};
