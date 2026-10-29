import PropTypes from "prop-types";
import { useEffect, useMemo, useState } from "react";
import { Box, Stack, Typography } from "@mui/material";
import { diffLines } from "../../_mock/agentFiles";
import { fileKindOf } from "../../_mock/skills";
import { clickable, focusRing } from "../../components/clickable";
import { MONO } from "../agentViewUtils";

const GROUPS = [
  { kind: "skill", label: "Instructions" },
  { kind: "script", label: "Scripts" },
  { kind: "reference", label: "References" },
  { kind: "asset", label: "Assets" },
  { kind: "other", label: "Other" },
];
const LINE_BG = { add: "rgba(34,197,94,0.12)", del: "rgba(239,68,68,0.12)" };
const LINE_MARK = { add: "+", del: "−", same: " " };

/**
 * A skill folder: files grouped as the format defines them (SKILL.md,
 * scripts, references, assets), the picked one's text beside them. With
 * `prevFiles`, changed files show their line diff.
 */
export default function SkillFiles({
  files,
  prevFiles,
  changes,
  minHeight = 360,
}) {
  const [path, setPath] = useState(files[0]?.path);
  useEffect(() => {
    if (!files.some((f) => f.path === path)) setPath(files[0]?.path);
  }, [files, path]);
  const file = files.find((f) => f.path === path);
  const change = (changes || []).find((c) => c.path === path)?.change;
  const prev = prevFiles?.find((f) => f.path === path);
  const lines = useMemo(() => {
    if (!file || file.binary) return [];
    if (prevFiles && change === "changed")
      return diffLines(prev?.text, file.text);
    return String(file.text || "")
      .split("\n")
      .map((text) => ({ type: change === "added" ? "add" : "same", text }));
  }, [file, prev, prevFiles, change]);

  return (
    <Box
      sx={{
        display: "grid",
        gridTemplateColumns: "224px 1fr",
        border: "1px solid",
        borderColor: "divider",
        borderRadius: "8px",
        overflow: "hidden",
        minHeight,
        bgcolor: "background.paper",
      }}
    >
      <Box sx={{ borderRight: "1px solid", borderColor: "divider", py: 1 }}>
        {GROUPS.map((g) => {
          const items = files.filter((f) => fileKindOf(f.path) === g.kind);
          if (!items.length) return null;
          return (
            <Box key={g.kind} sx={{ mb: 1 }}>
              <Typography
                sx={{
                  typography: "s3",
                  color: "text.tertiary",
                  px: 1.5,
                  py: 0.5,
                }}
              >
                {`${g.label} · ${items.length}`}
              </Typography>
              {items.map((f) => {
                const c = (changes || []).find(
                  (x) => x.path === f.path,
                )?.change;
                return (
                  <Stack
                    key={f.path}
                    direction="row"
                    alignItems="center"
                    spacing={1}
                    {...clickable(() => setPath(f.path))}
                    sx={{
                      px: 1.5,
                      height: 28,
                      cursor: "pointer",
                      bgcolor:
                        f.path === path ? "action.selected" : "transparent",
                      "&:hover": {
                        bgcolor:
                          f.path === path ? "action.selected" : "action.hover",
                      },
                      ...focusRing,
                    }}
                  >
                    <Typography
                      noWrap
                      sx={{
                        typography: "s2",
                        fontFamily: MONO,
                        flex: 1,
                        minWidth: 0,
                      }}
                      title={f.path}
                    >
                      {f.path.split("/").pop()}
                    </Typography>
                    {c && (
                      <Typography
                        sx={{ typography: "s3", color: "text.tertiary" }}
                      >
                        {c === "added" ? "new" : c}
                      </Typography>
                    )}
                  </Stack>
                );
              })}
            </Box>
          );
        })}
        {(changes || [])
          .filter((c) => c.change === "removed")
          .map((c) => (
            <Typography
              key={c.path}
              sx={{
                typography: "s3",
                color: "text.tertiary",
                px: 1.5,
                py: 0.5,
                textDecoration: "line-through",
              }}
            >
              {c.path}
            </Typography>
          ))}
      </Box>
      <Box sx={{ minWidth: 0, overflow: "auto" }}>
        <Typography
          sx={{
            typography: "s3",
            fontFamily: MONO,
            color: "text.tertiary",
            px: 1.5,
            py: 0.75,
            borderBottom: "1px solid",
            borderColor: "divider",
          }}
        >
          {file?.path || "—"}
        </Typography>
        {file?.binary ? (
          <Typography
            sx={{ typography: "s2", color: "text.secondary", p: 1.5 }}
          >
            {`Binary file · ${file.size}. Not previewed.`}
          </Typography>
        ) : (
          <Box
            component="pre"
            sx={{
              m: 0,
              py: 1,
              fontFamily: MONO,
              fontSize: 12,
              lineHeight: "18px",
            }}
          >
            {lines.map((l, i) => (
              <Box
                key={i}
                sx={{
                  px: 1.5,
                  bgcolor: LINE_BG[l.type] || "transparent",
                  whiteSpace: "pre-wrap",
                  wordBreak: "break-word",
                }}
              >
                {prevFiles && (
                  <Box component="span" sx={{ color: "text.tertiary", mr: 1 }}>
                    {LINE_MARK[l.type]}
                  </Box>
                )}
                {l.text || " "}
              </Box>
            ))}
          </Box>
        )}
      </Box>
    </Box>
  );
}

SkillFiles.propTypes = {
  files: PropTypes.array.isRequired,
  prevFiles: PropTypes.array,
  changes: PropTypes.array,
  minHeight: PropTypes.number,
};
