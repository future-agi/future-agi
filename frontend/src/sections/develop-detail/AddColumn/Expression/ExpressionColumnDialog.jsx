import React, { useMemo, useState } from "react";
import PropTypes from "prop-types";
import {
  Dialog,
  DialogContent,
  DialogTitle,
  IconButton,
  Stack,
  Typography,
} from "@mui/material";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useParams } from "react-router";
import Iconify from "src/components/iconify";
import axios, { endpoints } from "src/utils/axios";
import { enqueueSnackbar } from "src/components/snackbar";
import { getDatasetQueryOptions } from "src/api/develop/develop-detail";
import ExpressionColumnForm from "src/sections/common/filters/ExpressionColumnForm";
import { gridColumnsFromConfig } from "src/sections/common/filters/gridDoorModel";
import { useDevelopDetailContext } from "../../Context/DevelopDetailContext";
import RerunScopeField from "../RerunScopeField";
import {
  errorText,
  expressionBody,
  expressionEditBody,
  initialOf,
  previewCells,
  useExpressionColumnStore,
} from "./expressionColumnModel";

/**
 * HO D7 (W5 A-H7) · Add column › Expression, and its edit (Configure): Name,
 * Expression (the filter language with autocomplete), `Test on 3 rows`
 * through the server's preview, `Create column`. Edit (`Save and re-run`)
 * posts the same path with the column and the scope (all / changed /
 * failed): the server renames it, re-runs the scope through the one
 * dynamic-column re-run and keeps the type its values compute to.
 */
function ExpressionColumnBody({ datasetId, editId, onClose }) {
  const { refreshGrid } = useDevelopDetailContext();
  const queryClient = useQueryClient();
  const [scope, setScope] = useState("changed");
  const { data: table } = useQuery({
    ...getDatasetQueryOptions(datasetId, 0, [], [], ""),
    staleTime: 0,
  });
  const columns = useMemo(
    () =>
      gridColumnsFromConfig(table?.data?.result?.column_config).filter(
        (c) => c.id !== editId,
      ),
    [table, editId],
  );
  const { data: config, isLoading: loadingConfig } = useQuery({
    queryKey: ["dynamic-column-config", editId],
    queryFn: () =>
      axios.get(endpoints.develop.addColumns.getColumnConfig(editId)),
    enabled: Boolean(editId),
  });
  const initial = initialOf(config?.data?.result?.metadata);
  const url = endpoints.develop.w5.addExpressionColumn(datasetId);

  const test = async (expr) => {
    try {
      const { data } = await axios.post(url, {
        ...expressionBody({ name: initial.name || "preview", expr }, columns),
        dry_run: true,
      });
      return {
        cells: previewCells(data?.result),
        type: data?.result?.data_type,
      };
    } catch (e) {
      throw { result: errorText(e, "Test failed.") };
    }
  };

  const { mutate: save, isPending } = useMutation({
    mutationFn: ({ name, expr }) =>
      axios.post(
        url,
        expressionEditBody({ name, expr }, columns, editId, scope),
      ),
    onSuccess: (res, { name }) => {
      const said = res?.data?.result;
      enqueueSnackbar(
        editId
          ? said?.message ?? `“${name}” updated`
          : `Column “${name}” added · ${said?.cells ?? 0} cells`,
        { variant: "success" },
      );
      // The next Configure reads the saved name and expression, not a cache.
      if (editId)
        queryClient.removeQueries({
          queryKey: ["dynamic-column-config", editId],
        });
      refreshGrid?.(null, true);
      onClose();
    },
    onError: (e) =>
      enqueueSnackbar(errorText(e, "Couldn't save the column"), {
        variant: "error",
      }),
  });

  if (editId && loadingConfig)
    return (
      <Typography typography="s2" color="text.secondary">
        Loading the column…
      </Typography>
    );
  return (
    <ExpressionColumnForm
      key={editId ?? "new"}
      columns={columns}
      test={test}
      onCreate={save}
      onCancel={onClose}
      creating={isPending}
      initialName={initial.name}
      initialExpr={initial.expr}
      submitLabel={editId ? "Save and re-run" : "Create column"}
    >
      {editId ? <RerunScopeField value={scope} onChange={setScope} /> : null}
    </ExpressionColumnForm>
  );
}

ExpressionColumnBody.propTypes = {
  datasetId: PropTypes.string.isRequired,
  editId: PropTypes.string,
  onClose: PropTypes.func.isRequired,
};

export default function ExpressionColumnDialog() {
  const open = useExpressionColumnStore((s) => s.open);
  const close = useExpressionColumnStore((s) => s.close);
  const { dataset } = useParams();
  // Add column names the dataset; Configure (a column menu) the column only.
  const datasetId = open?.datasetId ?? dataset;
  if (!open || !datasetId) return null;
  return (
    <Dialog
      open
      onClose={close}
      fullWidth
      maxWidth={false}
      PaperProps={{ sx: { width: 640 } }}
      data-testid="expression-column-dialog"
    >
      <DialogTitle>
        <Stack
          direction="row"
          alignItems="center"
          justifyContent="space-between"
        >
          {open.editId ? "Edit expression column" : "Expression column"}
          <IconButton size="small" onClick={close} aria-label="Close">
            <Iconify icon="mingcute:close-line" width={18} />
          </IconButton>
        </Stack>
      </DialogTitle>
      {/* The Name field's floating label sits inside the content's top
          edge (the title's next sibling has no top padding otherwise). */}
      <DialogContent sx={{ pt: "8px !important" }}>
        <ExpressionColumnBody
          datasetId={datasetId}
          editId={open.editId ?? null}
          onClose={close}
        />
      </DialogContent>
    </Dialog>
  );
}
