import { useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { enqueueSnackbar } from "notistack";
import axios from "src/utils/axios";
import { apiPath } from "src/api/contracts/api-surface";
import { getRequestErrorMessage } from "src/utils/errorUtils";
import { notifyProductionImport } from "./productionImportEvents";

async function rowCount(id) {
  const response = await axios.get(
    apiPath("/model-hub/develops/{dataset_id}/get-dataset-table/", {
      dataset_id: id,
    }),
    { params: { current_page_index: 0, page_size: 1 }, timeout: 10_000 },
  );
  const count = response.data.result.metadata.total_rows;
  if (!Number.isInteger(count) || count < 0)
    throw new Error("Dataset progress is unavailable.");
  return count;
}

export default function useProductionImport({
  projectId,
  selected,
  mapping,
  columns,
  target,
  name,
  onClose,
}) {
  const navigate = useNavigate();
  const client = useQueryClient();
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const [pending, setPending] = useState(null);
  const running = useRef(false);
  const submit = async () => {
    if (running.current) return;
    running.current = true;
    setError(null);
    setBusy(true);
    let accepted = pending;
    try {
      if (!accepted) {
        const isNew = target === "__new__";
        const baseline = isNew ? 0 : await rowCount(target);
        const mapped = mapping.map((row) => ({
          ...row,
          col_name: row.col_name.trim(),
        }));
        const existing = new Set(columns.map((column) => column.name));
        const payload = {
          project: projectId,
          span_ids: selected.map((item) => item.span_id),
          ...(isNew
            ? { new_dataset_name: name.trim(), mapping_config: mapped }
            : {
                dataset_id: target,
                mapping_config: mapped.filter((row) =>
                  existing.has(row.col_name),
                ),
                new_mapping_config: mapped.filter(
                  (row) => !existing.has(row.col_name),
                ),
              }),
        };
        const endpoint = isNew
          ? apiPath("/tracer/dataset/add_to_new_dataset/")
          : apiPath("/tracer/dataset/add_to_existing_dataset/");
        const result = (await axios.post(endpoint, payload)).data.result;
        accepted = {
          id: result.dataset_id,
          minimum: baseline + selected.length,
        };
        setPending(accepted);
      }
      const deadline = Date.now() + 60_000;
      while ((await rowCount(accepted.id)) < accepted.minimum) {
        if (Date.now() >= deadline)
          throw new Error("Import is still processing.");
        await new Promise((resolve) => setTimeout(resolve, 1000));
      }
      client.invalidateQueries({ queryKey: ["develop"] });
      client.invalidateQueries({
        queryKey: ["get-dataset-columns", accepted.id],
      });
      notifyProductionImport(accepted.id);
      onClose();
      navigate(`/dashboard/develop/${accepted.id}?tab=data`);
      enqueueSnackbar("Imported rows are ready in the dataset.", {
        variant: "success",
      });
    } catch (e) {
      setError(
        accepted
          ? "Import accepted, but completion is not confirmed yet. Check import again; the rows will not be submitted twice."
          : getRequestErrorMessage(e, "Could not import the selected data."),
      );
    } finally {
      setBusy(false);
      running.current = false;
    }
  };
  return { submit, busy, pending, error, setError };
}
