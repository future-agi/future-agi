import PropTypes from "prop-types";
import { useEffect, useMemo, useState } from "react";
import {
  Box,
  Chip,
  IconButton,
  Stack,
  Switch,
  Tooltip,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { diffLines, fileChanges } from "../_mock/agentFiles";
import { GROUP_ORDER, LANG_ICON, MONO } from "./agentViewUtils";
import { clickable, focusRing } from "../components/clickable";

const CHANGE_TONE = { added: "success", changed: "warning", removed: "danger" };
// Text colour for change labels — amber stays on marks only, never on words.
const CHANGE_TEXT = {
  added: "status.success.fg",
  changed: "text.secondary",
  removed: "status.danger.fg",
};
const CHANGE_LETTER = { added: "A", changed: "M", removed: "D" };

/**
 * The version as a folder: a tree grouped by what each file is for, and a
 * read-only viewer. Files that changed since the previous version carry a
 * marker, and the viewer can show exactly which lines moved.
 */
export default function AgentFilesTab({
  files,
  prevFiles,
  label,
  prevLabel,
  sourceKind,
  focusPath,
  diffOn,
}) {
  const changes = useMemo(
    () => (prevFiles ? fileChanges(prevFiles, files) : {}),
    [files, prevFiles],
  );
  const [path, setPath] = useState(focusPath || files[0]?.path);
  const [showDiff, setShowDiff] = useState(!!diffOn);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    if (focusPath) setPath(focusPath);
  }, [focusPath]);
  /* Arriving from a change: show what changed. */
  useEffect(() => {
    if (diffOn) setShowDiff(true);
  }, [diffOn, focusPath]);
  useEffect(() => {
    if (!files.some((f) => f.path === path)) setPath(files[0]?.path);
  }, [files, path]);

  const file = files.find((f) => f.path === path) || files[0];
  const prev = prevFiles?.find((f) => f.path === file?.path);
  const change = file ? changes[file.path] : null;
  const removed = Object.entries(changes)
    .filter(([, c]) => c === "removed")
    .map(([p]) => p);

  const groups = GROUP_ORDER.map((g) => ({
    g,
    items: files.filter((f) => f.group === g),
  })).filter((x) => x.items.length);

  const copy = () => {
    navigator.clipboard?.writeText(file?.content || "");
    setCopied(true);
    setTimeout(() => setCopied(false), 1400);
  };

  const canDiff = !!prev && change === "changed" && !file?.doc;
  const lines =
    canDiff && showDiff
      ? diffLines(prev.content, file.content)
      : String(file?.content || "")
          .split("\n")
          .map((text) => ({ type: "same", text }));

  return (
    <Box
      sx={{
        display: "grid",
        gridTemplateColumns: { xs: "1fr", md: "290px 1fr" },
        border: "1px solid",
        borderColor: "divider",
        borderRadius: "8px",
        overflow: "hidden",
        minHeight: 520,
        bgcolor: "background.paper",
      }}
    >
      {/* ── tree ── */}
      <Box
        sx={{
          borderStyle: "solid",
          borderWidth: 0,
          borderRightWidth: { md: 1 },
          borderBottomWidth: { xs: 1, md: 0 },
          borderColor: "divider",
          py: 1,
          overflowY: "auto",
        }}
      >
        <Stack
          direction="row"
          alignItems="center"
          spacing={0.75}
          sx={{ px: 1.75, pb: 1 }}
        >
          <Typography
            sx={{
              typography: "s3",
              fontWeight: 600,
              color: "text.subtitle",
              textTransform: "none",
              letterSpacing: 0.4,
              flex: 1,
            }}
          >
            {`${files.length} files · ${label}`}
          </Typography>
          {Object.keys(changes).length > 0 && (
            <Tooltip arrow title={`Changed since ${prevLabel}`}>
              <Typography
                sx={{
                  typography: "s3",
                  fontWeight: 600,
                  color: "text.secondary",
                }}
              >
                {`${Object.keys(changes).length} changed`}
              </Typography>
            </Tooltip>
          )}
        </Stack>
        {sourceKind === "endpoint" && (
          <Box
            sx={{
              mx: 1.25,
              mb: 1,
              p: 1,
              borderRadius: 1,
              bgcolor: "background.neutral",
            }}
          >
            <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
              Endpoint agents are a black box. Tools are the ones we have seen
              it call in runs.
            </Typography>
          </Box>
        )}
        {groups.map(({ g, items }) => (
          <Box key={g} sx={{ mb: 0.75 }}>
            <Stack
              direction="row"
              alignItems="center"
              spacing={0.75}
              sx={{ px: 1.75, py: 0.5 }}
            >
              <Iconify
                icon="solar:folder-linear"
                width={14}
                sx={{ color: "text.subtitle" }}
              />
              <Typography
                sx={{
                  typography: "s3",
                  fontWeight: 600,
                  color: "text.subtitle",
                }}
              >
                {g}
              </Typography>
            </Stack>
            {items.map((f) => {
              const active = f.path === file?.path;
              const c = changes[f.path];
              return (
                <Stack
                  key={f.path}
                  direction="row"
                  alignItems="center"
                  spacing={0.75}
                  {...clickable(
                    () => {
                      setPath(f.path);
                      setShowDiff(false);
                    },
                    { pressed: active },
                  )}
                  sx={{
                    ...focusRing,
                    pl: 3.5,
                    pr: 1.5,
                    py: 0.5,
                    cursor: "pointer",
                    bgcolor: active ? "action.selected" : "transparent",
                    "&:hover": {
                      bgcolor: active ? "action.selected" : "action.hover",
                    },
                  }}
                >
                  <Iconify
                    icon={LANG_ICON[f.lang] || "solar:file-linear"}
                    width={14}
                    sx={{ color: "text.subtitle", flexShrink: 0 }}
                  />
                  <Typography
                    noWrap
                    title={f.path}
                    sx={{
                      typography: "s2",
                      flex: 1,
                      minWidth: 0,
                      fontFamily: MONO,
                      fontSize: 13,
                      fontWeight: active ? 600 : 400,
                    }}
                  >
                    {f.path.split("/").pop()}
                  </Typography>
                  {c && (
                    <Tooltip
                      arrow
                      title={`${c[0].toUpperCase()}${c.slice(1)} in ${label}`}
                    >
                      <Typography
                        sx={{
                          typography: "s3",
                          fontWeight: 600,
                          fontFamily: MONO,
                          color: CHANGE_TEXT[c],
                        }}
                      >
                        {CHANGE_LETTER[c]}
                      </Typography>
                    </Tooltip>
                  )}
                </Stack>
              );
            })}
          </Box>
        ))}
        {removed.length > 0 && (
          <Box sx={{ px: 1.75, pt: 0.5 }}>
            <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
              {`Removed in ${label}: ${removed.map((p) => p.split("/").pop()).join(", ")}`}
            </Typography>
          </Box>
        )}
      </Box>

      {/* ── viewer ── */}
      <Stack sx={{ minWidth: 0 }}>
        <Stack
          direction="row"
          alignItems="center"
          spacing={1}
          sx={{
            px: 2,
            py: 1.25,
            borderBottom: "1px solid",
            borderColor: "divider",
          }}
        >
          <Typography
            noWrap
            sx={{
              typography: "s2",
              fontWeight: 600,
              fontFamily: MONO,
              fontSize: 13,
              flex: 1,
              minWidth: 0,
            }}
          >
            {file?.path}
          </Typography>
          {change && (
            <Chip
              size="small"
              label={`${change === "added" ? "New" : "Changed"} in ${label}`}
              sx={{
                bgcolor: `status.${CHANGE_TONE[change]}.bg`,
                color: CHANGE_TEXT[change],
              }}
            />
          )}
          {file?.observed && (
            <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
              Observed in runs
            </Typography>
          )}
          {canDiff && (
            <Stack direction="row" alignItems="center" spacing={0.25}>
              <Switch
                size="small"
                checked={showDiff}
                onChange={(e) => setShowDiff(e.target.checked)}
              />
              <Typography
                sx={{
                  typography: "s3",
                  color: "text.secondary",
                  whiteSpace: "nowrap",
                }}
              >{`Diff vs ${prevLabel}`}</Typography>
            </Stack>
          )}
          {!file?.doc && (
            <Tooltip arrow title={copied ? "Copied" : "Copy file"}>
              <IconButton size="small" onClick={copy}>
                <Iconify
                  icon={
                    copied ? "solar:check-read-linear" : "solar:copy-linear"
                  }
                  width={16}
                />
              </IconButton>
            </Tooltip>
          )}
        </Stack>

        {file?.doc ? (
          <Box sx={{ p: 3 }}>
            <Stack
              direction="row"
              spacing={1.5}
              alignItems="center"
              sx={{
                p: 2,
                borderRadius: "8px",
                border: "1px solid",
                borderColor: "divider",
                maxWidth: 420,
              }}
            >
              <Iconify
                icon="solar:file-text-linear"
                width={28}
                sx={{ color: "text.subtitle" }}
              />
              <Box>
                <Typography sx={{ typography: "s2", fontWeight: 600 }}>
                  {file.path.split("/").pop()}
                </Typography>
                <Typography
                  sx={{ typography: "s3", color: "text.subtitle" }}
                >{`${file.content} · the agent looks things up here during calls`}</Typography>
              </Box>
            </Stack>
          </Box>
        ) : (
          <Box
            sx={{
              flex: 1,
              overflow: "auto",
              py: 1.25,
              bgcolor: "background.neutral",
            }}
          >
            {lines.map((l, i) => (
              <Stack
                // eslint-disable-next-line react/no-array-index-key
                key={i}
                direction="row"
                sx={{
                  bgcolor:
                    l.type === "add"
                      ? "status.success.bg"
                      : l.type === "del"
                        ? "status.danger.bg"
                        : "transparent",
                }}
              >
                <Typography
                  component="span"
                  sx={{
                    width: 44,
                    flexShrink: 0,
                    textAlign: "right",
                    pr: 1.5,
                    color: "text.disabled",
                    fontFamily: MONO,
                    fontSize: 12,
                    lineHeight: 1.7,
                    userSelect: "none",
                  }}
                >
                  {l.type === "del" ? "−" : l.type === "add" ? "+" : i + 1}
                </Typography>
                <Typography
                  component="span"
                  sx={{
                    fontFamily: MONO,
                    fontSize: 13,
                    lineHeight: 1.7,
                    whiteSpace: "pre-wrap",
                    wordBreak: "break-word",
                    pr: 2,
                    color: "text.primary",
                  }}
                >
                  {l.text || " "}
                </Typography>
              </Stack>
            ))}
          </Box>
        )}
      </Stack>
    </Box>
  );
}

AgentFilesTab.propTypes = {
  files: PropTypes.array.isRequired,
  prevFiles: PropTypes.array,
  label: PropTypes.string,
  prevLabel: PropTypes.string,
  sourceKind: PropTypes.string,
  focusPath: PropTypes.string,
  diffOn: PropTypes.bool,
};
