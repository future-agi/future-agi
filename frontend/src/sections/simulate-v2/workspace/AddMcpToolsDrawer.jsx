import PropTypes from "prop-types";
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  Box,
  Button,
  ButtonBase,
  Checkbox,
  Chip,
  CircularProgress,
  IconButton,
  Stack,
  Tooltip,
  Typography,
} from "@mui/material";
import { alpha } from "@mui/material/styles";
import Iconify from "src/components/iconify";
import SideDrawer from "../components/SideDrawer";
import { useSimStore } from "../store";
import { Field, SegmentedToggle } from "../environments/StartEnvironment";
import SecretRefField from "../agents/connect/SecretRefField";
import { isSecretRef } from "../_mock/secretRefs";
import { listMcpTools } from "../_mock/mcpServers";
import {
  addMcpToolsPlan,
  agentCallsOf,
  agentServerIdsOf,
  mcpPlacementOf,
  toolReachOf,
} from "../_mock/worldTools";
import { worldToolsFor } from "../_mock/toolFit";
import {
  GATEWAY_MCP_PATH,
  gatewayHiddenCount,
  gatewayServerForHost,
  gatewayServerTools,
  gatewayServersOf,
} from "../_mock/gatewayMcp";
import { sendToBuilder } from "../_mock/builderPromptBus";
import { focusRing } from "../components/clickable";

const MONO = "ui-monospace, Menlo, monospace";
const TRANSPORTS = [
  { value: "http", label: "HTTP" },
  { value: "sse", label: "SSE" },
  { value: "stdio", label: "stdio" },
];
const plural = (n, w) => `${n} ${w}${n === 1 ? "" : "s"}`;

/* Built to run on the person's machine through the CLI, not in our sandbox. */
const runsLocally = (env) =>
  env?.runsOn === "local" || env?.builtFrom?.worldSource?.runsOn === "local";

/**
 * Add tools to an environment's world from an MCP server — the way agent
 * platforms attach one (ElevenLabs, OpenAI's remote MCP): the server, then
 * the tools it lists, picked one by one. Picked tools join the world
 * Simulated; a tool that writes can never go Live.
 *
 * Servers registered in the workspace's Gateway come first: picking one
 * lists its tools from the Gateway's catalogue, with no URL or token to
 * enter again — the credentials stay there, Live calls go through it, and a
 * tool its guardrails block is answered by the world only. Another server,
 * by URL or command, is the second way in; a command (stdio) only for an
 * environment that runs on the person's machine.
 *
 * `prefill` opens straight on a server's tools ({ server, tools, reason }):
 * the agent's own server, whose tools the world doesn't answer yet — through
 * the Gateway when the Gateway has it — or a source's new tools on refresh.
 */
export default function AddMcpToolsDrawer({
  open,
  onClose,
  env,
  envState,
  patch,
  onGo,
  prefill = null,
}) {
  const { dispatch } = useSimStore();
  const navigate = useNavigate();
  const gwServers = gatewayServersOf();
  const [source, setSource] = useState(gwServers.length ? "gateway" : "url");
  const [gwPick, setGwPick] = useState(null);
  const [transport, setTransport] = useState("http");
  const [value, setValue] = useState("");
  const [tokenRef, setTokenRef] = useState("");
  const [listing, setListing] = useState(false);
  const [listed, setListed] = useState(null);
  const [error, setError] = useState(null);
  const [picked, setPicked] = useState({});
  const [done, setDone] = useState(null);

  const world = worldToolsFor(env, envState);
  const agentServerIds = agentServerIdsOf(envState);
  const placeOf = (t) =>
    mcpPlacementOf(t, listed.server, world, { agentServerIds });
  const agent = envState?.agent || null;
  const calls = agentCallsOf(env, envState);
  const tokenOk = !tokenRef.trim() || isSecretRef(tokenRef);
  const local = runsLocally(env);
  const stdioBlocked = transport === "stdio" && !local;
  /* What starts ticked: tools new to the world that the Gateway doesn't hold
     back. Opened on the agent's own server, its tools are ticked whatever the
     Gateway says — the agent calls them regardless. */
  const defaultPicks = (tools, server, own = false) =>
    Object.fromEntries(
      tools
        .filter(
          (t) =>
            mcpPlacementOf(t, server, world, { agentServerIds }).status !==
              "inWorld" &&
            !t.deprecated &&
            (own || !t.blocked),
        )
        .map((t) => [t.name, true]),
    );

  useEffect(() => {
    if (!open) return;
    setSource(gwServers.length ? "gateway" : "url");
    setGwPick(null);
    setTransport(prefill?.server?.transport || "http");
    setValue(prefill?.server?.value || "");
    setTokenRef("");
    setError(null);
    setDone(null);
    setListing(false);
    /* A prefilled server, through the Gateway when it's registered there. */
    const gw =
      prefill && !prefill.server?.gateway
        ? gatewayServerForHost(prefill.server?.host)
        : null;
    const viaGw = gw ? gatewayServerTools(gw.gateway.serverId) : null;
    const names = new Set((prefill?.tools || []).map((t) => t.name));
    const first = viaGw?.ok
      ? {
          server: viaGw.server,
          tools: viaGw.tools.filter((t) => names.has(t.name)),
        }
      : prefill
        ? { server: prefill.server, tools: prefill.tools }
        : null;
    setListed(first);
    setPicked(
      first
        ? defaultPicks(first.tools, first.server, prefill?.reason !== "refresh")
        : {},
    );
  }, [open]); // eslint-disable-line react-hooks/exhaustive-deps

  const list = () => {
    setError(null);
    setListing(true);
    setTimeout(() => {
      const r = listMcpTools({
        transport,
        value,
        tokenRef: tokenRef.trim() || null,
      });
      setListing(false);
      if (!r.ok) {
        setError(r.error);
        return;
      }
      setListed({ server: r.server, tools: r.tools });
      setPicked(defaultPicks(r.tools, r.server));
    }, 800);
  };
  /* The Gateway already listed this server's tools: nothing to connect to. */
  const pickGateway = (id) => {
    setError(null);
    setGwPick(id);
    const r = gatewayServerTools(id);
    if (!r.ok) {
      setListed(null);
      setError(r.error);
      return;
    }
    setListed({ server: r.server, tools: r.tools });
    setPicked(defaultPicks(r.tools, r.server));
  };
  const switchSource = (v) => {
    setSource(v);
    setListed(null);
    setGwPick(null);
    setError(null);
  };

  const chosen = listed
    ? listed.tools.filter(
        (t) => picked[t.name] && placeOf(t).status !== "inWorld",
      )
    : [];
  const viaGateway = !!listed?.server?.gateway;
  const add = () => {
    const plan = addMcpToolsPlan(env, envState, {
      server: listed.server,
      picked: chosen,
    });
    if (plan.envPatch)
      dispatch({
        type: "patchEnvironment",
        envId: env.id,
        patch: plan.envPatch,
      });
    patch(plan.statePatch);
    setDone(plan);
  };
  /* What adding does to versions, said before it happens. */
  const draftNote = (() => {
    const p =
      listed && chosen.length
        ? addMcpToolsPlan(env, envState, {
            server: listed.server,
            picked: chosen,
          })
        : null;
    if (!p) return null;
    if (p.inPlace)
      return `Added to environment ${p.versionLabel} in place — no run has used it yet.`;
    return `Waits as an unsaved edit${p.waiting ? `, beside your ${plural(p.waiting, "other")}` : ""}, until you save the next environment version. No scenario's proof changes.`;
  })();
  const hidden = gatewayHiddenCount();
  /* Asks the builder to write scenarios that call the new tools — it answers like any typed request. */
  const writeScenarios = (plan) => {
    onClose();
    onGo?.("scenarios");
    const names = plan.added.map((t) => t.name);
    sendToBuilder(
      `Add ${Math.max(3, Math.min(8, names.length * 2))} scenarios for conversations that need ${names.join(", ")}`,
    );
  };

  return (
    <SideDrawer open={open} onClose={onClose} width={{ xs: "100%", sm: 640 }}>
      <Stack sx={{ height: "100%" }}>
        <Stack
          direction="row"
          alignItems="center"
          spacing={1.5}
          sx={{
            px: 2.5,
            py: 2,
            borderBottom: "1px solid",
            borderColor: "divider",
          }}
        >
          <Box flex={1} minWidth={0}>
            <Typography sx={{ typography: "m2", fontWeight: 600 }}>
              Add tools from an MCP server
            </Typography>
            <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
              {prefill?.reason === "refresh"
                ? `${prefill.server.label} lists tools this world doesn't have yet.`
                : prefill
                  ? `${agent?.name || "The agent"} has this server attached — add its tools so the world answers them.`
                  : "Its tools join this world, answered by the world during runs."}
            </Typography>
          </Box>
          <IconButton size="small" onClick={onClose} aria-label="Close">
            <Iconify icon="solar:close-linear" width={16} />
          </IconButton>
        </Stack>

        {/* A block scroller, not a flex column: a fullWidth Field grows with
            flex: 1, and in a column that fills the drawer it splits the
            empty height between the fields. */}
        <Box
          sx={{ px: 2.5, py: 2.5, flex: 1, minHeight: 0, overflowY: "auto" }}
        >
          <Stack spacing={2}>
            {done ? (
              <DoneState
                plan={done}
                server={listed.server}
                onWrite={
                  onGo && done.added.length ? () => writeScenarios(done) : null
                }
              />
            ) : (
              <>
                {!prefill && gwServers.length > 0 && (
                  <SegmentedToggle
                    value={source}
                    onChange={switchSource}
                    options={[
                      {
                        value: "gateway",
                        label: `From your Gateway · ${gwServers.length}`,
                      },
                      { value: "url", label: "Another server" },
                    ]}
                  />
                )}

                {!prefill && source === "gateway" && (
                  <Box>
                    <Stack
                      direction="row"
                      alignItems="baseline"
                      spacing={1}
                      sx={{ mb: 0.75 }}
                    >
                      <Typography
                        sx={{
                          typography: "s3",
                          color: "text.tertiary",
                          flex: 1,
                          minWidth: 0,
                        }}
                      >
                        {`Registered in Gateway → MCP. Their credentials stay there, and Live calls go through it.${hidden ? ` ${hidden} more are outside its allowed servers.` : ""}`}
                      </Typography>
                      <ButtonBase
                        onClick={() => {
                          onClose();
                          navigate(GATEWAY_MCP_PATH);
                        }}
                        sx={{
                          ...focusRing,
                          typography: "s3",
                          fontWeight: 600,
                          color: "text.secondary",
                          borderRadius: "4px",
                          flexShrink: 0,
                          "&:hover": { color: "text.primary" },
                        }}
                      >
                        Manage in Gateway
                      </ButtonBase>
                    </Stack>
                    <Box
                      sx={{
                        border: "1px solid",
                        borderColor: "divider",
                        borderRadius: 1,
                        overflow: "hidden",
                      }}
                    >
                      {gwServers.map((g, i) => (
                        <GatewayServerRow
                          key={g.id}
                          server={g}
                          first={i === 0}
                          on={gwPick === g.id}
                          onPick={() => pickGateway(g.id)}
                        />
                      ))}
                    </Box>
                    {error && (
                      <Typography
                        sx={{ typography: "s3", color: "error.main", mt: 1 }}
                      >
                        {error}
                      </Typography>
                    )}
                  </Box>
                )}

                {!prefill && source === "url" && (
                  <>
                    <Box>
                      <Typography
                        sx={{ typography: "s3", fontWeight: 600, mb: 0.5 }}
                      >
                        Transport
                      </Typography>
                      <SegmentedToggle
                        value={transport}
                        onChange={(t) => {
                          setTransport(t);
                          setListed(null);
                          setError(null);
                        }}
                        options={TRANSPORTS}
                      />
                    </Box>
                    {stdioBlocked ? (
                      <Typography
                        sx={{
                          typography: "s3",
                          color: "text.secondary",
                          p: 1.25,
                          borderRadius: 1,
                          border: "1px solid",
                          borderColor: "divider",
                        }}
                      >
                        A stdio server is a command, and a pasted command never
                        runs in our sandbox. It works for an environment that
                        runs on your machine — use HTTP or SSE here.
                      </Typography>
                    ) : (
                      <Field
                        label={transport === "stdio" ? "Command" : "Server URL"}
                        required
                        mono
                        fullWidth
                        placeholder={
                          transport === "stdio"
                            ? "npx -y @acme/mcp-server"
                            : "https://mcp.yourapp.com/mcp"
                        }
                        value={value}
                        onChange={(v) => {
                          setValue(v);
                          setListed(null);
                        }}
                        helper={
                          transport === "stdio"
                            ? "Runs on your machine when the environment does, through the CLI."
                            : "We connect and list the tools it exposes — you pick which join the world."
                        }
                      />
                    )}
                    {transport !== "stdio" && (
                      <SecretRefField
                        label="Auth token (optional)"
                        fullWidth
                        placeholder="vault://acme/sim/mcp_token"
                        value={tokenRef}
                        onChange={setTokenRef}
                        helper="Sent as the Authorization header. Stored as a reference, never the key itself."
                      />
                    )}
                    {!listed && !stdioBlocked && (
                      <Stack direction="row" alignItems="center" spacing={1.25}>
                        <Button
                          variant="outlined"
                          size="small"
                          onClick={list}
                          disabled={listing || !value.trim() || !tokenOk}
                          startIcon={
                            listing ? (
                              <CircularProgress
                                size={12}
                                thickness={6}
                                sx={{ color: "inherit" }}
                              />
                            ) : (
                              <Iconify
                                icon="solar:plug-circle-linear"
                                width={16}
                              />
                            )
                          }
                          sx={{ color: "text.primary", borderColor: "divider" }}
                        >
                          {listing
                            ? "Connecting and listing tools…"
                            : "Connect and list tools"}
                        </Button>
                        {error && (
                          <Typography
                            sx={{ typography: "s3", color: "error.main" }}
                          >
                            {error}
                          </Typography>
                        )}
                      </Stack>
                    )}
                  </>
                )}

                {listed && (
                  <Box>
                    <Stack
                      direction="row"
                      alignItems="center"
                      spacing={1}
                      sx={{ mb: 0.75 }}
                    >
                      <Typography
                        sx={{ typography: "s2", fontWeight: 600 }}
                      >{`${listed.server.label} · ${plural(listed.tools.length, "tool")}`}</Typography>
                      <Typography
                        noWrap
                        sx={{
                          typography: "s3",
                          color: "text.tertiary",
                          fontFamily: MONO,
                          minWidth: 0,
                        }}
                      >
                        {listed.server.host}
                      </Typography>
                      {viaGateway && <Tag>Through your Gateway</Tag>}
                    </Stack>
                    <Box
                      sx={{
                        border: "1px solid",
                        borderColor: "divider",
                        borderRadius: 1,
                      }}
                    >
                      {listed.tools.map((t, i) => (
                        <ToolRow
                          key={t.name}
                          tool={t}
                          first={i === 0}
                          place={placeOf(t)}
                          checked={!!picked[t.name]}
                          reach={toolReachOf(t, { agent, calls })}
                          onCheck={(on) =>
                            setPicked((p) => ({ ...p, [t.name]: on }))
                          }
                        />
                      ))}
                    </Box>
                    <Typography
                      sx={{ typography: "s3", color: "text.tertiary", mt: 1 }}
                    >
                      {`Each tool starts Simulated — the world answers it against its seeded data. Change it per tool on the World tab; only a tool that reads can go Live, once you allow it${viaGateway ? ", and Live calls go through your Gateway, with its rate limits and checks." : "."}`}
                    </Typography>
                  </Box>
                )}
              </>
            )}
          </Stack>
        </Box>

        {!done && (
          <Stack
            direction="row"
            alignItems="center"
            spacing={1.5}
            sx={{
              px: 2.5,
              py: 1.75,
              borderTop: "1px solid",
              borderColor: "divider",
            }}
          >
            <Typography
              sx={{
                typography: "s3",
                color: "text.tertiary",
                flex: 1,
                minWidth: 0,
              }}
            >
              {draftNote}
            </Typography>
            <Button
              size="small"
              onClick={onClose}
              sx={{ color: "text.secondary" }}
            >
              Cancel
            </Button>
            <Button
              size="small"
              variant="contained"
              color="primary"
              disabled={!chosen.length}
              onClick={add}
            >
              {chosen.length
                ? `Add ${plural(chosen.length, "tool")}`
                : "Add tools"}
            </Button>
          </Stack>
        )}
      </Stack>
    </SideDrawer>
  );
}
AddMcpToolsDrawer.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func.isRequired,
  env: PropTypes.object.isRequired,
  envState: PropTypes.object,
  patch: PropTypes.func.isRequired,
  onGo: PropTypes.func,
  /* A server to open straight on: { server, tools, reason: "agent" | "refresh" }. */
  prefill: PropTypes.object,
};

/* One server registered in the Gateway — picked to list its tools; one the Gateway can't reach can't be. */
function GatewayServerRow({ server, first, on, onPick }) {
  const down = server.status !== "connected";
  return (
    <ButtonBase
      disabled={down}
      onClick={onPick}
      aria-pressed={on}
      sx={{
        ...focusRing,
        width: "100%",
        justifyContent: "flex-start",
        textAlign: "left",
        gap: 1.25,
        px: 1.5,
        py: 1,
        borderTop: first ? "none" : "1px solid",
        borderColor: "divider",
        bgcolor: on
          ? (t) => alpha(t.palette.text.primary, 0.06)
          : "transparent",
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
          <Typography
            sx={{
              typography: "s2_1",
              fontWeight: 500,
              color: down ? "text.disabled" : "text.primary",
            }}
          >
            {server.label}
          </Typography>
          <Typography
            noWrap
            sx={{
              typography: "s3",
              color: "text.tertiary",
              fontFamily: MONO,
              minWidth: 0,
            }}
          >
            {server.host}
          </Typography>
        </Stack>
        {down && (
          <Typography
            sx={{ typography: "s3", color: "warning.main" }}
          >{`Unreachable — ${server.error}`}</Typography>
        )}
      </Box>
      <Typography
        sx={{
          typography: "s3",
          color: "text.tertiary",
          flexShrink: 0,
          fontVariantNumeric: "tabular-nums",
        }}
      >
        {down
          ? `Checked ${server.checkedAgo}`
          : plural(server.toolCount, "tool")}
      </Typography>
    </ButtonBase>
  );
}
GatewayServerRow.propTypes = {
  server: PropTypes.object.isRequired,
  first: PropTypes.bool,
  on: PropTypes.bool,
  onPick: PropTypes.func.isRequired,
};

/* A tool the server lists: what it does by MCP's own hints, what the Gateway says of it, and where it lands. */
function ToolRow({ tool, first, place, checked, reach, onCheck }) {
  const inWorld = place.status === "inWorld";
  const line = tool.blocked
    ? "The Gateway's guardrails block it: the world answers it, but it never goes Live."
    : tool.deprecated
      ? tool.deprecated.message
      : tool.desc;
  return (
    <Stack
      direction="row"
      alignItems="flex-start"
      spacing={1}
      sx={{
        px: 1,
        py: 0.75,
        borderTop: first ? "none" : "1px solid",
        borderColor: "divider",
        opacity: inWorld ? 0.55 : 1,
      }}
    >
      <Checkbox
        size="small"
        checked={inWorld || checked}
        disabled={inWorld}
        onChange={(e) => onCheck(e.target.checked)}
        inputProps={{ "aria-label": tool.name }}
        sx={{
          p: 0.5,
          color: "text.disabled",
          "&.Mui-checked": { color: "text.primary" },
          "&.Mui-disabled.Mui-checked": { color: "text.disabled" },
        }}
      />
      <Box flex={1} minWidth={0} sx={{ pt: 0.25 }}>
        <Stack
          direction="row"
          alignItems="center"
          spacing={0.75}
          sx={{ flexWrap: "wrap", rowGap: 0.5 }}
        >
          <Typography
            sx={{ typography: "s2", fontWeight: 600, fontFamily: MONO }}
          >
            {tool.name}
          </Typography>
          <Tag>{tool.readOnly ? "Reads" : "Writes"}</Tag>
          {tool.destructive && (
            <Tooltip
              arrow
              title="The server marks it destructive: it can't be undone. It never runs Live — the world answers it."
            >
              <Box component="span" sx={{ display: "inline-flex" }}>
                <Tag>Destructive</Tag>
              </Box>
            </Tooltip>
          )}
          {tool.openWorld && (
            <Tooltip
              arrow
              title="The server marks it open-world: it reaches people or systems beyond this server, like sending a customer a reply."
            >
              <Box component="span" sx={{ display: "inline-flex" }}>
                <Tag>Open world</Tag>
              </Box>
            </Tooltip>
          )}
          {tool.blocked && <Tag warn>Blocked in Gateway · Simulated only</Tag>}
          {tool.deprecated && (
            <Tag warn>{`Deprecated · use ${tool.deprecated.replacedBy}`}</Tag>
          )}
          {inWorld && <Tag>In the world already</Tag>}
          {place.status === "renamed" && (
            <Tooltip
              arrow
              title={`${tool.name} is taken by a tool from ${place.clashWith}, so this one joins as ${place.as} — the agent is offered it under that name.`}
            >
              <Box component="span" sx={{ display: "inline-flex" }}>
                <Tag>{`Added as ${place.as}`}</Tag>
              </Box>
            </Tooltip>
          )}
        </Stack>
        <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
          {line}
          {!inWorld && !tool.deprecated && reach && ` · ${reach.label}`}
        </Typography>
      </Box>
    </Stack>
  );
}
ToolRow.propTypes = {
  tool: PropTypes.object.isRequired,
  first: PropTypes.bool,
  place: PropTypes.object.isRequired,
  checked: PropTypes.bool,
  reach: PropTypes.object,
  onCheck: PropTypes.func.isRequired,
};

function DoneState({ plan, server, onWrite }) {
  const n = plan.added.length;
  return (
    <Stack spacing={1.5}>
      <Stack direction="row" spacing={1} alignItems="flex-start">
        <Iconify
          icon="solar:check-circle-bold"
          width={20}
          sx={{ color: "status.success.fg", flexShrink: 0, mt: "1px" }}
        />
        <Box>
          <Typography
            sx={{ typography: "s2", fontWeight: 600 }}
          >{`${plural(n, "tool")} from ${server.label} added`}</Typography>
          <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
            {plan.inPlace
              ? `In environment ${plan.versionLabel}. The world answers them during runs, Simulated to start.`
              : "Waiting as an unsaved edit — save it as the next environment version from the bar at the top. Runs use the saved version until then."}
            {server.gateway ? " Live calls go through your Gateway." : ""}
          </Typography>
          {plan.renamed.length > 0 && (
            <Typography
              sx={{ typography: "s3", color: "text.tertiary", mt: 0.5 }}
            >
              {plan.renamed
                .map(
                  (r) =>
                    `${r.from} joined as ${r.as} — ${r.from} is ${r.clashWith}'s.`,
                )
                .join(" ")}
            </Typography>
          )}
        </Box>
      </Stack>
      <Typography
        sx={{ typography: "s3", color: "text.secondary", fontFamily: MONO }}
      >
        {plan.added.map((t) => t.name).join(" · ")}
      </Typography>
      {onWrite && (
        <Box>
          <Typography sx={{ typography: "s2", color: "text.secondary", mb: 1 }}>
            No scenario uses them yet. The builder can write some that do.
          </Typography>
          <Button
            size="small"
            variant="contained"
            color="primary"
            onClick={onWrite}
            startIcon={<Iconify icon="solar:magic-stick-3-linear" width={16} />}
          >
            Write scenarios for the new tools
          </Button>
        </Box>
      )}
    </Stack>
  );
}
DoneState.propTypes = {
  plan: PropTypes.object.isRequired,
  server: PropTypes.object.isRequired,
  onWrite: PropTypes.func,
};

function Tag({ children, warn = false }) {
  return (
    <Chip
      size="small"
      color={warn ? "warning" : "default"}
      label={children}
      sx={{ flexShrink: 0 }}
    />
  );
}
Tag.propTypes = { children: PropTypes.node, warn: PropTypes.bool };
