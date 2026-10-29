import PropTypes from "prop-types";
import { useEffect, useState } from "react";
import {
  Box,
  Button,
  IconButton,
  Stack,
  ToggleButton,
  ToggleButtonGroup,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import SideDrawer from "../../components/SideDrawer";
import {
  draftStateOf,
  frontmatterOf,
  latestOf,
  readSkillFolder,
  versionChangesOf,
} from "../../_mock/skills";
import { clickable, focusRing } from "../../components/clickable";
import { MONO } from "../agentViewUtils";
import { segmentedToggleSx } from "../segmentedToggleSx";
import SkillFiles from "./SkillFiles";
import SkillFacts, { FactBox, FactRow } from "./SkillFacts";

const when = (iso) =>
  new Date(iso).toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
  });
const STATE_TEXT = {
  on: "Loaded",
  off: "Turned off",
  library: "Imported, not loaded",
};
const sourceText = (s) =>
  s?.kind === "upload"
    ? `Uploaded ${s.name || "folder"}`
    : `${s?.path || "—"} @ ${s?.rev || "—"}`;

/**
 * One skill (skills.md "skill detail"): what it asks for, its files, and
 * every version with what changed — scripts and references beside the
 * text. Tested runtimes, coverage and failure modes come from its tests.
 */
export default function SkillDrawer({
  open,
  onClose,
  skill,
  draft,
  runtime,
  agentTools,
  onImportRevision,
}) {
  const [tab, setTab] = useState("about");
  const [pickedV, setPickedV] = useState(null);
  useEffect(() => {
    if (open) {
      setTab("about");
      setPickedV(null);
    }
  }, [open, skill?.id]);

  if (!open || !skill) return <SideDrawer open={false} onClose={onClose} />;
  const latest = latestOf(skill);
  const parsed = readSkillFolder({ files: latest.files });
  const fm =
    frontmatterOf(latest.files.find((f) => f.path === "SKILL.md")?.text) || {};
  const meta = parsed.ok
    ? parsed.meta
    : {
        description: fm.description || "—",
        tools: [],
        scripts: [],
        reads: [],
        credentials: [],
      };
  const state = draftStateOf(skill, draft);
  const versions = [...skill.versions].reverse();
  const shownV = pickedV ?? latest.v;
  const shown = skill.versions.find((v) => v.v === shownV);
  const prev = skill.versions.find((v) => v.v === shownV - 1);
  const changes = prev ? versionChangesOf(prev, shown) : null;

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
            <Typography
              sx={{ typography: "m3", fontWeight: 600, fontFamily: MONO }}
            >
              {skill.id}
            </Typography>
            <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
              {[
                `v${latest.v}`,
                STATE_TEXT[state] +
                  (state !== skill.state ? " in the draft" : ""),
                sourceText(latest.source),
                `${skill.versions.length} version${skill.versions.length === 1 ? "" : "s"}`,
              ].join(" · ")}
            </Typography>
          </Box>
          <IconButton size="small" aria-label="Close" onClick={onClose}>
            <Iconify icon="mingcute:close-line" width={18} />
          </IconButton>
        </Stack>
        <Box sx={{ px: 2, pt: 1.5 }}>
          <ToggleButtonGroup
            exclusive
            size="small"
            value={tab}
            onChange={(_, v) => v && setTab(v)}
            sx={segmentedToggleSx}
          >
            <ToggleButton value="about">About</ToggleButton>
            <ToggleButton value="files">{`Files · ${latest.files.length}`}</ToggleButton>
            <ToggleButton value="versions">{`Versions · ${skill.versions.length}`}</ToggleButton>
          </ToggleButtonGroup>
        </Box>

        <Box sx={{ flex: 1, minHeight: 0, overflow: "auto", px: 2, py: 1.5 }}>
          {tab === "about" && (
            <Stack spacing={1.5}>
              <SkillFacts
                meta={meta}
                agentTools={agentTools}
                runtime={runtime}
              />
              <FactBox>
                <FactRow k="Tested on">
                  <Box component="span" sx={{ color: "text.secondary" }}>
                    Not tested on this agent yet. A runtime counts as compatible
                    only after a test on it.
                  </Box>
                </FactRow>
                <FactRow k="Test coverage">
                  <Box component="span" sx={{ color: "text.secondary" }}>
                    No cases yet: when it should be used, when it
                    shouldn&apos;t, and next to the other skills.
                  </Box>
                </FactRow>
                <FactRow k="Known failure modes">
                  <Box component="span" sx={{ color: "text.secondary" }}>
                    None recorded. They come from test runs.
                  </Box>
                </FactRow>
              </FactBox>
            </Stack>
          )}
          {tab === "files" && (
            <SkillFiles files={latest.files} minHeight={480} />
          )}
          {tab === "versions" && (
            <Stack spacing={1.5}>
              <Box
                sx={{
                  border: "1px solid",
                  borderColor: "divider",
                  borderRadius: "8px",
                  overflow: "hidden",
                }}
              >
                {versions.map((v) => {
                  const c = versionChangesOf(
                    skill.versions.find((x) => x.v === v.v - 1),
                    v,
                  );
                  return (
                    <Stack
                      key={v.v}
                      direction="row"
                      alignItems="center"
                      spacing={1.5}
                      {...clickable(() => setPickedV(v.v))}
                      sx={{
                        px: 1.5,
                        height: 44,
                        cursor: "pointer",
                        bgcolor:
                          v.v === shownV ? "action.selected" : "transparent",
                        "&:hover": {
                          bgcolor:
                            v.v === shownV ? "action.selected" : "action.hover",
                        },
                        "& + &": {
                          borderTop: "1px solid",
                          borderColor: "divider",
                        },
                        ...focusRing,
                      }}
                    >
                      <Typography
                        sx={{ typography: "s2", fontWeight: 500, width: 36 }}
                      >{`v${v.v}`}</Typography>
                      <Box flex={1} minWidth={0}>
                        <Typography noWrap sx={{ typography: "s2" }}>
                          {v.note}
                        </Typography>
                        <Typography
                          noWrap
                          sx={{ typography: "s3", color: "text.tertiary" }}
                        >
                          {`${when(v.at)} · ${v.by} · ${sourceText(v.source)}`}
                        </Typography>
                      </Box>
                      <Typography
                        sx={{ typography: "s3", color: "text.tertiary" }}
                      >
                        {v.v === 1
                          ? `${v.files.length} file${v.files.length === 1 ? "" : "s"}`
                          : `${c.length} file${c.length === 1 ? "" : "s"} changed`}
                      </Typography>
                    </Stack>
                  );
                })}
              </Box>
              <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
                {prev
                  ? `v${shownV} against v${prev.v}: ${changes.length ? changes.map((c) => `${c.path} ${c.change}`).join(", ") : "no file changed"}`
                  : `v${shownV} is the first version.`}
              </Typography>
              <SkillFiles
                files={shown.files}
                prevFiles={prev ? prev.files : undefined}
                changes={changes || undefined}
                minHeight={300}
              />
            </Stack>
          )}
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
            Turning a skill off keeps every version here.
          </Typography>
          <Button
            size="small"
            variant="outlined"
            color="inherit"
            onClick={onImportRevision}
          >
            Import a new revision
          </Button>
        </Stack>
      </Stack>
    </SideDrawer>
  );
}

SkillDrawer.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func.isRequired,
  skill: PropTypes.object,
  draft: PropTypes.object,
  runtime: PropTypes.object,
  agentTools: PropTypes.array,
  onImportRevision: PropTypes.func,
};
