import PropTypes from "prop-types";

/*
  The suggested fix now sits in the Overview's Root cause panel
  (ClusterHeadlineCard: Suggested change · Wrong if), so this card shows
  nothing. Kept only until the page stops mounting it.
*/
export default function SuggestedFixCard() {
  return null;
}

SuggestedFixCard.propTypes = {
  status: PropTypes.shape({ key: PropTypes.string, label: PropTypes.string }),
  fix: PropTypes.shape({ change: PropTypes.string, wrongIf: PropTypes.string }),
};
