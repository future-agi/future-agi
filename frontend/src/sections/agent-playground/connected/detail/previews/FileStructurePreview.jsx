import PropTypes from "prop-types";
import React from "react";
import { Box, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { MONO } from "../format";

// Sample content for the File structure preview, from the designer prototype.
const GROUPS = [
  { name: "Config", files: ["agent.yaml", ".env.example"] },
  { name: "Prompts", files: ["system.md"] },
  { name: "Code", files: ["agent.py", "tools.py"] },
  {
    name: "Tools",
    files: [
      "lookup_booking.json",
      "search_flights.json",
      "rebook_segment.json",
    ],
  },
  { name: "Knowledge", files: ["policies.md", "faq.md"] },
];

const AGENT_YAML = [
  "entrypoint: src/agent.py:agent",
  "model:",
  "  name: gpt-4.1",
  "  temperature: 0.3",
  "prompt: prompts/system.md",
  "tools: src/tools.py",
  "source:",
  "  repo: https://github.com/acme/booking-agent",
  "  ref: main",
  "runtime:",
  "  endpoint: https://api.acme.com/agent/chat",
];

const fileCount = GROUPS.reduce((n, g) => n + g.files.length, 0);

export default function FileStructurePreview({ versionLabel = "v1" }) {
  return (
    <Box
      sx={{
        display: "grid",
        gridTemplateColumns: { xs: "1fr", md: "290px 1fr" },
        minHeight: 360,
      }}
    >
      <Box
        sx={{
          borderRight: { md: "1px solid" },
          borderColor: { md: "divider" },
          py: 1,
        }}
      >
        <Typography
          sx={{
            px: 2,
            pb: 1,
            typography: "s3",
            fontWeight: 600,
            color: "text.subtitle",
          }}
        >
          {`${fileCount} files · ${versionLabel}`}
        </Typography>
        {GROUPS.map((group) => (
          <Box key={group.name} sx={{ mb: 0.5 }}>
            <Stack
              direction="row"
              alignItems="center"
              spacing={0.75}
              sx={{ px: 2, py: 0.5 }}
            >
              <Iconify
                icon="solar:folder-linear"
                width={14}
                sx={{ color: "text.subtitle" }}
              />
              <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
                {group.name}
              </Typography>
            </Stack>
            {group.files.map((file) => {
              const active = file === "agent.yaml";
              return (
                <Stack
                  key={file}
                  direction="row"
                  alignItems="center"
                  spacing={0.75}
                  sx={{
                    pl: 4,
                    pr: 2,
                    py: 0.375,
                    bgcolor: active ? "action.selected" : "transparent",
                  }}
                >
                  <Iconify
                    icon="solar:file-text-linear"
                    width={14}
                    sx={{ color: "text.subtitle" }}
                  />
                  <Typography
                    noWrap
                    sx={{
                      fontFamily: MONO,
                      fontSize: 12.5,
                      fontWeight: active ? 600 : 400,
                    }}
                  >
                    {file}
                  </Typography>
                </Stack>
              );
            })}
          </Box>
        ))}
      </Box>
      <Box>
        <Stack
          direction="row"
          alignItems="center"
          sx={{
            px: 2,
            py: 1.25,
            borderBottom: "1px solid",
            borderColor: "divider",
          }}
        >
          <Typography
            sx={{ flex: 1, fontFamily: MONO, fontSize: 12.5, fontWeight: 600 }}
          >
            agent.yaml
          </Typography>
          <Iconify
            icon="solar:copy-linear"
            width={16}
            sx={{ color: "text.subtitle" }}
          />
        </Stack>
        <Box sx={{ py: 1 }}>
          {AGENT_YAML.map((line, i) => (
            <Stack key={line} direction="row">
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
                }}
              >
                {i + 1}
              </Typography>
              <Typography
                component="span"
                sx={{
                  fontFamily: MONO,
                  fontSize: 12.5,
                  lineHeight: 1.7,
                  whiteSpace: "pre",
                }}
              >
                {line}
              </Typography>
            </Stack>
          ))}
        </Box>
      </Box>
    </Box>
  );
}

FileStructurePreview.propTypes = { versionLabel: PropTypes.string };
