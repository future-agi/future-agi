import React, { useEffect, useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  Dialog,
  DialogContent,
  DialogTitle,
  IconButton,
  Popover,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  TextField,
  Typography,
  useTheme,
} from "@mui/material";
import { LoadingButton } from "@mui/lab";
import { enqueueSnackbar } from "notistack";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import ConfirmDialog from "src/components/custom-dialog/confirm-dialog";
import {
  useCreateSplit,
  useDatasetSplits,
  useDeleteSplit,
  useUpdateSplit,
} from "src/api/develop/dataset-splits";
import {
  nextSplitColor,
  SPLIT_COLOR_KEYS,
  SPLIT_COLOR_LABELS,
  splitChipColors,
} from "src/sections/common/Datasets/splitColors";
import { SplitChip } from "src/sections/common/Datasets/SplitChips";
import { useDevelopDetailContext } from "../../Context/DevelopDetailContext";
import { useDataGridViewStore } from "../DataGrid/useDataGridView";
import { SPLIT_NAME_RE } from "./AssignSplitsMenu";
import { useManageSplitsStore } from "./splitStores";
import { TrainingSetButton } from "./TrainingSetDialog";
import SplitCatalogState from "./SplitCatalogState";

/** Inline text cell: commits on blur / Enter, Esc reverts. */
const InlineText = ({ value, placeholder, onCommit, validate, maxLength }) => {
  const [draft, setDraft] = useState(value ?? "");
  useEffect(() => setDraft(value ?? ""), [value]);
  const error = validate ? validate(draft) : null;
  const commit = () => {
    const next = draft.trim();
    if (error || next === (value ?? "")) {
      setDraft(value ?? "");
      return;
    }
    onCommit(next);
  };
  return (
    <TextField
      size="small"
      fullWidth
      value={draft}
      placeholder={placeholder}
      error={Boolean(error)}
      helperText={error || undefined}
      inputProps={{ maxLength }}
      onChange={(e) => setDraft(e.target.value)}
      onBlur={commit}
      onKeyDown={(e) => {
        if (e.key === "Enter") e.currentTarget.querySelector("input")?.blur?.();
        if (e.key === "Escape") setDraft(value ?? "");
      }}
      InputProps={{
        sx: {
          typography: "s2",
          height: 30,
          "& fieldset": { borderColor: "transparent" },
          "&:hover fieldset": { borderColor: "divider" },
        },
      }}
    />
  );
};

InlineText.propTypes = {
  value: PropTypes.string,
  placeholder: PropTypes.string,
  onCommit: PropTypes.func,
  validate: PropTypes.func,
  maxLength: PropTypes.number,
};

/** 8 colour swatches. */
const ColourPicker = ({ value, onChange }) => {
  const theme = useTheme();
  const [anchor, setAnchor] = useState(null);
  const current = splitChipColors(value, theme);
  return (
    <>
      <CustomTooltip
        show
        title={SPLIT_COLOR_LABELS[value] ?? "Colour"}
        size="small"
        arrow
      >
        <IconButton
          size="small"
          onClick={(e) => setAnchor(e.currentTarget)}
          aria-label="Change colour"
        >
          <Box
            sx={{
              width: 14,
              height: 14,
              borderRadius: "50%",
              bgcolor: current.dot,
            }}
          />
        </IconButton>
      </CustomTooltip>
      <Popover
        open={Boolean(anchor)}
        anchorEl={anchor}
        onClose={() => setAnchor(null)}
        anchorOrigin={{ vertical: "bottom", horizontal: "left" }}
        PaperProps={{ sx: { p: 1 } }}
      >
        <Box
          sx={{
            display: "grid",
            gridTemplateColumns: "repeat(4, 28px)",
            gap: 0.5,
          }}
        >
          {SPLIT_COLOR_KEYS.map((k) => (
            <CustomTooltip
              key={k}
              show
              title={SPLIT_COLOR_LABELS[k]}
              size="small"
              arrow
            >
              <IconButton
                size="small"
                onClick={() => {
                  onChange(k);
                  setAnchor(null);
                }}
                sx={{
                  width: 28,
                  border: k === value ? "2px solid" : "2px solid transparent",
                  borderColor: k === value ? "text.primary" : "transparent",
                }}
              >
                <Box
                  sx={{
                    width: 16,
                    height: 16,
                    borderRadius: "50%",
                    bgcolor: splitChipColors(k, theme).dot,
                  }}
                />
              </IconButton>
            </CustomTooltip>
          ))}
        </Box>
      </Popover>
    </>
  );
};

ColourPicker.propTypes = { value: PropTypes.string, onChange: PropTypes.func };

/**
 * Manage splits (area-datasets §6): rename, colour and description apply
 * immediately and are not versioned; deleting a split takes it off its rows
 * and saves that as a new version.
 */
const ManageSplitsDialog = ({ datasetId }) => {
  const { open, setOpen } = useManageSplitsStore();
  const { data, isPending, isError, isFetching, refetch } = useDatasetSplits(
    datasetId,
    { enabled: open },
  );
  const catalogUnavailable = isPending || isError;
  const { mutate: update } = useUpdateSplit(datasetId);
  const { mutateAsync: remove, isPending: deleting } =
    useDeleteSplit(datasetId);
  const { mutateAsync: create, isPending: creating } =
    useCreateSplit(datasetId);
  const { refreshGrid } = useDevelopDetailContext();
  const [confirm, setConfirm] = useState(null);
  const [newName, setNewName] = useState("");
  const splits = data?.splits ?? [];

  const nameError = (exceptId) => (name) => {
    const n = name.trim();
    if (!n) return "Name is required";
    if (!SPLIT_NAME_RE.test(n))
      return "Lowercase letters, digits, dots, dashes or underscores";
    if (splits.some((s) => s.name === n && s.id !== exceptId))
      return "Name already used";
    return null;
  };
  const newError = newName ? nameError(null)(newName) : null;

  const addSplit = async () => {
    if (catalogUnavailable || !newName || newError) return;
    try {
      await create({ name: newName.trim(), color: nextSplitColor(splits) });
      setNewName("");
    } catch {
      // toast from the hook
    }
  };

  const doDelete = async () => {
    const split = confirm;
    if (!split) return;
    try {
      await remove(split.id);
      const view = useDataGridViewStore.getState().view(datasetId);
      if (view.splitIds.includes(split.id)) {
        useDataGridViewStore.getState().patch(datasetId, {
          splitIds: view.splitIds.filter((x) => x !== split.id),
        });
      }
      refreshGrid?.();
      enqueueSnackbar(`Deleted split ${split.name}`, { variant: "success" });
    } catch {
      // toast from the hook
    } finally {
      setConfirm(null);
    }
  };

  return (
    <>
      <Dialog
        open={open}
        onClose={() => setOpen(false)}
        maxWidth={false}
        PaperProps={{ sx: { width: 720, maxWidth: "95vw", p: 2 } }}
      >
        <DialogTitle sx={{ p: 0, mb: 0.5 }}>
          <Stack
            direction="row"
            alignItems="center"
            justifyContent="space-between"
          >
            <Typography typography="m3" fontWeight="fontWeightSemiBold">
              Manage splits
            </Typography>
            <IconButton onClick={() => setOpen(false)} aria-label="Close">
              <Iconify icon="mdi:close" />
            </IconButton>
          </Stack>
          <Typography typography="s2" color="text.secondary">
            Splits are named subsets of current rows. A row can be in several.
            Changes are saved immediately.
          </Typography>
        </DialogTitle>
        <DialogContent sx={{ p: 0, mt: 1.5 }}>
          <Table
            size="small"
            sx={{ "& td, & th": { px: 1, borderColor: "divider" } }}
          >
            <TableHead>
              <TableRow>
                <TableCell sx={{ width: 36 }} />
                <TableCell
                  sx={{ width: 170, typography: "s3", color: "text.secondary" }}
                >
                  Name
                </TableCell>
                <TableCell sx={{ typography: "s3", color: "text.secondary" }}>
                  Description
                </TableCell>
                <TableCell
                  align="right"
                  sx={{ width: 64, typography: "s3", color: "text.secondary" }}
                >
                  Rows
                </TableCell>
                <TableCell sx={{ width: 40 }} />
              </TableRow>
            </TableHead>
            <TableBody>
              {catalogUnavailable && (
                <TableRow>
                  <TableCell colSpan={5}>
                    <SplitCatalogState
                      isError={isError}
                      isFetching={isFetching}
                      onRetry={() => refetch()}
                    />
                  </TableCell>
                </TableRow>
              )}
              {!catalogUnavailable &&
                splits.map((s) => (
                  <TableRow key={s.id} hover>
                    <TableCell>
                      <ColourPicker
                        value={s.color}
                        onChange={(color) => update({ splitId: s.id, color })}
                      />
                    </TableCell>
                    <TableCell>
                      <InlineText
                        value={s.name}
                        maxLength={30}
                        validate={nameError(s.id)}
                        onCommit={(name) => update({ splitId: s.id, name })}
                      />
                    </TableCell>
                    <TableCell>
                      <InlineText
                        value={s.description}
                        placeholder="Add a description"
                        maxLength={200}
                        onCommit={(description) =>
                          update({ splitId: s.id, description })
                        }
                      />
                    </TableCell>
                    <TableCell align="right">
                      <Typography
                        typography="s2"
                        sx={{ fontVariantNumeric: "tabular-nums" }}
                      >
                        {s.row_count}
                      </Typography>
                    </TableCell>
                    <TableCell>
                      <CustomTooltip
                        show
                        title="Delete split"
                        size="small"
                        arrow
                      >
                        <IconButton
                          size="small"
                          onClick={() => setConfirm(s)}
                          aria-label={`Delete ${s.name}`}
                        >
                          <Iconify
                            icon="solar:trash-bin-trash-linear"
                            width={16}
                          />
                        </IconButton>
                      </CustomTooltip>
                    </TableCell>
                  </TableRow>
                ))}
              {!catalogUnavailable && !splits.length && (
                <TableRow>
                  <TableCell colSpan={5}>
                    <Typography
                      typography="s2"
                      color="text.secondary"
                      sx={{ py: 1 }}
                    >
                      No splits yet. Create one below, or select rows and use
                      Assign split.
                    </Typography>
                  </TableCell>
                </TableRow>
              )}
            </TableBody>
          </Table>
          <Stack direction="row" alignItems="flex-start" gap={1} sx={{ mt: 2 }}>
            <TextField
              size="small"
              placeholder="New split name, e.g. edge-cases"
              disabled={catalogUnavailable}
              value={newName}
              error={Boolean(newError)}
              helperText={newError || " "}
              onChange={(e) => setNewName(e.target.value.toLowerCase())}
              onKeyDown={(e) => e.key === "Enter" && addSplit()}
              sx={{ width: 280 }}
              InputProps={{ sx: { typography: "s2", height: 32 } }}
            />
            <LoadingButton
              size="small"
              variant="outlined"
              loading={creating}
              disabled={catalogUnavailable || !newName || Boolean(newError)}
              onClick={addSplit}
              startIcon={<Iconify icon="mdi:plus" width={16} />}
            >
              Create split
            </LoadingButton>
            <Box sx={{ flex: 1 }} />
            <TrainingSetButton
              datasetId={datasetId}
              splits={catalogUnavailable ? [] : splits}
            />
            <Button
              variant="contained"
              color="primary"
              size="small"
              onClick={() => setOpen(false)}
            >
              Done
            </Button>
          </Stack>
        </DialogContent>
      </Dialog>
      <ConfirmDialog
        open={Boolean(confirm)}
        onClose={() => setConfirm(null)}
        title={confirm ? `Delete split “${confirm.name}”?` : ""}
        content={
          confirm ? (
            <Stack gap={1}>
              <Box>
                <SplitChip split={confirm} />
              </Box>
              <Typography typography="s2" color="text.secondary">
                {confirm.row_count} row{confirm.row_count === 1 ? "" : "s"} lose
                this split immediately. The rows and their cell values are kept.
              </Typography>
            </Stack>
          ) : null
        }
        action={
          <LoadingButton
            variant="contained"
            color="error"
            loading={deleting}
            onClick={doDelete}
          >
            Delete split
          </LoadingButton>
        }
      />
    </>
  );
};

ManageSplitsDialog.propTypes = {
  datasetId: PropTypes.string.isRequired,
};

export default ManageSplitsDialog;
