import PropTypes from "prop-types";
import { useEffect, useMemo, useState } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import {
  Box,
  Button,
  Checkbox,
  DialogActions,
  DialogContent,
  DialogTitle,
  IconButton,
  MenuItem,
  Stack,
  Tab,
  Tabs,
  TextField,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { paths } from "src/routes/paths";
import ClosableDialog from "../components/ClosableDialog";
import SideDrawer from "../components/SideDrawer";
import { useConnectedAgents, useSimStore } from "../store";
import { skillRuntimeOf } from "../_mock/skills";
import {
  attach,
  detach,
  filesOf,
  latestV,
  libraryOf,
  newVersion,
  parseSkillMd,
  promote,
  relevantScenariosOf,
  resolveRef,
  runSkillTest,
  saveTestPlan,
  testPlanOf,
  usedByOf,
  verdictOf,
} from "../_mock/skillLibrary";
import { filesDiff, lineDiff } from "./skillDiff";

/*
  A skill's own page (13 Nov, Nikhil: the Skills UI was basic and confusing;
  competitor analysis in Claude Docs "Skills library: competitor analysis
  and proposed flow"). The clarified plan's four steps live here — the
  files of every version, what changed between two, how a version is
  tested, what the test showed against production and without the skill,
  which agents load it, and Promote with the evidence on screen.
*/

const mono = { fontFamily: "ui-monospace, SFMono-Regular, monospace" };
const head = {
  typography: "s3",
  color: "text.tertiary",
  fontWeight: 500,
  py: 0.875,
  px: 1.5,
  textAlign: "left",
};
const cell = {
  typography: "s2",
  py: 0.875,
  px: 1.5,
  borderTop: "1px solid",
  borderColor: "divider",
  verticalAlign: "top",
};
const tableBox = {
  border: "1px solid",
  borderColor: "divider",
  borderRadius: "8px",
  overflow: "hidden",
};
const ago = (iso) => {
  const d = Math.round((Date.now() - new Date(iso)) / 864e5);
  return d < 1 ? "today" : d < 45 ? `${d}d ago` : `${Math.round(d / 30)}mo ago`;
};
const TONE = {
  good: "success.main",
  bad: "error.main",
  warn: "warning.main",
  neutral: "text.secondary",
};
const TABS = ["files", "versions", "tests", "results", "used"];
const TAB_SX = {
  minHeight: 36,
  "& .MuiTab-root": {
    minHeight: 36,
    px: 0,
    mr: 3,
    typography: "s2",
    textTransform: "none",
  },
};

function TagChips({ skill, v }) {
  const tags = Object.entries(skill.tags || {}).filter(([, tv]) => tv === v);
  return tags.map(([t]) => (
    <Box
      key={t}
      component="span"
      sx={{
        ...mono,
        typography: "s3",
        px: 0.75,
        py: 0.125,
        ml: 0.5,
        borderRadius: "4px",
        border: "1px solid",
        borderColor: t === "production" ? "primary.main" : "divider",
        color: t === "production" ? "primary.main" : "text.secondary",
      }}
    >
      {t}
    </Box>
  ));
}
TagChips.propTypes = { skill: PropTypes.object, v: PropTypes.number };

function DiffLines({ lines }) {
  return (
    <Box
      component="pre"
      sx={{
        ...mono,
        m: 0,
        typography: "s3",
        whiteSpace: "pre-wrap",
        border: "1px solid",
        borderColor: "divider",
        borderRadius: "8px",
        overflow: "hidden",
      }}
    >
      {lines.map((l, i) => (
        <Box
          // eslint-disable-next-line react/no-array-index-key
          key={i}
          component="div"
          sx={{
            px: 1.25,
            bgcolor:
              l.t === "+"
                ? "success.lighter"
                : l.t === "-"
                  ? "error.lighter"
                  : "transparent",
            color:
              l.t === "+"
                ? "success.darker"
                : l.t === "-"
                  ? "error.darker"
                  : "text.secondary",
          }}
        >
          {`${l.t} ${l.line}`}
        </Box>
      ))}
    </Box>
  );
}
DiffLines.propTypes = { lines: PropTypes.array.isRequired };

/* ── Files: versions · tree · the file, Preview / Edit ─────────────────── */
function FilesTab({ skill, onSaveVersion }) {
  const versions = [...skill.versions].reverse();
  const [v, setV] = useState(latestV(skill));
  const version = skill.versions.find((x) => x.v === v) || versions[0];
  const files = filesOf(version);
  const [path, setPath] = useState("SKILL.md");
  const [draft, setDraft] = useState(null);
  const shownFiles = draft || files;
  const file = shownFiles.find((f) => f.path === path) || shownFiles[0];
  const parsed = parseSkillMd(shownFiles[0]?.text);
  const bodyLines = parsed.body ? parsed.body.split("\n").length : 0;
  const checks = [
    {
      ok: parsed.name === skill.name,
      text: `name matches the folder (${skill.name})`,
    },
    {
      ok: parsed.description.length <= 1024,
      text: `description ${parsed.description.length} / 1024`,
    },
    { ok: bodyLines <= 500, text: `body ${bodyLines} / 500 lines` },
  ];
  const editing = !!draft;
  return (
    <Box
      sx={{
        ...tableBox,
        display: "grid",
        gridTemplateColumns: "180px 200px minmax(0,1fr)",
        minHeight: 420,
      }}
    >
      <Box sx={{ borderRight: "1px solid", borderColor: "divider" }}>
        <Typography sx={{ ...head, display: "block" }}>Versions</Typography>
        {versions.map((x) => (
          <Box
            key={x.v}
            role="button"
            tabIndex={0}
            onClick={() => {
              if (editing) return;
              setV(x.v);
              setPath("SKILL.md");
            }}
            sx={{
              px: 1.5,
              py: 0.75,
              cursor: editing ? "default" : "pointer",
              borderTop: "1px solid",
              borderColor: "divider",
              bgcolor: x.v === v ? "action.selected" : "transparent",
            }}
          >
            <Typography sx={{ typography: "s2" }}>
              {`v${x.v}`}
              <TagChips skill={skill} v={x.v} />
            </Typography>
            <Typography
              noWrap
              sx={{ typography: "s3", color: "text.tertiary" }}
            >
              {`${x.note} · ${ago(x.at)}`}
            </Typography>
          </Box>
        ))}
      </Box>
      <Box sx={{ borderRight: "1px solid", borderColor: "divider" }}>
        <Typography sx={{ ...head, display: "block" }}>
          {editing ? `Draft from v${v}` : `Files in v${v}`}
        </Typography>
        {shownFiles.map((f) => (
          <Box
            key={f.path}
            role="button"
            tabIndex={0}
            onClick={() => setPath(f.path)}
            sx={{
              ...mono,
              typography: "s3",
              px: 1.5,
              py: 0.75,
              cursor: "pointer",
              borderTop: "1px solid",
              borderColor: "divider",
              bgcolor:
                f.path === file?.path ? "action.selected" : "transparent",
            }}
          >
            {f.path}
          </Box>
        ))}
      </Box>
      <Stack sx={{ minWidth: 0 }}>
        <Stack
          direction="row"
          alignItems="center"
          spacing={1}
          sx={{
            px: 1.5,
            py: 0.75,
            borderBottom: "1px solid",
            borderColor: "divider",
          }}
        >
          <Typography
            sx={{ ...mono, typography: "s3", flex: 1, minWidth: 0 }}
            noWrap
          >
            {file?.path}
          </Typography>
          {editing ? (
            <>
              <Button
                size="small"
                color="inherit"
                onClick={() => setDraft(null)}
              >
                Discard
              </Button>
              <Button
                size="small"
                variant="outlined"
                onClick={() => onSaveVersion(draft, v)}
              >
                Save version…
              </Button>
            </>
          ) : (
            <Button
              size="small"
              variant="outlined"
              startIcon={<Iconify icon="solar:pen-linear" width={14} />}
              onClick={() => setDraft(files.map((f) => ({ ...f })))}
            >
              Edit
            </Button>
          )}
        </Stack>
        {editing ? (
          <TextField
            multiline
            minRows={14}
            value={file?.text || ""}
            onChange={(e) =>
              setDraft((d) =>
                d.map((f) =>
                  f.path === file.path ? { ...f, text: e.target.value } : f,
                ),
              )
            }
            inputProps={{ "aria-label": file?.path }}
            sx={{ m: 1.5, "& textarea": { ...mono, typography: "s3" } }}
          />
        ) : file?.path === "SKILL.md" ? (
          <Box sx={{ p: 1.5 }}>
            <Box component="table" sx={{ borderCollapse: "collapse", mb: 1.5 }}>
              <tbody>
                {[
                  ["name", parsed.name],
                  ["description", parsed.description],
                ].map(([k, val]) => (
                  <tr key={k}>
                    <Box
                      component="td"
                      sx={{
                        ...mono,
                        typography: "s3",
                        color: "text.tertiary",
                        pr: 2,
                        py: 0.25,
                        verticalAlign: "top",
                      }}
                    >
                      {k}
                    </Box>
                    <Box component="td" sx={{ typography: "s2", py: 0.25 }}>
                      {val}
                    </Box>
                  </tr>
                ))}
              </tbody>
            </Box>
            <Typography sx={{ typography: "s2", whiteSpace: "pre-wrap" }}>
              {parsed.body}
            </Typography>
          </Box>
        ) : (
          <Box
            component="pre"
            sx={{
              ...mono,
              typography: "s3",
              m: 0,
              p: 1.5,
              whiteSpace: "pre-wrap",
            }}
          >
            {file?.text}
          </Box>
        )}
        <Stack
          direction="row"
          spacing={1.5}
          flexWrap="wrap"
          useFlexGap
          sx={{
            mt: "auto",
            px: 1.5,
            py: 0.75,
            borderTop: "1px solid",
            borderColor: "divider",
          }}
        >
          {checks.map((c) => (
            <Typography
              key={c.text}
              sx={{
                typography: "s3",
                color: c.ok ? "text.tertiary" : "error.main",
              }}
            >
              {`${c.ok ? "✓" : "✕"} ${c.text}`}
            </Typography>
          ))}
        </Stack>
      </Stack>
    </Box>
  );
}
FilesTab.propTypes = {
  skill: PropTypes.object.isRequired,
  onSaveVersion: PropTypes.func.isRequired,
};

/* ── Versions: one row per version, and any two compared ───────────────── */
function CompareView({ skill, from, to }) {
  const a = skill.versions.find((x) => x.v === from);
  const b = skill.versions.find((x) => x.v === to);
  if (!a || !b) return null;
  const pa = parseSkillMd(a.text);
  const pb = parseSkillMd(b.text);
  const others = filesDiff(filesOf(a).slice(1), filesOf(b).slice(1));
  const descChanged = pa.description !== pb.description;
  const bodyChanged = pa.body !== pb.body;
  return (
    <Stack spacing={1.5}>
      <Box>
        <Typography sx={{ typography: "s2", fontWeight: 600, mb: 0.5 }}>
          Description
          <Box
            component="span"
            sx={{ color: "text.tertiary", fontWeight: 400 }}
          >
            {descChanged ? " · decides when the skill fires" : " · unchanged"}
          </Box>
        </Typography>
        {descChanged && (
          <DiffLines lines={lineDiff(pa.description, pb.description)} />
        )}
      </Box>
      <Box>
        <Typography sx={{ typography: "s2", fontWeight: 600, mb: 0.5 }}>
          Instructions
          {!bodyChanged && (
            <Box
              component="span"
              sx={{ color: "text.tertiary", fontWeight: 400 }}
            >
              {" · unchanged"}
            </Box>
          )}
        </Typography>
        {bodyChanged && <DiffLines lines={lineDiff(pa.body, pb.body)} />}
      </Box>
      <Box>
        <Typography sx={{ typography: "s2", fontWeight: 600, mb: 0.5 }}>
          Scripts and references
          {!others.length && (
            <Box
              component="span"
              sx={{ color: "text.tertiary", fontWeight: 400 }}
            >
              {" · unchanged"}
            </Box>
          )}
        </Typography>
        <Stack spacing={1}>
          {others.map((f) => (
            <Box key={f.path}>
              <Typography sx={{ ...mono, typography: "s3", mb: 0.25 }}>
                {`${f.path} · ${f.change}`}
              </Typography>
              <DiffLines lines={f.lines} />
            </Box>
          ))}
        </Stack>
      </Box>
    </Stack>
  );
}
CompareView.propTypes = {
  skill: PropTypes.object.isRequired,
  from: PropTypes.number,
  to: PropTypes.number,
};

function VersionsTab({ skill }) {
  const versions = [...skill.versions].reverse();
  const prod = skill.tags?.production;
  const [from, setFrom] = useState(
    prod && prod !== latestV(skill) ? prod : Math.max(1, latestV(skill) - 1),
  );
  const [to, setTo] = useState(latestV(skill));
  return (
    <Stack spacing={2}>
      <Box sx={tableBox}>
        <Box
          component="table"
          sx={{ width: "100%", borderCollapse: "collapse" }}
        >
          <thead>
            <tr>
              {[
                "Version",
                "Change",
                "Labels",
                "Tested",
                "Against production",
                "Promoted",
              ].map((h) => (
                <Box component="th" key={h} sx={head}>
                  {h}
                </Box>
              ))}
            </tr>
          </thead>
          <tbody>
            {versions.map((x) => {
              const t = skill.tests?.[x.v];
              const vd = verdictOf(t);
              const promo = [...(skill.promotions || [])]
                .reverse()
                .find((p) => p.v === x.v);
              return (
                <tr key={x.v}>
                  <Box
                    component="td"
                    sx={{ ...cell, ...mono }}
                  >{`v${x.v}`}</Box>
                  <Box component="td" sx={cell}>
                    {x.note}
                    <Typography
                      sx={{ typography: "s3", color: "text.tertiary" }}
                    >
                      {`${x.by} · ${ago(x.at)}`}
                    </Typography>
                  </Box>
                  <Box component="td" sx={cell}>
                    <TagChips skill={skill} v={x.v} />
                  </Box>
                  <Box component="td" sx={{ ...cell, color: "text.secondary" }}>
                    {t
                      ? [t.envName, ago(t.at)].filter(Boolean).join(" · ")
                      : "Not tested"}
                  </Box>
                  <Box component="td" sx={{ ...cell, color: TONE[vd.tone] }}>
                    {t ? vd.word : "—"}
                  </Box>
                  <Box component="td" sx={{ ...cell, color: "text.secondary" }}>
                    {promo
                      ? `${promo.tag || "production"} · ${promo.by}${promo.reason ? " · untested, reason kept" : ""}`
                      : "—"}
                  </Box>
                </tr>
              );
            })}
          </tbody>
        </Box>
      </Box>
      {skill.versions.length > 1 && (
        <Box>
          <Stack
            direction="row"
            spacing={1}
            alignItems="center"
            sx={{ mb: 1.25 }}
          >
            <Typography sx={{ typography: "s2", fontWeight: 600 }}>
              Compare
            </Typography>
            {[
              [from, setFrom, "From version"],
              [to, setTo, "To version"],
            ].map(([val, set, label], i) => (
              <Stack
                key={label}
                direction="row"
                spacing={1}
                alignItems="center"
              >
                {i === 1 && (
                  <Iconify
                    icon="solar:arrow-right-linear"
                    width={14}
                    sx={{ color: "text.disabled" }}
                  />
                )}
                <TextField
                  select
                  size="small"
                  value={val}
                  onChange={(e) => set(Number(e.target.value))}
                  inputProps={{ "aria-label": label }}
                  sx={{
                    minWidth: 88,
                    "& .MuiSelect-select": { typography: "s2", py: 0.5 },
                  }}
                >
                  {versions.map((x) => (
                    <MenuItem key={x.v} value={x.v}>{`v${x.v}`}</MenuItem>
                  ))}
                </TextField>
              </Stack>
            ))}
          </Stack>
          <CompareView skill={skill} from={from} to={to} />
        </Box>
      )}
    </Stack>
  );
}
VersionsTab.propTypes = { skill: PropTypes.object.isRequired };

/* ── Tests: what a test of a version checks ────────────────────────────── */
function TestsTab({ skill, library, testers, onSavePlan }) {
  const plan = testPlanOf(skill);
  const [rows, setRows] = useState(plan.trigger);
  const [text, setText] = useState("");
  const changed = JSON.stringify(rows) !== JSON.stringify(plan.trigger);
  const pairs = testers.flatMap((t) =>
    t.envs.map((env) => ({ agent: t.agent, env })),
  );
  const withScenarios = pairs.filter((p) => p.env.relevant);
  const without = pairs.length - withScenarios.length;
  /* Other skills whose words overlap this one's could fire on the same
     request (26 Oct plan: interaction cases). */
  const words = (s) =>
    new Set(
      `${s.name.replace(/-/g, " ")} ${s.description}`
        .toLowerCase()
        .match(/[a-z]{5,}/g) || [],
    );
  const mine = words(skill);
  const clashes = library
    .filter((o) => o.id !== skill.id)
    .map((o) => ({ o, shared: [...words(o)].filter((w) => mine.has(w)) }))
    .filter((x) => x.shared.length);
  return (
    <Stack spacing={2.5}>
      <Box>
        <Stack
          direction="row"
          alignItems="baseline"
          spacing={1}
          sx={{ mb: 0.75 }}
        >
          <Typography sx={{ typography: "s2", fontWeight: 600 }}>
            Trigger requests
          </Typography>
          <Typography
            sx={{ typography: "s3", color: "text.tertiary", flex: 1 }}
          >
            Each runs 3 times; near-misses make the best should-not cases
          </Typography>
          {changed && (
            <Button
              size="small"
              variant="outlined"
              onClick={() => onSavePlan({ ...plan, trigger: rows })}
            >
              Save requests
            </Button>
          )}
        </Stack>
        <Box sx={tableBox}>
          <Box
            component="table"
            sx={{ width: "100%", borderCollapse: "collapse" }}
          >
            <thead>
              <tr>
                {["Request", "Should fire", ""].map((h) => (
                  <Box component="th" key={h} sx={head}>
                    {h}
                  </Box>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((r, i) => (
                <tr key={r.text}>
                  <Box component="td" sx={cell}>
                    {r.text}
                  </Box>
                  <Box component="td" sx={{ ...cell, width: 140 }}>
                    <TextField
                      select
                      size="small"
                      value={r.should ? "yes" : "no"}
                      onChange={(e) =>
                        setRows((rs) =>
                          rs.map((x, j) =>
                            j === i
                              ? { ...x, should: e.target.value === "yes" }
                              : x,
                          ),
                        )
                      }
                      inputProps={{ "aria-label": "Should fire" }}
                      sx={{
                        "& .MuiSelect-select": { typography: "s2", py: 0.25 },
                      }}
                    >
                      <MenuItem value="yes">Should</MenuItem>
                      <MenuItem value="no">Should not</MenuItem>
                    </TextField>
                  </Box>
                  <Box component="td" sx={{ ...cell, width: 40 }}>
                    <IconButton
                      size="small"
                      aria-label="Remove request"
                      onClick={() =>
                        setRows((rs) => rs.filter((_, j) => j !== i))
                      }
                    >
                      <Iconify
                        icon="solar:trash-bin-minimalistic-linear"
                        width={14}
                      />
                    </IconButton>
                  </Box>
                </tr>
              ))}
              <tr>
                <Box component="td" colSpan={3} sx={cell}>
                  <TextField
                    size="small"
                    fullWidth
                    value={text}
                    placeholder="Add a request and press Enter"
                    onChange={(e) => setText(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key !== "Enter" || !text.trim()) return;
                      setRows((rs) => [
                        ...rs,
                        { text: text.trim(), should: true },
                      ]);
                      setText("");
                    }}
                    inputProps={{ "aria-label": "Add a request" }}
                    sx={{ "& input": { typography: "s2", py: 0.5 } }}
                  />
                </Box>
              </tr>
            </tbody>
          </Box>
        </Box>
      </Box>
      <Box>
        <Stack
          direction="row"
          alignItems="baseline"
          spacing={1}
          sx={{ mb: 0.75 }}
        >
          <Typography sx={{ typography: "s2", fontWeight: 600 }}>
            Scenarios
          </Typography>
          <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
            Only scenarios about this skill run in a test
          </Typography>
        </Stack>
        <Box sx={tableBox}>
          <Box
            component="table"
            sx={{ width: "100%", borderCollapse: "collapse" }}
          >
            <thead>
              <tr>
                {["Agent", "Environment", "Scenarios about this skill"].map(
                  (h) => (
                    <Box component="th" key={h} sx={head}>
                      {h}
                    </Box>
                  ),
                )}
              </tr>
            </thead>
            <tbody>
              {withScenarios.length ? (
                withScenarios.map(({ agent, env }) => (
                  <tr key={`${agent.key}-${env.id}`}>
                    <Box component="td" sx={cell}>
                      {agent.name}
                    </Box>
                    <Box component="td" sx={cell}>
                      {env.name}
                    </Box>
                    <Box
                      component="td"
                      sx={{ ...cell, fontVariantNumeric: "tabular-nums" }}
                    >
                      {env.relevant}
                    </Box>
                  </tr>
                ))
              ) : (
                <tr>
                  <Box
                    component="td"
                    colSpan={3}
                    sx={{ ...cell, color: "text.tertiary" }}
                  >
                    No environment has scenarios about this skill yet.
                  </Box>
                </tr>
              )}
            </tbody>
          </Box>
        </Box>
        {without > 0 && (
          <Typography
            sx={{ typography: "s3", color: "text.tertiary", mt: 0.75 }}
          >
            {`${without} other agent–environment ${without === 1 ? "pair has" : "pairs have"} none about this skill.`}
          </Typography>
        )}
      </Box>
      <Box>
        <Stack
          direction="row"
          alignItems="baseline"
          spacing={1}
          sx={{ mb: 0.75 }}
        >
          <Typography sx={{ typography: "s2", fontWeight: 600 }}>
            Could clash with
          </Typography>
          <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
            Library skills that may fire on the same requests
          </Typography>
        </Stack>
        <Typography
          sx={{
            typography: "s2",
            color: clashes.length ? "text.primary" : "text.tertiary",
          }}
        >
          {clashes.length
            ? clashes
                .map(
                  (x) =>
                    `${x.o.name} (shares “${x.shared.slice(0, 2).join("”, “")}”)`,
                )
                .join(" · ")
            : "No overlap with other library skills"}
        </Typography>
      </Box>
    </Stack>
  );
}
TestsTab.propTypes = {
  skill: PropTypes.object.isRequired,
  library: PropTypes.array.isRequired,
  testers: PropTypes.array.isRequired,
  onSavePlan: PropTypes.func.isRequired,
};

/* ── Results: this version vs without the skill vs production ──────────── */
function ResultsTab({ skill, onTest }) {
  const tested = [...skill.versions]
    .reverse()
    .filter((x) => skill.tests?.[x.v]);
  const [v, setV] = useState(tested[0]?.v || latestV(skill));
  const t = skill.tests?.[v];
  if (!t)
    return (
      <Stack spacing={1} alignItems="flex-start">
        <Typography sx={{ typography: "s2", color: "text.secondary" }}>
          {`v${v} hasn't been tested yet.`}
        </Typography>
        <Button size="small" variant="outlined" onClick={() => onTest(v)}>
          {`Test v${v}`}
        </Button>
      </Stack>
    );
  const vd = verdictOf(t);
  const base = t.against ? "prod" : "without";
  const cases = [...(t.cases || [])].sort((a, b) => {
    const rank = (c) => (c[base] && !c.cand ? 0 : !c[base] && c.cand ? 1 : 2);
    return rank(a) - rank(b);
  });
  const should = (t.trigger || []).filter((x) => x.should);
  const shouldNot = (t.trigger || []).filter((x) => !x.should);
  const firedOk = should.filter((x) => x.fired >= 2).length;
  const quietOk = shouldNot.filter((x) => x.fired === 0).length;
  const delta = t.rates
    ? t.rates.cand - (t.against ? t.rates.prod : t.rates.without)
    : null;
  return (
    <Stack spacing={2}>
      <Stack direction="row" spacing={1} alignItems="center">
        <TextField
          select
          size="small"
          value={v}
          onChange={(e) => setV(Number(e.target.value))}
          inputProps={{ "aria-label": "Tested version" }}
          sx={{
            minWidth: 88,
            "& .MuiSelect-select": { typography: "s2", py: 0.5 },
          }}
        >
          {tested.map((x) => (
            <MenuItem key={x.v} value={x.v}>{`v${x.v}`}</MenuItem>
          ))}
        </TextField>
        <Typography sx={{ typography: "s2", flex: 1 }}>
          {`v${v} vs ${t.against ? `production v${t.against}` : "no skill"}`}
          <Box
            component="span"
            sx={{ color: TONE[vd.tone], fontWeight: 600 }}
          >{` · ${vd.word}`}</Box>
          {delta != null && (
            <Box component="span" sx={{ color: "text.secondary" }}>
              {` · ${delta >= 0 ? "+" : ""}${delta} pts in ${t.envName}`}
              {vd.improved != null &&
                `, improved ${vd.improved}, regressed ${vd.regressed}`}
            </Box>
          )}
        </Typography>
        <Typography
          sx={{ typography: "s3", color: "text.tertiary" }}
        >{`${t.agentName || ""} · ${ago(t.at)}`}</Typography>
      </Stack>

      {t.rates && (
        <Box
          sx={{
            ...tableBox,
            display: "grid",
            gridTemplateColumns: "repeat(5, minmax(0,1fr))",
          }}
        >
          {[
            ["This version", `${t.rates.cand}%`],
            [
              t.against ? `Production v${t.against}` : "Without the skill",
              `${t.against ? t.rates.prod : t.rates.without}%`,
            ],
            ...(t.against
              ? [["Without the skill", `${t.rates.without}%`]]
              : []),
            [
              "Trigger",
              `${firedOk}/${should.length} fire · ${quietOk}/${shouldNot.length} quiet`,
            ],
            ["Loaded", t.loaded === "observed" ? "Observed" : "Unknown"],
            ...(t.against
              ? []
              : [["Cost per trial", `$${t.cost.cand.toFixed(3)}`]]),
          ].map(([k, val], i) => (
            <Box
              key={k}
              sx={{
                px: 1.5,
                py: 1,
                borderLeft: i ? "1px solid" : "none",
                borderColor: "divider",
              }}
            >
              <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
                {k}
              </Typography>
              <Typography
                sx={{
                  typography: "s1",
                  fontWeight: 600,
                  fontVariantNumeric: "tabular-nums",
                }}
              >
                {val}
              </Typography>
            </Box>
          ))}
        </Box>
      )}

      {cases.length > 0 && (
        <Box sx={tableBox}>
          <Box
            component="table"
            sx={{ width: "100%", borderCollapse: "collapse" }}
          >
            <thead>
              <tr>
                {[
                  "Scenario",
                  "Environment",
                  "Without skill",
                  ...(t.against ? [`Production v${t.against}`] : []),
                  `v${v}`,
                  "",
                ].map((h) => (
                  <Box component="th" key={h} sx={head}>
                    {h}
                  </Box>
                ))}
              </tr>
            </thead>
            <tbody>
              {cases.map((c) => {
                const regressed = c[base] && !c.cand;
                const improved = !c[base] && c.cand;
                const mark = (x) => (x == null ? "—" : x ? "Pass" : "Fail");
                return (
                  <tr key={`${c.env}-${c.id}`}>
                    <Box
                      component="td"
                      sx={{
                        ...cell,
                        boxShadow: regressed
                          ? (th) => `inset 2px 0 0 ${th.palette.error.main}`
                          : "none",
                      }}
                    >
                      {c.title}
                    </Box>
                    <Box
                      component="td"
                      sx={{ ...cell, color: "text.secondary" }}
                    >
                      {c.env}
                    </Box>
                    <Box
                      component="td"
                      sx={{ ...cell, color: "text.secondary" }}
                    >
                      {mark(c.without)}
                    </Box>
                    {t.against ? (
                      <Box
                        component="td"
                        sx={{ ...cell, color: "text.secondary" }}
                      >
                        {mark(c.prod)}
                      </Box>
                    ) : null}
                    <Box component="td" sx={cell}>
                      {mark(c.cand)}
                    </Box>
                    <Box
                      component="td"
                      sx={{
                        ...cell,
                        color: regressed
                          ? "error.main"
                          : improved
                            ? "success.main"
                            : "text.tertiary",
                      }}
                    >
                      {regressed ? "Regressed" : improved ? "Improved" : "Same"}
                    </Box>
                  </tr>
                );
              })}
            </tbody>
          </Box>
        </Box>
      )}

      {(t.trigger || []).length > 0 && (
        <Box sx={tableBox}>
          <Box
            component="table"
            sx={{ width: "100%", borderCollapse: "collapse" }}
          >
            <thead>
              <tr>
                {["Request", "Should fire", "Fired", ""].map((h) => (
                  <Box component="th" key={h} sx={head}>
                    {h}
                  </Box>
                ))}
              </tr>
            </thead>
            <tbody>
              {t.trigger.map((r) => {
                const ok = r.should ? r.fired >= 2 : r.fired === 0;
                return (
                  <tr key={r.text}>
                    <Box component="td" sx={cell}>
                      {r.text}
                    </Box>
                    <Box
                      component="td"
                      sx={{ ...cell, color: "text.secondary" }}
                    >
                      {r.should ? "Should" : "Should not"}
                    </Box>
                    <Box
                      component="td"
                      sx={{ ...cell, fontVariantNumeric: "tabular-nums" }}
                    >{`${r.fired} of 3`}</Box>
                    <Box
                      component="td"
                      sx={{
                        ...cell,
                        color: ok ? "text.tertiary" : "error.main",
                      }}
                    >
                      {ok
                        ? "As expected"
                        : r.should
                          ? "Missed"
                          : "Fired when it shouldn't"}
                    </Box>
                  </tr>
                );
              })}
            </tbody>
          </Box>
        </Box>
      )}
      {!t.cases && (
        <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
          Recorded before per-scenario results were kept. Test again to see
          them.
        </Typography>
      )}
    </Stack>
  );
}
ResultsTab.propTypes = {
  skill: PropTypes.object.isRequired,
  onTest: PropTypes.func.isRequired,
};

/* ── Used by: which agents load it, pinned or following a label ────────── */
function UsedByTab({ skill, settings, agents, onSave }) {
  const used = usedByOf(skill.id, settings);
  const nameOf = (key) => agents.find((a) => a.key === key)?.name || "An agent";
  const free = agents.filter(
    (a) => skillRuntimeOf(a).canAdd && !used.some((u) => u.agentKey === a.key),
  );
  const [key, setKey] = useState("");
  const [ref, setRef] = useState(
    skill.tags?.production ? "production" : latestV(skill),
  );
  const refs = [
    ...Object.keys(skill.tags || {}).map((t) => ({
      value: t,
      label: `Follows ${t} (now v${skill.tags[t]})`,
    })),
    ...[...skill.versions]
      .reverse()
      .map((x) => ({ value: x.v, label: `Pinned to v${x.v}` })),
  ];
  return (
    <Stack spacing={1.5}>
      <Box sx={tableBox}>
        <Box
          component="table"
          sx={{ width: "100%", borderCollapse: "collapse" }}
        >
          <thead>
            <tr>
              {["Agent", "Gets", "Loaded", ""].map((h) => (
                <Box component="th" key={h} sx={head}>
                  {h}
                </Box>
              ))}
            </tr>
          </thead>
          <tbody>
            {used.map((u) => {
              const agent = agents.find((a) => a.key === u.agentKey);
              return (
                <tr key={u.agentKey}>
                  <Box component="td" sx={cell}>
                    {nameOf(u.agentKey)}
                  </Box>
                  <Box component="td" sx={cell}>
                    {`v${resolveRef(skill, u.ref)}`}
                    <Box component="span" sx={{ color: "text.tertiary" }}>
                      {typeof u.ref === "number"
                        ? " · pinned"
                        : ` · follows ${u.ref}`}
                    </Box>
                  </Box>
                  <Box component="td" sx={{ ...cell, color: "text.secondary" }}>
                    {skillRuntimeOf(agent).observed ? "Observed" : "Unknown"}
                  </Box>
                  <Box component="td" sx={{ ...cell, textAlign: "right" }}>
                    <Button
                      size="small"
                      color="inherit"
                      onClick={() =>
                        onSave(detach(settings, u.agentKey, skill.id))
                      }
                    >
                      Detach
                    </Button>
                  </Box>
                </tr>
              );
            })}
            <tr>
              <Box component="td" sx={cell}>
                <TextField
                  select
                  size="small"
                  value={key}
                  onChange={(e) => setKey(e.target.value)}
                  SelectProps={{ displayEmpty: true }}
                  inputProps={{ "aria-label": "Agent to attach" }}
                  sx={{
                    minWidth: 220,
                    "& .MuiSelect-select": { typography: "s2", py: 0.5 },
                  }}
                >
                  <MenuItem value="" disabled>
                    {free.length
                      ? "Attach to an agent…"
                      : "No other agent can load skills"}
                  </MenuItem>
                  {free.map((a) => (
                    <MenuItem key={a.key} value={a.key}>
                      {a.name}
                    </MenuItem>
                  ))}
                </TextField>
              </Box>
              <Box component="td" sx={cell}>
                <TextField
                  select
                  size="small"
                  value={ref}
                  onChange={(e) => setRef(e.target.value)}
                  inputProps={{ "aria-label": "Version or label" }}
                  sx={{
                    minWidth: 200,
                    "& .MuiSelect-select": { typography: "s2", py: 0.5 },
                  }}
                >
                  {refs.map((r) => (
                    <MenuItem key={String(r.value)} value={r.value}>
                      {r.label}
                    </MenuItem>
                  ))}
                </TextField>
              </Box>
              <Box component="td" sx={cell} />
              <Box component="td" sx={{ ...cell, textAlign: "right" }}>
                <Button
                  size="small"
                  variant="outlined"
                  disabled={!key}
                  onClick={() => {
                    onSave(attach(settings, key, skill.id, ref));
                    setKey("");
                  }}
                >
                  Attach
                </Button>
              </Box>
            </tr>
          </tbody>
        </Box>
      </Box>
      <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
        Agents following a label get the version it points to on their next run;
        pinned agents stay put. Runs record the exact version they loaded.
      </Typography>
    </Stack>
  );
}
UsedByTab.propTypes = {
  skill: PropTypes.object.isRequired,
  settings: PropTypes.object,
  agents: PropTypes.array.isRequired,
  onSave: PropTypes.func.isRequired,
};

/* ── Test a version: agent, environments, the three arms ───────────────── */
function TestDrawer({ skill, v, testers, onClose, onStart }) {
  const [version, setVersion] = useState(v);
  /* Start on an agent whose environments have scenarios about the skill. */
  const [agentKey, setAgentKey] = useState(
    (testers.find((t) => t.envs.some((e) => e.relevant)) || testers[0])?.agent
      .key || "",
  );
  const tester = testers.find((t) => t.agent.key === agentKey);
  const [picked, setPicked] = useState(() =>
    (tester?.envs || []).filter((e) => e.relevant).map((e) => e.id),
  );
  const [running, setRunning] = useState(false);
  const prod = skill.tags?.production;
  const envs = (tester?.envs || []).filter((e) => picked.includes(e.id));
  const plan = testPlanOf(skill);
  useEffect(() => {
    if (!running) return undefined;
    const t = setTimeout(() => onStart({ v: version, tester, envs }), 2200);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [running]);
  const arms = [
    `This version · v${version}`,
    "Without the skill",
    ...(prod && prod !== version ? [`Production · v${prod}`] : []),
  ];
  return (
    <Stack sx={{ height: "100%" }}>
      <Stack
        direction="row"
        alignItems="center"
        spacing={1.5}
        sx={{ px: 2.5, py: 2, borderBottom: 1, borderColor: "divider" }}
      >
        <Box flex={1} minWidth={0}>
          <Typography
            sx={{ typography: "m2", fontWeight: 600 }}
          >{`Test ${skill.name}`}</Typography>
          <Typography sx={{ typography: "s2", color: "text.tertiary" }}>
            Same scenarios, run each way, compared
          </Typography>
        </Box>
        <IconButton size="small" onClick={onClose} aria-label="Close">
          <Iconify icon="solar:close-linear" width={16} />
        </IconButton>
      </Stack>
      <Stack
        spacing={2}
        sx={{ px: 2.5, py: 2, flex: 1, minHeight: 0, overflowY: "auto" }}
      >
        {[
          [
            "Version",
            <TextField
              key="v"
              select
              size="small"
              value={version}
              onChange={(e) => setVersion(Number(e.target.value))}
              inputProps={{ "aria-label": "Version" }}
              sx={{ minWidth: 120 }}
            >
              {[...skill.versions].reverse().map((x) => (
                <MenuItem key={x.v} value={x.v}>{`v${x.v}`}</MenuItem>
              ))}
            </TextField>,
          ],
          [
            "Agent",
            <TextField
              key="a"
              select
              size="small"
              value={agentKey}
              onChange={(e) => {
                setAgentKey(e.target.value);
                const next = testers.find(
                  (t) => t.agent.key === e.target.value,
                );
                setPicked(
                  (next?.envs || []).filter((x) => x.relevant).map((x) => x.id),
                );
              }}
              inputProps={{ "aria-label": "Agent" }}
              sx={{ minWidth: 260 }}
            >
              {testers.map((t) => (
                <MenuItem key={t.agent.key} value={t.agent.key}>
                  {t.agent.name}
                </MenuItem>
              ))}
            </TextField>,
          ],
        ].map(([k, control]) => (
          <Stack key={k} direction="row" spacing={1} alignItems="center">
            <Typography
              sx={{ typography: "s2", color: "text.secondary", width: 96 }}
            >
              {k}
            </Typography>
            {control}
          </Stack>
        ))}
        <Box>
          <Typography
            sx={{ typography: "s2", color: "text.secondary", mb: 0.5 }}
          >
            Environments
          </Typography>
          <Box sx={tableBox}>
            {(tester?.envs || []).map((e) => (
              <Stack
                key={e.id}
                direction="row"
                alignItems="center"
                spacing={1}
                sx={{
                  px: 1,
                  py: 0.5,
                  borderTop: "1px solid",
                  borderColor: "divider",
                  "&:first-of-type": { borderTop: "none" },
                }}
              >
                <Checkbox
                  size="small"
                  disabled={!e.relevant}
                  checked={picked.includes(e.id)}
                  onChange={(ev) =>
                    setPicked((p) =>
                      ev.target.checked
                        ? [...p, e.id]
                        : p.filter((x) => x !== e.id),
                    )
                  }
                  inputProps={{ "aria-label": e.name }}
                />
                <Typography sx={{ typography: "s2", flex: 1 }}>
                  {e.name}
                </Typography>
                <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
                  {e.relevant
                    ? `${e.relevant} ${e.relevant === 1 ? "scenario" : "scenarios"} about this skill`
                    : "None about this skill"}
                </Typography>
              </Stack>
            ))}
          </Box>
        </Box>
        <Box>
          <Typography
            sx={{ typography: "s2", color: "text.secondary", mb: 0.5 }}
          >
            Runs each scenario
          </Typography>
          <Typography sx={{ typography: "s2" }}>{arms.join(" · ")}</Typography>
          <Typography
            sx={{ typography: "s3", color: "text.tertiary", mt: 0.5 }}
          >
            {`${plan.trigger.length} trigger requests × 3 runs · ${skillRuntimeOf(tester?.agent).observed ? "loading is observed" : "loading shows as Unknown on this runtime"}`}
          </Typography>
        </Box>
      </Stack>
      <Stack
        direction="row"
        justifyContent="flex-end"
        spacing={1}
        sx={{ px: 2.5, py: 1.5, borderTop: 1, borderColor: "divider" }}
      >
        <Button
          size="small"
          variant="outlined"
          onClick={onClose}
          sx={{ color: "text.primary", borderColor: "divider" }}
        >
          Cancel
        </Button>
        <Button
          size="small"
          variant="contained"
          color="primary"
          disabled={!envs.length || running}
          onClick={() => setRunning(true)}
          startIcon={<Iconify icon="solar:play-bold" width={14} />}
        >
          {running ? "Testing…" : "Start test"}
        </Button>
      </Stack>
    </Stack>
  );
}
TestDrawer.propTypes = {
  skill: PropTypes.object.isRequired,
  v: PropTypes.number,
  testers: PropTypes.array.isRequired,
  onClose: PropTypes.func.isRequired,
  onStart: PropTypes.func.isRequired,
};

/* ── Promote: the evidence, who it reaches, and the reason if untested ── */
function PromoteDialog({ skill, settings, agents, onClose, onPromote }) {
  const [tag, setTag] = useState("production");
  const [v, setV] = useState(skill.tags?.staging || latestV(skill));
  const [reason, setReason] = useState("");
  const t = skill.tests?.[v];
  const vd = verdictOf(t);
  const used = usedByOf(skill.id, settings);
  const nameOf = (key) => agents.find((a) => a.key === key)?.name || "An agent";
  const following = used.filter((u) => u.ref === tag);
  const pinned = used.filter((u) => typeof u.ref === "number");
  const same = skill.tags?.[tag] === v;
  return (
    <ClosableDialog
      open
      onClose={onClose}
      PaperProps={{ sx: { maxWidth: 520, width: "100%" } }}
    >
      <DialogTitle
        sx={{ typography: "m2" }}
      >{`Promote ${skill.name}`}</DialogTitle>
      <DialogContent>
        <Stack spacing={1.75}>
          <Stack direction="row" spacing={1} alignItems="center">
            <TextField
              select
              size="small"
              value={v}
              onChange={(e) => setV(Number(e.target.value))}
              inputProps={{ "aria-label": "Version" }}
            >
              {[...skill.versions].reverse().map((x) => (
                <MenuItem key={x.v} value={x.v}>{`v${x.v}`}</MenuItem>
              ))}
            </TextField>
            <Typography sx={{ typography: "s2", color: "text.secondary" }}>
              to
            </Typography>
            <TextField
              select
              size="small"
              value={tag}
              onChange={(e) => setTag(e.target.value)}
              inputProps={{ "aria-label": "Label" }}
            >
              <MenuItem value="production">production</MenuItem>
              <MenuItem value="staging">staging</MenuItem>
            </TextField>
            {skill.tags?.[tag] && (
              <Typography
                sx={{ typography: "s3", color: "text.tertiary" }}
              >{`now v${skill.tags[tag]}`}</Typography>
            )}
          </Stack>
          <Box sx={{ ...tableBox, px: 1.5, py: 1 }}>
            <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
              Evidence
            </Typography>
            {t ? (
              <Typography sx={{ typography: "s2" }}>
                <Box
                  component="span"
                  sx={{ color: TONE[vd.tone], fontWeight: 600 }}
                >
                  {vd.word}
                </Box>
                {` · v${v} vs ${t.against ? `production v${t.against}` : "no skill"} in ${t.envName}`}
                {t.rates &&
                  ` · ${t.rates.cand}% vs ${t.against ? t.rates.prod : t.rates.without}%`}
                {vd.regressed ? ` · ${vd.regressed} regressed` : ""}
              </Typography>
            ) : (
              <Typography
                sx={{ typography: "s2", color: "warning.main" }}
              >{`v${v} hasn't been tested`}</Typography>
            )}
          </Box>
          <Box>
            <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
              Who it reaches
            </Typography>
            <Typography sx={{ typography: "s2" }}>
              {following.length
                ? `${following.map((u) => nameOf(u.agentKey)).join(", ")} ${following.length === 1 ? "follows" : "follow"} ${tag} and ${following.length === 1 ? "gets" : "get"} v${v} on the next run.`
                : `No agent follows ${tag} yet.`}
              {pinned.length
                ? ` ${pinned.length} pinned ${pinned.length === 1 ? "agent stays" : "agents stay"} where ${pinned.length === 1 ? "it is" : "they are"}.`
                : ""}
            </Typography>
          </Box>
          {!t && (
            <TextField
              size="small"
              label="Why it goes out untested"
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              multiline
              minRows={2}
              helperText="Kept with the promotion, on the version's row"
            />
          )}
        </Stack>
      </DialogContent>
      <DialogActions sx={{ px: 3, pb: 2 }}>
        <Button
          size="small"
          variant="outlined"
          onClick={onClose}
          sx={{ color: "text.primary", borderColor: "divider" }}
        >
          Cancel
        </Button>
        <Button
          size="small"
          variant="contained"
          color="primary"
          disabled={same || (!t && !reason.trim())}
          onClick={() => onPromote(v, tag, t ? null : reason.trim())}
        >
          {same ? `Already ${tag}` : t ? `Promote to ${tag}` : "Promote anyway"}
        </Button>
      </DialogActions>
    </ClosableDialog>
  );
}
PromoteDialog.propTypes = {
  skill: PropTypes.object.isRequired,
  settings: PropTypes.object,
  agents: PropTypes.array.isRequired,
  onClose: PropTypes.func.isRequired,
  onPromote: PropTypes.func.isRequired,
};

/* ── Save a version: the diff and a message ────────────────────────────── */
function SaveVersionDialog({ skill, fromV, draft, onClose, onSave }) {
  const [note, setNote] = useState("");
  const [testAfter, setTestAfter] = useState(true);
  const base = skill.versions.find((x) => x.v === fromV);
  const changes = filesDiff(filesOf(base), draft);
  return (
    <ClosableDialog
      open
      onClose={onClose}
      PaperProps={{ sx: { maxWidth: 640, width: "100%" } }}
    >
      <DialogTitle
        sx={{ typography: "m2" }}
      >{`Save v${latestV(skill) + 1}`}</DialogTitle>
      <DialogContent>
        <Stack spacing={1.5}>
          {changes.length ? (
            changes.map((f) => (
              <Box key={f.path}>
                <Typography
                  sx={{ ...mono, typography: "s3", mb: 0.25 }}
                >{`${f.path} · ${f.change}`}</Typography>
                <DiffLines lines={f.lines} />
              </Box>
            ))
          ) : (
            <Typography
              sx={{ typography: "s2", color: "text.tertiary" }}
            >{`Nothing changed from v${fromV}.`}</Typography>
          )}
          <TextField
            size="small"
            label="What changed"
            value={note}
            onChange={(e) => setNote(e.target.value)}
          />
          <Stack direction="row" alignItems="center">
            <Checkbox
              size="small"
              checked={testAfter}
              onChange={(e) => setTestAfter(e.target.checked)}
              inputProps={{ "aria-label": "Test it after saving" }}
            />
            <Typography sx={{ typography: "s2" }}>
              Test it after saving
            </Typography>
          </Stack>
        </Stack>
      </DialogContent>
      <DialogActions sx={{ px: 3, pb: 2 }}>
        <Button
          size="small"
          variant="outlined"
          onClick={onClose}
          sx={{ color: "text.primary", borderColor: "divider" }}
        >
          Cancel
        </Button>
        <Button
          size="small"
          variant="contained"
          color="primary"
          disabled={!changes.length || !note.trim()}
          onClick={() => onSave(note.trim(), testAfter)}
        >
          Save version
        </Button>
      </DialogActions>
    </ClosableDialog>
  );
}
SaveVersionDialog.propTypes = {
  skill: PropTypes.object.isRequired,
  fromV: PropTypes.number,
  draft: PropTypes.array.isRequired,
  onClose: PropTypes.func.isRequired,
  onSave: PropTypes.func.isRequired,
};

export default function SkillPage() {
  const { skillId } = useParams();
  const navigate = useNavigate();
  const { state, dispatch } = useSimStore();
  const { agents } = useConnectedAgents();
  const settings = state.workspaceSettings;
  const library = useMemo(() => libraryOf(settings), [settings]);
  const skill = library.find((s) => s.id === skillId);
  const [params, setParams] = useSearchParams();
  const tab = TABS.includes(params.get("tab")) ? params.get("tab") : "files";
  const setTab = (v) => {
    const next = new URLSearchParams(params);
    if (v === "files") next.delete("tab");
    else next.set("tab", v);
    setParams(next, { replace: true });
  };
  const [testing, setTesting] = useState(null);
  const [promoting, setPromoting] = useState(false);
  const [saving, setSaving] = useState(null);
  const save = (patch) =>
    Object.entries(patch).forEach(([key, value]) => {
      if (key === "skill") return;
      dispatch({ type: "setWorkspaceSetting", key, value });
    });

  /* Agents that can load skills, each with the environments it runs in and
     how many of their scenarios are about this skill. */
  const testers = useMemo(() => {
    if (!skill) return [];
    const envs = state.myEnvironments || [];
    return agents
      .filter((a) => skillRuntimeOf(a).canAdd)
      .map((a) => ({
        agent: a,
        envs: (a.usedIn || [])
          .map((u) => envs.find((e) => e.id === u.envId))
          .filter((e, i, arr) => e && arr.indexOf(e) === i)
          .map((e) => {
            const scenarios = relevantScenariosOf(
              skill,
              state.byEnv?.[e.id]?.scenarios || [],
            );
            return {
              id: e.id,
              name: e.name,
              relevant: scenarios.length,
              scenarios,
            };
          }),
      }))
      .filter((t) => t.envs.length);
  }, [agents, skill, state.myEnvironments, state.byEnv]);

  if (!skill)
    return (
      <Box sx={{ p: 3 }}>
        <Typography sx={{ typography: "s1" }}>
          This skill isn&apos;t in the library.
        </Typography>
        <Button
          size="small"
          sx={{ mt: 1 }}
          onClick={() => navigate(paths.dashboard.simulate.skills)}
        >
          Back to Skills
        </Button>
      </Box>
    );

  const prodV = skill.tags?.production;
  const last = [...skill.versions].reverse().find((x) => skill.tests?.[x.v]);
  const lastTest = last ? skill.tests[last.v] : null;
  const lastVerdict = verdictOf(lastTest);
  const used = usedByOf(skill.id, settings);

  return (
    <Box sx={{ px: 3, py: 2.5, height: "100%", overflow: "auto" }}>
      <Button
        size="small"
        color="inherit"
        startIcon={<Iconify icon="solar:arrow-left-linear" width={14} />}
        onClick={() => navigate(paths.dashboard.simulate.skills)}
        sx={{ color: "text.secondary", ml: -1, mb: 1 }}
      >
        Skills
      </Button>
      <Stack direction="row" alignItems="flex-start" spacing={2}>
        <Box sx={{ flex: 1, minWidth: 0 }}>
          <Stack direction="row" alignItems="center" spacing={1}>
            <Typography sx={{ ...mono, typography: "m2", fontWeight: 600 }}>
              {skill.name}
            </Typography>
            {Object.entries(skill.tags || {}).map(([t, tv]) => (
              <Box
                key={t}
                component="span"
                sx={{
                  ...mono,
                  typography: "s3",
                  px: 0.75,
                  py: 0.125,
                  borderRadius: "4px",
                  border: "1px solid",
                  borderColor: t === "production" ? "primary.main" : "divider",
                  color: t === "production" ? "primary.main" : "text.secondary",
                }}
              >
                {`${t} · v${tv}`}
              </Box>
            ))}
          </Stack>
          <Typography
            sx={{ typography: "s2", color: "text.secondary", mt: 0.25 }}
          >
            {skill.description}
          </Typography>
          <Typography
            sx={{ typography: "s3", color: "text.tertiary", mt: 0.5 }}
          >
            {[
              `${skill.owner}`,
              `${skill.versions.length} version${skill.versions.length === 1 ? "" : "s"}`,
              `${used.length} agent${used.length === 1 ? "" : "s"}`,
              lastTest
                ? `last test v${last.v}: ${lastVerdict.word}${lastTest.rates ? ` (${lastTest.rates.cand}% vs ${lastTest.against ? lastTest.rates.prod : lastTest.rates.without}%)` : ""} · ${lastTest.loaded === "observed" ? "Observed" : "Unknown"}`
                : "not tested",
            ].join(" · ")}
          </Typography>
        </Box>
        <Button
          size="small"
          variant="outlined"
          startIcon={<Iconify icon="solar:play-linear" width={14} />}
          onClick={() => setTesting(latestV(skill))}
        >
          Test
        </Button>
        <Button
          size="small"
          variant="contained"
          color="primary"
          onClick={() => setPromoting(true)}
        >
          Promote
        </Button>
      </Stack>

      <Box
        sx={{ borderBottom: "1px solid", borderColor: "divider", mt: 2, mb: 2 }}
      >
        <Tabs value={tab} onChange={(_, v) => setTab(v)} sx={TAB_SX}>
          <Tab value="files" label="Files" />
          <Tab value="versions" label={`Versions · ${skill.versions.length}`} />
          <Tab value="tests" label="Tests" />
          <Tab value="results" label="Results" />
          <Tab value="used" label={`Used by · ${used.length}`} />
        </Tabs>
      </Box>

      {tab === "files" && (
        <FilesTab
          key={skill.versions.length}
          skill={skill}
          onSaveVersion={(draft, fromV) => setSaving({ draft, fromV })}
        />
      )}
      {tab === "versions" && <VersionsTab skill={skill} />}
      {tab === "tests" && (
        <TestsTab
          skill={skill}
          library={library}
          testers={testers}
          onSavePlan={(plan) => save(saveTestPlan(settings, skill.id, plan))}
        />
      )}
      {tab === "results" && (
        <ResultsTab
          key={JSON.stringify(Object.keys(skill.tests || {}))}
          skill={skill}
          onTest={(v) => setTesting(v)}
        />
      )}
      {tab === "used" && (
        <UsedByTab
          skill={skill}
          settings={settings}
          agents={agents}
          onSave={save}
        />
      )}
      {prodV == null && tab === "files" && (
        <Typography sx={{ typography: "s3", color: "text.tertiary", mt: 1 }}>
          No version is in production yet. Agents attached to a label get
          nothing until a version is promoted.
        </Typography>
      )}

      <SideDrawer
        open={testing != null}
        onClose={() => setTesting(null)}
        width={520}
      >
        {testing != null && (
          <TestDrawer
            skill={skill}
            v={testing}
            testers={testers}
            onClose={() => setTesting(null)}
            onStart={({ v, tester, envs }) => {
              save(
                runSkillTest(settings, skill.id, v, {
                  agentKey: tester.agent.key,
                  agentName: tester.agent.name,
                  observed: !!skillRuntimeOf(tester.agent).observed,
                  envs: envs.map((e) => ({
                    id: e.id,
                    name: e.name,
                    scenarios: e.scenarios.map((x) => ({
                      id: x.id,
                      title: x.title,
                    })),
                  })),
                }),
              );
              setTesting(null);
              setTab("results");
            }}
          />
        )}
      </SideDrawer>
      {promoting && (
        <PromoteDialog
          skill={skill}
          settings={settings}
          agents={agents}
          onClose={() => setPromoting(false)}
          onPromote={(v, tag, reason) => {
            save(promote(settings, skill.id, v, tag, reason));
            setPromoting(false);
          }}
        />
      )}
      {saving && (
        <SaveVersionDialog
          skill={skill}
          fromV={saving.fromV}
          draft={saving.draft}
          onClose={() => setSaving(null)}
          onSave={(note, testAfter) => {
            const [md, ...files] = saving.draft;
            save(
              newVersion(settings, skill.id, { text: md.text, note, files }),
            );
            setSaving(null);
            if (testAfter) setTesting(latestV(skill) + 1);
          }}
        />
      )}
    </Box>
  );
}
