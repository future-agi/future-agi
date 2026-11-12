// the interface contract-8b (lane w3-rows-in A10): the dataset header's notice when a
// knowledge base document its goldens cite has changed:
// `Refund policy changed · 3 goldens cite changed passages · Regenerate`.
// Regenerate asks IM1 (generate `mode: "kb_refresh"`) for the new answer,
// context and citation of those goldens and puts them in the change set;
// Save version keeps them. Renders nothing when no golden is stale.
import React, { useMemo } from "react";
import PropTypes from "prop-types";
import { Button, Stack, Typography } from "@mui/material";
import { alpha } from "@mui/material/styles";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { enqueueSnackbar } from "notistack";
import axios from "src/utils/axios";
import Iconify from "src/components/iconify";
import { getRequestErrorMessage } from "src/utils/errorUtils";
import { useChangeSetStore } from "src/sections/develop-detail/DataTab/Versions/changeSetStore";
import { toPendingOps } from "src/sections/develop/AddRowDrawer/fileImport";
import { IMPORTS_PATH } from "./mappingModel";

const body = (datasetId, op) => ({
  op,
  source: { kind: "generate", mode: "kb_refresh" },
  target: { dataset_id: datasetId },
});

export default function KbChangesBanner({ datasetId, canWrite = true }) {
  const queryClient = useQueryClient();
  const key = ["kb-changes", datasetId];
  const { data } = useQuery({
    queryKey: key,
    queryFn: () =>
      axios
        .post(IMPORTS_PATH, body(datasetId, "preview"))
        .then((r) => r?.data?.result),
    enabled: Boolean(datasetId),
    staleTime: 0,
    meta: { errorHandled: true },
  });
  const regen = useMutation({
    mutationFn: () =>
      axios
        .post(IMPORTS_PATH, body(datasetId, "commit"))
        .then((r) => r?.data?.result),
    meta: { errorHandled: true },
    onSuccess: (res) => {
      const pending = toPendingOps(res.operations ?? [], {
        hint: res.description_hint,
      });
      const cs = useChangeSetStore.getState();
      pending.forEach((op) => cs.addOp(datasetId, op));
      enqueueSnackbar(
        `${res.description_hint} · pending changes; Save version keeps them`,
        { variant: "success" },
      );
      queryClient.invalidateQueries({ queryKey: key });
    },
    onError: (e) =>
      enqueueSnackbar(
        getRequestErrorMessage(e, "Couldn't regenerate the goldens."),
        {
          variant: "error",
        },
      ),
  });
  // A stale golden with a pending cell edit (a Regenerate not saved yet,
  // or a hand edit) counts as handled.
  const ops = useChangeSetStore((s) => s.sets?.[datasetId]?.ops);
  const pendingRows = useMemo(
    () =>
      new Set(
        (ops ?? [])
          .filter((o) => o.op === "update_cell" && o.row_id)
          .map((o) => o.row_id),
      ),
    [ops],
  );
  const stale = (data?.stale ?? [])
    .map((s) => ({
      ...s,
      rows: s.rows.filter((r) => !pendingRows.has(r.row_id)),
    }))
    .filter((s) => s.rows.length);
  if (!stale.length) return null;
  const count = stale.reduce((n, s) => n + s.rows.length, 0);
  const regenerable = stale.some((s) => s.rows.some((r) => r.new));
  return (
    <Stack
      direction="row"
      alignItems="center"
      gap={0.75}
      data-testid="kb-changes"
      sx={{
        px: 1,
        py: 0.25,
        borderRadius: 1,
        bgcolor: (t) => alpha(t.palette.warning.main, 0.12),
        flexShrink: 0,
      }}
    >
      <Iconify icon="mdi:book-alert-outline" width={16} />
      <Typography typography="s2" noWrap>
        {`${stale.map((s) => s.kb_name).join(", ")} changed · ${count} ${count === 1 ? "golden cites a changed passage" : "goldens cite changed passages"}`}
      </Typography>
      {canWrite && regenerable ? (
        <Button
          size="small"
          variant="text"
          onClick={() => regen.mutate()}
          disabled={regen.isPending}
          data-testid="kb-regenerate"
          sx={{ minWidth: 0, px: 0.75 }}
        >
          Regenerate
        </Button>
      ) : null}
    </Stack>
  );
}
KbChangesBanner.propTypes = {
  datasetId: PropTypes.string,
  canWrite: PropTypes.bool,
};
