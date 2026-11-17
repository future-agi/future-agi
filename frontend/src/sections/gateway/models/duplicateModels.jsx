import React from "react";
import PropTypes from "prop-types";
import Alert from "@mui/material/Alert";
import AlertTitle from "@mui/material/AlertTitle";
import Box from "@mui/material/Box";
import Typography from "@mui/material/Typography";

// A model id that two or more of the org's providers list. The gateway sends
// a plain id to the provider whose id sorts first in byte order, unless the
// org's model map names one of them; "<provider>/<model>" reaches the
// provider it names (agentcc-gateway server/handlers.go orgRouteFor and
// orgModelMatches; org_route_order_test.go, org_route_prefix_test.go).

const encoder = new TextEncoder();

/** Byte order of UTF-8 ids, as the gateway compares provider ids. */
export function byteOrder(a, b) {
  const x = encoder.encode(String(a));
  const y = encoder.encode(String(b));
  for (let i = 0; i < Math.min(x.length, y.length); i += 1) {
    if (x[i] !== y[i]) return x[i] - y[i];
  }
  return x.length - y.length;
}

const listOf = (value) => (Array.isArray(value) ? value : []);

/**
 * The ids that two or more providers the gateway has list (a provider not
 * pushed to the gateway is left out), sorted by id: { model, providerIds (in
 * byte order), reaches, pinnedBy: "order" | "model_map" | "model_map_other",
 * mapEntry }. `reaches` is null when a model map entry names a provider that
 * does not list the id: that case is stated, not resolved.
 */
export function duplicateModelIds(providers, modelMap = {}) {
  const listers = new Map();
  listOf(providers).forEach((p) => {
    const id = String(p?.id || p?.name || "");
    if (!id || p?.pushed === false) return;
    new Set(listOf(p?.models)).forEach((model) => {
      if (typeof model !== "string" || !model) return;
      if (!listers.has(model)) listers.set(model, []);
      listers.get(model).push(id);
    });
  });
  const map = modelMap && typeof modelMap === "object" ? modelMap : {};
  return [...listers]
    .filter(([, ids]) => ids.length > 1)
    .map(([model, ids]) => {
      const providerIds = [...ids].sort(byteOrder);
      const mapped = typeof map[model] === "string" ? map[model] : null;
      if (mapped && providerIds.includes(mapped)) {
        return {
          model,
          providerIds,
          reaches: mapped,
          pinnedBy: "model_map",
          mapEntry: mapped,
        };
      }
      if (mapped) {
        return {
          model,
          providerIds,
          reaches: null,
          pinnedBy: "model_map_other",
          mapEntry: mapped,
        };
      }
      return {
        model,
        providerIds,
        reaches: providerIds[0],
        pinnedBy: "order",
        mapEntry: null,
      };
    })
    .sort((a, b) => byteOrder(a.model, b.model));
}

const andList = (items) =>
  items.length < 2
    ? items.join("")
    : `${items.slice(0, -1).join(", ")} and ${items.at(-1)}`;

function lineFor(entry) {
  const listed = `"${entry.model}" is listed by ${andList(entry.providerIds)}.`;
  if (entry.pinnedBy === "model_map")
    return `${listed} Your Model Map entry sends a plain "${entry.model}" to ${entry.reaches}.`;
  if (entry.pinnedBy === "model_map_other")
    return `${listed} Your Model Map has an entry for "${entry.model}" naming ${entry.mapEntry}.`;
  return `${listed} A plain "${entry.model}" reaches ${entry.reaches}, the provider whose id sorts first.`;
}

/**
 * Says which model ids two or more providers list, where a plain id goes and
 * how to pick a provider. Nothing when no id is listed twice.
 */
export default function DuplicateModelNotice({ providers, modelMap }) {
  const entries = duplicateModelIds(providers, modelMap);
  if (!entries.length) return null;
  const example = entries[0];
  const exampleId = `${example.providerIds.at(-1)}/${example.model}`;
  return (
    <Alert severity="info" data-testid="duplicate-model-notice">
      <AlertTitle>Models listed by more than one provider</AlertTitle>
      <Box component="ul" sx={{ m: 0, pl: 2.5 }}>
        {entries.map((entry) => (
          <Typography component="li" typography="s2" key={entry.model}>
            {lineFor(entry)}
          </Typography>
        ))}
      </Box>
      <Typography typography="s2" sx={{ mt: 1 }}>
        {`To pick a provider, send "<provider>/<model>" (for example "${exampleId}"), or add an entry under Gateway settings › Org config › Model Map.`}
      </Typography>
    </Alert>
  );
}

DuplicateModelNotice.propTypes = {
  // [{ id, models, pushed }]: the org's providers and the models each lists.
  providers: PropTypes.array,
  // The org config's model_map: { model: provider id }.
  modelMap: PropTypes.object,
};
