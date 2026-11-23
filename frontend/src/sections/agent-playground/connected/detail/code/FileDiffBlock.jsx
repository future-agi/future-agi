import PropTypes from "prop-types";
import React, { useEffect, useMemo, useState } from "react";
import { Box, Button, IconButton, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { useCodeFile } from "src/api/agents/agentDetail";
import { useInView } from "src/hooks/use-in-view";
import { LineSkeleton } from "../bits";
import { MONO } from "../format";
import {
  ROW_PX,
  STATUS_LABEL,
  estimatedHeight,
  gapRows,
  githubFileAnchor,
  hunkRows,
  lineCount,
  segments,
  splitRows,
} from "./diffModel";

const ROW_TONE = {
  add: { bgcolor: "status.success.bg", mark: "+", color: "status.success.fg" },
  del: { bgcolor: "status.danger.bg", mark: "-", color: "status.danger.fg" },
  context: { bgcolor: "transparent", mark: " ", color: "text.secondary" },
};

const cell = {
  fontFamily: MONO,
  typography: "s1",
  lineHeight: `${ROW_PX}px`,
  whiteSpace: "pre",
};

function LineNo({ value }) {
  return (
    <Box
      component="span"
      sx={{
        ...cell,
        color: "text.tertiary",
        textAlign: "right",
        px: 1,
        userSelect: "none",
      }}
    >
      {value ?? ""}
    </Box>
  );
}

LineNo.propTypes = { value: PropTypes.number };

function Code({ row, wrap = false }) {
  const tone = ROW_TONE[row?.type ?? "context"];
  return (
    <Box
      component="span"
      sx={{
        ...cell,
        pr: 2,
        bgcolor: row ? tone.bgcolor : "action.hover",
        minWidth: 0,
        ...(wrap && {
          whiteSpace: "pre-wrap",
          overflowWrap: "anywhere",
          overflow: "hidden",
        }),
      }}
    >
      <Box
        component="span"
        sx={{ color: tone.color, userSelect: "none", px: 0.5 }}
      >
        {row ? tone.mark : " "}
      </Box>
      {row?.text ?? ""}
    </Box>
  );
}

Code.propTypes = { row: PropTypes.object, wrap: PropTypes.bool };

function UnifiedRows({ rows }) {
  return rows.map((row) => (
    <Box
      key={`${row.oldNo ?? "x"}-${row.newNo ?? "x"}`}
      sx={{
        display: "grid",
        gridTemplateColumns: "48px 48px 1fr",
        bgcolor: ROW_TONE[row.type].bgcolor,
      }}
    >
      <LineNo value={row.oldNo} />
      <LineNo value={row.newNo} />
      <Code row={row} />
    </Box>
  ));
}

function SplitRows({ rows }) {
  return splitRows(rows).map(({ left, right }) => (
    <Box
      key={`${left?.oldNo ?? "x"}-${right?.newNo ?? "x"}`}
      sx={{
        display: "grid",
        gridTemplateColumns: "48px minmax(0, 1fr) 48px minmax(0, 1fr)",
      }}
    >
      <LineNo value={left?.oldNo} />
      <Code row={left} wrap />
      <LineNo value={right?.newNo} />
      <Code row={right} wrap />
    </Box>
  ));
}

UnifiedRows.propTypes = {
  rows: PropTypes.arrayOf(PropTypes.object).isRequired,
};

SplitRows.propTypes = { rows: PropTypes.arrayOf(PropTypes.object).isRequired };

function Rows({ rows, split }) {
  return split ? <SplitRows rows={rows} /> : <UnifiedRows rows={rows} />;
}

Rows.propTypes = {
  rows: PropTypes.arrayOf(PropTypes.object).isRequired,
  split: PropTypes.bool.isRequired,
};

function Gap({ gap, onExpand }) {
  const count = gap.to == null ? null : gap.to - gap.from + 1;
  return (
    <Box
      component="button"
      type="button"
      onClick={onExpand}
      data-testid="diff-gap"
      sx={{
        display: "flex",
        alignItems: "center",
        gap: 1,
        width: "100%",
        border: 0,
        px: 1.5,
        height: ROW_PX + 4,
        bgcolor: "action.hover",
        color: "text.secondary",
        cursor: "pointer",
        typography: "s3",
        "&:hover": { color: "text.primary" },
      }}
    >
      <Iconify icon="solar:alt-arrow-down-linear" width={14} />
      {count == null
        ? "Show the rest of the file"
        : `Show ${count} unchanged line${count === 1 ? "" : "s"}`}
    </Box>
  );
}

Gap.propTypes = {
  gap: PropTypes.object.isRequired,
  onExpand: PropTypes.func.isRequired,
};

function Notice({ children }) {
  return (
    <Typography
      sx={{ px: 1.5, py: 1.25, typography: "s2", color: "text.secondary" }}
    >
      {children}
    </Typography>
  );
}

Notice.propTypes = { children: PropTypes.node };

function Body({
  agentId,
  versionId,
  file,
  patch,
  split,
  expandAll,
  onLoadDiff,
}) {
  const [opened, setOpened] = useState(() => new Set());
  const wantsContent = expandAll || opened.size > 0;
  const content = useCodeFile(
    agentId,
    versionId,
    wantsContent && file.status !== "removed" ? file.path : "",
  );
  const text = content.data?.content;
  const parts = useMemo(
    () =>
      segments(
        patch?.hunks ?? [],
        text != null && file.status === "modified" ? lineCount(text) : null,
      ),
    [patch, text, file.status],
  );

  if (file.kind === "binary") return <Notice>Binary file not shown.</Notice>;
  if (file.kind === "too_large") {
    return <Notice>This diff is too large to show.</Notice>;
  }
  if (!patch) {
    if (file.collapsed) {
      return (
        <Stack alignItems="center" sx={{ py: 1.5 }}>
          <Button size="small" variant="outlined" onClick={onLoadDiff}>
            Load diff
          </Button>
          <Typography
            sx={{ typography: "s3", color: "text.secondary", mt: 0.5 }}
          >
            Large diffs are not shown until you load them.
          </Typography>
        </Stack>
      );
    }
    return (
      <Box sx={{ px: 1.5, py: 1, height: estimatedHeight(file) }}>
        <LineSkeleton width="60%" />
      </Box>
    );
  }
  if (!patch.hunks.length) {
    return (
      <Notice>
        {file.status === "renamed"
          ? "File renamed without changes."
          : "Only whitespace or line endings changed."}
      </Notice>
    );
  }
  const canExpand = file.status === "modified";
  return (
    <Box sx={{ overflowX: split ? "hidden" : "auto" }}>
      <Box sx={{ minWidth: split ? 0 : "fit-content" }}>
        {parts.map((part) => {
          if (part.kind === "hunk") {
            return (
              <Rows key={part.key} rows={hunkRows(part.hunk)} split={split} />
            );
          }
          if (!canExpand) return null;
          const shown = (expandAll || opened.has(part.key)) && text != null;
          if (shown) {
            return (
              <Rows key={part.key} rows={gapRows(part, text)} split={split} />
            );
          }
          return (
            <Gap
              key={part.key}
              gap={part}
              onExpand={() => setOpened((prev) => new Set(prev).add(part.key))}
            />
          );
        })}
      </Box>
    </Box>
  );
}

Body.propTypes = {
  agentId: PropTypes.string.isRequired,
  versionId: PropTypes.string.isRequired,
  file: PropTypes.object.isRequired,
  patch: PropTypes.object,
  split: PropTypes.bool.isRequired,
  expandAll: PropTypes.bool.isRequired,
  onLoadDiff: PropTypes.func.isRequired,
};

export default function FileDiffBlock({
  agentId,
  versionId,
  againstId,
  file,
  patch,
  split,
  root,
  githubUrl,
  onNear,
  onLoadDiff,
  onViewFile,
}) {
  const [anchor, setAnchor] = useState("");
  useEffect(() => {
    let live = true;
    if (githubUrl) {
      githubFileAnchor(file.path)
        .then((value) => live && setAnchor(value))
        .catch(() => live && setAnchor(""));
    }
    return () => {
      live = false;
    };
  }, [githubUrl, file.path]);
  const [ref, near] = useInView({
    root,
    rootMargin: "800px",
    triggerOnce: true,
  });
  const [open, setOpen] = useState(true);
  const [expandAll, setExpandAll] = useState(false);

  useEffect(() => {
    if (near) onNear(file.path);
  }, [near, onNear, file.path]);

  const side = file.status === "removed" ? againstId : versionId;
  return (
    <Box
      ref={ref}
      id={`diff-${file.path}`}
      data-testid="file-diff-block"
      sx={{
        border: "1px solid",
        borderColor: "divider",
        borderRadius: "6px",
        overflow: "hidden",
        flexShrink: 0,
        scrollMarginTop: 8,
      }}
    >
      <Stack
        direction="row"
        alignItems="center"
        spacing={1}
        sx={{
          px: 1,
          minHeight: 36,
          bgcolor: "background.default",
          borderBottom: open ? "1px solid" : 0,
          borderColor: "divider",
        }}
      >
        <IconButton
          size="small"
          aria-label={open ? "Collapse file" : "Expand file"}
          aria-expanded={open}
          onClick={() => setOpen((v) => !v)}
        >
          <Iconify
            icon={
              open
                ? "solar:alt-arrow-down-linear"
                : "solar:alt-arrow-right-linear"
            }
            width={14}
          />
        </IconButton>
        {file.additions != null && (
          <Typography sx={{ typography: "s3", fontFamily: MONO }}>
            <Box component="span" sx={{ color: "status.success.fg" }}>
              +{file.additions}
            </Box>{" "}
            <Box component="span" sx={{ color: "status.danger.fg" }}>
              -{file.deletions}
            </Box>
          </Typography>
        )}
        <Typography
          sx={{
            typography: "s1",
            fontFamily: MONO,
            flex: 1,
            minWidth: 0,
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
          }}
          title={file.path}
        >
          {file.previousPath
            ? `${file.previousPath} → ${file.path}`
            : file.path}
        </Typography>
        <Typography sx={{ typography: "s3", color: "text.secondary" }}>
          {STATUS_LABEL[file.status]}
        </Typography>
        {file.status === "modified" && file.kind === "text" && patch && (
          <Button
            size="small"
            color="inherit"
            onClick={() => setExpandAll((v) => !v)}
            aria-pressed={expandAll}
          >
            {expandAll ? "Collapse unchanged" : "Expand all"}
          </Button>
        )}
        <Button
          size="small"
          color="inherit"
          onClick={() => onViewFile(side, file.path)}
        >
          View file
        </Button>
        {githubUrl && (
          <IconButton
            size="small"
            component="a"
            href={`${githubUrl}${anchor}`}
            target="_blank"
            rel="noopener noreferrer"
            aria-label="View this file's changes on GitHub"
            data-testid="file-on-github"
          >
            <Iconify icon="simple-icons:github" width={14} />
          </IconButton>
        )}
      </Stack>
      {open && (
        <Body
          agentId={agentId}
          versionId={versionId}
          file={file}
          patch={patch}
          split={split}
          expandAll={expandAll}
          onLoadDiff={() => onLoadDiff(file.path)}
        />
      )}
    </Box>
  );
}

FileDiffBlock.propTypes = {
  agentId: PropTypes.string.isRequired,
  versionId: PropTypes.string.isRequired,
  againstId: PropTypes.string.isRequired,
  file: PropTypes.object.isRequired,
  patch: PropTypes.object,
  split: PropTypes.bool.isRequired,
  root: PropTypes.object,
  githubUrl: PropTypes.string,
  onNear: PropTypes.func.isRequired,
  onLoadDiff: PropTypes.func.isRequired,
  onViewFile: PropTypes.func.isRequired,
};
