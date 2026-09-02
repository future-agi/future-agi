import PropTypes from "prop-types";
import { useMemo } from "react";
import { alpha } from "@mui/material/styles";
import {
  Dialog, DialogTitle, DialogContent, DialogActions,
  Box, Stack, Typography, Button, IconButton,
} from "@mui/material";
import Iconify from "src/components/iconify";
import TwinLogo from "../components/TwinLogo";
import GenericSandboxMock from "./GenericSandboxMock";

/**
 * OpenAPI spec preview modal for a twin sandbox. Shared between the
 * Twin detail page and the workspace Overview panel — both surface
 * an OpenAPI button next to the sandbox preview and route it here.
 */
export function OpenApiDialog({ open, onClose, twin, endpoint }) {
  const spec = useMemo(() => stubOpenApiFor(twin, endpoint), [twin, endpoint]);
  const copy = () => navigator.clipboard?.writeText(spec.json);
  return (
    <Dialog
      open={open} onClose={onClose} maxWidth="md" fullWidth
      PaperProps={{
        sx: { borderRadius: 2, bgcolor: "background.paper", backgroundImage: "none", border: "1px solid", borderColor: "divider" },
      }}
    >
      <DialogTitle sx={{ p: 2, pb: 1.5 }}>
        <Stack direction="row" alignItems="center" spacing={1.25}>
          <TwinLogo twin={twin} width={20} />
          <Box flex={1} minWidth={0}>
            <Typography sx={{ typography: "s1_2", fontWeight: 700 }}>
              {twin?.name || "Sandbox"} OpenAPI
            </Typography>
            <Typography sx={{ typography: "s3", color: "text.subtitle" }} noWrap>
              {endpoint || "—"}
            </Typography>
          </Box>
          <Button size="small" variant="outlined"
            onClick={copy}
            startIcon={<Iconify icon="solar:copy-linear" width={12} />}
            sx={{ typography: "s2", fontWeight: 700, color: "text.primary", borderColor: "divider" }}
          >
            Copy JSON
          </Button>
          <IconButton size="small" onClick={onClose}>
            <Iconify icon="solar:close-circle-linear" width={16} sx={{ color: "text.subtitle" }} />
          </IconButton>
        </Stack>
      </DialogTitle>
      <DialogContent sx={{ p: 2, pt: 1 }} dividers>
        <Stack spacing={1}>
          {spec.endpoints.map((e) => (
            <Stack key={`${e.method} ${e.path}`} direction="row" alignItems="flex-start" spacing={1.5}
              sx={{ p: 1.25, borderRadius: 1, border: "1px solid", borderColor: "divider" }}
            >
              <Typography sx={{
                px: 0.75, py: 0.25, borderRadius: 0.5,
                fontSize: 10, fontWeight: 700, minWidth: 44, textAlign: "center", flexShrink: 0,
                color: methodColor(e.method).fg,
                bgcolor: (t) => alpha(methodColor(e.method).fg, t.palette.mode === "dark" ? 0.16 : 0.09),
              }}>
                {e.method}
              </Typography>
              <Box flex={1} minWidth={0}>
                <Typography sx={{
                  typography: "s2", fontWeight: 700,
                  fontFamily: "ui-monospace, Menlo, monospace",
                }}>
                  {e.path}
                </Typography>
                <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
                  {e.summary}
                </Typography>
              </Box>
            </Stack>
          ))}
        </Stack>
        <Box sx={{
          mt: 2, p: 1.5, borderRadius: 1,
          bgcolor: "background.neutral", maxHeight: 220, overflow: "auto",
        }}>
          <Typography component="pre" sx={{
            typography: "s3", fontFamily: "ui-monospace, Menlo, monospace",
            color: "text.primary", whiteSpace: "pre", m: 0,
          }}>
            {spec.json}
          </Typography>
        </Box>
      </DialogContent>
      <DialogActions sx={{ p: 1.5 }}>
        <Button onClick={onClose} sx={{ typography: "s2", fontWeight: 600, color: "text.secondary" }}>
          Close
        </Button>
      </DialogActions>
    </Dialog>
  );
}
OpenApiDialog.propTypes = {
  open: PropTypes.bool, onClose: PropTypes.func,
  twin: PropTypes.object, endpoint: PropTypes.string,
};

/**
 * Fullscreen sandbox preview modal. Renders the same SandboxMock the
 * inline preview uses, but expanded to fill the viewport so users
 * can inspect the full surface.
 */
export function OpenSurfaceDialog({ open, onClose, twin, env, SandboxMock }) {
  return (
    <Dialog
      open={open} onClose={onClose} fullScreen
      PaperProps={{ sx: { bgcolor: "background.default", backgroundImage: "none" } }}
    >
      <Stack
        direction="row" alignItems="center" spacing={1.5}
        sx={{ px: 3, py: 1.75, borderBottom: "1px solid", borderColor: "divider", flexShrink: 0 }}
      >
        <TwinLogo twin={twin} width={20} />
        <Box flex={1} minWidth={0}>
          <Typography sx={{ typography: "s1_2", fontWeight: 700 }} noWrap>
            {twin?.name || "Sandbox"} · {env?.name}
          </Typography>
          <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
            Full-window sandbox preview · non-interactive · agent writes appear as they land
          </Typography>
        </Box>
        <Button
          variant="outlined" size="small"
          onClick={onClose}
          startIcon={<Iconify icon="solar:close-circle-linear" width={13} />}
          sx={{ typography: "s2", fontWeight: 700, color: "text.primary", borderColor: "divider" }}
        >
          Close
        </Button>
      </Stack>
      <Box sx={{ flex: 1, minHeight: 0, p: 3 }}>
        <Box sx={{ height: "100%", minHeight: 0, "& > *": { height: "100% !important" } }}>
          {SandboxMock
            ? <SandboxMock workspace={env?.name} />
            : <GenericSandboxMock twin={twin} />}
        </Box>
      </Box>
    </Dialog>
  );
}
OpenSurfaceDialog.propTypes = {
  open: PropTypes.bool, onClose: PropTypes.func,
  twin: PropTypes.object, env: PropTypes.object, SandboxMock: PropTypes.elementType,
};

/* ── helpers ─────────────────────────────────────────────────────────── */

function methodColor(m) {
  return ({
    GET: { fg: "#0EA5E9" },
    POST: { fg: "#16A34A" },
    PUT: { fg: "#F59E0B" },
    PATCH: { fg: "#7857FC" },
    DELETE: { fg: "#DC2626" },
  })[m] || { fg: "#6B7280" };
}

function stubOpenApiFor(twin, endpoint) {
  if (!twin) return { endpoints: [], json: "{}" };
  const paths = {
    slack: [
      { method: "GET", path: "/api/conversations.list", summary: "List channels the agent can see" },
      { method: "POST", path: "/api/chat.postMessage", summary: "Post a message to a channel or thread" },
      { method: "POST", path: "/api/chat.postEphemeral", summary: "Post a message visible only to the target user" },
      { method: "GET", path: "/api/users.info", summary: "Look up a user by id" },
    ],
    notion: [
      { method: "GET", path: "/v1/databases/{id}/query", summary: "Query a database" },
      { method: "POST", path: "/v1/pages", summary: "Create a page" },
      { method: "PATCH", path: "/v1/pages/{id}", summary: "Update a page's properties" },
      { method: "POST", path: "/v1/comments", summary: "Add a comment on a page or block" },
    ],
    gmail: [
      { method: "GET", path: "/gmail/v1/users/me/messages", summary: "List messages in the inbox" },
      { method: "POST", path: "/gmail/v1/users/me/messages/send", summary: "Send a message" },
      { method: "POST", path: "/gmail/v1/users/me/messages/{id}/modify", summary: "Apply or remove labels" },
    ],
    salesforce: [
      { method: "GET", path: "/services/data/v58.0/query?q=…", summary: "Run a SOQL query" },
      { method: "POST", path: "/services/data/v58.0/sobjects/Task", summary: "Create a Task" },
      { method: "PATCH", path: "/services/data/v58.0/sobjects/Opportunity/{id}", summary: "Update an opportunity" },
    ],
  };
  const endpoints = paths[twin.id] || [
    { method: "GET", path: `/api/${twin.id}/list`, summary: `List ${twin.name} entities` },
    { method: "POST", path: `/api/${twin.id}/create`, summary: `Create a ${twin.name} entity` },
  ];
  const json = JSON.stringify({
    openapi: "3.1.0",
    info: {
      title: `${twin.name} sandbox`,
      version: "1.0.0",
      description: `Clone-backed sandbox for ${twin.name}. Auth via per-run bearer token.`,
    },
    servers: [{ url: endpoint || `https://${twin.id}.sandbox.futureagi.com` }],
    paths: Object.fromEntries(endpoints.map((e) => [
      e.path.split("?")[0],
      { [e.method.toLowerCase()]: { summary: e.summary } },
    ])),
  }, null, 2);
  return { endpoints, json };
}
