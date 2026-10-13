import { useEffect, useState } from "react";

function modelId(value) {
  return (
    typeof value === "string" &&
    value.length > 0 &&
    value.trim() === value &&
    new TextEncoder().encode(value).length <= 256 &&
    !Array.from(value).some(
      (character) =>
        character.charCodeAt(0) < 32 || character.charCodeAt(0) === 127,
    )
  );
}

function validRow(model) {
  if (
    !model ||
    !modelId(model.modelId) ||
    typeof model.selectable !== "boolean"
  )
    return false;
  for (const key of ["provider", "reason", "refusal"]) {
    if (
      model[key] !== undefined &&
      (typeof model[key] !== "string" || model[key].length > 4000)
    )
      return false;
  }
  if (
    model.billing !== undefined &&
    !["omega_served", "included", "byo"].includes(model.billing)
  )
    return false;
  if (model.rate !== undefined) {
    if (
      !model.rate ||
      typeof model.rate !== "object" ||
      Array.isArray(model.rate)
    )
      return false;
    for (const key of [
      "inputPer1MUsd",
      "outputPer1MUsd",
      "cachedInputPer1MUsd",
      "cacheWritePer1MUsd",
    ]) {
      if (
        typeof model.rate[key] !== "number" ||
        !Number.isFinite(model.rate[key]) ||
        model.rate[key] < 0
      )
        return false;
    }
    if (
      typeof model.rate.rateCardVersion !== "string" ||
      !model.rate.rateCardVersion ||
      model.rate.rateCardVersion.length > 256
    )
      return false;
    if (
      model.rate.caveat !== undefined &&
      (typeof model.rate.caveat !== "string" || model.rate.caveat.length > 4000)
    )
      return false;
  }
  return true;
}

function catalog(response) {
  if (
    response?.available !== true ||
    response.schemaVersion !== "omega-workforce/v1" ||
    !modelId(response.defaultModelId) ||
    !Array.isArray(response.models) ||
    response.models.length > 1000
  )
    return null;
  const ids = new Set();
  for (const model of response.models) {
    if (!validRow(model) || ids.has(model.modelId)) return null;
    ids.add(model.modelId);
  }
  return response;
}

// A catalog from a previous client/workspace must never authorize a selection.
export default function useAgentModels({ client, open, allowed, modelId }) {
  const [request, setRequest] = useState(null);
  const [attempt, setAttempt] = useState(0);
  const canRead =
    open && allowed && typeof client?.listWorkforceModels === "function";
  useEffect(() => {
    if (!canRead) {
      setRequest(null);
      return undefined;
    }
    let canceled = false;
    const identity = { client, attempt };
    setRequest({ ...identity, loading: true });
    Promise.resolve()
      .then(() => client.listWorkforceModels())
      .then((response) => {
        if (canceled) return;
        const result = catalog(response);
        setRequest({
          ...identity,
          loading: false,
          result,
          error: result
            ? ""
            : (typeof response?.reason === "string" && response.reason) ||
              "The model catalog could not be verified.",
        });
      })
      .catch(() => {
        if (!canceled)
          setRequest({
            ...identity,
            loading: false,
            error: "Couldn't read the model catalog. Your draft is kept here.",
          });
      });
    return () => {
      canceled = true;
    };
  }, [canRead, client, attempt]);
  const current =
    canRead && request?.client === client && request.attempt === attempt
      ? request
      : null;
  const models = current?.result?.models || [];
  const selected = models.find((entry) => entry.modelId === modelId);
  return {
    models,
    selected,
    defaultModelId: current?.result?.defaultModelId,
    available: Boolean(current?.result),
    loading: canRead && (!current || current.loading),
    valid: !modelId || selected?.selectable === true,
    error: current?.error || "",
    reload: () => setAttempt((value) => value + 1),
  };
}
