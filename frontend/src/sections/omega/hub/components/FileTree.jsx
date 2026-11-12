import React from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import ButtonBase from "@mui/material/ButtonBase";
import Typography from "@mui/material/Typography";

import { formatBytes } from "../lib/format";
import { checkTone, TONE_ROLES } from "../lib/status";

import HubIcon from "./HubIcon";
import { toneSx } from "./ToneChip";

// The bundle's files with byte sizes (SPEC §7.2), and per-file finding marks
// when a check report is given (SPEC §1.1). File names are in the theme's
// mono face, the same as every command and code span on the Hub.
export default function FileTree({
  files,
  selected,
  onSelect,
  findingsByFile = {},
  notesByFile = {},
}) {
  const list = [...(files || [])].sort((a, b) => a.path.localeCompare(b.path));
  return (
    <Box
      component="ul"
      data-testid="hub-file-tree"
      sx={{
        m: 0,
        p: 0,
        listStyle: "none",
        border: "1px solid",
        borderColor: "divider",
        borderRadius: 1,
      }}
    >
      {list.map((file, index) => {
        const findings = findingsByFile[file.path] || [];
        const findingTones = findings.map((finding) =>
          checkTone(finding.severity),
        );
        const findingTone =
          ["error", "attention", "neutral", "success"].find((tone) =>
            findingTones.includes(tone),
          ) || "neutral";
        const notes = notesByFile[file.path] || [];
        const active = selected === file.path;
        const depth = file.path.split("/").length - 1;
        return (
          <Box
            component="li"
            key={file.path}
            sx={{ borderTop: index ? "1px solid" : 0, borderColor: "divider" }}
          >
            <ButtonBase
              onClick={() => onSelect?.(file.path)}
              data-testid="hub-file-row"
              data-path={file.path}
              aria-current={active ? "true" : undefined}
              sx={{
                width: "100%",
                justifyContent: "flex-start",
                gap: 1,
                px: 1.25,
                py: 0.75,
                pl: 1.25 + depth * 1.5,
                textAlign: "left",
                bgcolor: active ? "action.selected" : "transparent",
                "&:hover": { bgcolor: "action.hover" },
              }}
            >
              <Box sx={{ color: "text.disabled", display: "flex" }}>
                <HubIcon name="file" size={14} />
              </Box>
              <Typography
                typography="code_sm"
                sx={{
                  flex: 1,
                  minWidth: 0,
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                }}
              >
                {file.path}
              </Typography>
              {findings.length > 0 && (
                <Box
                  component="span"
                  title={`${findings.length} finding${findings.length === 1 ? "" : "s"}`}
                  data-testid="hub-file-finding-mark"
                  sx={{
                    display: "inline-flex",
                    alignItems: "center",
                    gap: 0.25,
                    ...toneSx(findingTone),
                    bgcolor: (theme) =>
                      theme.palette.status[TONE_ROLES[findingTone]].bg,
                    px: 0.5,
                    borderRadius: 0.75,
                    typography: "s3",
                  }}
                >
                  <HubIcon name="flag" size={12} />
                  {findings.length}
                </Box>
              )}
              {notes.length > 0 && (
                <Box
                  component="span"
                  title={`${notes.length} reviewer note${notes.length === 1 ? "" : "s"}`}
                  sx={{
                    display: "inline-flex",
                    alignItems: "center",
                    gap: 0.25,
                    ...toneSx("info"),
                    typography: "s3",
                  }}
                >
                  <HubIcon name="info" size={12} />
                  {notes.length}
                </Box>
              )}
              <Typography
                typography="s3"
                color={active ? "text.secondary" : "text.tertiary"}
                sx={{ whiteSpace: "nowrap" }}
              >
                {formatBytes(file.bytes)}
              </Typography>
            </ButtonBase>
          </Box>
        );
      })}
    </Box>
  );
}

FileTree.propTypes = {
  files: PropTypes.array,
  selected: PropTypes.string,
  onSelect: PropTypes.func,
  findingsByFile: PropTypes.object,
  notesByFile: PropTypes.object,
};
