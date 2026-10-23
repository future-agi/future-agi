import PropTypes from "prop-types";
import { useEffect, useState } from "react";
import { Box, Button, Checkbox, CircularProgress, IconButton, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import SideDrawer from "../components/SideDrawer";
import { useSimStore } from "../store";
import { Field, SegmentedToggle } from "../environments/StartEnvironment";
import SecretRefField from "../agents/connect/SecretRefField";
import { isSecretRef } from "../_mock/secretRefs";
import { listMcpTools } from "../_mock/mcpServers";
import { addMcpToolsPlan, agentCallsOf, toolReachOf } from "../_mock/worldTools";
import { worldToolsFor } from "../_mock/toolFit";

const MONO = "ui-monospace, Menlo, monospace";
const TRANSPORTS = [
  { value: "http", label: "HTTP" },
  { value: "sse", label: "SSE" },
  { value: "stdio", label: "stdio" },
];

/**
 * Add tools to an environment's world from an MCP server — the way agent
 * platforms attach one (ElevenLabs, OpenAI's remote MCP): the server's
 * address and token, then the tools it lists, picked one by one. Picked
 * tools join the world Simulated; a tool that writes can never go Live.
 *
 * `prefill` opens straight on the tools of a server the agent already has
 * attached ({ server, tools }) — its tools the world doesn't answer yet.
 */
export default function AddMcpToolsDrawer({ open, onClose, env, envState, patch, onGo, prefill = null }) {
  const { dispatch } = useSimStore();
  const [transport, setTransport] = useState("http");
  const [value, setValue] = useState("");
  const [tokenRef, setTokenRef] = useState("");
  const [listing, setListing] = useState(false);
  const [listed, setListed] = useState(null);
  const [error, setError] = useState(null);
  const [picked, setPicked] = useState({});
  const [done, setDone] = useState(null);

  useEffect(() => {
    if (!open) return;
    setTransport(prefill?.server?.transport || "http");
    setValue(prefill?.server?.value || "");
    setTokenRef("");
    setError(null);
    setDone(null);
    setListing(false);
    const first = prefill ? { server: prefill.server, tools: prefill.tools } : null;
    setListed(first);
    setPicked(first ? Object.fromEntries(first.tools.map((t) => [t.name, true])) : {});
  }, [open]); // eslint-disable-line react-hooks/exhaustive-deps

  const world = new Set(worldToolsFor(env, envState).map((t) => t.name));
  const agent = envState?.agent || null;
  const calls = agentCallsOf(env, envState);
  const tokenOk = !tokenRef.trim() || isSecretRef(tokenRef);

  const list = () => {
    setError(null);
    setListing(true);
    setTimeout(() => {
      const r = listMcpTools({ transport, value, tokenRef: tokenRef.trim() || null });
      setListing(false);
      if (!r.ok) { setError(r.error); return; }
      setListed({ server: r.server, tools: r.tools });
      setPicked(Object.fromEntries(r.tools.filter((t) => !world.has(t.name)).map((t) => [t.name, true])));
    }, 800);
  };

  const chosen = listed ? listed.tools.filter((t) => picked[t.name] && !world.has(t.name)) : [];
  const add = () => {
    const plan = addMcpToolsPlan(env, envState, { server: listed.server, picked: chosen });
    if (plan.envPatch) dispatch({ type: "patchEnvironment", envId: env.id, patch: plan.envPatch });
    patch(plan.statePatch);
    setDone(plan);
  };
  /* What adding does to versions, said before it happens. */
  const draftNote = (() => {
    const p = listed && chosen.length ? addMcpToolsPlan(env, envState, { server: listed.server, picked: chosen }) : null;
    if (!p) return null;
    return p.inPlace
      ? `Added to environment ${p.versionLabel} — it hasn't run yet, so nothing else changes.`
      : `Saves environment ${p.versionLabel}. Scenarios keep their proofs — adding a tool doesn't change them.`;
  })();

  return (
    <SideDrawer open={open} onClose={onClose} width={{ xs: "100%", sm: 600 }}>
      <Stack sx={{ height: "100%" }}>
        <Stack direction="row" alignItems="center" spacing={1.5} sx={{ px: 2.5, py: 2, borderBottom: "1px solid", borderColor: "divider" }}>
          <Box flex={1} minWidth={0}>
            <Typography sx={{ typography: "s1", fontWeight: 700 }}>Add tools from an MCP server</Typography>
            <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
              {prefill ? `${agent?.name || "The agent"} has this server attached — add its tools so the world answers them.` : "Its tools join this world, answered by the world during runs."}
            </Typography>
          </Box>
          <IconButton size="small" onClick={onClose} aria-label="Close"><Iconify icon="eva:close-fill" width={17} /></IconButton>
        </Stack>

        {/* A block scroller, not a flex column: a fullWidth Field grows with
            flex: 1, and in a column that fills the drawer it splits the
            empty height between the fields. */}
        <Box sx={{ px: 2.5, py: 2.5, flex: 1, minHeight: 0, overflowY: "auto" }}>
          <Stack spacing={2}>
            {done ? (
              <DoneState plan={done} server={listed.server} onWrite={onGo ? () => { onClose(); onGo("scenarios"); } : null} />
            ) : (
              <>
                {!prefill && (
                  <>
                    <Box>
                      <Typography sx={{ typography: "s3", fontWeight: 600, mb: 0.5 }}>Transport</Typography>
                      <SegmentedToggle value={transport} onChange={(t) => { setTransport(t); setListed(null); }} options={TRANSPORTS} />
                    </Box>
                    <Field
                      label={transport === "stdio" ? "Command" : "Server URL"} required mono fullWidth
                      placeholder={transport === "stdio" ? "npx -y @acme/mcp-server" : "https://mcp.yourapp.com/mcp"}
                      value={value} onChange={(v) => { setValue(v); setListed(null); }}
                      helper="We connect and list the tools it exposes — you pick which join the world."
                    />
                    {transport !== "stdio" && (
                      <SecretRefField
                        label="Auth token (optional)" fullWidth
                        placeholder="vault://acme/sim/mcp_token"
                        value={tokenRef} onChange={setTokenRef}
                        helper="Sent as the Authorization header. Stored as a reference, never the key itself."
                      />
                    )}
                    {!listed && (
                      <Stack direction="row" alignItems="center" spacing={1.25}>
                        <Button
                          variant="outlined" size="small" onClick={list} disabled={listing || !value.trim() || !tokenOk}
                          startIcon={listing ? <CircularProgress size={12} thickness={6} sx={{ color: "inherit" }} /> : <Iconify icon="solar:plug-circle-linear" width={15} />}
                          sx={{ typography: "s2", fontWeight: 700, color: "text.primary", borderColor: "divider" }}
                        >
                          {listing ? "Connecting and listing tools…" : "Connect and list tools"}
                        </Button>
                        {error && <Typography sx={{ typography: "s3", color: "error.main" }}>{error}</Typography>}
                      </Stack>
                    )}
                  </>
                )}

                {listed && (
                  <Box>
                    <Stack direction="row" alignItems="baseline" spacing={1} sx={{ mb: 0.75 }}>
                      <Typography sx={{ typography: "s2", fontWeight: 700 }}>{`${listed.server.label} · ${listed.tools.length} tools`}</Typography>
                      <Typography noWrap sx={{ typography: "s3", color: "text.subtitle", fontFamily: MONO }}>{listed.server.host}</Typography>
                    </Stack>
                    <Box sx={{ border: "1px solid", borderColor: "divider", borderRadius: 1 }}>
                      {listed.tools.map((t, i) => {
                        const inWorld = world.has(t.name);
                        const reach = toolReachOf(t, { agent, calls });
                        return (
                          <Stack
                            key={t.name} direction="row" alignItems="flex-start" spacing={1}
                            sx={{ px: 1, py: 0.75, borderTop: i ? "1px solid" : "none", borderColor: "divider", opacity: inWorld ? 0.55 : 1 }}
                          >
                            <Checkbox
                              size="small" checked={inWorld || !!picked[t.name]} disabled={inWorld}
                              onChange={(e) => setPicked((p) => ({ ...p, [t.name]: e.target.checked }))}
                              inputProps={{ "aria-label": t.name }}
                              sx={{ p: 0.5, color: "text.disabled", "&.Mui-checked": { color: "text.primary" }, "&.Mui-disabled.Mui-checked": { color: "text.disabled" } }}
                            />
                            <Box flex={1} minWidth={0} sx={{ pt: 0.25 }}>
                              <Stack direction="row" alignItems="center" spacing={0.75}>
                                <Typography sx={{ typography: "s2", fontWeight: 600, fontFamily: MONO }}>{t.name}</Typography>
                                <Tag>{t.readOnly ? "Reads" : "Writes"}</Tag>
                                {inWorld && <Tag>In the world already</Tag>}
                              </Stack>
                              <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
                                {t.desc}
                                {!inWorld && reach && ` · ${reach.label}`}
                              </Typography>
                            </Box>
                          </Stack>
                        );
                      })}
                    </Box>
                    <Typography sx={{ typography: "s3", color: "text.subtitle", mt: 1 }}>
                      Each tool starts Simulated — the world answers it against its seeded data. Change it per tool on the World tab; only a tool that reads can go Live, once you allow it.
                    </Typography>
                  </Box>
                )}
              </>
            )}
          </Stack>
        </Box>

        {!done && (
          <Stack direction="row" alignItems="center" spacing={1.5} sx={{ px: 2.5, py: 1.75, borderTop: "1px solid", borderColor: "divider" }}>
            <Typography sx={{ typography: "s3", color: "text.subtitle", flex: 1, minWidth: 0 }}>{draftNote}</Typography>
            <Button size="small" onClick={onClose} sx={{ typography: "s2", fontWeight: 700, color: "text.secondary" }}>Cancel</Button>
            <Button
              size="small" variant="contained" color="primary" disabled={!chosen.length} onClick={add}
              sx={{ typography: "s2", fontWeight: 700 }}
            >
              {chosen.length ? `Add ${chosen.length} tool${chosen.length === 1 ? "" : "s"}` : "Add tools"}
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
  /* A server the agent already has attached: { server, tools }. */
  prefill: PropTypes.object,
};

function DoneState({ plan, server, onWrite }) {
  const n = plan.added.length;
  return (
    <Stack spacing={1.5}>
      <Stack direction="row" spacing={1} alignItems="flex-start">
        <Iconify icon="solar:check-circle-bold" width={18} sx={{ color: "#16A34A", flexShrink: 0, mt: "1px" }} />
        <Box>
          <Typography sx={{ typography: "s2", fontWeight: 700 }}>{`${n} tool${n === 1 ? "" : "s"} from ${server.label} added`}</Typography>
          <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
            {plan.inPlace ? `In environment ${plan.versionLabel}.` : `Saved as environment ${plan.versionLabel}.`}
            {" The world answers them during runs, Simulated to start."}
          </Typography>
        </Box>
      </Stack>
      <Typography sx={{ typography: "s3", color: "text.secondary", fontFamily: MONO }}>{plan.added.map((t) => t.name).join(" · ")}</Typography>
      {onWrite && (
        <Box>
          <Typography sx={{ typography: "s2", color: "text.secondary", mb: 1 }}>No scenario uses them yet. Write some so runs test them.</Typography>
          <Button
            size="small" variant="contained" color="primary" onClick={onWrite}
            startIcon={<Iconify icon="solar:magic-stick-3-linear" width={15} />}
            sx={{ typography: "s2", fontWeight: 700 }}
          >
            Write scenarios for the new tools
          </Button>
        </Box>
      )}
    </Stack>
  );
}
DoneState.propTypes = { plan: PropTypes.object.isRequired, server: PropTypes.object.isRequired, onWrite: PropTypes.func };

function Tag({ children }) {
  return (
    <Typography component="span" sx={{ typography: "s3", fontSize: 11, fontWeight: 600, color: "text.subtitle", px: 0.5, borderRadius: 0.5, border: "1px solid", borderColor: "divider", lineHeight: "16px", flexShrink: 0 }}>
      {children}
    </Typography>
  );
}
Tag.propTypes = { children: PropTypes.node };
