import PropTypes from "prop-types";
import { useEffect, useState } from "react";
import { enqueueSnackbar } from "notistack";
import TagPicker from "src/components/tag-picker";
import {
  useEnvironmentTags,
  useSetScenarioTags,
} from "src/api/simulate-environments/scenarioTags";
import { SCENARIO_TAGS_COPY as C } from "./scenarios.constants";

const NO_TAGS = [];

export default function ScenarioTagCell({
  envId,
  scenarioKey,
  tags = NO_TAGS,
  locked,
  lockTooltip,
}) {
  const [shown, setShown] = useState(tags);
  const [opened, setOpened] = useState(false);
  const known = useEnvironmentTags(envId, { enabled: opened });
  const save = useSetScenarioTags(envId);

  useEffect(() => {
    if (!save.isPending) setShown(tags);
  }, [tags, save.isPending]);

  const onChange = (next) => {
    const before = shown;
    setShown(next);
    save.mutate(
      { scenarioKey, tags: next },
      {
        onError: () => {
          setShown(before);
          enqueueSnackbar(C.failed, { variant: "error" });
        },
      },
    );
  };

  return (
    <TagPicker
      tags={shown}
      knownTags={known.data || []}
      onChange={onChange}
      onOpen={() => setOpened(true)}
      knownError={known.isError}
      knownFetching={known.isFetching}
      onRetryKnown={() => known.refetch()}
      disabled={locked || !scenarioKey}
      disabledReason={locked ? lockTooltip : ""}
    />
  );
}

ScenarioTagCell.propTypes = {
  envId: PropTypes.string.isRequired,
  scenarioKey: PropTypes.string,
  tags: PropTypes.arrayOf(PropTypes.string),
  locked: PropTypes.bool,
  lockTooltip: PropTypes.string,
};
