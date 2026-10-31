import PropTypes from "prop-types";
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { enqueueSnackbar } from "notistack";
import { alpha } from "@mui/material/styles";
import {
  Box,
  Button,
  ButtonBase,
  Checkbox,
  Chip,
  IconButton,
  Stack,
  TextField,
  ToggleButton,
  ToggleButtonGroup,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { paths } from "src/routes/paths";
import {
  SAMPLE_GATEWAY_SERVERS,
  sampleGatewayToolsOf,
} from "src/api/simulate-environments/_fixtures/detail/world";
import SideDrawer from "../../components/SideDrawer";
import UiOnlyFlag from "../../components/UiOnlyFlag";
import { UI_ONLY_SAVED } from "../gaps/NeedsInputButton";
import useGatewayMcp from "./useGatewayMcp";

const MONO = "ui-monospace, Menlo, monospace";
const plural = (n, w) => `${n} ${w}${n === 1 ? "" : "s"}`;
const TOGGLE_SX = {
  alignSelf: "flex-start",
  p: "2px",
  bgcolor: "background.neutral",
  "& .MuiToggleButton-root": {
    border: 0,
    borderRadius: "4px !important",
    px: 1,
    py: 0.25,
    fontSize: 12,
    lineHeight: "20px",
    textTransform: "none",
    color: "text.secondary",
  },
  "& .Mui-selected": { color: "text.primary", bgcolor: "background.paper" },
};

// Vel's "Add tools from an MCP server". The Gateway's servers and the tools
// each lists are real (Gateway → MCP); `sample` shows Vel's five. Adding tools
// to a world has no endpoint yet, so Add sends nothing, and listing another
// server's tools by URL is UI only.
export default function AddMcpToolsDrawer({ open, onClose, sample }) {
  const navigate = useNavigate();
  const real = useGatewayMcp(open && !sample);
  const servers = sample ? SAMPLE_GATEWAY_SERVERS : real.servers;
  const toolsOf = sample ? sampleGatewayToolsOf : real.toolsOf;
  const [source, setSource] = useState("gateway");
  const [pick, setPick] = useState(null);
  const [picked, setPicked] = useState({});
  const [url, setUrl] = useState("");

  useEffect(() => {
    if (!open) return;
    setSource("gateway");
    setPick(null);
    setPicked({});
    setUrl("");
  }, [open]);

  const server = servers.find((s) => s.id === pick) || null;
  const listed = server ? toolsOf(server.id) : [];
  const chosen = listed.filter((t) => picked[t.name]);
  const pickServer = (s) => {
    setPick(s.id);
    setPicked(Object.fromEntries(toolsOf(s.id).filter((t) => !t.deprecated && !t.blocked).map((t) => [t.name, true])));
  };
  const uiOnly = () => enqueueSnackbar(UI_ONLY_SAVED, { variant: "info" });

  return (
    <SideDrawer open={open} onClose={onClose} width={{ xs: "100%", sm: 640 }} hideClose>
      <Stack sx={{ height: "100%" }}>
        <Stack direction="row" alignItems="center" spacing={1.5} sx={{ px: 2.5, py: 2, borderBottom: "1px solid", borderColor: "divider" }}>
          <Box flex={1} minWidth={0}>
            <Stack direction="row" alignItems="center" spacing={0.75}>
              <Typography sx={{ typography: "m3", fontWeight: 600 }}>Add tools from an MCP server</Typography>
              <UiOnlyFlag feature="mcpTools" />
            </Stack>
            <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
              Its tools join this world, answered by the world during runs.
            </Typography>
          </Box>
          <IconButton size="small" onClick={onClose} aria-label="Close">
            <Iconify icon="solar:close-linear" width={16} />
          </IconButton>
        </Stack>

        <Box sx={{ px: 2.5, py: 2.5, flex: 1, minHeight: 0, overflowY: "auto" }}>
          <Stack spacing={2}>
            <ToggleButtonGroup
              exclusive
              size="small"
              value={source}
              onChange={(_, v) => {
                if (!v) return;
                setSource(v);
                setPick(null);
                setPicked({});
              }}
              aria-label="Where the tools come from"
              sx={TOGGLE_SX}
            >
              <ToggleButton value="gateway">{`From your Gateway · ${servers.length}`}</ToggleButton>
              <ToggleButton value="url">Another server</ToggleButton>
            </ToggleButtonGroup>

            {source === "gateway" && (
              <Box>
                <Stack direction="row" alignItems="baseline" spacing={1} sx={{ mb: 0.75 }}>
                  <Typography sx={{ typography: "s3", color: "text.subtitle", flex: 1, minWidth: 0 }}>
                    Registered in Gateway → MCP. Their credentials stay there, and Live calls go through it.
                  </Typography>
                  <ButtonBase
                    onClick={() => {
                      onClose();
                      navigate(paths.dashboard.gateway.mcp);
                    }}
                    sx={{ typography: "s3", fontWeight: 600, color: "text.secondary", borderRadius: "4px", flexShrink: 0, "&:hover": { color: "text.primary" } }}
                  >
                    Manage in Gateway
                  </ButtonBase>
                </Stack>
                {servers.length === 0 ? (
                  <Typography sx={{ typography: "s3", color: "text.subtitle", p: 1.5, border: "1px solid", borderColor: "divider", borderRadius: 1 }}>
                    {!sample && real.isLoading
                      ? "Reading your Gateway's MCP servers…"
                      : "No MCP servers in your Gateway yet. Add one in Gateway → MCP, or connect another server by URL."}
                  </Typography>
                ) : (
                  <Box sx={{ border: "1px solid", borderColor: "divider", borderRadius: 1, overflow: "hidden" }}>
                    {servers.map((s, i) => (
                      <ServerRow key={s.id} server={s} first={i === 0} on={pick === s.id} onPick={() => pickServer(s)} />
                    ))}
                  </Box>
                )}
              </Box>
            )}

            {source === "url" && (
              <Stack spacing={1.5}>
                <TextField
                  size="small"
                  fullWidth
                  label="Server URL"
                  placeholder="https://mcp.yourapp.com/mcp"
                  value={url}
                  onChange={(e) => setUrl(e.target.value)}
                  helperText="We connect and list the tools it exposes. You pick which join the world."
                  InputProps={{ sx: { fontFamily: MONO } }}
                />
                <Box>
                  <Button
                    size="small"
                    variant="outlined"
                    disabled={!url.trim()}
                    onClick={uiOnly}
                    startIcon={<Iconify icon="solar:plug-circle-linear" width={16} />}
                    sx={{ color: "text.primary", borderColor: "divider" }}
                  >
                    Connect and list tools
                  </Button>
                </Box>
              </Stack>
            )}

            {server && (
              <Box>
                <Stack direction="row" alignItems="center" spacing={1} sx={{ mb: 0.75 }}>
                  <Typography sx={{ typography: "s2", fontWeight: 600 }}>{`${server.label} · ${plural(listed.length, "tool")}`}</Typography>
                  <Typography noWrap sx={{ typography: "s3", color: "text.subtitle", fontFamily: MONO, minWidth: 0 }}>
                    {server.host}
                  </Typography>
                  <Chip size="small" label="Through your Gateway" sx={{ flexShrink: 0 }} />
                </Stack>
                <Box sx={{ border: "1px solid", borderColor: "divider", borderRadius: 1 }}>
                  {listed.map((t, i) => (
                    <ToolRow
                      key={t.name}
                      tool={t}
                      first={i === 0}
                      checked={!!picked[t.name]}
                      onCheck={(on) => setPicked((p) => ({ ...p, [t.name]: on }))}
                    />
                  ))}
                </Box>
                <Typography sx={{ typography: "s3", color: "text.subtitle", mt: 1 }}>
                  Each tool starts Simulated; change it per tool on the World tab. Live calls go through your Gateway.
                </Typography>
              </Box>
            )}
          </Stack>
        </Box>

        <Stack direction="row" alignItems="center" spacing={1.5} sx={{ px: 2.5, py: 1.75, borderTop: "1px solid", borderColor: "divider" }}>
          <Box flex={1} />
          <Button size="small" onClick={onClose} sx={{ color: "text.secondary" }}>
            Cancel
          </Button>
          <Button
            size="small"
            variant="contained"
            color="primary"
            disabled={!chosen.length}
            onClick={() => {
              uiOnly();
              onClose();
            }}
          >
            {chosen.length ? `Add ${plural(chosen.length, "tool")}` : "Add tools"}
          </Button>
        </Stack>
      </Stack>
    </SideDrawer>
  );
}
AddMcpToolsDrawer.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func.isRequired,
  sample: PropTypes.bool,
};

function ServerRow({ server, first, on, onPick }) {
  const down = server.status !== "connected";
  return (
    <ButtonBase
      disabled={down}
      onClick={onPick}
      aria-pressed={on}
      sx={{
        width: "100%",
        justifyContent: "flex-start",
        textAlign: "left",
        gap: 1.25,
        px: 1.5,
        py: 1,
        borderTop: first ? "none" : "1px solid",
        borderColor: "divider",
        bgcolor: on ? (t) => alpha(t.palette.text.primary, 0.06) : "transparent",
        "&:hover": { bgcolor: (t) => alpha(t.palette.text.primary, 0.04) },
      }}
    >
      <Iconify
        icon={on ? "solar:check-circle-bold" : "solar:plug-circle-linear"}
        width={16}
        sx={{ color: on ? "text.primary" : "text.disabled", flexShrink: 0 }}
      />
      <Box flex={1} minWidth={0}>
        <Stack direction="row" alignItems="baseline" spacing={1}>
          <Typography sx={{ typography: "s2", fontWeight: 500, color: down ? "text.disabled" : "text.primary" }}>
            {server.label}
          </Typography>
          <Typography noWrap sx={{ typography: "s3", color: "text.subtitle", fontFamily: MONO, minWidth: 0 }}>
            {server.host}
          </Typography>
        </Stack>
        {down && (
          <Typography sx={{ typography: "s3", color: "warning.main" }}>{`Unreachable: ${server.error}`}</Typography>
        )}
      </Box>
      <Typography sx={{ typography: "s3", color: "text.subtitle", flexShrink: 0, fontVariantNumeric: "tabular-nums" }}>
        {down ? (server.checkedAgo ? `Checked ${server.checkedAgo}` : "-") : plural(server.toolCount, "tool")}
      </Typography>
    </ButtonBase>
  );
}
ServerRow.propTypes = {
  server: PropTypes.object.isRequired,
  first: PropTypes.bool,
  on: PropTypes.bool,
  onPick: PropTypes.func.isRequired,
};

function ToolRow({ tool, first, checked, onCheck }) {
  const line = tool.blocked
    ? "The Gateway's guardrails block it: the world answers it, but it never goes Live."
    : tool.desc;
  return (
    <Stack direction="row" alignItems="flex-start" spacing={1} sx={{ px: 1, py: 0.75, borderTop: first ? "none" : "1px solid", borderColor: "divider" }}>
      <Checkbox
        size="small"
        checked={checked}
        onChange={(e) => onCheck(e.target.checked)}
        inputProps={{ "aria-label": tool.name }}
        sx={{ p: 0.5, color: "text.disabled", "&.Mui-checked": { color: "text.primary" } }}
      />
      <Box flex={1} minWidth={0} sx={{ pt: 0.25 }}>
        <Stack direction="row" alignItems="center" spacing={0.75} sx={{ flexWrap: "wrap", rowGap: 0.5 }}>
          <Typography sx={{ typography: "s2", fontWeight: 600, fontFamily: MONO }}>{tool.name}</Typography>
          <Chip size="small" label={tool.readOnly ? "Reads" : "Writes"} />
          {tool.destructive && <Chip size="small" label="Destructive" />}
          {tool.openWorld && <Chip size="small" label="Open world" />}
          {tool.blocked && <Chip size="small" color="warning" label="Blocked in Gateway · Simulated only" />}
          {tool.deprecated && <Chip size="small" color="warning" label={`Deprecated · use ${tool.deprecated}`} />}
        </Stack>
        <Typography sx={{ typography: "s3", color: "text.subtitle" }}>{line}</Typography>
      </Box>
    </Stack>
  );
}
ToolRow.propTypes = {
  tool: PropTypes.object.isRequired,
  first: PropTypes.bool,
  checked: PropTypes.bool,
  onCheck: PropTypes.func.isRequired,
};
