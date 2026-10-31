import PropTypes from "prop-types";
import { Box } from "@mui/material";
import {
  CONNECTOR_LABEL,
  MODE_LABEL,
  OVERVIEW_COPY,
  sourceLabel,
} from "../overview/overview.constants";
import { Fact } from "../overview/OverviewPrimitives";
import SettingsCard from "./SettingsCard";
import { humanize } from "./settings.constants";

const memoryWords = (mb) =>
  mb % 1024 === 0 ? `${mb / 1024} GB memory` : `${mb} MB memory`;

const resourcesOf = (runtime) => {
  const parts = [
    runtime?.cpu_units != null && `${runtime.cpu_units} CPU`,
    runtime?.memory_mb != null && memoryWords(runtime.memory_mb),
  ].filter(Boolean);
  return parts.length ? parts.join(" · ") : null;
};

const artifactsOf = (artifacts) => {
  if (!artifacts?.level) return null;
  const kept =
    artifacts.retention_days != null
      ? ` · kept ${artifacts.retention_days} day${artifacts.retention_days === 1 ? "" : "s"}`
      : "";
  return `${humanize(artifacts.level)}${kept}`;
};

// How the environment was built: where the agent was read from, how it is
// connected and run (moved here from the Overview), and the runtime and
// artifacts the build recorded. A field the API sends empty reads "-".
export default function BuildDetailsCard({ env, connector }) {
  const runtime = env?.runtime;
  const facts = [
    [
      OVERVIEW_COPY.facts.connector,
      connector ? CONNECTOR_LABEL[connector] || connector : null,
    ],
    [OVERVIEW_COPY.facts.source, sourceLabel(env?.source)],
    [
      OVERVIEW_COPY.facts.mode,
      env?.agentMode ? MODE_LABEL[env.agentMode] || env.agentMode : null,
    ],
    ["Isolation", runtime?.isolation ? humanize(runtime.isolation) : null],
    ["Resources", resourcesOf(runtime)],
    [
      "Network",
      runtime?.network_policy ? humanize(runtime.network_policy) : null,
    ],
    ["Artifacts", artifactsOf(env?.artifacts)],
  ];
  return (
    <SettingsCard label="Build details" title="Build details">
      <Box
        sx={{
          p: 2.5,
          display: "grid",
          gridTemplateColumns: "repeat(auto-fill, minmax(160px, 1fr))",
          columnGap: 4,
          rowGap: 1.5,
        }}
      >
        {facts.map(([label, value]) => (
          <Fact key={label} label={label} value={value || "-"} />
        ))}
      </Box>
    </SettingsCard>
  );
}
BuildDetailsCard.propTypes = {
  env: PropTypes.object,
  connector: PropTypes.string,
};
