// What each model reads (options-truth L3 G7 W6): `capabilities.inputs` of
// the model list, by model name. Fetched once per page load and shared by
// the Data step's mapping tables and Review, which warn when a run's model
// is sent a column of another modality. A list without capabilities (or a
// failed request) gives no warnings rather than invented ones.
import { useEffect, useState } from "react";
import axios, { endpoints } from "src/utils/axios";

/** `{[modelName]: ["text", "image", …]}` from a model list page (pure). */
export function inputsByModel(results = []) {
  return Object.fromEntries(
    (results ?? [])
      .filter((m) => m?.capabilities?.inputs?.length)
      .map((m) => [m.model_name ?? m.name, m.capabilities.inputs]),
  );
}

let request = null;

/** The shared request (retried on the next call after a failure). */
export function loadModelInputs() {
  request ??= axios
    .get(endpoints.develop.modelList, { params: { page: 1 } })
    .then((res) => inputsByModel(res?.data?.results))
    .catch(() => {
      request = null;
      return {};
    });
  return request;
}

/** Hook: the inputs map once loaded (null before), only when `needed`. */
export function useModelInputs(needed) {
  const [inputs, setInputs] = useState(null);
  useEffect(() => {
    if (!needed) return undefined;
    let live = true;
    loadModelInputs().then((byName) => live && setInputs(byName));
    return () => {
      live = false;
    };
  }, [needed]);
  return inputs;
}
