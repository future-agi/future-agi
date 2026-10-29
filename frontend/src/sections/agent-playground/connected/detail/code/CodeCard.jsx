import PropTypes from "prop-types";
import React, { useMemo } from "react";
import { useSearchParams } from "react-router-dom";
import { Box, Button, Stack, Typography } from "@mui/material";
import {
  useCodeChanges,
  useCodeFile,
  useCodeTree,
} from "src/api/agents/agentDetail";
import { Card, LineSkeleton } from "../bits";
import { MONO } from "../format";
import FileDiff from "./FileDiff";
import FileTree from "./FileTree";
import FileViewer from "./FileViewer";

const TEMPLATE = "minmax(180px, 260px) minmax(0, 1fr)";

/**
 * The files of a version, a viewer, and the diff against the previous
 * version. `?file=` names the open file and `?diff=1` turns the diff on, so a
 * link lands on the same view.
 */
export default function CodeCard({ agentId, versionId, versionLabel }) {
  const [params, setParams] = useSearchParams();
  const selected = params.get("file") || "";
  const diffOn = params.get("diff") === "1";
  const tree = useCodeTree(agentId, versionId);
  const changes = useCodeChanges(agentId, versionId);
  const file = useCodeFile(agentId, versionId, diffOn ? "" : selected);
  const changedBy = useMemo(
    () =>
      Object.fromEntries(
        (changes.data?.files ?? []).map((one) => [one.path, one.status]),
      ),
    [changes.data],
  );
  const againstId = changes.data?.against || null;

  const set = (next) => {
    const copy = new URLSearchParams(params);
    Object.entries(next).forEach(([key, value]) => {
      if (value) copy.set(key, value);
      else copy.delete(key);
    });
    setParams(copy, { replace: true });
  };

  if (tree.isLoading) {
    return (
      <Card title="Files">
        <Box sx={{ px: 2, py: 1.5 }}>
          <LineSkeleton width={160} />
        </Box>
      </Card>
    );
  }
  if (tree.isError) {
    return (
      <Card title="Files">
        <Typography
          sx={{ px: 2, py: 1.5, typography: "s2", color: "text.secondary" }}
        >
          {tree.error?.message || "Code is temporarily unavailable"}
        </Typography>
      </Card>
    );
  }
  if (!tree.data) {
    return (
      <Card title="Files">
        <Typography
          sx={{ px: 2, py: 1.5, typography: "s2", color: "text.secondary" }}
          data-testid="code-empty"
        >
          No code is stored for this version.
        </Typography>
      </Card>
    );
  }

  const count = tree.data.files.length;
  const changedCount = Object.keys(changedBy).length;
  return (
    <Card
      title={`Files · ${count} file${count === 1 ? "" : "s"} · ${versionLabel}`}
      action={
        <Stack direction="row" alignItems="center" spacing={1}>
          {changes.data?.against && (
            <Typography
              sx={{ typography: "s2", color: "text.secondary" }}
              data-testid="code-changed-count"
            >
              {changedCount
                ? `${changedCount} changed since the previous version`
                : "No change since the previous version"}
            </Typography>
          )}
          {changes.data?.against && (
            <Button
              size="small"
              variant={diffOn ? "contained" : "outlined"}
              color={diffOn ? "primary" : "inherit"}
              onClick={() => set({ diff: diffOn ? "" : "1" })}
              aria-pressed={diffOn}
            >
              Diff vs previous
            </Button>
          )}
        </Stack>
      }
    >
      <Box
        sx={{
          display: "grid",
          gridTemplateColumns: TEMPLATE,
          minHeight: 360,
          maxHeight: 640,
        }}
      >
        <Box sx={{ borderRight: "1px solid", borderColor: "divider", p: 1 }}>
          <FileTree
            files={tree.data.files}
            subfolder={tree.data.subfolder}
            selected={selected}
            changes={changedBy}
            onSelect={(path) => set({ file: path })}
          />
        </Box>
        <Box sx={{ minWidth: 0, display: "flex", flexDirection: "column" }}>
          {selected && (
            <Stack
              direction="row"
              alignItems="center"
              spacing={1}
              sx={{
                px: 2,
                py: 0.75,
                borderBottom: "1px solid",
                borderColor: "divider",
              }}
            >
              <Typography
                noWrap
                sx={{ typography: "s2", fontFamily: MONO }}
                data-testid="code-path"
              >
                {selected}
              </Typography>
              {changedBy[selected] && (
                <Typography
                  sx={{ typography: "s2", color: "text.secondary" }}
                  data-testid="code-changed-chip"
                >
                  {`${changedBy[selected]} in ${versionLabel}`}
                </Typography>
              )}
            </Stack>
          )}
          <Box sx={{ flex: 1, minHeight: 0 }}>
            {diffOn && selected && againstId ? (
              <FileDiff
                agentId={agentId}
                versionId={versionId}
                againstId={againstId}
                path={selected}
              />
            ) : selected && file.isError ? (
              <Typography
                sx={{ p: 2, typography: "s2", color: "text.secondary" }}
                data-testid="file-viewer-error"
              >
                Could not load this file.
              </Typography>
            ) : selected && !file.isLoading && !file.data ? (
              <Typography
                sx={{ p: 2, typography: "s2", color: "text.secondary" }}
                data-testid="file-viewer-missing"
              >
                This file is not in this version.
              </Typography>
            ) : (
              <FileViewer
                file={selected ? file.data : null}
                loading={!!selected && file.isLoading}
              />
            )}
          </Box>
        </Box>
      </Box>
    </Card>
  );
}

CodeCard.propTypes = {
  agentId: PropTypes.string.isRequired,
  versionId: PropTypes.string.isRequired,
  versionLabel: PropTypes.string.isRequired,
};
