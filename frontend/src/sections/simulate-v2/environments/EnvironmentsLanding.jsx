import PropTypes from "prop-types";
import { Suspense, lazy, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Box, Button, Stack, Typography } from "@mui/material";
import { paths } from "src/routes/paths";
import {
  WORLD_TYPES,
  detectPaste,
  prebuiltWorlds,
  worldTotal,
} from "./landingData";
import { newAgentChoicesFor } from "../agents/connect/agentConnectData";
import ConnectCatalog from "../agents/connect/ConnectCatalog";
import { SectionHead, WorldCard } from "./LandingBits";
import { OUTLINE_BTN, focusRing } from "./landingStyles";
import WorldDrawer from "./WorldDrawer";
import SaasSandboxRow from "./SaasSandbox";
import { ConnectSubmitContext } from "../agents/connect/connectSubmit";
import { useSaveConnectedAgent } from "../agents/connect/useSaveConnectedAgent";
import { clickable } from "../components/clickable";
import { YourAgentsSection } from "./AgentChoice";

/*
  The Environments landing screen — every way in, in the order people reach
  for them. Every environment belongs to exactly one agent in Agents (RSI
  PRD), so each way in ends at an agent:

    1. Your agents: the agents already in Agents — pick one and build.
    2. Prebuilt worlds and the SaaS sandbox: a world to start from — the
       agent it's built for is picked (or connected) before anything builds.
    3. Connect your agent: every way in, searchable — and the same box takes
       a paste (a repository, an assistant ID, an MCP URL, an endpoint) and
       opens the right setup, filled in.

  Picking anything calls `onPick(choice, prefill, from, itemId, meta)`; the
  page opens the matching setup in a drawer, so the list stays put.

  One way to connect an agent: Agents is its home (RSI PRD), so a new agent —
  pasted or picked from the directory — is connected in Agents → New agent
  (source, what we found, Observe project, constraints), which comes back
  here with it picked. The directory is the same one Agents shows. An MCP
  server's tools are a world source: its setup picks the agent first.
*/

/* Loaded on demand: the picker reaches back into StartEnvironment (FlowPanel),
   and a static import here would close a cycle through this file. */
const ConnectAgentPicker = lazy(
  () => import("../agents/connect/ConnectAgentPicker"),
);

/* Pasted things that are agents, and the Agents flow each opens. */
const AGENT_KIND_OF = {
  hosted: "hosted",
  source: "repo",
  running: "running",
  phone: "phone",
  scratch: "builtin",
};
/* Agents → New agent on that source, coming back here with the new agent. */
const connectUrl = ({ kind, initial, id, meta }) => {
  const q = new URLSearchParams({
    src: kind,
    item: id || kind,
    return: `${paths.dashboard.simulate.environments}?agent={agent}`,
  });
  if (initial) q.set("init", JSON.stringify(initial));
  if (meta?.name) q.set("name", meta.name);
  if (meta?.logo) q.set("logo", meta.logo);
  return `${paths.dashboard.agents}/connect?${q.toString()}`;
};
export default function EnvironmentsLanding({
  onPick,
  onBrowseWorlds,
  picked,
  examplesRef: givenExamplesRef,
  onAgentConnected,
}) {
  const navigate = useNavigate();
  /* Connecting an agent happens here, in a drawer over this page — the same
     flows Agents → New agent uses. Once it's saved its environment builds. */
  const saveAgent = useSaveConnectedAgent();
  const [connectReq, setConnectReq] = useState(null);
  const openConnect = (kind, initial, id, meta) => {
    if (!onAgentConnected) {
      navigate(connectUrl({ kind, initial, id, meta }));
      return;
    }
    setConnectReq({
      kind,
      initial: initial || null,
      id,
      meta: meta || {},
      at: Date.now(),
    });
  };
  const onConnected = (source) => {
    const { id } = saveAgent(source);
    onAgentConnected?.(id, source.scenarioCount);
  };
  /* Prebuilt worlds sit where the ready-made examples were. */
  const ownWorldsRef = useRef(null);
  const worldsRef = givenExamplesRef || ownWorldsRef;
  const connectRef = useRef(null);

  return (
    <Stack spacing={4} sx={{ pb: 4 }}>
      <PrebuiltSection sectionRef={worldsRef} onBrowse={onBrowseWorlds} />

      <ConnectSection
        sectionRef={connectRef}
        pickedId={picked?.from === "directory" ? picked.itemId : null}
        onPick={(item, prefill) =>
          onPick(
            item.choice,
            prefill ?? item.prefill ?? null,
            "directory",
            item.id,
            { name: item.name, logo: item.logo, icon: item.icon },
          )
        }
        onConnect={(item) =>
          openConnect(
            item.kind,
            item.initial || null,
            item.id,
            item.kind === "traces"
              ? /* The traces flow switches between its two ways in — named for both. */
                {
                  name: "An agent from its traces",
                  icon: "solar:pulse-2-linear",
                }
              : { name: item.name, logo: item.logo, icon: item.icon },
          )
        }
        onTools={() =>
          onPick("mcp", null, "directory", "mcp", {
            name: "MCP server",
            logo: "mcp",
          })
        }
        /* One box searches the list and reads a paste: an agent opens its
           connect flow, an MCP server its world setup. */
        onPaste={(choice, prefill, meta) => {
          const kind = AGENT_KIND_OF[choice];
          if (!kind) {
            onPick(choice, prefill, "paste", null, meta);
            return;
          }
          openConnect(
            kind,
            choice === "scratch"
              ? { prompt: prefill?.flowText || "" }
              : prefill,
            "paste",
            meta,
          );
        }}
      />

      {/* Or one already in Agents (Flow 2: pick the agent from Agents). */}
      <YourAgentsSection />

      {/* The connect flow's drawer, opened in place. */}
      {connectReq && (
        <Suspense fallback={null}>
          <ConnectSubmitContext.Provider value="Connect and build environment">
            <ConnectAgentPicker
              catalogue={false}
              request={connectReq}
              onConnect={onConnected}
            />
          </ConnectSubmitContext.Provider>
        </Suspense>
      )}
    </Stack>
  );
}
EnvironmentsLanding.propTypes = {
  onPick: PropTypes.func.isRequired,
  /* Called with a newly connected agent's id — its environment then builds. */
  onAgentConnected: PropTypes.func,
  onBrowseWorlds: PropTypes.func,
  examplesRef: PropTypes.object,
  picked: PropTypes.object,
};

/* ── shared bits ───────────────────────────────────────────────────────── */

/* ── 3. connect your agent ─────────────────────────────────────────────── */

const linkSx = {
  color: "text.primary",
  fontWeight: 600,
  cursor: "pointer",
  "&:hover": { textDecoration: "underline" },
};

/* The same catalogue as Agents → New agent — searchable, by where the agent
   lives, every row saying how a fix comes back. The same box reads a paste —
   a repo, an ID, an MCP URL or a prompt — and opens the right setup. Agents
   already connected are under Your agents, at the bottom. */
function ConnectSection({
  sectionRef,
  onPick,
  onConnect,
  onTools,
  onPaste,
  pickedId,
}) {
  const { groups } = useMemo(() => newAgentChoicesFor(null), []);
  const all = useMemo(() => groups.flatMap((g) => g.items), [groups]);
  return (
    <Box ref={sectionRef} sx={{ scrollMarginTop: 16 }}>
      <SectionHead
        title="Connect your agent"
        subtitle="Pick where it runs today — it joins Agents, then you build its environment here."
      />
      <ConnectCatalog
        groups={groups}
        /* Other ways in sit above and below it, so no All: one group at a time, Code first. */
        showAll={false}
        detect={detectPaste}
        onPaste={onPaste}
        pasteHint="a repo, an ID, an MCP URL or a prompt"
        pickedId={pickedId}
        onPick={(item) => (item.choice ? onPick(item) : onConnect(item))}
        emptyHint="If it runs somewhere we can call, connect it as an agent endpoint."
        onFallback={() => onConnect(all.find((it) => it.id === "running"))}
      />
      <Typography sx={{ typography: "s3", color: "text.subtitle", mt: 1.25 }}>
        {"Tools on an MCP server? "}
        <Box
          component="span"
          {...clickable(onTools)}
          sx={{ ...linkSx, "&:focus-visible": focusRing }}
        >
          Use them as an agent&apos;s world
        </Box>
      </Typography>
    </Box>
  );
}
ConnectSection.propTypes = {
  sectionRef: PropTypes.object,
  pickedId: PropTypes.string,
  onPick: PropTypes.func.isRequired,
  onConnect: PropTypes.func.isRequired,
  onTools: PropTypes.func,
  onPaste: PropTypes.func,
};

/* ── 4. prebuilt worlds ────────────────────────────────────────────────── */

function PrebuiltSection({ sectionRef, onBrowse }) {
  const worlds = useMemo(() => prebuiltWorlds(), []);
  /* Three upfront, each a different agent type — the rest are in Browse all.
     Coming-soon types (computer use) are never featured: they can't be built yet. */
  const featured = useMemo(() => {
    const soon = new Set(WORLD_TYPES.filter((t) => t.soon).map((t) => t.id));
    const seen = new Set();
    const open = worlds.filter((w) => !soon.has(w.type));
    const firsts = open.filter((w) =>
      seen.has(w.type) ? false : seen.add(w.type),
    );
    return [...firsts, ...open.filter((w) => !firsts.includes(w))].slice(0, 3);
  }, [worlds]);
  const [open, setOpen] = useState(null);
  return (
    <Box ref={sectionRef} sx={{ scrollMarginTop: 16 }}>
      <SectionHead
        title="Use a prebuilt world"
        subtitle="Tables, tools and rules, as designed. Try one now with its sample agent, or use it with one of your agents."
        action={
          <Button
            size="small"
            variant="outlined"
            onClick={onBrowse}
            sx={OUTLINE_BTN}
          >
            {`Browse all ${worldTotal()}`}
          </Button>
        }
      />
      <Box
        sx={{
          display: "grid",
          gap: 1.5,
          gridTemplateColumns: {
            xs: "1fr",
            sm: "1fr 1fr",
            lg: "repeat(3, 1fr)",
          },
        }}
      >
        {featured.map((w) => (
          <WorldCard
            key={w.id}
            world={w}
            onOpen={setOpen}
            selected={open?.id === w.id}
          />
        ))}
      </Box>
      {/* Or no made-up world at all: the agent's own apps, sandboxed. */}
      <Box sx={{ mt: 1.5 }}>
        <SaasSandboxRow />
      </Box>
      <WorldDrawer world={open} onClose={() => setOpen(null)} />
    </Box>
  );
}
PrebuiltSection.propTypes = {
  sectionRef: PropTypes.object,
  onBrowse: PropTypes.func,
};

/* ── web environments, as one world card ────────────────────────────── */
