import PropTypes from "prop-types";
import { useState } from "react";
import { enqueueSnackbar } from "notistack";
import { Box, Button, Chip, Collapse, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import SectionCard from "../../components/SectionCard";
import SoonChip from "../../components/SoonChip";
import UiOnlyFlag from "../../components/UiOnlyFlag";
import { UI_ONLY_SAVED } from "../gaps/NeedsInputButton";
import { TIERS } from "src/api/simulate-environments/_fixtures/detail/world";
import ToolArgsGrid, { realToolArgs } from "./ToolArgsGrid";
import { FaultsControl, TierDial } from "./ToolDial";
import CodeBlock from "./CodeBlock";

const MONO = "ui-monospace, Menlo, monospace";
const EDIT_NOTE =
  "how each tool answers during a run. A change is a world edit, saved as the next environment version.";

// Where ALK says a tool runs: module.callable, or service METHOD /endpoint.
// The chip shows the callable; the module path is in its tooltip.
const runsAtShort = (entry) => entry?.callable || null;
const runsAtOf = (entry) =>
  entry?.callable
    ? `${entry.module ? `${entry.module}.` : ""}${entry.callable}`
    : entry?.endpoint
      ? `${entry.service ? `${entry.service} ` : ""}${entry.method || "POST"} /${entry.endpoint}`
      : null;

const tierSummary = (tiers) =>
  TIERS.map((t) => [t.word.toLowerCase(), Object.values(tiers).filter((x) => x === t.id).length])
    .filter(([, n]) => n)
    .map(([w, n]) => `${n} ${w}`)
    .join(" · ");

// The world's tools as Vel lists them. A real environment shows each tool's
// name, description, where it runs and its arguments; its tier, faults and
// handler code aren't in the API, so the card carries one Tiers soon chip and
// each open row a Code soon chip. `sample` swaps in Vel's tools with the dial.
export default function ToolsCard({ tools, entrypoints, sample, onAddMcp }) {
  const [open, setOpen] = useState(null);
  const [tiers, setTiers] = useState(() =>
    sample ? Object.fromEntries(tools.map((t) => [t.name, t.tier])) : {},
  );
  const [faults, setFaults] = useState({});
  const uiOnly = () => enqueueSnackbar(UI_ONLY_SAVED, { variant: "info" });

  return (
    <SectionCard
      title={
        <Stack direction="row" alignItems="center" spacing={0.75}>
          <span>{`Tools · ${tools.length}`}</span>
          {sample ? <UiOnlyFlag feature="toolFidelity" /> : <SoonChip label="Tiers" detail="How each tool answers during a run: Fixed (a set response), Recorded (a real response played back), Simulated (answered by the environment's world, reading and writing its data) or Live (the real tool). Faults are timeouts, errors or slow replies injected on purpose." what="Each tool's Fixed, Recorded, Simulated or Live setting and its faults" plural size="tiny" />}
        </Stack>
      }
      subtitle={
        sample ? (
          <>
            <Box component="span" sx={{ color: "text.primary", fontWeight: 600, fontVariantNumeric: "tabular-nums" }}>
              {tierSummary(tiers)}
            </Box>
            {`, ${EDIT_NOTE}`}
          </>
        ) : (
          `The tools the agent declares: ${EDIT_NOTE}`
        )
      }
      action={
        <Button
          size="small"
          variant="outlined"
          onClick={onAddMcp}
          startIcon={<Iconify icon="solar:add-circle-linear" width={16} />}
          sx={{ color: "text.primary", borderColor: "divider", flexShrink: 0, whiteSpace: "nowrap" }}
        >
          Add tools
        </Button>
      }
    >
      <Stack divider={<Box sx={{ borderBottom: "1px solid", borderColor: "divider" }} />}>
        {tools.map((tool) => {
          const isOpen = open === tool.name;
          const entry = sample ? null : entrypoints[tool.name];
          const runsAt = runsAtOf(entry);
          const runsAtLabel = runsAtShort(entry) || runsAt;
          const args = sample ? tool.args : realToolArgs(tool);
          return (
            <Box key={tool.name}>
              <Stack
                direction="row"
                alignItems="center"
                spacing={1.5}
                role="button"
                tabIndex={0}
                aria-expanded={isOpen}
                onClick={() => setOpen(isOpen ? null : tool.name)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    setOpen(isOpen ? null : tool.name);
                  }
                }}
                sx={{ px: 2.5, py: 1.125, cursor: "pointer", "&:hover": { bgcolor: "action.hover" } }}
              >
                <Iconify
                  icon={isOpen ? "solar:alt-arrow-down-linear" : "solar:alt-arrow-right-linear"}
                  width={14}
                  sx={{ color: "text.subtitle", flexShrink: 0 }}
                />
                <Iconify icon="solar:code-linear" width={16} sx={{ color: "primary.main", flexShrink: 0 }} />
                <Typography noWrap sx={{ typography: "s2", fontWeight: 600, fontFamily: MONO, width: 220, flexShrink: 0 }}>
                  {tool.name}
                </Typography>
                <Typography noWrap sx={{ typography: "s3", color: "text.subtitle", flex: 1, minWidth: 0 }}>
                  {tool.desc}
                </Typography>
                {runsAt && (
                  <CustomTooltip show arrow size="small" title={`Runs at ${runsAt}`}>
                    <Chip
                      size="small"
                      label={runsAtLabel}
                      sx={{
                        height: 18, maxWidth: 280, borderRadius: 0.5, color: "text.subtitle",
                        border: "1px solid", borderColor: "divider", bgcolor: "transparent", flexShrink: 1,
                        "& .MuiChip-label": { px: 0.75, typography: "s3", fontWeight: 600, fontFamily: MONO },
                      }}
                    />
                  </CustomTooltip>
                )}
                {sample && (
                  <>
                    <TierDial
                      name={tool.name}
                      tier={tiers[tool.name]}
                      defaultTier={tool.tier}
                      liveLocked={tool.tier === "stateful"}
                      onChange={(tier) => {
                        setTiers((t) => ({ ...t, [tool.name]: tier }));
                        uiOnly();
                      }}
                    />
                    <FaultsControl
                      preset={faults[tool.name] || "none"}
                      onApply={(p) => {
                        setFaults((f) => ({ ...f, [tool.name]: p }));
                        uiOnly();
                      }}
                    />
                  </>
                )}
              </Stack>
              <Collapse in={isOpen} unmountOnExit>
                <Box sx={{ borderTop: "1px solid", borderColor: "divider", px: 2.5, pl: 7.25, py: 1.25 }}>
                  <Typography sx={{ typography: "s3", fontWeight: 600, color: "text.subtitle", mb: 0.5 }}>
                    Arguments
                  </Typography>
                  {args.length === 0 ? (
                    <Typography sx={{ typography: "s3", color: "text.disabled" }}>None</Typography>
                  ) : (
                    <ToolArgsGrid args={args} />
                  )}
                </Box>
                {sample ? (
                  <CodeBlock file={`tools/${tool.name}.py`} code={tool.code} />
                ) : (
                  <Stack
                    direction="row"
                    alignItems="center"
                    spacing={1}
                    sx={{ borderTop: "1px solid", borderColor: "divider", px: 2.5, pl: 7.25, py: 1 }}
                  >
                    <Iconify icon="solar:file-text-linear" width={12} sx={{ color: "text.subtitle" }} />
                    <Typography sx={{ typography: "s3", fontFamily: MONO, color: "text.secondary" }}>
                      {`tools/${tool.name}.py`}
                    </Typography>
                    <SoonChip label="Code" detail="The handler the environment runs to answer this tool, so you can read exactly what a call gets back." what="The handler code the world runs for this tool" size="tiny" />
                  </Stack>
                )}
              </Collapse>
            </Box>
          );
        })}
        <Stack
          direction="row"
          alignItems="center"
          spacing={1.5}
          role="button"
          tabIndex={0}
          onClick={onAddMcp}
          onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && onAddMcp()}
          sx={{ px: 2.5, py: 1.25, cursor: "pointer", "&:hover": { bgcolor: "action.hover" } }}
        >
          <Box sx={{ width: 14, flexShrink: 0 }} />
          <Iconify icon="solar:add-circle-linear" width={16} sx={{ color: "text.secondary", flexShrink: 0 }} />
          <Typography sx={{ typography: "s2", fontWeight: 600, width: 220, flexShrink: 0 }}>
            Add tools from an MCP server
          </Typography>
          <Typography noWrap sx={{ typography: "s3", color: "text.subtitle", minWidth: 0 }}>
            Shopify, Stripe, Zendesk or any server URL. Each tool starts Simulated.
          </Typography>
        </Stack>
      </Stack>
    </SectionCard>
  );
}
ToolsCard.propTypes = {
  tools: PropTypes.array.isRequired,
  entrypoints: PropTypes.object,
  sample: PropTypes.bool,
  onAddMcp: PropTypes.func.isRequired,
};

// No tools yet: they come from the agent, or from an MCP server.
export function NoToolsCard({ agentName, onAddMcp }) {
  return (
    <SectionCard
      title="Tools · 0"
      subtitle={
        agentName
          ? `${agentName} declares no tools, so the world has none to answer. Add tools from an MCP server to give it some to act on.`
          : "None yet. The agent's tools are read when it's connected. Or add tools from an MCP server now."
      }
      action={
        <Button
          size="small"
          variant="outlined"
          onClick={onAddMcp}
          startIcon={<Iconify icon="solar:add-circle-linear" width={14} />}
          sx={{ color: "text.primary", borderColor: "divider", flexShrink: 0, whiteSpace: "nowrap" }}
        >
          Add tools from an MCP server
        </Button>
      }
    />
  );
}
NoToolsCard.propTypes = { agentName: PropTypes.string, onAddMcp: PropTypes.func.isRequired };
