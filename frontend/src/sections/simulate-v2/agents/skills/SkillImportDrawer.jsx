import PropTypes from "prop-types";
import { useEffect, useRef, useState } from "react";
import {
  Box,
  Button,
  IconButton,
  Stack,
  TextField,
  ToggleButton,
  ToggleButtonGroup,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import SideDrawer from "../../components/SideDrawer";
import CopyCommandButton from "../../components/CopyCommandButton";
import {
  REPO_EXAMPLES,
  latestOf,
  readSkillFolder,
  readUploadedFiles,
  skillImportCli,
} from "../../_mock/skills";
import { MONO } from "../agentViewUtils";
import { segmentedToggleSx } from "../segmentedToggleSx";
import SkillFiles from "./SkillFiles";
import SkillFacts, { FactBox, FactRow } from "./SkillFacts";

const label = { typography: "s3", color: "text.tertiary", mb: 0.5 };

/**
 * Import a skill (skills.md "Import and inspect"): a folder from the
 * agent's repository at a revision, or one uploaded. Reading it shows
 * SKILL.md, scripts, references and assets, what it asks for, and how this
 * agent would load it. An import error keeps the source so a repair can
 * retry it. Importing adds it to the agent's skills, not loaded — it's
 * not proof it helps.
 */
export default function SkillImportDrawer({
  open,
  onClose,
  agent,
  set,
  runtime,
  agentTools,
  startPath,
  onImport,
}) {
  const [mode, setMode] = useState("repo");
  const [path, setPath] = useState("");
  const [rev, setRev] = useState("main");
  const [upload, setUpload] = useState(null);
  const [repairs, setRepairs] = useState({});
  const [result, setResult] = useState(null);
  const [skillMd, setSkillMd] = useState("");
  const input = useRef(null);

  useEffect(() => {
    if (!open) return;
    setMode("repo");
    setPath(startPath || "");
    setRev("main");
    setUpload(null);
    setRepairs({});
    setResult(null);
  }, [open, startPath]);

  if (!open) return <SideDrawer open={false} onClose={onClose} />;

  const repo = agent?.values?.repoUrl || "the agent's repository";
  const source =
    mode === "repo"
      ? { kind: "repo", path: path.replace(/\/+$/, ""), rev: rev || "main" }
      : { kind: "upload", name: upload?.name };
  /* A result, with SKILL.md's text ready to edit when its metadata is wrong. */
  const show = (r) => {
    setResult(r);
    if (!r.ok && r.error.skillMd != null) setSkillMd(r.error.skillMd);
  };
  const read = (next = repairs) => {
    setRepairs(next);
    const r = readSkillFolder(
      mode === "repo" ? { path: source.path } : { files: upload?.files },
      next,
    );
    show(r);
  };
  const pick = async (e) => {
    const picked = await readUploadedFiles(e.target.files);
    e.target.value = "";
    if (!picked.files.length) return;
    setUpload(picked);
    setRepairs({});
    show(readSkillFolder({ files: picked.files }, {}));
  };
  const existing = result?.ok
    ? set.skills.find((s) => s.id === result.meta.name)
    : null;
  const sourceLine =
    mode === "repo"
      ? `${source.path || "—"} @ ${source.rev} · ${repo}`
      : `Uploaded folder ${upload?.name || "—"}`;

  return (
    <SideDrawer open onClose={onClose} width={960}>
      <Stack sx={{ height: "100%", minHeight: 0 }}>
        <Stack
          direction="row"
          alignItems="flex-start"
          spacing={1}
          sx={{
            px: 2,
            py: 1.5,
            borderBottom: "1px solid",
            borderColor: "divider",
          }}
        >
          <Box flex={1} minWidth={0}>
            <Typography sx={{ typography: "m3", fontWeight: 600 }}>
              Import a skill
            </Typography>
            <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
              {`For ${agent?.name || "this agent"} · a folder with a SKILL.md, and any scripts, references and assets`}
            </Typography>
          </Box>
          <IconButton size="small" aria-label="Close" onClick={onClose}>
            <Iconify icon="mingcute:close-line" width={18} />
          </IconButton>
        </Stack>

        <Box sx={{ flex: 1, minHeight: 0, overflow: "auto", px: 2, py: 2 }}>
          <Stack spacing={2}>
            {/* Where the folder comes from. */}
            <Stack spacing={1.5}>
              <ToggleButtonGroup
                exclusive
                size="small"
                value={mode}
                onChange={(_, v) => {
                  if (!v) return;
                  setMode(v);
                  setResult(null);
                  setRepairs({});
                }}
                sx={(t) => ({
                  ...segmentedToggleSx(t),
                  alignSelf: "flex-start",
                })}
              >
                <ToggleButton value="repo">From the repository</ToggleButton>
                <ToggleButton value="upload">Upload a folder</ToggleButton>
              </ToggleButtonGroup>
              {mode === "repo" ? (
                <>
                  <Stack direction="row" spacing={1.5} alignItems="flex-end">
                    <Box flex={1}>
                      <Typography sx={label}>{`Path in ${repo}`}</Typography>
                      <TextField
                        size="small"
                        fullWidth
                        placeholder="skills/refund-retry"
                        value={path}
                        onChange={(e) => setPath(e.target.value)}
                        onKeyDown={(e) => e.key === "Enter" && path && read({})}
                        inputProps={{
                          style: { fontFamily: MONO, fontSize: 12 },
                        }}
                      />
                    </Box>
                    <Box sx={{ width: 160 }}>
                      <Typography sx={label}>Revision</Typography>
                      <TextField
                        size="small"
                        fullWidth
                        value={rev}
                        onChange={(e) => setRev(e.target.value)}
                        inputProps={{
                          style: { fontFamily: MONO, fontSize: 12 },
                        }}
                      />
                    </Box>
                    <Button
                      variant="contained"
                      color="primary"
                      size="small"
                      disabled={!path.trim()}
                      onClick={() => read({})}
                      sx={{ height: 36 }}
                    >
                      Read folder
                    </Button>
                  </Stack>
                  <Stack
                    direction="row"
                    spacing={1}
                    alignItems="center"
                    flexWrap="wrap"
                    useFlexGap
                  >
                    <Typography
                      sx={{ typography: "s3", color: "text.tertiary" }}
                    >
                      Skill folders found:
                    </Typography>
                    {REPO_EXAMPLES.map((p) => (
                      <Button
                        key={p}
                        size="small"
                        onClick={() => {
                          setPath(p);
                          setRepairs({});
                          show(readSkillFolder({ path: p }, {}));
                        }}
                        sx={{
                          fontFamily: MONO,
                          fontSize: 12,
                          fontWeight: 400,
                          color: "text.secondary",
                          minWidth: 0,
                          px: 0.75,
                        }}
                      >
                        {p}
                      </Button>
                    ))}
                  </Stack>
                </>
              ) : (
                <Stack direction="row" spacing={1.5} alignItems="center">
                  <input
                    ref={input}
                    type="file"
                    webkitdirectory=""
                    multiple
                    hidden
                    onChange={pick}
                  />
                  <Button
                    variant="outlined"
                    color="inherit"
                    size="small"
                    startIcon={
                      <Iconify icon="solar:folder-open-linear" width={16} />
                    }
                    onClick={() => input.current?.click()}
                  >
                    Choose folder
                  </Button>
                  <Typography
                    sx={{ typography: "s2", color: "text.secondary" }}
                  >
                    {upload
                      ? `${upload.name} · ${upload.files.length} file${upload.files.length === 1 ? "" : "s"}`
                      : "Pick the skill's folder. Its files are read in the browser and shown before anything is saved."}
                  </Typography>
                </Stack>
              )}
              <Stack direction="row" spacing={1} alignItems="center">
                <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
                  From code:
                </Typography>
                <Typography
                  sx={{
                    typography: "s3",
                    fontFamily: MONO,
                    color: "text.secondary",
                    flex: 1,
                    minWidth: 0,
                    wordBreak: "break-all",
                  }}
                >
                  {skillImportCli(
                    agent,
                    mode === "repo" ? source.path : upload?.name,
                  )}
                </Typography>
                <CopyCommandButton
                  command={skillImportCli(
                    agent,
                    mode === "repo" ? source.path : upload?.name,
                  )}
                />
              </Stack>
            </Stack>

            {/* An import error, with the repair that fixes it. */}
            {result && !result.ok && (
              <Box
                sx={{
                  border: "1px solid",
                  borderColor: "divider",
                  borderRadius: "8px",
                  p: 1.5,
                }}
              >
                <Stack
                  direction="row"
                  spacing={1}
                  alignItems="center"
                  sx={{ mb: 0.75 }}
                >
                  <Iconify
                    icon="solar:danger-triangle-linear"
                    width={16}
                    sx={{ color: "warning.main" }}
                  />
                  <Typography sx={{ typography: "s1", fontWeight: 600 }}>
                    {result.error.title}
                  </Typography>
                </Stack>
                {result.error.lines.map((l) => (
                  <Typography
                    key={l}
                    sx={{ typography: "s2", color: "text.secondary" }}
                  >
                    {l}
                  </Typography>
                ))}
                <Typography
                  sx={{ typography: "s3", color: "text.tertiary", mt: 0.75 }}
                >
                  {`Source kept: ${sourceLine}`}
                </Typography>
                {result.error.code === "invalid_metadata" && (
                  <TextField
                    multiline
                    fullWidth
                    minRows={4}
                    value={skillMd}
                    onChange={(e) => setSkillMd(e.target.value)}
                    sx={{ mt: 1.25 }}
                    inputProps={{ style: { fontFamily: MONO, fontSize: 12 } }}
                  />
                )}
                <Stack direction="row" spacing={1} sx={{ mt: 1.25 }}>
                  {result.error.canUseReadme && (
                    <Button
                      size="small"
                      variant="contained"
                      color="primary"
                      onClick={() => read({ ...repairs, readme: true })}
                    >
                      Use README.md as SKILL.md
                    </Button>
                  )}
                  {result.error.code === "invalid_metadata" && (
                    <Button
                      size="small"
                      variant="contained"
                      color="primary"
                      onClick={() => read({ ...repairs, skillMd })}
                    >
                      Retry with this SKILL.md
                    </Button>
                  )}
                  {result.error.drop && (
                    <Button
                      size="small"
                      variant="contained"
                      color="primary"
                      onClick={() =>
                        read({
                          ...repairs,
                          drop: [...(repairs.drop || []), ...result.error.drop],
                        })
                      }
                    >
                      {`Leave ${result.error.drop.join(", ")} out and retry`}
                    </Button>
                  )}
                  <Button
                    size="small"
                    color="inherit"
                    onClick={() => read(repairs)}
                  >
                    Retry
                  </Button>
                </Stack>
              </Box>
            )}

            {/* What the folder is and asks for, then the files themselves. */}
            {result?.ok && (
              <>
                <FactBox>
                  <FactRow k="Skill">
                    <Box
                      component="span"
                      sx={{ fontFamily: MONO, fontSize: 12 }}
                    >
                      {result.meta.name}
                    </Box>
                    <Box component="span" sx={{ color: "text.tertiary" }}>
                      {existing
                        ? ` · v${latestOf(existing).v + 1}, a new version of the one this agent has (v${latestOf(existing).v})`
                        : " · v1, new to this agent"}
                    </Box>
                  </FactRow>
                  <FactRow k="Source">{sourceLine}</FactRow>
                  {(repairs.readme ||
                    repairs.skillMd != null ||
                    repairs.drop) && (
                    <FactRow k="Repaired">
                      {[
                        repairs.readme && "README.md used as SKILL.md",
                        repairs.skillMd != null &&
                          "SKILL.md frontmatter edited",
                        repairs.drop && `left out ${repairs.drop.join(", ")}`,
                      ]
                        .filter(Boolean)
                        .join(" · ")}
                    </FactRow>
                  )}
                </FactBox>
                <SkillFacts
                  meta={result.meta}
                  agentTools={agentTools}
                  runtime={runtime}
                />
                <SkillFiles files={result.files} />
              </>
            )}
          </Stack>
        </Box>

        <Stack
          direction="row"
          alignItems="center"
          spacing={1.5}
          sx={{
            px: 2,
            py: 1.25,
            borderTop: "1px solid",
            borderColor: "divider",
          }}
        >
          <Typography
            sx={{ typography: "s3", color: "text.tertiary", flex: 1 }}
          >
            {existing
              ? "Importing adds a version. The agent keeps loading the one it has until you change it in a draft."
              : "Importing doesn't load it or prove it helps. Turn it on in a draft, then test it against the agent without it."}
          </Typography>
          <Button size="small" color="inherit" onClick={onClose}>
            Cancel
          </Button>
          <Button
            size="small"
            variant="contained"
            color="primary"
            disabled={!result?.ok}
            onClick={() =>
              onImport({
                meta: result.meta,
                files: result.files,
                source: {
                  ...source,
                  ...(Object.keys(repairs).length && { repaired: true }),
                },
              })
            }
          >
            {existing ? `Import as v${latestOf(existing).v + 1}` : "Import"}
          </Button>
        </Stack>
      </Stack>
    </SideDrawer>
  );
}

SkillImportDrawer.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func.isRequired,
  agent: PropTypes.object,
  set: PropTypes.object.isRequired,
  runtime: PropTypes.object,
  agentTools: PropTypes.array,
  startPath: PropTypes.string,
  onImport: PropTypes.func.isRequired,
};
