import PropTypes from "prop-types";
import { useState } from "react";
import { alpha } from "@mui/material/styles";
import {
  Dialog, DialogContent, Box, Stack, Typography, IconButton, Tab, Button, Tooltip,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { SegmentedTabs } from "src/components/tabs/tabs";
import TwinLogo from "../components/TwinLogo";

const TWIN_TINT = "#7857FC";

/**
 * "Connect your agent" — the missing bridge between provisioning a
 * twin and actually pointing an agent at it. Shows the base URL, the
 * auth header, and ready-to-copy snippets in Python / TypeScript /
 * curl so a user can wire the sandbox into their code without leaving
 * the workspace.
 *
 * The snippets swap the real SDK's base URL for the twin's endpoint
 * and inject the sandbox session header — the same shape the twin
 * runtime accepts in production. Language switch is a segmented tab
 * because Python vs TS is the axis users pivot on most often; curl
 * sits alongside as a reference for cross-language debugging.
 */
export default function TwinConnectDialog({ open, onClose, twin, endpoint, sessionId }) {
  const [lang, setLang] = useState("python");
  if (!twin) return null;
  const authHeader = `X-Clone-Session: ${sessionId || "sess_01ABC"}`;
  const snippet = SNIPPETS[lang](twin, endpoint, sessionId || "sess_01ABC");
  return (
    <Dialog open={open} onClose={onClose} maxWidth="md" fullWidth
      PaperProps={{ sx: { borderRadius: 2 } }}>
      <DialogContent sx={{ p: 0 }}>
        {/* header */}
        <Stack direction="row" alignItems="center" spacing={1.5} sx={{
          px: 3, py: 2, borderBottom: "1px solid", borderColor: "divider",
        }}>
          <TwinLogo twin={twin} width={22} />
          <Box flex={1}>
            <Typography sx={{ typography: "s1_2", fontWeight: 700 }}>
              Connect your agent to {twin.name}
            </Typography>
            <Typography sx={{ typography: "s2", color: "text.subtitle" }}>
              Swap the real base URL for the clone&apos;s. Add the session header. Everything else stays the same.
            </Typography>
          </Box>
          <IconButton onClick={onClose} size="small">
            <Iconify icon="mdi:close" width={16} />
          </IconButton>
        </Stack>

        <Box sx={{ p: 3 }}>
          {/* base URL + auth */}
          <Stack spacing={1.5} sx={{ mb: 2.5 }}>
            <FieldRow label="Base URL" value={endpoint} />
            <FieldRow label="Auth header" value={authHeader} />
          </Stack>

          {/* snippet */}
          <Stack direction="row" alignItems="center" spacing={1.5} sx={{ mb: 1 }}>
            <Typography sx={{ typography: "s3", fontWeight: 700, color: "text.subtitle", textTransform: "uppercase", letterSpacing: 0.4 }}>
              Snippet
            </Typography>
            <Box flex={1} />
            <SegmentedTabs value={lang} onChange={(_, v) => setLang(v)}>
              <Tab value="python" label="Python" />
              <Tab value="typescript" label="TypeScript" />
              <Tab value="curl" label="curl" />
            </SegmentedTabs>
            <Tooltip arrow title="Copy snippet">
              <IconButton size="small" onClick={() => navigator.clipboard?.writeText(snippet)}>
                <Iconify icon="solar:copy-linear" width={13} />
              </IconButton>
            </Tooltip>
          </Stack>
          <Box sx={{
            p: 1.5, borderRadius: 1, border: "1px solid", borderColor: "divider",
            bgcolor: "background.neutral", maxHeight: 320, overflow: "auto",
          }}>
            <Typography component="pre" sx={{
              typography: "s2", m: 0, whiteSpace: "pre",
              fontFamily: "ui-monospace, Menlo, monospace",
              color: "text.primary",
            }}>
              {snippet}
            </Typography>
          </Box>

          {/* why-this-works strip */}
          <Stack direction="row" alignItems="flex-start" spacing={1.25} sx={{
            mt: 2.5, p: 1.5, borderRadius: 1,
            border: (t) => `1px solid ${alpha(TWIN_TINT, t.palette.mode === "dark" ? 0.4 : 0.28)}`,
            bgcolor: (t) => alpha(TWIN_TINT, t.palette.mode === "dark" ? 0.08 : 0.04),
          }}>
            <Iconify icon="solar:info-circle-linear" width={14} sx={{ color: TWIN_TINT, mt: "2px", flexShrink: 0 }} />
            <Box>
              <Typography sx={{ typography: "s2", fontWeight: 700, color: TWIN_TINT }}>
                Wire-compatible with the real {twin.name} SDK
              </Typography>
              <Typography sx={{ typography: "s3", color: "text.subtitle", mt: 0.25 }}>
                Requests hit our sandbox, not production. State is scoped to this session ID — reset the env or start a new run and the session rotates automatically.
              </Typography>
            </Box>
          </Stack>

          <Stack direction="row" justifyContent="flex-end" spacing={1} sx={{ mt: 2.5 }}>
            <Button size="small" onClick={onClose}>Close</Button>
          </Stack>
        </Box>
      </DialogContent>
    </Dialog>
  );
}
TwinConnectDialog.propTypes = {
  open: PropTypes.bool, onClose: PropTypes.func,
  twin: PropTypes.object, endpoint: PropTypes.string, sessionId: PropTypes.string,
};

function FieldRow({ label, value }) {
  return (
    <Stack direction="row" alignItems="center" spacing={1.25}>
      <Typography sx={{
        typography: "s3", fontWeight: 700, color: "text.subtitle",
        textTransform: "uppercase", letterSpacing: 0.4, minWidth: 92, flexShrink: 0,
      }}>
        {label}
      </Typography>
      <Box sx={{
        flex: 1, minWidth: 0, px: 1, py: 0.75, borderRadius: 0.75,
        border: "1px solid", borderColor: "divider", bgcolor: "background.paper",
      }}>
        <Typography noWrap sx={{
          typography: "s2", fontFamily: "ui-monospace, Menlo, monospace",
        }}>
          {value}
        </Typography>
      </Box>
      <Tooltip arrow title="Copy">
        <IconButton size="small" onClick={() => navigator.clipboard?.writeText(value)}>
          <Iconify icon="solar:copy-linear" width={12} />
        </IconButton>
      </Tooltip>
    </Stack>
  );
}
FieldRow.propTypes = { label: PropTypes.string, value: PropTypes.string };

/* ── snippets ────────────────────────────────────────────────────────── */

const SNIPPETS = {
  python: (twin, endpoint, sess) => {
    if (twin.id === "slack") {
      return `# Point the Slack SDK at your clone
from slack_sdk import WebClient

client = WebClient(
    token="xoxb-sandbox",
    base_url="${endpoint}",
    headers={"X-Clone-Session": "${sess}"},
)

client.chat_postMessage(channel="#support-urgent", text="Hello from the agent")`;
    }
    if (twin.id === "notion") {
      return `# Point notion-client at your clone
from notion_client import Client

notion = Client(
    auth="secret_sandbox",
    base_url="${endpoint}",
    headers={"X-Clone-Session": "${sess}"},
)

notion.pages.create(parent={"database_id": "db_launch"}, properties={"title": "New page"})`;
    }
    return `# Point the ${twin.name} client at your clone
import requests

BASE = "${endpoint}"
HEADERS = {"X-Clone-Session": "${sess}", "Authorization": "Bearer sandbox"}

r = requests.get(f"{BASE}/v1/records", headers=HEADERS)
r.raise_for_status()
print(r.json())`;
  },
  typescript: (twin, endpoint, sess) => {
    if (twin.id === "slack") {
      return `// Point the Slack SDK at your clone
import { WebClient } from "@slack/web-api";

const slack = new WebClient("xoxb-sandbox", {
  slackApiUrl: "${endpoint}/api/",
  headers: { "X-Clone-Session": "${sess}" },
});

await slack.chat.postMessage({
  channel: "#support-urgent",
  text: "Hello from the agent",
});`;
    }
    if (twin.id === "notion") {
      return `// Point @notionhq/client at your clone
import { Client } from "@notionhq/client";

const notion = new Client({
  auth: "secret_sandbox",
  baseUrl: "${endpoint}",
  fetch: (url, init) => fetch(url, {
    ...init,
    headers: { ...init?.headers, "X-Clone-Session": "${sess}" },
  }),
});

await notion.pages.create({
  parent: { database_id: "db_launch" },
  properties: { title: { title: [{ text: { content: "New page" } }] } },
});`;
    }
    return `// Point the ${twin.name} client at your clone
const BASE = "${endpoint}";
const HEADERS = {
  "X-Clone-Session": "${sess}",
  Authorization: "Bearer sandbox",
};

const res = await fetch(\`\${BASE}/v1/records\`, { headers: HEADERS });
const data = await res.json();
console.log(data);`;
  },
  curl: (twin, endpoint, sess) => `curl -sS "${endpoint}/v1/records" \\
  -H "X-Clone-Session: ${sess}" \\
  -H "Authorization: Bearer sandbox"`,
};
