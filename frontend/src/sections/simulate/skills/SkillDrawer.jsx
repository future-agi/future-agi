import PropTypes from "prop-types";
import React, { useMemo, useState } from "react";
import {
  Box,
  Button,
  Divider,
  Drawer,
  IconButton,
  ListItemIcon,
  Menu,
  MenuItem,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  Typography,
} from "@mui/material";
import { formatDistanceToNow } from "date-fns";
import ContentSkeleton from "src/components/content-skeleton";
import ErrorState from "src/components/error-state";
import Iconify from "src/components/iconify";
import StatusBadge from "src/components/status-badge";
import {
  useSkill,
  useSkillTests,
  useSkillVersionFiles,
} from "src/api/simulate-skills/skills";
import { ROW_HEIGHT } from "src/theme/sizes";
import AttachSection from "./AttachSection";
import PromoteDialog from "./PromoteDialog";
import SkillFilesPanel from "./SkillFilesPanel";
import SkillEditorDialog from "./SkillEditorDialog";
import TestDialog from "./TestDialog";
import { loadedLabel, testBadge } from "./skillLabels";

const ago = (iso) =>
  iso ? formatDistanceToNow(new Date(iso), { addSuffix: true }) : "";

function VersionMenu({ onView, onTest, onPromote }) {
  const [anchor, setAnchor] = useState(null);
  const run = (fn) => () => {
    setAnchor(null);
    fn();
  };
  return (
    <>
      <IconButton
        size="small"
        aria-label="Version actions"
        onClick={(event) => setAnchor(event.currentTarget)}
      >
        <Iconify icon="solar:menu-dots-bold" width={16} />
      </IconButton>
      <Menu
        anchorEl={anchor}
        open={Boolean(anchor)}
        onClose={() => setAnchor(null)}
      >
        <MenuItem onClick={run(onView)}>
          <ListItemIcon>
            <Iconify icon="solar:document-text-linear" width={16} />
          </ListItemIcon>
          View files
        </MenuItem>
        <MenuItem onClick={run(onTest)}>
          <ListItemIcon>
            <Iconify icon="solar:test-tube-linear" width={16} />
          </ListItemIcon>
          Test in environment
        </MenuItem>
        <MenuItem onClick={run(onPromote)}>
          <ListItemIcon>
            <Iconify icon="solar:arrow-up-linear" width={16} />
          </ListItemIcon>
          Promote
        </MenuItem>
      </Menu>
    </>
  );
}

VersionMenu.propTypes = {
  onView: PropTypes.func.isRequired,
  onTest: PropTypes.func.isRequired,
  onPromote: PropTypes.func.isRequired,
};

function FilesView({ skillId, version }) {
  const { data, isLoading, isError, refetch } = useSkillVersionFiles(
    skillId,
    version,
  );
  if (isLoading) return <ContentSkeleton variant="panel" />;
  if (isError)
    return <ErrorState title="Couldn't load the files" onRetry={refetch} />;
  return (
    <SkillFilesPanel files={data?.files || []} label={`Files in v${version}`} />
  );
}

FilesView.propTypes = {
  skillId: PropTypes.string.isRequired,
  version: PropTypes.number.isRequired,
};

function TestsSection({ tests }) {
  if (!tests.length) {
    return (
      <Typography sx={{ typography: "s1", color: "text.secondary" }}>
        No tests yet. Test a version before promoting it.
      </Typography>
    );
  }
  return (
    <Table size="small">
      <TableHead>
        <TableRow sx={{ height: ROW_HEIGHT.default }}>
          <TableCell>Version</TableCell>
          <TableCell>Agent</TableCell>
          <TableCell>Result</TableCell>
          <TableCell>Pass rate</TableCell>
          <TableCell>Loaded</TableCell>
        </TableRow>
      </TableHead>
      <TableBody>
        {tests.map((test) => {
          const badge = testBadge(test.state, test.verdict);
          const comparison = test.summary?.comparison;
          return (
            <TableRow key={test.id} hover sx={{ height: ROW_HEIGHT.default }}>
              <TableCell>
                {`v${test.version} vs ${test.baseline_version ? `v${test.baseline_version}` : "without"}`}
              </TableCell>
              <TableCell>{test.agent_name}</TableCell>
              <TableCell>
                <StatusBadge
                  status={badge.status}
                  label={badge.label}
                  size="sm"
                />
              </TableCell>
              <TableCell sx={{ color: "text.secondary" }}>
                {comparison
                  ? `${comparison.current_pass_rate ?? "-"}% vs ${comparison.against_pass_rate ?? "-"}%`
                  : "-"}
              </TableCell>
              <TableCell sx={{ color: "text.secondary" }}>
                {test.summary?.loads ? loadedLabel(test.summary.loads) : "-"}
              </TableCell>
            </TableRow>
          );
        })}
      </TableBody>
    </Table>
  );
}

TestsSection.propTypes = { tests: PropTypes.array.isRequired };

export default function SkillDrawer({ skillId, onClose }) {
  const skill = useSkill(skillId);
  const tests = useSkillTests(skillId);
  const [viewing, setViewing] = useState(null);
  const [testing, setTesting] = useState(null);
  const [promoting, setPromoting] = useState(null);
  const [editing, setEditing] = useState(false);
  const data = skill.data;
  const latest = data?.versions?.[0]?.version_number ?? null;
  const latestFiles = useSkillVersionFiles(editing ? skillId : null, latest);
  const testedVersions = useMemo(
    () =>
      new Set(
        (tests.data || [])
          .filter((test) => test.state === "finished")
          .map((test) => test.version),
      ),
    [tests.data],
  );
  const tagsOf = (number) =>
    (data?.tags || [])
      .filter((tag) => tag.version === number)
      .map((tag) => tag.name);

  let body;
  if (skill.isLoading) body = <ContentSkeleton variant="panel" />;
  else if (skill.isError)
    body = (
      <ErrorState title="Couldn't load this skill" onRetry={skill.refetch} />
    );
  else if (data)
    body = (
      <Stack spacing={2}>
        <Stack spacing={1}>
          <Stack direction="row" alignItems="center" spacing={1}>
            <Typography sx={{ typography: "m3", flex: 1 }}>Versions</Typography>
            <Button
              size="small"
              variant="contained"
              startIcon={<Iconify icon="solar:pen-linear" width={16} />}
              onClick={() => setEditing(true)}
            >
              Edit
            </Button>
          </Stack>
          <Table size="small">
            <TableBody>
              {data.versions.map((version) => (
                <TableRow
                  key={version.version_number}
                  hover
                  sx={{ height: ROW_HEIGHT.default }}
                >
                  <TableCell
                    sx={{ width: 56 }}
                  >{`v${version.version_number}`}</TableCell>
                  <TableCell>
                    <Typography sx={{ typography: "s1" }}>
                      {version.note || "No note"}
                    </Typography>
                    <Typography
                      sx={{ typography: "s3", color: "text.secondary" }}
                    >
                      {[version.created_by, ago(version.created_at)]
                        .filter(Boolean)
                        .join(" · ")}
                    </Typography>
                  </TableCell>
                  <TableCell sx={{ color: "text.secondary" }}>
                    {tagsOf(version.version_number).join(", ")}
                  </TableCell>
                  <TableCell align="right" sx={{ width: 48 }}>
                    <VersionMenu
                      onView={() => setViewing(version.version_number)}
                      onTest={() => setTesting(version.version_number)}
                      onPromote={() => setPromoting(version.version_number)}
                    />
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
          {viewing != null && <FilesView skillId={skillId} version={viewing} />}
        </Stack>
        <Divider />
        <AttachSection key={data.id} skill={data} />
        <Divider />
        <Stack spacing={1}>
          <Typography sx={{ typography: "m3" }}>Tests</Typography>
          <TestsSection tests={tests.data || []} />
        </Stack>
        <Divider />
        <Stack spacing={1}>
          <Typography sx={{ typography: "m3" }}>Promotion history</Typography>
          {data.promotions.length ? (
            data.promotions.map((one) => (
              <Typography
                key={`${one.tag}-${one.version}-${one.created_at}`}
                sx={{ typography: "s1" }}
              >
                {`${one.tag} → v${one.version} · ${one.tested ? "tested" : `untested: ${one.reason}`} · ${[one.created_by, ago(one.created_at)].filter(Boolean).join(" · ")}`}
              </Typography>
            ))
          ) : (
            <Typography sx={{ typography: "s1", color: "text.secondary" }}>
              Nothing promoted yet.
            </Typography>
          )}
        </Stack>
      </Stack>
    );

  return (
    <Drawer
      anchor="right"
      open={Boolean(skillId)}
      onClose={onClose}
      PaperProps={{ sx: { width: { xs: "100%", md: 640 }, p: 2 } }}
    >
      <Stack direction="row" alignItems="flex-start" spacing={1} sx={{ mb: 2 }}>
        <Box sx={{ flex: 1, minWidth: 0 }}>
          <Typography sx={{ typography: "m1" }} noWrap>
            {data?.name || "Skill"}
          </Typography>
          <Typography sx={{ typography: "s1", color: "text.secondary" }}>
            {data
              ? `${data.kind === "context" ? "Agent context" : "Skill"} · ${data.description || "No description"}`
              : ""}
          </Typography>
        </Box>
        <IconButton size="small" aria-label="Close" onClick={onClose}>
          <Iconify icon="solar:close-circle-linear" width={20} />
        </IconButton>
      </Stack>
      {body}
      {data && (
        <>
          <SkillEditorDialog
            open={editing && latestFiles.isSuccess}
            onClose={() => setEditing(false)}
            mode="version"
            skill={data}
            initialFiles={latestFiles.data?.files}
          />
          <TestDialog
            open={testing != null}
            onClose={() => setTesting(null)}
            skill={data}
            version={testing}
          />
          <PromoteDialog
            open={promoting != null}
            onClose={() => setPromoting(null)}
            skill={data}
            version={promoting}
            testedVersions={testedVersions}
          />
        </>
      )}
    </Drawer>
  );
}

SkillDrawer.propTypes = {
  skillId: PropTypes.string,
  onClose: PropTypes.func.isRequired,
};
