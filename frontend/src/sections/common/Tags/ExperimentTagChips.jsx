// W5-3 (C-H1): an experiment's tag chips, read from the registry by id, so a
// rename or recolour follows on every row at once.
import React from "react";
import PropTypes from "prop-types";
import TagChips from "./TagChips";
import { tagsByIds } from "./tagModel";
import { useTags } from "./useTags";

export default function ExperimentTagChips({ experimentId, max = 3 }) {
  const { data } = useTags();
  const tags = tagsByIds(data?.tags, data?.experiments?.[experimentId]);
  if (!tags.length) return null;
  return <TagChips tags={tags} max={max} />;
}

ExperimentTagChips.propTypes = {
  experimentId: PropTypes.string,
  max: PropTypes.number,
};
