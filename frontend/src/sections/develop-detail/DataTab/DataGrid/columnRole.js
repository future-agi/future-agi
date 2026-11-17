import { enqueueSnackbar } from "notistack";
import axios, { endpoints } from "src/utils/axios";
import { ROLE_LABEL } from "./dataGridView";

// G3 "Set role ▸": persists `column_config[id].role` through
// edit_dataset_behavior and patches the cached column configs so the header
// tag changes at once.

const patchColumns = (columns, columnId, role) =>
  Array.isArray(columns)
    ? columns.map((c) => (c?.id === columnId ? { ...c, role } : c))
    : columns;

const patchTableResponse = (old, columnId, role) => {
  const result = old?.data?.result;
  if (!result?.column_config) return old;
  return {
    ...old,
    data: {
      ...old.data,
      result: {
        ...result,
        column_config: patchColumns(result.column_config, columnId, role),
      },
    },
  };
};

export async function saveColumnRole({ queryClient, datasetId, column, role }) {
  const columnId = column?.id;
  if (!datasetId || !columnId) return;
  const next = role ?? null;
  queryClient.setQueriesData(
    { queryKey: ["dataset-detail", datasetId] },
    (old) => patchTableResponse(old, columnId, next),
  );
  queryClient.setQueryData(["dataset-detail-average", datasetId], (old) =>
    patchTableResponse(old, columnId, next),
  );
  try {
    await axios.put(endpoints.develop.updateDataset(datasetId), {
      column_config: { [columnId]: { role: next ?? "none" } },
    });
    enqueueSnackbar(
      next
        ? `${column.name} is now ${ROLE_LABEL[next]}`
        : `${column.name} has no role`,
      { variant: "success" },
    );
  } catch {
    enqueueSnackbar("Couldn't change the column role.", { variant: "error" });
  } finally {
    // Header stats depend on the role (e.g. `4 empty` on Expected columns).
    queryClient.invalidateQueries({
      queryKey: ["dataset-detail-average", datasetId],
    });
    queryClient.invalidateQueries({ queryKey: ["dataset-detail", datasetId] });
  }
}
