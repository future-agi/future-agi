import React, { useState, useEffect, useCallback } from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Typography from "@mui/material/Typography";
import Paper from "@mui/material/Paper";
import Iconify from "src/components/iconify";
import useFalconStore from "../store/useFalconStore";

const SYSTEM_COMMANDS = [
  {
    command: "/clear",
    description: "New conversation",
    icon: "solar:magic-stick-3-linear",
    type: "system",
  },
];

export default function SlashCommandPicker({
  inputText,
  onSelect,
  anchorRef: _anchorRef,
  pickerRef,
}) {
  const [selectedIndex, setSelectedIndex] = useState(0);
  const skills = useFalconStore((s) => s.skills);

  // Build combined command list: system commands + skills as slash commands
  const allCommands = [
    ...SYSTEM_COMMANDS,
    ...(skills || [])
      .filter((skill) => skill.slug)
      .map((skill) => ({
        command: `/${skill.slug}`,
        description: skill.description || skill.name,
        icon: skill.icon || "solar:bolt-linear",
        type: "skill",
        skill,
      })),
  ];

  // Only show when input starts with "/" and has no spaces
  const isVisible =
    inputText.startsWith("/") && !inputText.includes(" ") && inputText !== "";

  const query = inputText.slice(1).toLowerCase();
  const filtered = allCommands.filter(
    (cmd) =>
      cmd.command.toLowerCase().includes(query) ||
      cmd.description.toLowerCase().includes(query),
  );

  // Reset selection when filtered list changes
  useEffect(() => {
    setSelectedIndex(0);
  }, [query]);

  // Expose keyboard handler for parent to call from its onKeyDown
  const handlePickerKeyDown = useCallback(
    (e) => {
      if (!isVisible || filtered.length === 0) return false;

      if (e.key === "ArrowUp") {
        e.preventDefault();
        setSelectedIndex((prev) =>
          prev <= 0 ? filtered.length - 1 : prev - 1,
        );
        return true;
      }
      if (e.key === "ArrowDown") {
        e.preventDefault();
        setSelectedIndex((prev) =>
          prev >= filtered.length - 1 ? 0 : prev + 1,
        );
        return true;
      }
      if (e.key === "Enter" || e.key === "Tab") {
        e.preventDefault();
        e.stopPropagation();
        onSelect(filtered[selectedIndex]);
        return true;
      }
      if (e.key === "Escape") {
        e.preventDefault();
        onSelect(null);
        return true;
      }
      return false;
    },
    [isVisible, filtered, selectedIndex, onSelect],
  );

  // Assign handler to ref synchronously (no useEffect delay)
  if (pickerRef) {
    pickerRef.current = { handleKeyDown: handlePickerKeyDown };
  }

  if (!isVisible || filtered.length === 0) return null;

  // Group: system commands first, then skills
  const systemItems = filtered.filter((c) => c.type === "system");
  const skillItems = filtered.filter((c) => c.type === "skill");

  return (
    <Paper
      elevation={0}
      sx={{
        position: "absolute",
        bottom: "100%",
        left: 0,
        right: 0,
        mb: 0.5,
        mx: 2,
        overflow: "hidden",
        zIndex: 10,
        maxHeight: 360,
        overflowY: "auto",
      }}
    >
      {systemItems.length > 0 && (
        <>
          <Box sx={{ px: 1.5, pt: 1, pb: 0.5 }}>
            <Typography
              variant="caption"
              sx={{
                color: "text.disabled",
                fontWeight: 600,
                fontSize: 12,
                textTransform: "none",
                letterSpacing: 0,
              }}
            >
              Commands
            </Typography>
          </Box>
          {systemItems.map((cmd) => {
            const globalIdx = filtered.indexOf(cmd);
            return (
              <CommandRow
                key={cmd.command}
                cmd={cmd}
                isSelected={globalIdx === selectedIndex}
                onSelect={onSelect}
              />
            );
          })}
        </>
      )}

      {skillItems.length > 0 && (
        <>
          <Box sx={{ px: 1.5, pt: 1, pb: 0.5 }}>
            <Typography
              variant="caption"
              sx={{
                color: "text.disabled",
                fontWeight: 600,
                fontSize: 12,
                textTransform: "none",
                letterSpacing: 0,
              }}
            >
              Skills
            </Typography>
          </Box>
          {skillItems.map((cmd) => {
            const globalIdx = filtered.indexOf(cmd);
            return (
              <CommandRow
                key={cmd.command}
                cmd={cmd}
                isSelected={globalIdx === selectedIndex}
                onSelect={onSelect}
              />
            );
          })}
        </>
      )}
    </Paper>
  );
}

function CommandRow({ cmd, isSelected, onSelect }) {
  return (
    <Box
      onClick={() => onSelect(cmd)}
      sx={{
        display: "flex",
        alignItems: "center",
        gap: 1.5,
        px: 1.5,
        py: 0.75,
        mx: 0.5,
        mb: 0.25,
        borderRadius: "8px",
        cursor: "pointer",
        bgcolor: isSelected ? "action.selected" : "transparent",
        "&:hover": {
          bgcolor: "action.hover",
        },
        transition: "background-color 0.12s ease",
      }}
    >
      <Iconify
        icon={cmd.icon}
        width={16}
        sx={{ color: "text.secondary", flexShrink: 0 }}
      />
      <Typography
        sx={{
          fontFamily: "'SF Mono', 'Fira Code', monospace",
          fontSize: 13,
          fontWeight: 600,
          color: "text.primary",
          minWidth: 90,
          flexShrink: 0,
        }}
      >
        {cmd.command}
      </Typography>
      <Typography
        sx={{
          fontSize: 12,
          color: "text.secondary",
          whiteSpace: "nowrap",
          overflow: "hidden",
          textOverflow: "ellipsis",
        }}
      >
        {cmd.description}
      </Typography>
    </Box>
  );
}

CommandRow.propTypes = {
  cmd: PropTypes.object.isRequired,
  isSelected: PropTypes.bool,
  onSelect: PropTypes.func.isRequired,
};

SlashCommandPicker.propTypes = {
  inputText: PropTypes.string.isRequired,
  onSelect: PropTypes.func.isRequired,
  anchorRef: PropTypes.object,
  pickerRef: PropTypes.object,
};
