import PropTypes from "prop-types";
import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  Box, Stack, Typography, Button, Chip, CircularProgress, IconButton, Alert,
  Table, TableBody, TableCell, TableContainer, TableHead, TableRow,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { useAvailableEvaluations, useAddEvaluation } from "src/api/simulate-environments/environments";
import { harnessEnvironmentQuery } from "src/api/simulate-environments/environment";
import SideDrawer from "../../components/SideDrawer";
import EmptyState from "../../components/EmptyState";
import { modalityOf, mappingRowsFor } from "./evalSourceMapping";

const MONO = "ui-monospace, Menlo, monospace";

// §10: an environment runs at most 8 evaluations; the backend 409s past it.
const EVAL_CAP = 8;

// The axios interceptor puts the API body on the error ({ detail } for §10) plus
// statusCode — surface the backend's own message (e.g. the 8-eval cap) verbatim.
const addErrorMessage = (error) =>
  error?.detail || error?.message || "Couldn’t add the evaluation. Try again.";

/**
 * §10 add-evaluation picker.
 *
 * Matches the product eval list (src/sections/common/EvalPicker/EvalPickerList):
 * a table of rows, each with an expand chevron that opens an inline detail
 * (description + the read-only input mapping) and an Add button. The list comes
 * from `GET …/evaluations/available/` (catalogue filtered to this environment's
 * modality, minus what's already selected). The add is `POST { name }` only —
 * the backend resolves the mapping by modality — so there is no config/naming
 * step, and the key→source mapping is shown read-only.
 */
export default function AddEvaluationDrawer({ open, env, onClose, onAdded }) {
  const envId = env?.id;
  const modality = modalityOf(env);
  const { data: evaluations = [], isLoading, isError, refetch } =
    useAvailableEvaluations(envId, { enabled: open });
  const addEval = useAddEvaluation();
  const addingName = addEval.isPending ? addEval.variables?.name : null;
  const [expanded, setExpanded] = useState(null);

  // Which evals are already applied (§6 selected). The add's response seeds this
  // cache, so a just-added row flips to "Added" in place — no list refetch/flash.
  const detailQuery = useQuery(harnessEnvironmentQuery(envId, { enabled: open }));
  const selected = detailQuery.data?.evaluations?.selected;
  const addedNames = useMemo(
    () => new Set((Array.isArray(selected) ? selected : []).map((e) => e.name)),
    [selected],
  );
  // At the 8-eval cap, adding 409s — so disable Add and say why up front, rather
  // than letting every click fail silently.
  const appliedCount = Array.isArray(selected) ? selected.length : addedNames.size;
  const atCap = appliedCount >= EVAL_CAP;

  const add = (name) =>
    addEval.mutate({ id: envId, name }, { onSuccess: () => onAdded?.(name) });

  const headerCellSx = {
    fontSize: 12, fontWeight: 600, color: "text.secondary",
    py: 1, px: 1, whiteSpace: "nowrap",
    borderBottom: "1px solid", borderColor: "divider",
  };
  const bodyCellSx = {
    fontSize: 13, py: 0.75, px: 1,
    borderBottom: "1px solid", borderColor: "divider",
  };

  return (
    <SideDrawer open={open} onClose={onClose} width={560}>
      <Stack sx={{ height: "100%", minHeight: 0 }}>
        <Box sx={{ px: 3, pt: 3, pb: 2, flexShrink: 0 }}>
          <Typography sx={{ typography: "m2", fontWeight: "fontWeightSemiBold" }}>
            Add evaluations
          </Typography>
          <Typography sx={{ typography: "s2", color: "text.secondary", maxWidth: 440 }}>
            Pick from the library. Expand a row to see what it reads; inputs are
            mapped automatically from this {modality === "voice" ? "voice" : "chat"} environment.
          </Typography>
        </Box>

        <Box sx={{ flex: 1, minHeight: 0, overflow: "auto", px: 3, pb: 3 }}>
          {atCap && (
            <Alert severity="warning" sx={{ mb: 2, typography: "s3" }}>
              This environment already has the maximum {EVAL_CAP} evaluations.
              Remove one before adding another.
            </Alert>
          )}
          {addEval.isError && !atCap && (
            <Alert severity="error" sx={{ mb: 2, typography: "s3" }}>
              {addErrorMessage(addEval.error)}
            </Alert>
          )}
          {isLoading ? (
            <Stack alignItems="center" sx={{ py: 6 }}>
              <CircularProgress size={22} />
            </Stack>
          ) : isError ? (
            <EmptyState
              icon="solar:danger-triangle-linear"
              title="Couldn’t load evaluations"
              body="Something went wrong fetching the library. Try again."
              action={
                <Button variant="outlined" size="small" onClick={() => refetch()}>
                  Retry
                </Button>
              }
            />
          ) : evaluations.length === 0 ? (
            <EmptyState
              icon="solar:shield-check-linear"
              title="Nothing left to add"
              body="Every evaluation this environment can be graded by is already applied."
            />
          ) : (
            <TableContainer>
              <Table size="small" sx={{ tableLayout: "fixed" }}>
                <TableHead>
                  <TableRow>
                    <TableCell sx={{ ...headerCellSx, width: 36 }} />
                    <TableCell sx={{ ...headerCellSx, width: 72 }} />
                    <TableCell sx={headerCellSx}>Evaluation</TableCell>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {evaluations.map((item) => {
                    const isExpanded = expanded === item.name;
                    const adding = addingName === item.name;
                    const added = addedNames.has(item.name);
                    return [
                      <TableRow
                        key={item.name}
                        hover
                        onClick={() => setExpanded(isExpanded ? null : item.name)}
                        sx={{
                          cursor: "pointer",
                          bgcolor: isExpanded ? "action.selected" : "inherit",
                          "&:hover": { bgcolor: "action.hover" },
                        }}
                      >
                        <TableCell sx={{ ...bodyCellSx, width: 36, px: 0.5 }}>
                          <IconButton size="small" sx={{ p: 0.25 }}>
                            <Iconify
                              icon={isExpanded ? "solar:alt-arrow-down-bold" : "solar:alt-arrow-right-bold"}
                              width={14}
                              sx={{ color: isExpanded ? "primary.main" : "text.disabled" }}
                            />
                          </IconButton>
                        </TableCell>
                        <TableCell sx={{ ...bodyCellSx, width: 72, px: 0.5 }}>
                          <Button
                            size="small"
                            variant={added ? "outlined" : "contained"}
                            disabled={added || atCap || addEval.isPending}
                            onClick={(e) => {
                              e.stopPropagation();
                              if (!added && !atCap) add(item.name);
                            }}
                            startIcon={adding ? <CircularProgress size={12} color="inherit" /> : null}
                            sx={{ minWidth: 50, height: 24, fontSize: 11, textTransform: "none", px: 1 }}
                          >
                            {added ? "Added" : adding ? "…" : "Add"}
                          </Button>
                        </TableCell>
                        <TableCell sx={bodyCellSx}>
                          <Typography noWrap sx={{ typography: "s2", fontWeight: "fontWeightSemiBold" }}>
                            {item.name}
                          </Typography>
                        </TableCell>
                      </TableRow>,

                      isExpanded && (
                        <TableRow key={`${item.name}-detail`}>
                          <TableCell colSpan={3} sx={{ p: 0, borderBottom: "1px solid", borderColor: "divider" }}>
                            <EvalDetail item={item} modality={modality} />
                          </TableCell>
                        </TableRow>
                      ),
                    ];
                  })}
                </TableBody>
              </Table>
            </TableContainer>
          )}
        </Box>
      </Stack>
    </SideDrawer>
  );
}

AddEvaluationDrawer.propTypes = {
  open: PropTypes.bool,
  env: PropTypes.shape({ id: PropTypes.string, agentType: PropTypes.string }),
  onClose: PropTypes.func,
  onAdded: PropTypes.func,
};

// The inline expanded panel: description + the read-only input mapping (left the
// required key, right the source it resolves to for this modality). Mirrors the
// product EvalDetailPanel's panel styling.
function EvalDetail({ item, modality }) {
  const rows = mappingRowsFor(item.required_keys, modality);
  return (
    <Box sx={{ p: 2, bgcolor: "action.hover", display: "flex", flexDirection: "column", gap: 1.5 }}>
      {item.description && (
        <Typography sx={{ typography: "s3", color: "text.secondary" }}>
          {item.description}
        </Typography>
      )}

      {rows.length > 0 && (
        <Box>
          <Stack direction="row" alignItems="center" spacing={0.75} sx={{ mb: 1 }}>
            <Typography sx={{ fontSize: 10, color: "text.disabled", letterSpacing: 0.3 }}>
              VARIABLE MAPPING
            </Typography>
            <Iconify icon="solar:lock-keyhole-minimalistic-linear" width={11} sx={{ color: "text.disabled" }} />
          </Stack>
          <Stack spacing={0.75}>
            {rows.map(({ key, value }) => (
              <Stack key={key} direction="row" alignItems="center" spacing={1}>
                <Chip
                  label={`{{${key}}}`}
                  size="small"
                  sx={{
                    fontSize: 10, height: 22, fontFamily: MONO,
                    bgcolor: "background.neutral", color: "text.secondary",
                    "& .MuiChip-label": { px: 0.75 },
                  }}
                />
                <Iconify icon="solar:arrow-right-linear" width={13} sx={{ color: "text.disabled", flexShrink: 0 }} />
                <Box
                  sx={{
                    flex: 1, minWidth: 0,
                    px: 1, py: 0.5, borderRadius: 0.75,
                    border: "1px solid", borderColor: "divider", bgcolor: "background.paper",
                    fontFamily: MONO, fontSize: 11.5,
                    color: value ? "text.primary" : "text.disabled",
                  }}
                >
                  {value || "—"}
                </Box>
              </Stack>
            ))}
          </Stack>
        </Box>
      )}
    </Box>
  );
}

EvalDetail.propTypes = {
  item: PropTypes.shape({
    name: PropTypes.string,
    description: PropTypes.string,
    required_keys: PropTypes.arrayOf(PropTypes.string),
    modality: PropTypes.string,
  }),
  modality: PropTypes.string,
};
