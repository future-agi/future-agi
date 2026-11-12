import React from "react";
import PropTypes from "prop-types";
import { useModelListEntry } from "src/sections/develop-detail/Common/modelAccess";
import NoUsableModelNotice from "./NoUsableModelNotice";
import { isRowUsable, useFutureAgiCreditsRoute } from "./noUsableModel";

// Under a model field whose menu lets any model be picked (Agents): when the
// server says the picked model cannot run here, the same sentence as the
// menus, its credits link going to this model's Try it tab. Nothing shows
// while the verdict is loading, unknown or usable.
const PickedModelNotice = ({ modelName, sx }) => {
  const name = typeof modelName === "string" ? modelName.trim() : "";
  const { data: entry } = useModelListEntry(name);
  const unusable = Boolean(name && entry) && !isRowUsable(entry);
  const creditsRoute = useFutureAgiCreditsRoute({ enabled: unusable });
  if (!unusable) return null;
  return (
    <NoUsableModelNotice
      creditsRoute={creditsRoute}
      model={{
        provider: entry?.providers ?? entry?.provider ?? "",
        model: entry?.model_name ?? name,
      }}
      data-testid="picked-model-notice"
      sx={{ mt: 0.5, ...sx }}
    />
  );
};

PickedModelNotice.propTypes = {
  modelName: PropTypes.string,
  sx: PropTypes.object,
};

export default PickedModelNotice;
