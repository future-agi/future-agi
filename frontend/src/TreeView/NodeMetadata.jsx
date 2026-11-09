import { formatExactMoney } from "src/sections/common/format/money";
import Iconify from "src/components/iconify";
import PropTypes from "prop-types";

const formatDuration = (ms) => {
  if (ms < 1000) return `${ms}ms`;
  const seconds = Math.floor(ms / 1000);
  const minutes = Math.floor(seconds / 60);
  const remainingSeconds = seconds % 60;
  if (minutes > 0) {
    return `${minutes}m ${remainingSeconds}s`;
  }
  return `${seconds}s`;
};

const formatCost = formatExactMoney;

/**
 * @param {{ duration: number, tokens: number, cost: number }} props
 */
export const NodeMetadata = ({ duration, tokens, cost }) => {
  return (
    <div className="tree-node-metadata">
      <span className="tree-node-metadata__item">
        <Iconify icon="tabler:clock" width={16} color="black.1000" />
        {formatDuration(duration)}
      </span>
      <span className="tree-node-metadata__item">
        <Iconify icon="tabler:hash" width={16} color="black.1000" />
        {tokens}
      </span>
      <span className="tree-node-metadata__item">
        <Iconify icon="tabler:coins" width={16} color="black.1000" />
        {formatCost(cost)}
      </span>
    </div>
  );
};

NodeMetadata.propTypes = {
  duration: PropTypes.number.isRequired,
  tokens: PropTypes.number.isRequired,
  cost: PropTypes.number.isRequired,
};
