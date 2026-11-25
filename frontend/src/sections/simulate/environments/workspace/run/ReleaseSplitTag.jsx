import PropTypes from "prop-types";
import { VersionBadge } from "../EnvVersionPin";
import { releaseSplitLabel } from "../workspace.constants";

// Names the release a run's versions belong to and the split it ran.
export default function ReleaseSplitTag({ release, split }) {
  const label = releaseSplitLabel(release, split);
  return label ? <VersionBadge label={label} /> : null;
}

ReleaseSplitTag.propTypes = {
  release: PropTypes.shape({ label: PropTypes.string }),
  split: PropTypes.oneOf(["train", "test"]),
};
