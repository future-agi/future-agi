import {
  Box,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  IconButton,
  Link,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import PropTypes from "prop-types";
import React, { useEffect, useMemo, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { LoadingButton } from "@mui/lab";
import { useNavigate } from "react-router";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import axios, { endpoints } from "src/utils/axios";
import { trackEvent, Events, PropertyName } from "src/utils/Mixpanel";
import { getRequestErrorMessage } from "src/utils/errorUtils";
import { useDevelopDatasetList } from "src/api/develop/develop-detail";
import { notify } from "src/sections/common/ux/toast";
import { KeyCap } from "src/sections/common/ux/KeyCap";
import { fmtCount } from "src/sections/common/format/fmt";
import { getCreatedDatasetId } from "./manualCreateDatasetResponse";
import V1PreviewLine from "./V1PreviewLine";
import BlankGridColumns from "./BlankGridColumns";
import {
  cleanColumns,
  DEFAULT_NEW_COLUMNS,
  NAME_MAX,
  validateDatasetName,
} from "./datasetName";

// The theme registers m3; keep this type extension local to the existing title.
const BlankGridTitle =
  /** @type {React.ComponentType<Omit<import("@mui/material").TypographyProps<"span">, "variant"> & { variant?: import("@mui/material").TypographyProps["variant"] | "m3" }>} */ (
    Typography
  );

const ROLES = [
  { value: "input", label: "Input" },
  { value: "expected", label: "Expected" },
  { value: "metadata", label: "Metadata" },
];

/**
 * Start with a blank grid (DL#6, DL#7, DL#30): name (validated live
 * against the loaded datasets), columns pre-seeded `input` +
 * `expected_output` (rename inline, add, remove, role), rows default 0.
 * Enter or ⌘↵ creates; success opens the dataset with an Undo toast.
 */
const ManuallyCreateDataset = ({ open, onClose, refreshGrid, initialName }) => {
  const [name, setName] = useState(initialName ?? "");
  const [columns, setColumns] = useState(() => [...DEFAULT_NEW_COLUMNS]);
  const [rows, setRows] = useState(0);
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const { data: existing } = useDevelopDatasetList(undefined, undefined, {
    enabled: open,
  });
  useEffect(() => {
    if (open) {
      setName(initialName ?? "");
      setColumns([...DEFAULT_NEW_COLUMNS]);
      setRows(0);
    }
  }, [open, initialName]);

  const check = useMemo(
    () =>
      validateDatasetName(
        name,
        (existing ?? []).map((d) => ({ id: d.datasetId, name: d.name })),
      ),
    [name, existing],
  );
  const cols = cleanColumns(columns);
  const valid =
    check.ok &&
    cols.length > 0 &&
    cols.length === columns.length &&
    Number.isInteger(rows) &&
    rows >= 0 &&
    rows <= 100;

  const { mutate: create, isPending } = useMutation({
    mutationFn: (body) =>
      axios.post(endpoints.develop.createDatasetManually, body, {
        headers: { "Content-Type": "application/json" },
      }),
    onSuccess: (res, body) => {
      trackEvent(Events.dataAddSuccessfull, {
        [PropertyName.method]: "add using manually create dataset",
      });
      queryClient.invalidateQueries({ queryKey: ["develop", "dataset-list"] });
      queryClient.invalidateQueries({
        queryKey: ["develop", "dataset-name-list"],
      });
      refreshGrid?.();
      onClose();
      const id = getCreatedDatasetId(res);
      // After the dialog's close commits (the dataset page is lazy).
      if (id)
        setTimeout(() => navigate(`/dashboard/develop/${id}?tab=data`), 0);
      const toast = notify(
        `Created "${body.dataset_name}" · ${fmtCount(body.number_of_rows, "row")}`,
        {
          variant: "success",
          key: "dataset-created",
          undo: { onUndo: () => {}, ms: 10000 },
        },
      );
      toast.undone.then(async (undone) => {
        if (!undone || !id) return;
        await axios
          .delete(endpoints.develop.deleteDataset(), {
            data: { dataset_ids: [id] },
          })
          .catch(() => {});
        queryClient.invalidateQueries({ queryKey: ["datasets", "list"] });
        queryClient.invalidateQueries({
          queryKey: ["develop", "dataset-name-list"],
        });
        navigate("/dashboard/develop");
        notify(`Removed "${body.dataset_name}"`, {
          key: "dataset-created-undo",
        });
      });
    },
    meta: { errorHandled: true },
    onError: (error) =>
      notify(
        getRequestErrorMessage(error, "Couldn't create the dataset", {
          retryAction: "creating this dataset",
        }),
        { variant: "error", key: "dataset-create-failed" },
      ),
  });

  const submit = () => {
    if (!valid || isPending) return;
    trackEvent(Events.datasetManualAdditionSuccessful, {
      [PropertyName.name]: name.trim(),
      [PropertyName.rowCount]: rows,
      [PropertyName.columnCount]: cols.length,
    });
    create({
      dataset_name: name.trim(),
      model_type: "GenerativeLLM",
      number_of_rows: rows,
      number_of_columns: cols.length,
      columns: cols.map((c) => ({
        name: c.name,
        type: c.type ?? "text",
        role: c.role,
      })),
    });
  };

  return (
    <Dialog
      open={open}
      onClose={onClose}
      maxWidth={false}
      PaperProps={{ sx: { maxWidth: 720 } }}
      fullWidth
      aria-labelledby="manual-create-title"
      onKeyDown={(e) => {
        // Enter (or ⌘↵) creates, unless a select menu or button has focus.
        const tag = e.target?.getAttribute?.("role");
        if (
          !e.defaultPrevented &&
          e.key === "Enter" &&
          !e.shiftKey &&
          tag !== "combobox" &&
          tag !== "option" &&
          e.target?.tagName !== "BUTTON"
        ) {
          e.preventDefault();
          submit();
        }
      }}
    >
      <Box sx={{ p: 2 }}>
        <DialogTitle
          id="manual-create-heading"
          sx={{ p: 0, mb: 0.5, display: "flex", alignItems: "center" }}
        >
          <BlankGridTitle
            id="manual-create-title"
            component="span"
            variant="m3"
            fontWeight={600}
            sx={{ flex: 1 }}
          >
            Start with a blank grid
          </BlankGridTitle>
          <IconButton onClick={onClose} aria-label="Close" size="small">
            <Iconify icon="solar:close-linear" color="text.primary" />
          </IconButton>
        </DialogTitle>
        <Typography typography="s2" color="text.secondary" sx={{ mb: 2 }}>
          Name the dataset and its columns. You can add rows now or later.
        </Typography>
        <DialogContent
          sx={{ p: 0, pt: 1, display: "flex", flexDirection: "column", gap: 2 }}
        >
          <TextField
            autoFocus
            size="small"
            label="Name"
            required
            value={name}
            onChange={(e) => setName(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                submit();
              }
            }}
            inputProps={{ maxLength: NAME_MAX + 20 }}
            error={Boolean(check.error)}
            helperText={
              check.error ? (
                <Box
                  component="span"
                  sx={{
                    display: "flex",
                    gap: 1,
                    justifyContent: "space-between",
                  }}
                >
                  <span>
                    {check.error}{" "}
                    {check.clash && (
                      <Link
                        component="button"
                        type="button"
                        typography="s3"
                        onClick={() => {
                          onClose();
                          navigate(
                            `/dashboard/develop/${check.clash.id}?tab=data`,
                          );
                        }}
                      >
                        Open it
                      </Link>
                    )}
                  </span>
                  {check.counter && <span>{check.counter}</span>}
                </Box>
              ) : (
                check.counter ?? " "
              )
            }
          />
          <BlankGridColumns
            columns={columns}
            setColumns={setColumns}
            roles={ROLES}
          />
          <Stack direction="row" alignItems="center" gap={1.5}>
            <Typography typography="s2" fontWeight={600}>
              Blank rows
            </Typography>
            <TextField
              size="small"
              type="number"
              value={rows}
              onChange={(e) =>
                setRows(Math.max(0, Math.min(100, Number(e.target.value) || 0)))
              }
              inputProps={{
                min: 0,
                max: 100,
                "aria-label": "Blank rows",
                style: { textAlign: "right" },
              }}
              sx={{ width: 96 }}
            />
            <Typography typography="s3" color="text.secondary">
              0 to 100 · type the values in the grid
            </Typography>
          </Stack>
          <V1PreviewLine
            name={name}
            enabled={valid}
            spec={{
              kind: "rows",
              number_of_rows: rows,
              number_of_columns: cols.length,
              columns: cols.map((c) => ({
                name: c.name,
                type: c.type ?? "text",
                role: c.role,
              })),
            }}
          />
        </DialogContent>
        <DialogActions sx={{ p: 0, mt: 3, gap: 1 }}>
          <Typography
            typography="s3"
            color="text.secondary"
            sx={{ flex: 1, display: "flex", gap: 0.5, alignItems: "center" }}
          >
            <KeyCap>↵</KeyCap> creates
          </Typography>
          <Button onClick={onClose} variant="outlined" color="inherit">
            Cancel
          </Button>
          <CustomTooltip
            show={!valid}
            size="small"
            title={
              !name.trim()
                ? "Name the dataset to continue"
                : check.error ?? (cols.length ? "" : "Add at least one column")
            }
          >
            <span>
              <LoadingButton
                onClick={submit}
                variant="contained"
                color="primary"
                loading={isPending}
                disabled={!valid}
                sx={{
                  "&.Mui-disabled": {
                    bgcolor: (t) => `${t.palette.primary.main}66`,
                    color: "common.white",
                  },
                }}
              >
                Create dataset
              </LoadingButton>
            </span>
          </CustomTooltip>
        </DialogActions>
      </Box>
    </Dialog>
  );
};

ManuallyCreateDataset.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  refreshGrid: PropTypes.func,
  initialName: PropTypes.string,
};

export default ManuallyCreateDataset;
