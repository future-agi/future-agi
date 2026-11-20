import PropTypes from "prop-types";
import React, { useCallback, useMemo, useState } from "react";
import {
  Box,
  Button,
  MenuItem,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import { useCodeCompare, useCodePatches } from "src/api/agents/agentDetail";
import { errorMessage } from "src/pages/dashboard/harness/harnessShared";
import Iconify from "src/components/iconify";
import { Card, LineSkeleton } from "../bits";
import FileDiffBlock from "./FileDiffBlock";
import FileTree from "./FileTree";
import { githubCompareUrl, olderFirst, planBatches } from "./diffModel";

const COMPARE_HEIGHT = 720;

const toFile = (raw) => ({
  path: raw.path,
  previousPath: raw.previous_path ?? null,
  status: raw.status,
  kind: raw.kind,
  additions: raw.additions ?? null,
  deletions: raw.deletions ?? null,
  collapsed: !!raw.collapsed,
});

function VersionField({ label, value, versions, onChange }) {
  return (
    <TextField
      select
      size="small"
      label={label}
      value={value || ""}
      onChange={(e) => onChange(e.target.value)}
      sx={{ minWidth: 140 }}
      SelectProps={{ MenuProps: { PaperProps: { sx: { maxHeight: 320 } } } }}
    >
      {versions.map((v) => (
        <MenuItem key={v.id} value={v.id}>
          {v.label}
          {v.isActive ? " · in production" : ""}
        </MenuItem>
      ))}
    </TextField>
  );
}

VersionField.propTypes = {
  label: PropTypes.string.isRequired,
  value: PropTypes.string,
  versions: PropTypes.arrayOf(PropTypes.object).isRequired,
  onChange: PropTypes.func.isRequired,
};

function Summary({ data, files, base, head }) {
  if (data.unavailable === "no_code") {
    return `${base?.label ?? "The older version"} has no stored code to compare.`;
  }
  if (data.same_code) {
    return data.env_changed
      ? "Only environment values changed in this version; the code is the same."
      : "The code is the same in both versions.";
  }
  if (!files.length)
    return `No files changed between ${base?.label} and ${head?.label}.`;
  const count = data.total_files;
  return (
    <>
      {`${count} file${count === 1 ? "" : "s"} changed · `}
      <Box component="span" sx={{ color: "status.success.fg" }}>
        +{data.additions}
      </Box>{" "}
      <Box component="span" sx={{ color: "status.danger.fg" }}>
        -{data.deletions}
      </Box>
      {data.truncated && ` · showing the first ${files.length} files`}
    </>
  );
}

Summary.propTypes = {
  data: PropTypes.object.isRequired,
  files: PropTypes.arrayOf(PropTypes.object).isRequired,
  base: PropTypes.object,
  head: PropTypes.object,
};

export default function CompareCard({
  agentId,
  versions,
  headId,
  baseId,
  onChange,
  onViewFile,
}) {
  const [split, setSplit] = useState(false);
  const [wanted, setWanted] = useState(() => new Set());
  const [singles, setSingles] = useState(() => new Set());
  const [scroller, setScroller] = useState(null);
  const [selected, setSelected] = useState("");
  const head = versions.find((v) => v.id === headId);
  const base = versions.find((v) => v.id === baseId);
  const same = headId && headId === baseId;
  const githubUrl = same ? null : githubCompareUrl(base, head);
  const compare = useCodeCompare(agentId, headId, same ? null : baseId);
  const files = useMemo(
    () => (compare.data?.files ?? []).map(toFile),
    [compare.data],
  );
  const { batches, batchOf } = useMemo(() => planBatches(files), [files]);
  const requested = useMemo(
    () => [
      ...[...wanted].sort((a, b) => a - b).map((i) => batches[i]),
      ...[...singles].map((path) => [path]),
    ],
    [wanted, singles, batches],
  );
  const results = useCodePatches(agentId, headId, baseId, requested);
  const patchByPath = useMemo(() => {
    const found = {};
    results.forEach((result) => {
      (result.data?.files ?? []).forEach((patch) => {
        found[patch.path] = patch;
      });
    });
    return found;
  }, [results]);
  const failed = results.find((result) => result.isError);

  const pick = (left, right) => {
    const [older, newer] = olderFirst(versions, left, right);
    setWanted(new Set());
    setSingles(new Set());
    setSelected("");
    onChange(newer, older);
  };
  const onNear = useCallback(
    (path) => {
      const index = batchOf[path];
      if (index == null) return;
      setWanted((prev) => (prev.has(index) ? prev : new Set(prev).add(index)));
    },
    [batchOf],
  );
  const onLoadDiff = useCallback(
    (path) => setSingles((prev) => new Set(prev).add(path)),
    [],
  );
  const jumpTo = (path) => {
    setSelected(path);
    scroller
      ?.querySelector(`[id="diff-${CSS.escape(path)}"]`)
      ?.scrollIntoView?.({ block: "start" });
  };
  const changes = useMemo(
    () => Object.fromEntries(files.map((file) => [file.path, file.status])),
    [files],
  );

  return (
    <Card
      title="Compare"
      action={
        <Stack direction="row" spacing={0.5}>
          {githubUrl && (
            <Button
              size="small"
              color="inherit"
              variant="outlined"
              component="a"
              href={githubUrl}
              target="_blank"
              rel="noopener noreferrer"
              startIcon={<Iconify icon="simple-icons:github" width={14} />}
              data-testid="compare-on-github"
            >
              Open in GitHub
            </Button>
          )}
          {[
            [false, "Unified"],
            [true, "Split"],
          ].map(([value, label]) => (
            <Button
              key={label}
              size="small"
              variant={split === value ? "contained" : "outlined"}
              color={split === value ? "primary" : "inherit"}
              aria-pressed={split === value}
              onClick={() => setSplit(value)}
            >
              {label}
            </Button>
          ))}
        </Stack>
      }
    >
      <Stack
        direction="row"
        alignItems="center"
        spacing={1}
        flexWrap="wrap"
        rowGap={1}
        sx={{
          px: 1.5,
          py: 1.25,
          borderBottom: "1px solid",
          borderColor: "divider",
        }}
      >
        <VersionField
          label="Older"
          value={baseId}
          versions={versions}
          onChange={(id) => pick(id, headId)}
        />
        <Typography sx={{ typography: "s1", color: "text.secondary" }}>
          →
        </Typography>
        <VersionField
          label="Newer"
          value={headId}
          versions={versions}
          onChange={(id) => pick(baseId, id)}
        />
        <Typography
          component="div"
          data-testid="compare-summary"
          sx={{ typography: "s1", color: "text.secondary", ml: 1 }}
        >
          {same && "Pick two different versions."}
          {!same && compare.isLoading && <LineSkeleton width={180} />}
          {!same &&
            compare.isError &&
            (errorMessage(compare.error) || "Couldn't compare these versions.")}
          {!same && compare.data && (
            <Summary
              data={compare.data}
              files={files}
              base={base}
              head={head}
            />
          )}
        </Typography>
      </Stack>
      {failed && (
        <Typography
          sx={{ px: 1.5, py: 1, typography: "s2", color: "status.danger.fg" }}
        >
          {errorMessage(failed.error) || "Some diffs couldn't be loaded."}
        </Typography>
      )}
      {!same && files.length > 0 && (
        <Box
          sx={{
            display: "grid",
            gridTemplateColumns: "minmax(180px, 260px) minmax(0, 1fr)",
            height: COMPARE_HEIGHT,
          }}
        >
          <Box
            sx={{
              borderRight: "1px solid",
              borderColor: "divider",
              overflowY: "auto",
              p: 1,
            }}
          >
            <FileTree
              files={files}
              selected={selected}
              changes={changes}
              onSelect={jumpTo}
              openAll
            />
          </Box>
          <Box
            ref={setScroller}
            data-testid="compare-diffs"
            sx={{
              overflowY: "auto",
              p: 1.5,
              display: "flex",
              flexDirection: "column",
              gap: 1.5,
            }}
          >
            {scroller &&
              files.map((file) => (
                <FileDiffBlock
                  key={file.path}
                  agentId={agentId}
                  versionId={headId}
                  againstId={baseId}
                  file={file}
                  patch={patchByPath[file.path]}
                  split={split}
                  root={scroller}
                  githubUrl={githubUrl}
                  onNear={onNear}
                  onLoadDiff={onLoadDiff}
                  onViewFile={onViewFile}
                />
              ))}
          </Box>
        </Box>
      )}
    </Card>
  );
}

CompareCard.propTypes = {
  agentId: PropTypes.string.isRequired,
  versions: PropTypes.arrayOf(PropTypes.object).isRequired,
  headId: PropTypes.string,
  baseId: PropTypes.string,
  onChange: PropTypes.func.isRequired,
  onViewFile: PropTypes.func.isRequired,
};
