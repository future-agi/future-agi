import React, { useMemo } from "react";
import PropTypes from "prop-types";
import { Stack } from "@mui/material";
import { runModelOf } from "src/sections/common/RunSources/rerunWhere";
import { mediaModelsOf } from "src/sections/common/RunSources/mediaChangesModel";
import {
  normalizeModelOption,
  pickerEligibility,
} from "src/components/custom-model-dropdown/common";
import NoUsableModelNotice from "src/components/custom-model-dropdown/NoUsableModelNotice";
import {
  noModelUsable,
  useFutureAgiCreditsRoute,
} from "src/components/custom-model-dropdown/noUsableModel";
import { NumberInput, ParamRow, SmallSelect } from "./changeParts";

const modelName = (m) => m?.model_name ?? m?.name ?? m?.id ?? null;

// The menu hint of a model the server says cannot run here.
const NOT_AVAILABLE_HINT = "Not available in this workspace";

/**
 * Model & params tab: model (from /api/models_list/), temperature, max
 * tokens; one row each, the parent's value greyed on the right.
 */
const ModelChange = ({ dock }) => {
  const { parent, draft, patch, canEdit, models } = dock;
  // options-truth L5 G2: an agent's model is the one its reply node calls.
  // L7b G3: a TTS / STT / image run offers its type's models, no params.
  const media = parent.config?.media?.type ? parent.config.media : null;
  const pm = {
    ...(parent.config?.model ?? {}),
    id: (media ? media.model : runModelOf(parent.config ?? {})) ?? undefined,
  };
  const params = pm.params ?? {};
  const cur = draft.model ?? {};
  const id = cur.id ?? pm.id ?? "";
  const temperature = cur.temperature ?? params.temperature ?? null;
  const maxTokens = cur.maxTokens ?? params.max_tokens ?? null;
  const mediaType = media?.type ?? null;
  // The catalogue is models_list, so each row carries the server's access
  // verdict. Every option stays selectable; the menu only marks it.
  const catalogue = useMemo(
    () => (models ?? []).filter(modelName).map(normalizeModelOption),
    [models],
  );
  const options = useMemo(() => {
    const byName = new Map(catalogue.map((row) => [modelName(row), row]));
    const names = mediaType
      ? mediaModelsOf(mediaType, pm.id)
      : [...new Set([pm.id, ...models.map(modelName)].filter(Boolean))];
    return names.map((n) => {
      const row = byName.get(n);
      const access = row ? pickerEligibility(row) : null;
      const hint = !access
        ? null
        : access.selectable
          ? access.payerLabel
          : NOT_AVAILABLE_HINT;
      return { value: n, label: n, ...(hint && { hint }) };
    });
  }, [catalogue, models, pm.id, mediaType]);
  const noneUsable = noModelUsable(catalogue);
  const creditsRoute = useFutureAgiCreditsRoute({ enabled: noneUsable });
  const set = (k, v) =>
    patch((d) => ({ model: { ...(d.model ?? {}), [k]: v } }));
  return (
    <Stack spacing={0.5}>
      <ParamRow label="Model" parent={pm.id ?? "—"} changed={id !== pm.id}>
        <SmallSelect
          label="Model"
          value={id}
          options={options}
          disabled={!canEdit}
          onChange={(v) => set("id", v)}
          sx={{ width: "100%", maxWidth: 260 }}
        />
      </ParamRow>
      {noneUsable ? (
        <NoUsableModelNotice creditsRoute={creditsRoute} sx={{ pl: "124px" }} />
      ) : null}
      {media ? null : (
        <>
          <ParamRow
            label="Temperature"
            parent={params.temperature ?? "not set"}
            changed={temperature !== (params.temperature ?? null)}
          >
            <NumberInput
              label="Temperature"
              value={temperature}
              step={0.1}
              max={2}
              disabled={!canEdit}
              onChange={(v) => set("temperature", v)}
            />
          </ParamRow>
          <ParamRow
            label="Max tokens"
            parent={params.max_tokens ?? "not set"}
            changed={maxTokens !== (params.max_tokens ?? null)}
          >
            <NumberInput
              label="Max tokens"
              value={maxTokens}
              step={100}
              disabled={!canEdit}
              onChange={(v) => set("maxTokens", v)}
            />
          </ParamRow>
        </>
      )}
    </Stack>
  );
};

ModelChange.propTypes = { dock: PropTypes.object.isRequired };

export default ModelChange;
