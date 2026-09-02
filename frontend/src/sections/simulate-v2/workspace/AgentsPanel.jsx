import PropTypes from "prop-types";
import { useMemo, useState } from "react";
import { alpha } from "@mui/material/styles";
import {
  Box, Stack, Typography, Button, IconButton, Tooltip, Collapse,
  Dialog, DialogTitle, DialogContent, DialogActions, Chip, Divider,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { getAgentType } from "../_mock/agentTypes";
import AddAgentDrawer from "./agents/AddAgentDrawer";

/*
  Purple accent used to mark the environment source. Kept as a literal
  so the source stripe and the SOURCE-related chip share exactly one
  hue — swapping to a theme token means threading a component color
  spec, which is more code than this page needs.
*/
const SOURCE_ACCENT = "#7857FC";
const ACTIVE_ACCENT = "#16A34A";

/**
 * The Agents tab.
 *
 * The model this tab is trying to communicate:
 *  · Every environment has **one source agent** — the one it was
 *    originally derived from (its tools, rules and contract were
 *    read from that agent's code / config).
 *  · You can attach more agents to test against the same environment.
 *    They don't change the contract; they're candidate implementations
 *    that get graded against the scenarios the source produced.
 *  · At any moment **one agent is active for runs**. Runs execute
 *    against that one.
 *  · You can **promote** an additional agent to source. That re-reads
 *    the contract from it, re-derives the scenarios, and moves the
 *    old source to Additional.
 *
 * The UI translates that model directly:
 *  · One list. Every agent is a card of the same shape, so there is
 *    no "big detail block for the source and a small strip for the
 *    others" split — the reason last-pass looked broken.
 *  · The source card carries a 3px purple left stripe and a small
 *    "ENV SOURCE" label above the name. That's enough to say "this is
 *    the one that defined the environment" without giving it a totally
 *    different layout.
 *  · Active-for-runs is a filled dot at the left (a radio, effectively)
 *    plus a small green pill. Nothing else uses green in this list.
 *  · Everything else — connection details, "where it came from", the
 *    issued env credentials — is one uniform prop table shown on
 *    expand. The tools + rules blob that used to spill down the page
 *    is a single "Contract summary" pill row linking to the Contract
 *    tab (where that content already lives).
 */
export default function AgentsPanel({ env, envState, patch, onGo, buildMode, onBuilderTurn }) {
  /*
    Every agent (source + additionals) is normalised so it carries a
    `versions[]` array and an `activeVersionId`. Agents that were
    stored before versioning existed get wrapped as a single v1 on
    read, and their top-level fields (`values`, `via`, `connectedAt`,
    `note`) mirror the active version so downstream code that reads
    them keeps working unchanged.
  */
  const source = useMemo(() => normalizeAgentVersions(envState?.agent), [envState?.agent]);
  const additional = useMemo(
    () => (envState?.additionalAgents || []).map(normalizeAgentVersions),
    [envState?.additionalAgents],
  );
  const activeId = envState?.activeAgentId; // null = source
  const [adding, setAdding] = useState(false);
  const [addingVersionFor, setAddingVersionFor] = useState(null);
  const [promoteFor, setPromoteFor] = useState(null);

  const allAgents = useMemo(() => {
    if (!source) return [];
    return [
      { ...source, id: "source", isSource: true },
      ...additional.map((a) => ({ ...a, isSource: false })),
    ];
  }, [source, additional]);

  const activeAgent = activeId
    ? additional.find((a) => a.id === activeId)
    : null;

  /* Source starts expanded — it's the primary information on the
     page. Extras start collapsed to keep the tab short. */
  const [expanded, setExpanded] = useState({ source: true });
  const toggleExpanded = (id) => setExpanded((e) => ({ ...e, [id]: !e[id] }));

  const setActive = (id) => patch({ activeAgentId: id === "source" ? null : id });

  const addAgent = (agent) => {
    /* Wrap the first version in the same shape everything else uses. */
    const versionId = `v1`;
    const versionRecord = {
      id: versionId,
      label: "v1",
      values: agent.values,
      via: agent.via,
      connectedAt: agent.connectedAt || new Date().toISOString(),
      note: agent.note || "Initial version",
    };
    const withId = {
      ...agent,
      id: `agent-${Date.now()}`,
      versions: [versionRecord],
      activeVersionId: versionId,
    };
    patch({
      additionalAgents: [...additional, withId],
      activeAgentId: withId.id,
    });
    setExpanded((e) => ({ ...e, [withId.id]: true }));
    setAdding(false);
  };

  /*
    Attaching a new version of an existing agent — same drawer, but
    on save we append to the target agent's `versions[]` and switch
    its `activeVersionId` to the new one. Adding a new source version
    also mints an env-version bump (existing behaviour would be a
    natural next step; for now we just track the new version on the
    agent itself).
  */
  const addVersion = (agentId, record) => {
    if (agentId === "source") {
      if (!source) return;
      const next = mintNextVersion(source, record);
      patch({
        agent: applyActiveVersion({
          ...source,
          versions: [...(source.versions || []), next],
          activeVersionId: next.id,
        }),
      });
    } else {
      const target = additional.find((a) => a.id === agentId);
      if (!target) return;
      const next = mintNextVersion(target, record);
      patch({
        additionalAgents: additional.map((a) =>
          a.id === agentId
            ? applyActiveVersion({
              ...a,
              versions: [...(a.versions || []), next],
              activeVersionId: next.id,
            })
            : a,
        ),
      });
    }
    setAddingVersionFor(null);
  };

  const setActiveVersion = (agentId, versionId) => {
    if (agentId === "source") {
      if (!source) return;
      patch({
        agent: applyActiveVersion({ ...source, activeVersionId: versionId }),
      });
    } else {
      patch({
        additionalAgents: additional.map((a) =>
          a.id === agentId
            ? applyActiveVersion({ ...a, activeVersionId: versionId })
            : a,
        ),
      });
    }
  };

  const removeAgent = (id) => {
    /*
      Removing the source clears `agent`. The env then has no
      implementation — the empty state above renders, and the user
      can attach another. Any additional agents stay put so nothing
      previously attached silently disappears alongside the source.
    */
    if (id === "source") {
      patch({ agent: null, activeAgentId: null });
      return;
    }
    patch({
      additionalAgents: additional.filter((a) => a.id !== id),
      activeAgentId: activeId === id ? null : activeId,
    });
  };

  const confirmPromote = () => {
    if (!promoteFor) return;
    const promoted = additional.find((a) => a.id === promoteFor.id);
    if (!promoted) return;
    const demotedSource = source
      ? { ...source, id: `agent-${Date.now()}`, note: "Previous source — demoted" }
      : null;
    patch({
      agent: {
        typeId: promoted.typeId, values: promoted.values,
        via: promoted.via, connectedAt: promoted.connectedAt,
        note: "Environment source",
      },
      additionalAgents: [
        ...additional.filter((a) => a.id !== promoted.id),
        ...(demotedSource ? [demotedSource] : []),
      ],
      activeAgentId: null,
    });
    if (onBuilderTurn) {
      onBuilderTurn(
        `Re-deriving environment from ${getAgentType(promoted.typeId)?.label || "the promoted agent"}`,
        buildPromoteSteps({ from: source, to: promoted }),
      );
    }
    setPromoteFor(null);
  };

  if (!source) {
    /*
      Empty state — the environment has no agent attached. Previous
      version was a bare sentence with no way to fix it; users had to
      hunt for a CTA. Now the empty state IS the CTA: one card, one
      button, one clear next step.
    */
    return (
      <Box sx={{ p: 2 }}>
        <Box
          sx={{
            p: 4, borderRadius: 1.5, textAlign: "center",
            border: "1px dashed", borderColor: "divider",
            bgcolor: "background.paper",
          }}
        >
          <Box
            sx={{
              width: 40, height: 40, borderRadius: 1, mx: "auto", mb: 1.5,
              display: "grid", placeItems: "center",
              bgcolor: (t) => alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.06 : 0.04),
              color: "text.subtitle",
            }}
          >
            <Iconify icon="solar:link-circle-linear" width={20} />
          </Box>
          <Typography sx={{ typography: "s1", fontWeight: 600, mb: 0.5 }}>
            No agent attached
          </Typography>
          <Typography sx={{ typography: "s2", color: "text.subtitle", mb: 2, maxWidth: 460, mx: "auto" }}>
            An environment needs a source agent — the one its contract and scenarios
            were read from. Attach one to start running simulations.
          </Typography>
          <Button
            variant="contained" color="primary" size="small"
            onClick={() => setAdding(true)}
            startIcon={<Iconify icon="solar:add-circle-linear" width={16} />}
            sx={{ typography: "s2", fontWeight: 700 }}
          >
            Attach an agent
          </Button>
        </Box>
        <AddAgentDrawer
          open={adding}
          onClose={() => setAdding(false)}
          env={env}
          onAdd={(agent) => {
            patch({ agent: { ...agent, note: "Environment source" } });
            setAdding(false);
          }}
        />
      </Box>
    );
  }

  return (
    <Box sx={{ p: 2 }}>
      {/* ── header ── */}
      <Stack
        direction={{ xs: "column", sm: "row" }}
        alignItems={{ sm: "flex-end" }} spacing={2}
        sx={{ mb: 2 }}
      >
        <Box flex={1}>
          <Stack direction="row" alignItems="baseline" spacing={0.75}>
            <Typography sx={{ typography: "m2", fontWeight: 600 }}>Agents</Typography>
            <Typography sx={{
              typography: "s1", fontWeight: 500, color: "text.subtitle",
              fontVariantNumeric: "tabular-nums",
            }}>
              ({allAgents.length})
            </Typography>
          </Stack>
          <Typography sx={{ typography: "s2", color: "text.secondary", maxWidth: 720 }}>
            The <Box component="span" sx={{ color: "text.primary", fontWeight: 600 }}>source</Box>{" "}
            defines this environment&apos;s scenarios. Runs execute against whichever agent is{" "}
            <Box component="span" sx={{ color: "text.primary", fontWeight: 600 }}>active</Box>{" "}
            — attach more to A/B test candidates without changing the definition.
          </Typography>
        </Box>
        <Button
          variant="contained" color="primary" size="small"
          onClick={() => setAdding(true)}
          startIcon={<Iconify icon="solar:add-circle-linear" width={16} />}
          sx={{ typography: "s2", fontWeight: 700, flexShrink: 0 }}
        >
          Attach another agent
        </Button>
      </Stack>

      {/* ── divergence banner ── */}
      {activeAgent && (
        <DivergenceBanner
          activeLabel={getAgentType(activeAgent.typeId)?.label || "this agent"}
          onPromote={() => setPromoteFor(activeAgent)}
          onRestore={() => setActive("source")}
        />
      )}

      {/* ── list ── */}
      <Stack spacing={1.25}>
        {allAgents.map((a) => (
          <AgentCard
            key={a.id}
            agent={a}
            env={env}
            isActive={a.isSource ? activeId == null : activeId === a.id}
            expanded={!!expanded[a.id]}
            onToggleExpand={() => toggleExpanded(a.id)}
            onSetActive={() => setActive(a.id)}
            onPromote={() => setPromoteFor(a)}
            onRemove={() => removeAgent(a.id)}
            onAddVersion={() => setAddingVersionFor(a)}
            onSetActiveVersion={(vId) => setActiveVersion(a.id, vId)}
            onGo={onGo}
            buildMode={buildMode}
          />
        ))}
      </Stack>

      {/* ── add drawer (new agent) ── */}
      <AddAgentDrawer
        open={adding}
        onClose={() => setAdding(false)}
        env={env}
        onAdd={addAgent}
      />

      {/* ── add drawer (new version of an existing agent) ── */}
      <AddAgentDrawer
        open={!!addingVersionFor}
        onClose={() => setAddingVersionFor(null)}
        env={env}
        editing={addingVersionFor}
        onAdd={(record) => addVersion(addingVersionFor?.id, record)}
      />

      {/* ── promote confirmation ── */}
      <PromoteDialog
        agent={promoteFor}
        onCancel={() => setPromoteFor(null)}
        onConfirm={confirmPromote}
      />
    </Box>
  );
}
AgentsPanel.propTypes = {
  env: PropTypes.object.isRequired,
  envState: PropTypes.object.isRequired,
  patch: PropTypes.func.isRequired,
  onGo: PropTypes.func,
  buildMode: PropTypes.bool,
  onBuilderTurn: PropTypes.func,
};

/* ── one card ─────────────────────────────────────────────────────────────── */

/**
 * A single agent row. Shape is the same for source and additional; a
 * left accent stripe + a small ENV SOURCE label are what mark the
 * source. Clicking anywhere on the header toggles the detail;
 * click-inside on action icons is `stopPropagation`-guarded so those
 * don't also toggle.
 */
function AgentCard({
  agent, env,
  isActive, expanded, onToggleExpand,
  onSetActive, onPromote, onRemove,
  onAddVersion, onSetActiveVersion,
  onGo,   // eslint-disable-line no-unused-vars
}) {
  const type = getAgentType(agent.typeId);
  /*
    Primary label is the agent's own name — the identifier we read
    from the source (repo name, agentId on a platform, endpoint
    hostname, etc.). The *type* (e.g. "Voice agent · platform") is
    secondary metadata sitting under the name.
  */
  const name = deriveAgentName(agent, type);
  const typeLine = deriveTypeLine(agent, type);
  const versions = agent.versions || [];
  const activeVersionLabel = versions.find((v) => v.id === agent.activeVersionId)?.label || "v1";

  return (
    <Box
      sx={{
        border: "1px solid",
        /*
          Uniform divider stroke on every card — role is communicated
          purely by the inline chips (ENV SOURCE / ACTIVE FOR RUNS /
          PREVIOUS SOURCE). Highlighting the active card's border made
          it read as visually louder than the others; the chip already
          identifies it.
        */
        borderColor: "divider",
        borderRadius: 1.5, overflow: "hidden",
        bgcolor: "background.paper",
      }}
    >
      {/* Header (clickable) */}
      <Stack
        direction="row" alignItems="center" spacing={1.5}
        onClick={onToggleExpand}
        sx={{
          px: 2, py: 1.75, cursor: "pointer",
          "&:hover": { bgcolor: "action.hover" },
        }}
      >
        {/*
          Type icon (channel/kind at a glance). The old
          "active-for-runs" radio circle that lived to the left of this
          was dropped — active state is already carried by the ACTIVE
          FOR RUNS chip and the highlighted border, and the row footer
          has the explicit "Set active for runs" action. A separate
          radio dot at the leading edge was decorative noise.
        */}
        <Box
          sx={{
            width: 34, height: 34, borderRadius: 1, flexShrink: 0,
            display: "grid", placeItems: "center",
            bgcolor: (t) => alpha(type?.color || t.palette.text.primary, t.palette.mode === "dark" ? 0.16 : 0.1),
            color: type?.color || "text.secondary",
          }}
        >
          <Iconify icon={type?.icon || "solar:cpu-bolt-linear"} width={18} />
        </Box>

        {/* Name + one-liner */}
        <Box flex={1} minWidth={0}>
          <Stack direction="row" alignItems="center" spacing={0.75} flexWrap="wrap" rowGap={0.5}>
            <Typography noWrap sx={{
              typography: "s1", fontWeight: 600, color: "text.primary",
              fontFamily: "ui-monospace, Menlo, monospace",
            }}>
              {name}
            </Typography>
            {/*
              Version chip — sits between the name and the role pill.
              Faint by default, tinted when the agent carries more than
              one version so the version stack is discoverable without
              being loud. Same visual weight as the role pills so the
              header still reads as one label row.
            */}
            <VersionChip label={activeVersionLabel} multi={versions.length > 1} />
            {/*
              Only one role chip per card. Source card carries ENV SOURCE
              always; additional card carries ACTIVE FOR RUNS only when
              runs actually target it (default = source, no chip needed).
            */}
            {agent.isSource && <RolePill label="ENV SOURCE" tint={SOURCE_ACCENT} />}
            {!agent.isSource && isActive && <RolePill label="ACTIVE FOR RUNS" tint={ACTIVE_ACCENT} />}
          </Stack>
          <Typography noWrap sx={{ typography: "s3", color: "text.subtitle", mt: 0.125 }}>
            {typeLine}
            {versions.length > 1 && (
              <Box component="span" sx={{ ml: 1, color: "text.disabled" }}>
                · {versions.length} versions
              </Box>
            )}
            {!agent.isSource && (agent.note || "").toLowerCase().includes("previous source") && (
              <Box component="span" sx={{ ml: 1, color: "text.disabled" }}>
                · previously the source
              </Box>
            )}
          </Typography>
        </Box>

        {/*
          Actions live inline in the header so the user doesn't have to
          expand the card to reach them. Each is a compact outlined
          chip-button with a leading icon — same height as the row so
          the top-right corner reads as a proper action bar, not a mess
          of hover-only icons or hidden footer links.
        */}
        <HeaderActions
          agent={agent}
          isActive={isActive}
          onSetActive={onSetActive}
          onPromote={onPromote}
          onRemove={onRemove}
        />

        {/* Expand chevron */}
        <Box
          onClick={(e) => { e.stopPropagation(); onToggleExpand(); }}
          sx={{
            width: 26, height: 26, borderRadius: 1, flexShrink: 0,
            display: "grid", placeItems: "center", cursor: "pointer",
            color: "text.subtitle",
            "&:hover": { bgcolor: "action.hover" },
          }}
        >
          <Iconify
            icon={expanded ? "solar:alt-arrow-up-linear" : "solar:alt-arrow-down-linear"}
            width={14}
          />
        </Box>
      </Stack>

      {/* Detail */}
      <Collapse in={expanded} unmountOnExit>
        <Divider />
        <AgentDetail
          agent={agent}
          type={type}
          onAddVersion={onAddVersion}
          onSetActiveVersion={onSetActiveVersion}
        />
      </Collapse>
    </Box>
  );
}
AgentCard.propTypes = {
  agent: PropTypes.object, env: PropTypes.object,
  isActive: PropTypes.bool, expanded: PropTypes.bool,
  onToggleExpand: PropTypes.func, onSetActive: PropTypes.func,
  onPromote: PropTypes.func, onRemove: PropTypes.func,
  onAddVersion: PropTypes.func, onSetActiveVersion: PropTypes.func,
  onGo: PropTypes.func, buildMode: PropTypes.bool,
};

/**
 * The card body when expanded. Same layout for source and non-source
 * so there's only one styling to learn: connection block on the left,
 * source-location block on the right, credentials block full-width
 * beneath. The tools/rules summary that used to sit at the top has
 * been dropped — the Contract tab is one click away in the tab strip,
 * so re-stating it inside the card was noise.
 */
function AgentDetail({ agent, type, onAddVersion, onSetActiveVersion }) {
  const values = agent.values || {};
  const connectionRows = connectionRowsFor(agent, type, values);
  const sourceRows = sourceRowsFor(agent);
  const showToken = agent.isSource;
  const versions = agent.versions || [];

  return (
    <Box sx={{ p: 2.5 }}>
      <Box
        sx={{
          display: "grid", gap: 2,
          gridTemplateColumns: { xs: "1fr", md: "1fr 1fr" },
        }}
      >
        <DetailBlock title="Connection" icon="solar:link-round-linear" rows={connectionRows} />
        <DetailBlock title="Source" icon="solar:code-square-linear" rows={sourceRows} />
      </Box>

      {/* ── versions timeline ── */}
      <Box sx={{ mt: 2 }}>
        <VersionsBlock
          versions={versions}
          activeVersionId={agent.activeVersionId}
          onAddVersion={onAddVersion}
          onSetActiveVersion={onSetActiveVersion}
        />
      </Box>

      {showToken && (
        <Box sx={{ mt: 2 }}>
          <DetailBlock
            title="Issued for this environment"
            icon="solar:key-linear"
            rows={[
              { label: "Test phone number", value: "+1 (415) 555-0182", copy: true },
              { label: "Environment token", value: "fagi_sim_sk_9c2f4b7ae15d8306", copy: true, mono: true },
            ]}
            subtitle="Rotates whenever you reset the environment"
          />
        </Box>
      )}

      {!agent.isSource && (
        <Stack direction="row" spacing={1} alignItems="center"
          sx={{
            mt: 2, px: 1.5, py: 1.25, borderRadius: 1.25,
            bgcolor: "background.neutral",
            border: "1px solid", borderColor: "divider",
          }}
        >
          <Iconify icon="solar:info-circle-linear" width={13} sx={{ color: "text.subtitle", flexShrink: 0 }} />
          <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
            Tested against the source&apos;s scenarios, tools and rules.
          </Typography>
        </Stack>
      )}
    </Box>
  );
}
AgentDetail.propTypes = {
  agent: PropTypes.object, type: PropTypes.object,
  onAddVersion: PropTypes.func, onSetActiveVersion: PropTypes.func,
};

/**
 * The version stack for one agent. Renders a compact timeline where
 * each row is one version — its label, when it was connected, the
 * note carried when it was added — with a radio-style "active"
 * indicator on the left and a "Set active" text action on the right
 * for the non-active rows. A prominent "New version" button sits at
 * the bottom of the block.
 *
 * Active-version selection is a purely-local decision (which build
 * of THIS agent to run against), separate from active-for-runs
 * (which agent to run against). Both need to be set for a run to
 * pick a concrete (agent, version) pair.
 */
function VersionsBlock({ versions, activeVersionId, onAddVersion, onSetActiveVersion }) {
  const list = versions?.length ? versions : [];
  return (
    <Box>
      <Stack direction="row" alignItems="center" spacing={0.75} sx={{ mb: 0.75 }}>
        <Iconify icon="solar:layers-minimalistic-linear" width={13} sx={{ color: "text.subtitle" }} />
        <Typography sx={{ typography: "s3", color: "text.subtitle", letterSpacing: 0.5, fontWeight: 700 }}>
          VERSIONS
        </Typography>
        <Typography sx={{ typography: "s3", color: "text.subtitle", fontVariantNumeric: "tabular-nums" }}>
          ({list.length})
        </Typography>
      </Stack>
      <Box sx={{
        borderRadius: 1.25, border: "1px solid", borderColor: "divider",
        bgcolor: "background.neutral", overflow: "hidden",
      }}>
        <Stack divider={<Box sx={{ borderBottom: "1px solid", borderColor: "divider" }} />}>
          {list.length === 0 ? (
            <Typography sx={{ typography: "s3", color: "text.subtitle", p: 1.25 }}>
              No versions recorded.
            </Typography>
          ) : (
            list.slice().reverse().map((v) => {
              const isActive = v.id === activeVersionId;
              return (
                <Stack
                  key={v.id}
                  direction="row" alignItems="center" spacing={1.25}
                  sx={{ px: 1.5, py: 1 }}
                >
                  <Iconify
                    icon={isActive ? "solar:record-circle-bold" : "solar:circle-linear"}
                    width={15}
                    sx={{ color: isActive ? SOURCE_ACCENT : "text.subtitle", flexShrink: 0 }}
                  />
                  <Box flex={1} minWidth={0}>
                    <Stack direction="row" alignItems="center" spacing={0.75} flexWrap="wrap" rowGap={0.25}>
                      <Typography sx={{
                        typography: "s3", fontWeight: 700, color: "text.primary",
                        fontFamily: "ui-monospace, Menlo, monospace",
                      }}>
                        {v.label}
                      </Typography>
                      {v.connectedAt && (
                        <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
                          · {new Date(v.connectedAt).toLocaleString(undefined, {
                            month: "short", day: "numeric", year: "numeric",
                            hour: "2-digit", minute: "2-digit",
                          })}
                        </Typography>
                      )}
                    </Stack>
                    {v.note && v.note !== "Environment source" && (
                      <Typography noWrap sx={{ typography: "s3", color: "text.subtitle" }}>
                        {v.note}
                      </Typography>
                    )}
                  </Box>
                  {!isActive && (
                    <Button
                      size="small"
                      onClick={() => onSetActiveVersion?.(v.id)}
                      sx={{ typography: "s3", fontWeight: 700, color: "text.secondary", minWidth: 0 }}
                    >
                      Set active
                    </Button>
                  )}
                </Stack>
              );
            })
          )}
        </Stack>
      </Box>
      <Stack direction="row" justifyContent="flex-end" sx={{ mt: 1 }}>
        <ChipButton
          icon="solar:add-circle-linear"
          label="New version"
          onClick={() => onAddVersion?.()}
        />
      </Stack>
    </Box>
  );
}
VersionsBlock.propTypes = {
  versions: PropTypes.array,
  activeVersionId: PropTypes.string,
  onAddVersion: PropTypes.func,
  onSetActiveVersion: PropTypes.func,
};

/* ── inline header actions ────────────────────────────────────────────────── */

/**
 * The action bar that lives on the right side of every card header.
 *
 * Compact outlined chip-buttons — 26px tall to match the chevron
 * next to them — so the top-right corner reads as one action group
 * rather than a mix of hover-only icons or hidden overflow. Every
 * button carries a leading icon plus a short label so intent is
 * scannable without hovering.
 *
 * `stopPropagation` on every click so the button doesn't also toggle
 * the row's expand.
 */
/**
 * Header actions — both action tiers live here now, styled so their
 * relative weight reads on sight:
 *
 *  · Set active         — filled chip button. Cheap, reversible flip
 *                         of which agent runs execute against.
 *  · Promote to source  — text link (underlined on hover). Rare,
 *                         destructive commitment — re-derives the
 *                         contract from this agent. Visible but
 *                         clearly subordinate to Set active so users
 *                         don't read them as parallel choices.
 *  · Remove             — icon-only, right-most.
 *
 * The earlier version hid Promote inside the expanded body, which
 * users could not find; the version before that had Promote and Set
 * active as same-weight chips, which made a big commitment look
 * equivalent to a temporary switch. Hierarchy fixes both.
 */
function HeaderActions({ agent, isActive, onSetActive, onPromote, onRemove }) {
  return (
    <Stack direction="row" alignItems="center" spacing={0.75} sx={{ flexShrink: 0 }}>
      {/*
        Green is reserved for the ACTIVE FOR RUNS *state* chip. The
        "Set active" *action* is neutral so users can tell what is a
        badge and what is a button at a glance — the chip says
        "current state", the button asks for a click.
      */}
      {!isActive && (
        <ChipButton
          icon="solar:play-circle-linear"
          label="Set active"
          onClick={(e) => { e.stopPropagation(); onSetActive(); }}
        />
      )}
      {!agent.isSource && (
        <Button
          size="small" variant="text"
          onClick={(e) => { e.stopPropagation(); onPromote?.(); }}
          sx={{
            height: 26, minHeight: 26, minWidth: 0, px: 0.75, borderRadius: 1,
            typography: "s3", fontWeight: 600, color: "text.subtitle",
            "&:hover": { color: SOURCE_ACCENT, bgcolor: "transparent", textDecoration: "underline" },
          }}
        >
          Promote to source
        </Button>
      )}
      <ChipIconButton
        icon="solar:trash-bin-minimalistic-linear"
        tooltip={agent.isSource
          ? "Remove source — the environment will need a new one before it can run"
          : "Remove from this environment"}
        onClick={(e) => { e.stopPropagation(); onRemove(); }}
      />
    </Stack>
  );
}
HeaderActions.propTypes = {
  agent: PropTypes.object, isActive: PropTypes.bool,
  onSetActive: PropTypes.func, onPromote: PropTypes.func, onRemove: PropTypes.func,
};

/**
 * Small outlined button used in card headers. Style is consistent
 * across all row actions so the group reads as a coherent bar. When
 * `tint` is passed, the icon + border take that color; otherwise
 * neutral (divider border, text-secondary content). Hover deepens
 * the tint background.
 */
function ChipButton({ icon, label, tint, onClick }) {
  return (
    <Button
      size="small" variant="outlined" onClick={onClick}
      startIcon={<Iconify icon={icon} width={14} />}
      sx={{
        height: 26, minHeight: 26, minWidth: 0, px: 1, borderRadius: 1,
        typography: "s3", fontWeight: 700,
        color: tint || "text.secondary",
        borderColor: (t) => tint ? alpha(tint, 0.35) : t.palette.divider,
        bgcolor: (t) => tint ? alpha(tint, t.palette.mode === "dark" ? 0.06 : 0.03) : "transparent",
        "& .MuiButton-startIcon": { mr: 0.5, ml: 0 },
        "&:hover": {
          borderColor: (t) => tint ? alpha(tint, 0.6) : t.palette.text.subtitle,
          bgcolor: (t) => tint ? alpha(tint, t.palette.mode === "dark" ? 0.14 : 0.08) : t.palette.action.hover,
        },
      }}
    >
      {label}
    </Button>
  );
}
ChipButton.propTypes = {
  icon: PropTypes.string, label: PropTypes.string,
  tint: PropTypes.string, onClick: PropTypes.func,
};

/**
 * Icon-only variant of ChipButton, for destructive / secondary
 * actions where the icon alone is unambiguous (e.g. remove). Same
 * shell as ChipButton so the row's action group stays visually
 * consistent.
 */
function ChipIconButton({ icon, tooltip, onClick }) {
  return (
    <Tooltip arrow title={tooltip || ""}>
      <IconButton
        size="small" onClick={onClick}
        sx={{
          width: 26, height: 26, borderRadius: 1,
          border: "1px solid", borderColor: "divider",
          color: "text.subtitle",
          "&:hover": {
            borderColor: "text.subtitle",
            bgcolor: "action.hover",
            color: "text.primary",
          },
        }}
      >
        <Iconify icon={icon} width={14} />
      </IconButton>
    </Tooltip>
  );
}
ChipIconButton.propTypes = {
  icon: PropTypes.string, tooltip: PropTypes.string, onClick: PropTypes.func,
};

/* ── shared bits ──────────────────────────────────────────────────────────── */

function DetailBlock({ title, icon, rows, subtitle }) {
  return (
    <Box>
      <Stack direction="row" alignItems="center" spacing={0.75} sx={{ mb: 0.75 }}>
        <Iconify icon={icon} width={13} sx={{ color: "text.subtitle" }} />
        <Typography sx={{ typography: "s3", color: "text.subtitle", letterSpacing: 0.5, fontWeight: 700 }}>
          {title.toUpperCase()}
        </Typography>
      </Stack>
      {subtitle && (
        <Typography sx={{ typography: "s3", color: "text.subtitle", mb: 0.75 }}>
          {subtitle}
        </Typography>
      )}
      <Box
        sx={{
          borderRadius: 1.25,
          border: "1px solid", borderColor: "divider",
          bgcolor: "background.neutral",
          p: 1.25,
        }}
      >
        <Stack spacing={0.75}>
          {rows.map((r, i) => <PropRow key={r.label + i} {...r} />)}
        </Stack>
      </Box>
    </Box>
  );
}
DetailBlock.propTypes = {
  title: PropTypes.string, icon: PropTypes.string,
  rows: PropTypes.array, subtitle: PropTypes.string,
};

function PropRow({ label, value, mono, copy }) {
  return (
    <Stack direction={{ xs: "column", sm: "row" }} spacing={{ xs: 0.25, sm: 1.5 }} alignItems={{ sm: "center" }}>
      <Typography sx={{
        typography: "s3", color: "text.subtitle",
        width: { sm: 130 }, flexShrink: 0,
      }}>
        {label}
      </Typography>
      <Stack direction="row" alignItems="center" spacing={0.75} flex={1} minWidth={0}>
        <Typography sx={{
          typography: "s3", color: "text.primary",
          flex: 1, minWidth: 0, wordBreak: "break-word",
          fontFamily: mono ? "ui-monospace, Menlo, monospace" : undefined,
        }}>
          {value}
        </Typography>
        {copy && (
          <Tooltip arrow title="Copy">
            <IconButton
              size="small"
              onClick={() => navigator.clipboard?.writeText(String(value))}
              sx={{ p: 0.25 }}
            >
              <Iconify icon="solar:copy-linear" width={13} sx={{ color: "text.subtitle" }} />
            </IconButton>
          </Tooltip>
        )}
      </Stack>
    </Stack>
  );
}
PropRow.propTypes = {
  label: PropTypes.string, value: PropTypes.any,
  mono: PropTypes.bool, copy: PropTypes.bool,
};

/**
 * Neutral-toned version chip that sits between the agent name and
 * the role pill in the card header. Tinted stronger when the agent
 * carries more than one version so the version stack is visible.
 */
function VersionChip({ label, multi }) {
  return (
    <Box
      sx={{
        display: "inline-flex", alignItems: "center",
        height: 18, borderRadius: 0.75, px: 0.75,
        typography: "s3", fontWeight: 700, letterSpacing: 0.3,
        fontFamily: "ui-monospace, Menlo, monospace",
        color: multi ? SOURCE_ACCENT : "text.subtitle",
        border: "1px solid",
        borderColor: (t) => multi ? alpha(SOURCE_ACCENT, 0.35) : t.palette.divider,
        bgcolor: (t) => multi
          ? alpha(SOURCE_ACCENT, t.palette.mode === "dark" ? 0.14 : 0.08)
          : "transparent",
      }}
    >
      {label}
    </Box>
  );
}
VersionChip.propTypes = { label: PropTypes.string, multi: PropTypes.bool };

function RolePill({ label, tint }) {
  return (
    <Chip
      size="small" label={label}
      sx={{
        height: 18, borderRadius: 0.75,
        bgcolor: (t) => alpha(tint, t.palette.mode === "dark" ? 0.18 : 0.1),
        color: tint,
        border: "1px solid", borderColor: alpha(tint, 0.35),
        "& .MuiChip-label": {
          px: 0.75, typography: "s3",
          fontWeight: 700, letterSpacing: 0.4,
        },
      }}
    />
  );
}
RolePill.propTypes = { label: PropTypes.string, tint: PropTypes.string };

function StatPill({ value, label }) {
  return (
    <Stack direction="row" alignItems="baseline" spacing={0.375}>
      <Typography sx={{ typography: "s2", fontWeight: 700, color: "text.primary", fontVariantNumeric: "tabular-nums" }}>
        {value}
      </Typography>
      {label && (
        <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
          {label}
        </Typography>
      )}
    </Stack>
  );
}
StatPill.propTypes = { value: PropTypes.node, label: PropTypes.string };

/**
 * Shown only when active-for-runs is a non-source agent. Deliberately
 * neutral — the earlier amber/warning-triangle version framed the
 * intended A/B testing flow as a mistake. What this actually is: a
 * heads-up that runs and scenarios have different provenance right
 * now, plus one-click paths to either revert or commit the change.
 */
function DivergenceBanner({ activeLabel, onPromote, onRestore }) {
  return (
    <Stack
      direction={{ xs: "column", sm: "row" }}
      alignItems={{ sm: "center" }} spacing={1.25}
      sx={{
        mb: 2, px: 1.75, py: 1.25, borderRadius: 1.25,
        bgcolor: "background.neutral",
        border: "1px solid", borderColor: "divider",
      }}
    >
      <Iconify icon="solar:info-circle-linear" width={15}
        sx={{ color: "text.subtitle", flexShrink: 0 }} />
      <Box flex={1} minWidth={0}>
        <Typography sx={{ typography: "s2", color: "text.primary" }}>
          Runs target{" "}
          <Box component="span" sx={{ fontFamily: "ui-monospace, Menlo, monospace", fontWeight: 700 }}>
            {activeLabel}
          </Box>
          . Scenarios and rules still come from the source.
        </Typography>
      </Box>
      <Stack direction="row" spacing={0.5} sx={{ flexShrink: 0 }}>
        <Button
          size="small" onClick={onRestore}
          sx={{ typography: "s3", fontWeight: 600, color: "text.subtitle" }}
        >
          Restore to source
        </Button>
        <Button
          size="small" onClick={onPromote}
          sx={{ typography: "s3", fontWeight: 700, color: SOURCE_ACCENT }}
        >
          Promote to source
        </Button>
      </Stack>
    </Stack>
  );
}
DivergenceBanner.propTypes = {
  activeLabel: PropTypes.string, onPromote: PropTypes.func, onRestore: PropTypes.func,
};

function PromoteDialog({ agent, onCancel, onConfirm }) {
  return (
    <Dialog open={!!agent} onClose={onCancel} maxWidth="sm" fullWidth>
      <DialogTitle sx={{ typography: "m2", fontWeight: 600 }}>
        Update environment from this agent?
      </DialogTitle>
      <DialogContent>
        <Typography sx={{ typography: "s2", color: "text.secondary", mb: 2 }}>
          The environment re-reads its contract, tools and rules from{" "}
          <Box component="span" sx={{ fontFamily: "ui-monospace, Menlo, monospace", color: "text.primary" }}>
            {getAgentType(agent?.typeId)?.label}
          </Box>
          . That means:
        </Typography>
        <Stack spacing={1} sx={{ mb: 2 }}>
          <ImpactRow icon="solar:document-text-linear" title="Contract regenerates"
            body="Tools and rules will change to whatever this agent declares." />
          <ImpactRow icon="solar:layers-minimalistic-linear" title="Scenarios re-derive"
            body="Each goes back through pre-verification. Ones that no longer pass move to a stale archive." />
          <ImpactRow icon="solar:shield-check-linear" title="Evaluations may shift"
            body="Preset evals referencing the old contract get re-evaluated. Custom evals are kept." />
          <ImpactRow icon="solar:history-linear" title="Old source stays attached"
            body="It moves to Additional agents so you can still run against it and compare." />
        </Stack>
      </DialogContent>
      <DialogActions sx={{ px: 3, pb: 2 }}>
        <Button onClick={onCancel} sx={{ typography: "s2", fontWeight: 600, color: "text.secondary" }}>
          Cancel
        </Button>
        <Button
          variant="contained" color="primary" onClick={onConfirm}
          startIcon={<Iconify icon="solar:refresh-circle-linear" width={15} />}
          sx={{ typography: "s2", fontWeight: 700 }}
        >
          Update environment
        </Button>
      </DialogActions>
    </Dialog>
  );
}
PromoteDialog.propTypes = {
  agent: PropTypes.object, onCancel: PropTypes.func, onConfirm: PropTypes.func,
};

function ImpactRow({ icon, title, body }) {
  return (
    <Stack direction="row" alignItems="flex-start" spacing={1.25}>
      <Iconify icon={icon} width={15} sx={{ color: "text.subtitle", mt: "2px", flexShrink: 0 }} />
      <Box>
        <Typography sx={{ typography: "s2", fontWeight: 600 }}>{title}</Typography>
        <Typography sx={{ typography: "s3", color: "text.subtitle" }}>{body}</Typography>
      </Box>
    </Stack>
  );
}
ImpactRow.propTypes = { icon: PropTypes.string, title: PropTypes.string, body: PropTypes.string };

/* ── data shaping helpers ─────────────────────────────────────────────────── */

/**
 * Derive the display name for an agent card. In production, this
 * mirrors what we would extract from the source at connect time —
 * the agent's own identifier, not the platform kind.
 *
 *  · Hosted platform → the assistant/agent id ("asst_9f2c1188")
 *  · Repo            → the repo name from the URL ("customer-support-bot")
 *  · Endpoint        → the last path segment or the hostname
 *  · MCP             → the hostname
 *  · Upload          → the file's base name
 *  · Fallback        → an anonymised handle so nothing renders blank
 */
function deriveAgentName(agent, type) {
  const values = agent.values || {};

  if (values.agentId) return values.agentId;
  if (values.assistantId) return values.assistantId;
  if (values.agentName) return values.agentName;

  if (values.repoUrl) return repoNameFrom(values.repoUrl);
  if (agent.location && agent.location.includes("github.com")) return repoNameFrom(agent.location);

  if (values.endpoint) return endpointNameFrom(values.endpoint);
  if (values.mcpUrl) return short(values.mcpUrl);

  if (values.file || values.filename) return (values.file || values.filename).replace(/\.[^.]+$/, "");

  /* Last resort — a stable short id derived from the agent's id
     so multiple unnamed agents don't all read as the same. */
  const suffix = String(agent.id || "").slice(-6) || "unnamed";
  const stem = (type?.label || "agent").split(" ")[0].toLowerCase();
  return `${stem}-${suffix}`;
}

/**
 * Secondary line under the name. Says what kind of agent this is +
 * where it lives, in that order — the pieces the name alone doesn't
 * convey. Keeps things scannable without duplicating the name.
 */
function deriveTypeLine(agent, type) {
  const values = agent.values || {};
  const parts = [type?.label];
  if (values.provider) parts.push(values.provider);
  else if (values.repoUrl) parts.push(`branch: ${values.ref?.value || "main"}`);
  else if (values.endpoint) parts.push(short(values.endpoint));
  else if (values.mcpUrl) parts.push("MCP");
  if (values.direction || values.callDirection) parts.push(values.direction || values.callDirection);
  return parts.filter(Boolean).join(" · ");
}

function repoNameFrom(url) {
  if (!url) return "repo";
  const clean = url.replace(/\.git$/, "").replace(/\/$/, "");
  const seg = clean.split("/").filter(Boolean).pop() || "repo";
  return seg;
}

function endpointNameFrom(url) {
  if (!url) return "endpoint";
  try {
    const u = new URL(url);
    const seg = u.pathname.split("/").filter(Boolean).pop();
    return seg || u.hostname;
  } catch {
    return short(url);
  }
}

function short(u) {
  if (!u) return "";
  return u.replace(/^https?:\/\//, "").replace(/^www\./, "");
}

function connectionRowsFor(agent, type, values) {
  /* Voice platform */
  if (values.provider) {
    return [
      { label: "Channel", value: type?.channel || "Voice" },
      { label: "Provider", value: values.provider, mono: true },
      { label: "Agent name", value: values.agentId || values.assistantId || "—", mono: true },
      { label: "Call direction", value: values.direction || "inbound", mono: true },
    ];
  }
  /* Repo */
  if (values.repoUrl) {
    return [
      { label: "Kind", value: "Repository" },
      { label: "URL", value: values.repoUrl, mono: true },
      values.ref && { label: "Pinned to", value: `${values.ref.kind} · ${values.ref.value}`, mono: true },
    ].filter(Boolean);
  }
  /* Endpoint */
  if (values.endpoint) {
    return [
      { label: "Kind", value: "Running endpoint" },
      { label: "URL", value: values.endpoint, mono: true },
    ];
  }
  /* MCP */
  if (values.mcpUrl) {
    return [
      { label: "Kind", value: "MCP server" },
      { label: "URL", value: values.mcpUrl, mono: true },
    ];
  }
  return [{ label: "Kind", value: type?.label || "Agent" }];
}

function sourceRowsFor(agent) {
  const values = agent.values || {};
  const attached = agent.connectedAt ? new Date(agent.connectedAt).toLocaleString() : null;
  const rows = [];
  if (values.repoUrl) rows.push({ label: "Location", value: values.repoUrl, mono: true });
  if (values.endpoint) rows.push({ label: "Location", value: values.endpoint, mono: true });
  if (values.mcpUrl) rows.push({ label: "Location", value: values.mcpUrl, mono: true });
  if (agent.via) rows.push({ label: "Attached via", value: agent.via });
  if (attached) rows.push({ label: "Attached at", value: attached });
  if (agent.note) rows.push({ label: "Note", value: agent.note });
  if (rows.length === 0) rows.push({ label: "How we read it", value: "Read from the imports and call sites in the source." });
  return rows;
}

const DEFAULT_CONTRACT_STATS = { tools: 12, rules: 5, modality: "Voice" };

/**
 * Scripted step list for the "environment re-derived from a promoted
 * agent" builder turn. Mirrors the shape of stages in
 * _mock/builder.js (kind: think | tool | note | json) so the same
 * AssistantConsole components render it without any new step types.
 *
 * These steps don't actually rerun the pipeline in the mock — the
 * state change already happened synchronously in confirmPromote. What
 * they do is *narrate* the change so the user has a mental model of
 * what the platform just did on their behalf, and a record of it in
 * the chat scrollback.
 */
/* ── version helpers ──────────────────────────────────────────────────────── */

/**
 * Wrap a bare agent (no `versions[]`) as its own initial version so
 * every agent — source or additional, freshly attached or stored
 * before versioning existed — has the same shape after normalisation.
 * Idempotent: an already-normalised agent passes through unchanged.
 */
function normalizeAgentVersions(agent) {
  if (!agent) return agent;
  if (Array.isArray(agent.versions) && agent.versions.length > 0) return agent;
  const v1 = {
    id: "v1",
    label: "v1",
    values: agent.values || {},
    via: agent.via,
    connectedAt: agent.connectedAt || new Date().toISOString(),
    note: agent.note || "Initial version",
  };
  return { ...agent, versions: [v1], activeVersionId: "v1" };
}

/**
 * Mint the next version record for an agent, keyed sequentially off
 * the existing stack (`v1` → `v2` → `v3`). Not tied to time so the
 * label reads the same regardless of when the version was minted.
 */
function mintNextVersion(agent, record) {
  const existing = agent.versions || [];
  const nextNumber = existing.length + 1;
  const versionId = `v${nextNumber}`;
  return {
    id: versionId,
    label: `v${nextNumber}`,
    values: record?.values || {},
    via: record?.via,
    connectedAt: record?.connectedAt || new Date().toISOString(),
    note: record?.note || `Version ${nextNumber}`,
  };
}

/**
 * Copy the active version's connection fields up to the agent's
 * top level so any downstream code that reads `agent.values`,
 * `agent.via`, `agent.connectedAt`, or `agent.note` (name derivation,
 * connection-row rendering, promotion, etc.) sees the currently
 * chosen version's data without knowing versions exist. The version
 * stack itself lives underneath.
 */
function applyActiveVersion(agent) {
  const active = (agent.versions || []).find((v) => v.id === agent.activeVersionId)
    || (agent.versions || [])[0];
  if (!active) return agent;
  return {
    ...agent,
    values: active.values,
    via: active.via,
    connectedAt: active.connectedAt,
    note: active.note,
  };
}

function buildPromoteSteps({ from, to }) {
  const fromLabel = getAgentType(from?.typeId)?.label || "the previous source";
  const toLabel = getAgentType(to?.typeId)?.label || "the new source";
  const toVia = to?.via || to?.location || "the attached source";
  return [
    { kind: "think", text: `Setting ${toLabel} as the source of this environment. Detaching ${fromLabel}.` },
    { kind: "tool", label: `read_agent(${toVia})`, result: "loaded" },
    { kind: "tool", label: "extract_tools()", result: "12 tools" },
    { kind: "tool", label: "extract_rules()", result: "5 rules" },
    { kind: "note", text: "Contract regenerated. Comparing against the previous derivation." },
    { kind: "json", label: "contract diff", value: JSON.stringify({ tools_added: 1, tools_removed: 0, rules_changed: 1 }, null, 2) },
    { kind: "tool", label: "re_derive_scenarios()", result: "88 kept · 3 archived (no longer solvable)" },
    { kind: "tool", label: "reevaluate_preset_evals()", result: "no changes" },
    { kind: "note", text: `${fromLabel} is still attached as an additional agent. Runs default to the new source; switch back any time from the Agents tab.` },
  ];
}
