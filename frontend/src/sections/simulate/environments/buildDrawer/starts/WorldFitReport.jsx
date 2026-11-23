import PropTypes from "prop-types";
import { useState } from "react";
import {
  Box,
  ButtonBase,
  Link,
  MenuItem,
  Select,
  Stack,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { T12, T13 } from "../drawerStyles";
import { fitVerdict } from "./worldFit";

const firstLower = (text) => `${text.charAt(0).toLowerCase()}${text.slice(1)}`;
// A case named only by its key reads as words ("a-deleted-policy" → "A deleted policy").
const readable = (title) =>
  /\s/.test(title)
    ? title
    : title
        .replace(/[-_]+/g, " ")
        .replace(/^./, (first) => first.toUpperCase());

const VERDICT = {
  ready: {
    label: "Ready",
    icon: "solar:check-circle-linear",
    tone: "status.success.fg",
  },
  partial: {
    label: "Partial",
    icon: "solar:danger-triangle-linear",
    tone: "status.warning.fg",
  },
  none: {
    label: "Can't be measured",
    icon: "solar:danger-triangle-linear",
    tone: "status.warning.fg",
  },
};

function TextLink({ children, onClick }) {
  return (
    <Link
      component="button"
      type="button"
      underline="hover"
      onClick={onClick}
      sx={{ ...T12, color: "text.secondary", whiteSpace: "nowrap" }}
    >
      {children}
    </Link>
  );
}
TextLink.propTypes = { children: PropTypes.node, onClick: PropTypes.func };

function PairRow({ row, agentName, agentTools, onPair }) {
  return (
    <Stack
      direction="row"
      spacing={1.5}
      alignItems="center"
      sx={{ px: 1.5, py: 0.5, borderTop: "1px solid", borderColor: "divider" }}
    >
      <Typography
        noWrap
        sx={{ ...T13, fontFamily: "monospace", width: 190, flexShrink: 0 }}
      >
        {row.world}
      </Typography>
      <Box flex={1} minWidth={0}>
        <Select
          size="small"
          fullWidth
          value={row.agent || ""}
          displayEmpty
          inputProps={{ "aria-label": `${agentName}'s tool for ${row.world}` }}
          onChange={(event) => onPair(row.world, event.target.value || null)}
          sx={{
            ...T13,
            fontFamily: "monospace",
            "& .MuiSelect-select": { py: 0.5 },
          }}
        >
          <MenuItem value="" sx={{ ...T13, fontFamily: "monospace" }}>
            Not available
          </MenuItem>
          {agentTools.map((tool) => (
            <MenuItem
              key={tool.name}
              value={tool.name}
              sx={{ ...T13, fontFamily: "monospace" }}
            >
              {tool.name}
            </MenuItem>
          ))}
        </Select>
      </Box>
      <Iconify
        icon={
          row.agent ? "solar:check-circle-linear" : "solar:minus-circle-linear"
        }
        width={16}
        aria-label={row.agent ? "Paired" : "Not available"}
        sx={{
          color: row.agent ? "status.success.fg" : "status.warning.fg",
          flexShrink: 0,
        }}
      />
    </Stack>
  );
}
PairRow.propTypes = {
  row: PropTypes.object.isRequired,
  agentName: PropTypes.string.isRequired,
  agentTools: PropTypes.array.isRequired,
  onPair: PropTypes.func.isRequired,
};

/**
 * How it fits (env-world-agent-picked, env-world-no-match): each world tool paired with the
 * agent's tool that does its job, or Not available. A case can be measured only when every
 * tool it needs is paired; the rest stay listed, never dropped. The suite is never rewritten.
 */
export default function WorldFitReport({
  agentName,
  fit,
  onPair,
  onOtherWorld,
}) {
  const [showMatched, setShowMatched] = useState(false);
  const [showUnmeasured, setShowUnmeasured] = useState(false);
  const { verdict } = fitVerdict(fit);
  const matched = fit.rows.filter((row) => row.agent);
  const missing = fit.rows.filter((row) => !row.agent);
  const cases = (n) => `${n} ${n === 1 ? "case" : "cases"}`;
  return (
    <Stack spacing={1.5}>
      <Stack direction="row" spacing={1} alignItems="flex-start">
        <Iconify
          icon={VERDICT[verdict].icon}
          width={16}
          sx={{ color: VERDICT[verdict].tone, flexShrink: 0, mt: "2px" }}
        />
        <Typography sx={T13}>
          <Box component="span" sx={{ fontWeight: 600 }}>
            {VERDICT[verdict].label}
          </Box>
          {verdict === "ready"
            ? ` — all ${cases(fit.total)} can be measured with ${agentName}.`
            : verdict === "none"
              ? ` — ${agentName} has none of this world's tools. Pair one below, or choose another world.`
              : ` — ${fit.unmeasured.length} of ${cases(fit.total)} can't be measured. They stay listed, never dropped.`}
        </Typography>
      </Stack>
      <Typography sx={{ ...T12, color: "text.secondary", mt: -0.5 }}>
        A case can only be graded if your agent has the tool it needs — pair
        each world tool with yours.
      </Typography>

      <Box
        sx={{ border: "1px solid", borderColor: "divider", borderRadius: 1 }}
      >
        <Stack direction="row" spacing={1.5} sx={{ px: 1.5, py: 0.75 }}>
          <Typography
            sx={{ ...T12, color: "text.tertiary", width: 190, flexShrink: 0 }}
          >
            World tool
          </Typography>
          <Typography sx={{ ...T12, color: "text.tertiary", flex: 1 }}>
            {`${agentName}'s tool`}
          </Typography>
        </Stack>
        {matched.length > 0 && (
          <ButtonBase
            onClick={() => setShowMatched((open) => !open)}
            aria-expanded={showMatched}
            sx={{
              width: 1,
              justifyContent: "flex-start",
              gap: 1,
              px: 1.5,
              py: 0.75,
              borderTop: "1px solid",
              borderColor: "divider",
              "&:hover": { bgcolor: "action.hover" },
            }}
          >
            <Iconify
              icon="solar:check-circle-linear"
              width={16}
              sx={{ color: "status.success.fg" }}
            />
            <Typography sx={{ ...T13, flex: 1, textAlign: "left" }}>
              {`${matched.length} matched`}
            </Typography>
            <Iconify
              icon={
                showMatched
                  ? "solar:alt-arrow-up-linear"
                  : "solar:alt-arrow-down-linear"
              }
              width={14}
              sx={{ color: "text.tertiary" }}
            />
          </ButtonBase>
        )}
        {[...(showMatched ? matched : []), ...missing].map((row) => (
          <PairRow
            key={row.world}
            row={row}
            agentName={agentName}
            agentTools={fit.agent_tools}
            onPair={onPair}
          />
        ))}
      </Box>

      {fit.platform_actions.length > 0 && (
        <Typography sx={{ ...T12, color: "text.secondary" }}>
          {`Runs for real on ${agentName}'s platform: ${fit.platform_actions
            .map((tool) =>
              tool.description
                ? `${tool.name} (${firstLower(tool.description).replace(/\.$/, "")})`
                : tool.name,
            )
            .join(" · ")}. The world can't redirect platform actions.`}
        </Typography>
      )}
      <Typography sx={{ ...T12, color: "text.secondary" }}>
        Live: only the starting state is checked.
      </Typography>

      {fit.unmeasured.length > 0 && (
        <Box
          sx={{
            border: "1px solid",
            borderColor: "divider",
            borderRadius: 1,
            px: 1.5,
            py: 1,
          }}
        >
          <Stack direction="row" alignItems="baseline" spacing={1.5}>
            <Typography sx={{ ...T12, fontWeight: 600, flex: 1 }}>
              {`${cases(fit.unmeasured.length)} can't be measured with ${agentName}`}
            </Typography>
            <TextLink onClick={() => setShowUnmeasured((open) => !open)}>
              {showUnmeasured ? "Hide" : "Show which"}
            </TextLink>
            {onOtherWorld && (
              <TextLink onClick={onOtherWorld}>Choose another world</TextLink>
            )}
          </Stack>
          {showUnmeasured &&
            fit.unmeasured.map((one) => (
              <Typography
                key={one.key}
                noWrap
                sx={{ ...T12, color: "text.secondary", mt: 0.5 }}
              >
                {readable(one.title)}
              </Typography>
            ))}
        </Box>
      )}

      <Stack
        direction="row"
        spacing={1}
        alignItems="flex-start"
        sx={{
          border: "1px solid",
          borderColor: "divider",
          borderRadius: 1,
          bgcolor: "action.hover",
          px: 1.5,
          py: 1,
        }}
      >
        <Iconify
          icon="solar:info-circle-linear"
          width={16}
          sx={{ color: "text.secondary", flexShrink: 0, mt: "1px" }}
        />
        <Typography sx={{ ...T12, color: "text.secondary" }}>
          {`${agentName} is tested live and calls its own tools; this world only grades it. Test a simulated copy for simulated tools.`}
        </Typography>
      </Stack>
    </Stack>
  );
}
WorldFitReport.propTypes = {
  agentName: PropTypes.string.isRequired,
  fit: PropTypes.object.isRequired,
  // (worldTool, agentTool or null): the person pairs a world tool with one of the agent's.
  onPair: PropTypes.func.isRequired,
  onOtherWorld: PropTypes.func,
};
