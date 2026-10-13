import PropTypes from "prop-types";
import { Box, Chip, Stack, Typography } from "@mui/material";

import {
  SCENARIO_ORIGIN_LABELS,
  displayText,
  readable,
  shortDigest,
} from "src/pages/dashboard/harness/harnessShared";
import SectionCard from "../../components/SectionCard";
import {
  alignmentRows,
  bindingModeLabel,
  proofSummary,
} from "./composition.helpers";

// A composition is a released environment bound to the customer's agent
// (environment-releases.md §7.3, §7.6). This tab shows how the agent's tools reach the
// environment, which scenarios cannot be scored, the policy the suite was proved under and
// whether the binding's proof held.

const ADMISSION_COLORS = {
  routed: "success",
  replaced: "info",
  kept: "default",
  disabled: "warning",
};

const PROOF_COLORS = {
  proved: "success",
  failed: "error",
  unscorable: "warning",
};

export function BindingProofChip({ composition }) {
  const mode = composition?.binding?.mode;
  const proof = composition?.binding_proof;
  if (mode !== "tool_routed") {
    return <Chip size="small" variant="outlined" label="No proof needed" />;
  }
  if (!proof)
    return <Chip size="small" color="warning" label="Proof missing" />;
  return (
    <Chip
      size="small"
      color={proof.passed ? "success" : "error"}
      label={proof.passed ? "Binding proved" : "Binding proof failed"}
    />
  );
}

BindingProofChip.propTypes = { composition: PropTypes.object };

// The labels a run is pinned to: which agent version, suite and binding. Shown where a run is
// started, so the reader knows what the next run will compare against.
export function CompositionLabels({ composition, dense = false }) {
  if (!composition) return null;
  const agent = composition.agent || {};
  const suite = composition.suite || {};
  const binding = composition.binding || {};
  const rows = [
    [
      "Agent",
      [agent.name, agent.version_name].filter(Boolean).join(" · ") ||
        agent.agent_version_id ||
        "\u2014",
    ],
    ["Suite", `${suite.name || "\u2014"} · ${shortDigest(suite.digest)}`],
    [
      "Binding",
      `${bindingModeLabel(binding.mode)} · ${shortDigest(binding.digest)}`,
    ],
  ];
  return (
    <Stack spacing={dense ? 0.25 : 0.5} aria-label="Composition labels">
      {rows.map(([label, value]) => (
        <Stack key={label} direction="row" spacing={1} alignItems="baseline">
          <Typography
            sx={{ typography: "s3", color: "text.subtitle", minWidth: 52 }}
          >
            {label}
          </Typography>
          <Typography
            sx={{ typography: "s3", fontWeight: 600, fontFamily: "monospace" }}
            noWrap
          >
            {value}
          </Typography>
        </Stack>
      ))}
    </Stack>
  );
}

CompositionLabels.propTypes = {
  composition: PropTypes.object,
  dense: PropTypes.bool,
};

function Cell({ children, sx }) {
  return (
    <Box
      sx={{
        py: 1,
        px: 1.5,
        borderTop: "1px solid",
        borderColor: "divider",
        ...sx,
      }}
    >
      {children}
    </Box>
  );
}

Cell.propTypes = { children: PropTypes.node, sx: PropTypes.object };

function AlignmentTable({ composition }) {
  const rows = alignmentRows(composition);
  if (!rows.length) {
    return (
      <Typography sx={{ typography: "s2", color: "text.subtitle", p: 2 }}>
        {composition?.binding?.mode === "canonical"
          ? "The agent calls the environment's tools by their own names."
          : "No agent tools reach this environment."}
      </Typography>
    );
  }
  return (
    <Box
      role="table"
      aria-label="Tool alignment"
      sx={{
        display: "grid",
        gridTemplateColumns: "minmax(140px, 1fr) 110px minmax(200px, 2fr)",
      }}
    >
      {["Agent tool", "Admission", "Environment"].map((label) => (
        <Box key={label} sx={{ py: 1, px: 1.5 }}>
          <Typography
            sx={{ typography: "s3", fontWeight: 700, color: "text.subtitle" }}
          >
            {label}
          </Typography>
        </Box>
      ))}
      {rows.map((row) => (
        <Box key={row.name} role="row" sx={{ display: "contents" }}>
          <Cell>
            <Typography sx={{ typography: "s2", fontFamily: "monospace" }}>
              {row.name}
            </Typography>
          </Cell>
          <Cell>
            {row.admission?.action ? (
              <Chip
                size="small"
                variant="outlined"
                color={ADMISSION_COLORS[row.admission.action] || "default"}
                label={readable(row.admission.action)}
                title={row.admission.reason || ""}
              />
            ) : (
              <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
                {"\u2014"}
              </Typography>
            )}
          </Cell>
          <Cell>
            {row.kind === "mapped" && (
              <Typography sx={{ typography: "s2", fontFamily: "monospace" }}>
                {row.steps.join(" \u2192 ") || "\u2014"}
              </Typography>
            )}
            {row.kind === "unsupported" && (
              <Typography sx={{ typography: "s2", color: "text.subtitle" }}>
                Unsupported: {displayText(row.note)}
              </Typography>
            )}
            {row.kind === "extension" && (
              <Typography sx={{ typography: "s2", color: "text.subtitle" }}>
                Extension candidate: {displayText(row.note)}
              </Typography>
            )}
            {!row.kind && (
              <Typography sx={{ typography: "s2", color: "text.subtitle" }}>
                {row.admission?.reason
                  ? displayText(row.admission.reason)
                  : composition?.binding?.mode === "canonical"
                    ? "Same-named environment tool"
                    : "Not routed to the environment"}
              </Typography>
            )}
          </Cell>
        </Box>
      ))}
    </Box>
  );
}

AlignmentTable.propTypes = { composition: PropTypes.object };

function PolicyProfile({ profile }) {
  const parameters = Object.entries(profile?.parameters || {});
  const rules = profile?.scored_agent_rules || [];
  if (!parameters.length && !rules.length) {
    return (
      <Typography sx={{ typography: "s2", color: "text.subtitle", p: 2 }}>
        The suite uses the environment&apos;s default policy.
      </Typography>
    );
  }
  return (
    <Stack spacing={1} sx={{ p: 2 }}>
      {parameters.map(([name, value]) => (
        <Stack key={name} direction="row" spacing={1}>
          <Typography
            sx={{ typography: "s2", fontFamily: "monospace", minWidth: 180 }}
          >
            {name}
          </Typography>
          <Typography sx={{ typography: "s2", fontWeight: 600 }}>
            {displayText(value)}
          </Typography>
        </Stack>
      ))}
      {rules.length > 0 && (
        <Typography sx={{ typography: "s2" }}>
          Scored agent rules: {rules.map(displayText).join(", ")}
        </Typography>
      )}
    </Stack>
  );
}

PolicyProfile.propTypes = { profile: PropTypes.object };

function ScenarioProof({ composition }) {
  const proof = composition?.binding_proof?.scenarios || {};
  const unscorable = new Map(
    (composition?.alignment?.unscorable || []).map((item) => [
      item.scenario_key,
      item.reason,
    ]),
  );
  const scenarios = composition?.suite?.scenarios || [];
  if (!scenarios.length && !unscorable.size) return null;
  const keys = scenarios.length
    ? scenarios.map((item) => item.key)
    : [...unscorable.keys()];
  const originOf = new Map(scenarios.map((item) => [item.key, item]));
  return (
    <Stack
      divider={<Box sx={{ borderTop: "1px solid", borderColor: "divider" }} />}
    >
      {keys.map((key) => {
        const status =
          proof[key]?.status || (unscorable.has(key) ? "unscorable" : null);
        const reason = proof[key]?.reason || unscorable.get(key);
        const origin = originOf.get(key)?.origin;
        return (
          <Stack
            key={key}
            direction="row"
            spacing={1}
            alignItems="center"
            sx={{ px: 2, py: 1 }}
          >
            <Typography
              sx={{ typography: "s2", fontFamily: "monospace", flex: 1 }}
              noWrap
            >
              {key}
            </Typography>
            {origin && (
              <Chip
                size="small"
                variant="outlined"
                label={SCENARIO_ORIGIN_LABELS[origin] || origin}
              />
            )}
            {status && (
              <Chip
                size="small"
                color={PROOF_COLORS[status] || "default"}
                label={readable(status)}
                title={reason ? displayText(reason) : ""}
              />
            )}
          </Stack>
        );
      })}
    </Stack>
  );
}

ScenarioProof.propTypes = { composition: PropTypes.object };

export default function CompositionPanel({ composition }) {
  const binding = composition.binding || {};
  const suite = composition.suite || {};
  const chain = (composition.environment || [])
    .map((item) => item?.name)
    .filter(Boolean);
  const counts = proofSummary(composition.binding_proof);
  const unscorable = composition.alignment?.unscorable || [];
  return (
    <Stack spacing={2} sx={{ p: 2 }}>
      <SectionCard
        title={
          <Stack direction="row" spacing={1} alignItems="center">
            <span>Binding</span>
            <Chip
              size="small"
              color="primary"
              variant="outlined"
              label={bindingModeLabel(binding.mode)}
            />
            <BindingProofChip composition={composition} />
          </Stack>
        }
        subtitle={`Environment ${chain.join(" + ") || "\u2014"} · suite ${suite.name || "\u2014"}${
          suite.parent_digest
            ? ` (derived from ${shortDigest(suite.parent_digest)})`
            : ""
        }`}
      >
        <Box sx={{ p: 2 }}>
          <CompositionLabels composition={composition} />
          {binding.mode === "tool_routed" && composition.binding_proof && (
            <Typography
              sx={{ typography: "s3", color: "text.subtitle", mt: 1 }}
            >
              {counts.proved} proved · {counts.failed} failed ·{" "}
              {counts.unscorable} unscorable
            </Typography>
          )}
        </Box>
      </SectionCard>

      <SectionCard
        title="Tool alignment"
        subtitle="How each tool your agent can call reaches this environment"
      >
        <AlignmentTable composition={composition} />
      </SectionCard>

      {unscorable.length > 0 && (
        <SectionCard
          title={`Unscorable scenarios (${unscorable.length})`}
          subtitle="Their reference solution needs an environment tool no agent tool reaches; runs leave them out"
        >
          <Stack spacing={0.75} sx={{ p: 2 }}>
            {unscorable.map((item) => (
              <Typography key={item.scenario_key} sx={{ typography: "s2" }}>
                <Box component="span" sx={{ fontFamily: "monospace" }}>
                  {item.scenario_key}
                </Box>
                {item.reason ? ` \u2014 ${displayText(item.reason)}` : ""}
              </Typography>
            ))}
          </Stack>
        </SectionCard>
      )}

      <SectionCard
        title="Policy profile"
        subtitle="The answers the suite was proved under"
      >
        <PolicyProfile profile={suite.policy_profile} />
      </SectionCard>

      <SectionCard
        title="Scenarios"
        subtitle="Origin and binding proof per scenario"
      >
        <ScenarioProof composition={composition} />
      </SectionCard>
    </Stack>
  );
}

CompositionPanel.propTypes = {
  composition: PropTypes.shape({
    environment: PropTypes.array,
    suite: PropTypes.object,
    binding: PropTypes.object,
    alignment: PropTypes.object,
    binding_proof: PropTypes.object,
    agent: PropTypes.object,
  }).isRequired,
};
