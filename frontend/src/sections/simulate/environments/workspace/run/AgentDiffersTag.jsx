import PropTypes from "prop-types";
import { VersionBadge } from "../EnvVersionPin";
import { agentDiffersTag } from "../workspace.constants";

// Marks a run whose agent is not the one its environment version was built with.
export default function AgentDiffersTag({ agentVersion, environmentVersion }) {
  return (
    <VersionBadge
      label={agentDiffersTag(agentVersion?.label, environmentVersion?.label)}
      role="warning"
    />
  );
}

AgentDiffersTag.propTypes = {
  agentVersion: PropTypes.shape({ label: PropTypes.string }),
  environmentVersion: PropTypes.shape({ label: PropTypes.string }),
};
