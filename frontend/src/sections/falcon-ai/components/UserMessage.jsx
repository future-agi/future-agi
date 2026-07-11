import React, { useState, useMemo } from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Typography from "@mui/material/Typography";
import { alpha, useTheme } from "@mui/material/styles";
import Iconify from "src/components/iconify";
import useFalconStore from "../store/useFalconStore";
import AttachedFileChip from "./AttachedFileChip";

function formatTime(timestamp) {
  if (!timestamp) return "";
  const d = new Date(timestamp);
  return d.toLocaleTimeString([], {
    hour: "numeric",
    minute: "2-digit",
    hour12: true,
  });
}

// Render message content with /skill-slug highlighted inline
function RenderContent({ content, theme }) {
  const skills = useFalconStore((s) => s.skills);
  const slugSet = useMemo(
    () => new Set((skills || []).filter((s) => s.slug).map((s) => s.slug)),
    [skills],
  );

  if (!content) return null;

  // Split on /word patterns and highlight matching skill slugs
  const parts = content.split(/(\/[a-z0-9-]+)/gi);
  return parts.map((part, i) => {
    if (part.startsWith("/") && slugSet.has(part.slice(1))) {
      const skill = skills.find((s) => s.slug === part.slice(1));
      return (
        <Box
          key={i}
          component="span"
          sx={{
            display: "inline-flex",
            alignItems: "center",
            gap: 0.4,
            px: 0.75,
            py: 0.15,
            mx: 0.25,
            borderRadius: "6px",
            bgcolor: alpha(theme.palette.primary.main, 0.2),
            color: "primary.main",
            fontSize: 13,
            fontWeight: 600,
            verticalAlign: "baseline",
          }}
        >
          <Iconify icon={skill?.icon || "mdi:lightning-bolt"} width={13} />
          {part}
        </Box>
      );
    }
    return <span key={i}>{part}</span>;
  });
}

RenderContent.propTypes = {
  content: PropTypes.string,
  theme: PropTypes.object.isRequired,
};

export default function UserMessage({ message, dimmed }) {
  const theme = useTheme();
  const [hovered, setHovered] = useState(false);

  return (
    <Box
      sx={{
        display: "flex",
        justifyContent: "flex-end",
        px: 0,
        py: 0.75,
        maxWidth: 800,
        width: "100%",
        mx: "auto",
        opacity: dimmed ? 0.62 : 1,
        transition: "opacity 0.3s ease",
      }}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
    >
      <Box sx={{ maxWidth: "85%", position: "relative" }}>
        <Box
          sx={{
            bgcolor: alpha(
              theme.palette.primary.main,
              theme.palette.mode === "dark" ? 0.15 : 0.08,
            ),
            borderRadius: "18px",
            px: 2,
            py: 1.25,
          }}
        >
          {message.files?.length > 0 && (
            <Box
              sx={{
                display: "flex",
                flexWrap: "wrap",
                gap: 0.5,
                mb: message.content ? 0.75 : 0,
              }}
            >
              {message.files.map((f) => (
                <AttachedFileChip key={f.id} file={f} readOnly />
              ))}
            </Box>
          )}
          <Typography
            variant="body2"
            component="div"
            sx={{
              whiteSpace: "pre-wrap",
              wordBreak: "break-word",
              overflowWrap: "break-word",
              fontSize: 14,
              lineHeight: 1.8,
              color: "text.primary",
            }}
          >
            <RenderContent content={message.content} theme={theme} />
          </Typography>
        </Box>

        {/* Timestamp on hover */}
        <Typography
          variant="caption"
          sx={{
            display: "block",
            textAlign: "right",
            mt: 0.5,
            mr: 0.5,
            fontSize: 11,
            color: "text.disabled",
            opacity: hovered ? 1 : 0,
            transition: "opacity 0.15s ease",
            userSelect: "none",
          }}
        >
          {formatTime(message.created_at)}
        </Typography>
      </Box>
    </Box>
  );
}

UserMessage.propTypes = {
  message: PropTypes.shape({
    id: PropTypes.string,
    content: PropTypes.string,
    created_at: PropTypes.string,
    files: PropTypes.arrayOf(
      PropTypes.shape({
        id: PropTypes.string,
        name: PropTypes.string,
        size: PropTypes.number,
        url: PropTypes.string,
      }),
    ),
  }).isRequired,
  dimmed: PropTypes.bool,
};
