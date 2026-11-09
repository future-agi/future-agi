// The ids a generated script needs (dataset, reserved experiment and run
// ids, run label, input column, tries, keys), from a slot's `dataset` and
// `ids` props plus lane Ra's reserved ids (same query the drawer uses).
import { useReservedIds } from "src/sections/develop-detail/Experiment/reservedIds";

const letterOf = (label) => String(label ?? "").replace(/^#\d+/, "") || "A";

export default function useScriptIds(
  dataset,
  ids = {},
  { enabled = true } = {},
) {
  const datasetId = ids.datasetId ?? dataset?.id;
  const { data: reserved } = useReservedIds(datasetId, { enabled });
  return {
    datasetId,
    datasetName: dataset?.name,
    experimentId: reserved?.experimentId ?? null,
    runId: reserved?.runIds?.[letterOf(ids.runLabel)] ?? null,
    runLabel: ids.runLabel,
    inputColumn: dataset?.inputColumn ?? "input",
    tries: dataset?.trials,
    keys: dataset?.keys,
    token: ids.token,
  };
}
