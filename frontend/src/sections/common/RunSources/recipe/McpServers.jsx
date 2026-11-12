// MCP servers of a harness recipe (lane Rb MUST 1): the Environment's
// server first (`acme-support · stdio · 12 tools · from this task suite's
// environment`), then `+ Add MCP server` (name, command or URL, env secrets
// by reference, `Test` → its tool list, tagged `Simulated`: it stands in
// for starting the command and calling tools/list). An added server goes
// into the recipe with its tool names, so the script's MCP_SERVERS, the run
// config and the run's traces carry it. Adding one widens the recipe (a
// confirmation on someone else's machine).
import React, { useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  Checkbox,
  Chip,
  Collapse,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import { plural } from "src/sections/common/vocab";
import { mcpTestTools } from "./recipeModel";
import { SimulatedTag } from "src/sections/common/Connect/connectParts";
import { mcpListingEvent } from "src/sections/common/Connect/connectCopy";

/** `10 of 12 tools` when some are unticked, else `12 tools`. */
export function toolCountWords(s) {
  const all = Number(s?.tools) || (s?.tool_names ?? []).length;
  if (!all) return null;
  const off = (s?.disabled_tools ?? []).length;
  return off ? `${all - off} of ${all} tools` : plural(all, "tool");
}

/** Tick or untick one tool of a server (pure): the new server. */
export function toggleServerTool(server, tool) {
  const off = new Set(server?.disabled_tools ?? []);
  if (off.has(tool)) off.delete(tool);
  else off.add(tool);
  return {
    ...server,
    disabled_tools: (server?.tool_names ?? []).filter((t) => off.has(t)),
  };
}

export function mcpLine(s) {
  return [
    s.name,
    s.url ? "http" : s.transport ?? "stdio",
    toolCountWords(s),
    s.from === "environment" ? "from this task suite's environment" : null,
  ]
    .filter(Boolean)
    .join(" · ");
}

function AddMcpDialog({ open, onClose, onAdd, taken }) {
  const [name, setName] = useState("");
  const [target, setTarget] = useState("");
  const [secret, setSecret] = useState("");
  const [tools, setTools] = useState(null);
  const reset = () => {
    setName("");
    setTarget("");
    setSecret("");
    setTools(null);
  };
  const url = /^https?:\/\//.test(target.trim());
  const dup = taken.includes(name.trim());
  const ready = name.trim() && target.trim() && !dup;
  const draft = {
    name: name.trim(),
    transport: url ? "http" : "stdio",
    command: url ? null : target.trim(),
    url: url ? target.trim() : null,
    env: secret.trim() ? [secret.trim()] : [],
    from: "recipe",
  };
  // Adding without a Test still lists the tools the server answers with.
  const names = tools ?? mcpTestTools(draft);
  const server = { ...draft, tools: names.length, tool_names: names };
  return (
    <Dialog
      open={open}
      onClose={() => {
        reset();
        onClose();
      }}
      maxWidth="xs"
      fullWidth
    >
      <DialogTitle typography="m3">Add MCP server</DialogTitle>
      <DialogContent>
        <Stack spacing={1.5} sx={{ pt: 0.5 }}>
          <TextField
            size="small"
            label="Name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            error={dup}
            helperText={
              dup ? "This recipe already has a server with that name" : " "
            }
            autoFocus
          />
          <TextField
            size="small"
            label="Command or URL"
            placeholder="npx -y @modelcontextprotocol/server-github"
            value={target}
            onChange={(e) => {
              setTarget(e.target.value);
              setTools(null);
            }}
            helperText={
              url ? "Streamable HTTP" : "stdio: started in each try's folder"
            }
          />
          <TextField
            size="small"
            label="Env secret (by reference)"
            placeholder="GITHUB_TOKEN"
            value={secret}
            onChange={(e) => setSecret(e.target.value.toUpperCase())}
            helperText="The name of a workspace secret; the value never shows here"
          />
          {tools ? (
            <Box data-testid="mcp-test-tools">
              <Typography typography="s2" fontWeight="fontWeightMedium">
                {`${plural(tools.length, "tool")} found`}
              </Typography>
              <Typography
                typography="s2"
                color="text.secondary"
                sx={{ fontFamily: "monospace" }}
              >
                {tools.join(", ")}
              </Typography>
              <SimulatedTag
                event={mcpListingEvent(
                  server.command ?? server.url ?? server.name,
                )}
              />
            </Box>
          ) : null}
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button
          size="small"
          disabled={!target.trim()}
          onClick={() => setTools(mcpTestTools(draft))}
          data-testid="mcp-test"
        >
          Test
        </Button>
        <Box sx={{ flex: 1 }} />
        <Button
          size="small"
          onClick={() => {
            reset();
            onClose();
          }}
        >
          Cancel
        </Button>
        <Button
          size="small"
          variant="contained"
          disabled={!ready}
          data-testid="mcp-add-server"
          onClick={() => {
            onAdd(server);
            reset();
          }}
        >
          Add server
        </Button>
      </DialogActions>
    </Dialog>
  );
}
AddMcpDialog.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func.isRequired,
  onAdd: PropTypes.func.isRequired,
  taken: PropTypes.array,
};

export default function McpServers({ servers, onChange, readOnly }) {
  const [open, setOpen] = useState(false);
  // the server whose tool list is open (tick the tools the run may call)
  const [choosing, setChoosing] = useState(null);
  const picked = servers.find((s) => s.name === choosing) ?? null;
  return (
    <Stack spacing={0.5} sx={{ minWidth: 0, flex: 1 }}>
      <Stack
        direction="row"
        alignItems="center"
        gap={0.75}
        flexWrap="wrap"
        sx={{ minWidth: 0 }}
      >
        {servers.length ? (
          servers.map((s) => (
            <React.Fragment key={s.name}>
              <CustomTooltip
                key={s.name}
                show
                size="small"
                placement="top"
                title={
                  s.tool_names?.length
                    ? `${s.command ?? s.url ?? s.name} · ${s.tool_names.join(", ")}`
                    : s.command ?? s.url ?? s.name
                }
              >
                <Chip
                  size="small"
                  variant="outlined"
                  icon={<Iconify icon="mdi:tools" width={14} />}
                  label={mcpLine(s)}
                  onDelete={
                    readOnly || s.from === "environment"
                      ? undefined
                      : () => onChange(servers.filter((x) => x.name !== s.name))
                  }
                  data-testid="mcp-chip"
                  sx={{ typography: "s2", maxWidth: "100%" }}
                />
              </CustomTooltip>
              {!readOnly && (s.tool_names ?? []).length ? (
                <Button
                  size="small"
                  onClick={() =>
                    setChoosing(choosing === s.name ? null : s.name)
                  }
                  aria-label={`Choose the tools of ${s.name}`}
                  aria-expanded={choosing === s.name}
                  data-testid="mcp-choose-tools"
                  sx={{ typography: "s2", minWidth: 0, px: 0.75 }}
                >
                  {choosing === s.name ? "Hide tools" : "Choose tools"}
                </Button>
              ) : null}
            </React.Fragment>
          ))
        ) : (
          <Typography typography="s2" color="text.secondary">
            None
          </Typography>
        )}
        {readOnly ? null : (
          <Button
            size="small"
            startIcon={<Iconify icon="mdi:plus" width={14} />}
            onClick={() => setOpen(true)}
            sx={{ typography: "s2", minWidth: 0, px: 0.75 }}
            data-testid="add-mcp"
          >
            Add MCP server
          </Button>
        )}
        <AddMcpDialog
          open={open}
          onClose={() => setOpen(false)}
          taken={servers.map((s) => s.name)}
          onAdd={(s) => {
            onChange([...servers, s]);
            setOpen(false);
          }}
        />
      </Stack>
      <Collapse in={Boolean(picked)} unmountOnExit>
        {picked ? (
          <Box
            data-testid="mcp-tool-list"
            sx={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fill, minmax(170px, 1fr))",
              columnGap: 1,
            }}
          >
            {(picked.tool_names ?? []).map((t) => {
              const on = !(picked.disabled_tools ?? []).includes(t);
              return (
                <Stack
                  key={t}
                  direction="row"
                  alignItems="center"
                  sx={{ minWidth: 0 }}
                >
                  <Checkbox
                    size="small"
                    checked={on}
                    onChange={() =>
                      onChange(
                        servers.map((x) =>
                          x.name === picked.name ? toggleServerTool(x, t) : x,
                        ),
                      )
                    }
                    inputProps={{ "aria-label": `${picked.name} · ${t}` }}
                    sx={{ p: 0.5 }}
                  />
                  <Typography
                    typography="s3"
                    noWrap
                    sx={{
                      fontFamily: "monospace",
                      color: on ? "text.primary" : "text.disabled",
                    }}
                  >
                    {t}
                  </Typography>
                </Stack>
              );
            })}
          </Box>
        ) : null}
      </Collapse>
    </Stack>
  );
}

McpServers.propTypes = {
  servers: PropTypes.array.isRequired,
  onChange: PropTypes.func.isRequired,
  readOnly: PropTypes.bool,
};
