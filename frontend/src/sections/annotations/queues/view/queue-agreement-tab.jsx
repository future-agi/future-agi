import PropTypes from "prop-types";
import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  Box,
  Card,
  CardContent,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Typography,
  Popover,
  Link,
  Stack,
} from "@mui/material";
import { LoadingScreen } from "src/components/loading-screen";
import { useQueueAgreement } from "src/api/annotation-queues/annotation-queues";
import { paths } from "src/routes/paths";
import Iconify from "src/components/iconify";
import { shortId } from "../constants";
import { WORKSPACE_MODES } from "../annotate/annotation-view-mode";

function getAgreementColor(pct) {
  if (pct === null || pct === undefined) return "text.secondary";
  if (pct >= 0.8) return "success.main";
  if (pct >= 0.6) return "warning.main";
  return "error.main";
}

function formatPct(val) {
  if (val === null || val === undefined) return "N/A";
  return `${(val * 100).toFixed(1)}%`;
}

export default function QueueAgreementTab({
  queueId,
  canViewSubmissions = false,
}) {
  const navigate = useNavigate();
  const { data: agreement, isLoading } = useQueueAgreement(queueId);
  const [selection, setSelection] = useState(null);
  const resultsRef = useRef(null);
  const selectedLabel =
    selection?.queueId === queueId
      ? agreement?.labels?.[selection.labelId]
      : null;
  const hasSelectedItems =
    selectedLabel?.disagreement_count > 0 &&
    selectedLabel?.disagreement_items?.length > 0;
  const popoverOpen = Boolean(selection && hasSelectedItems);

  useEffect(() => {
    if (selection && !hasSelectedItems) {
      setSelection(null);
      // The count button may have disappeared; return focus to surviving results.
      resultsRef.current?.focus();
    }
  }, [selection, hasSelectedItems]);

  const handleOpenPopover = (event, labelId) => {
    setSelection({ anchorEl: event.currentTarget, labelId, queueId });
  };

  const handleClosePopover = () => setSelection(null);

  if (isLoading) {
    return (
      <LoadingScreen variant="orbit" sx={{ minHeight: "50vh" }} />
    );
  }

  if (!agreement) return null;

  const { overall_agreement, labels, annotator_pairs } = agreement;
  const overallAgreement = overall_agreement;
  const labelEntries = Object.entries(labels || {});
  const pairs = annotator_pairs || [];

  return (
    <Box ref={resultsRef} role="region" aria-label="Agreement results" tabIndex={-1} sx={{ p: 3 }}>
      {/* Overall Agreement */}
      <Card sx={{ mb: 3 }}>
        <CardContent sx={{ textAlign: "center" }}>
          <Typography variant="caption" color="text.secondary">
            Overall Agreement
          </Typography>
          <Typography variant="h2" color={getAgreementColor(overallAgreement)}>
            {formatPct(overallAgreement)}
          </Typography>
          {overallAgreement == null && (
            <Typography variant="body2" color="text.secondary">
              Need at least 2 annotators per item to calculate agreement
            </Typography>
          )}
        </CardContent>
      </Card>

      {/* Per-Label Agreement */}
      {labelEntries.length > 0 && (
        <Box sx={{ mb: 3 }}>
          <Typography variant="subtitle2" sx={{ mb: 1 }}>
            Per-Label Agreement
          </Typography>
          <TableContainer>
            <Table size="small" sx={{ tableLayout: "fixed" }}>
              <TableHead>
                <TableRow>
                  <TableCell sx={{ width: "32%" }}>Label</TableCell>
                  <TableCell>Type</TableCell>
                  <TableCell align="right">Agreement</TableCell>
                  <TableCell align="right">Cohen&apos;s Kappa</TableCell>
                  <TableCell align="right">Disagreements</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {labelEntries.map(([id, label]) => (
                  <TableRow key={id}>
                    <TableCell title={label.label_name} sx={{ overflowWrap: "anywhere" }}>
                      {label.label_name}
                    </TableCell>
                    <TableCell>
                      <Typography variant="caption">
                        {label.label_type}
                      </Typography>
                    </TableCell>
                    <TableCell align="right">
                      <Typography
                        color={getAgreementColor(label.agreement_pct)}
                        fontWeight="fontWeightSemiBold"
                      >
                        {formatPct(label.agreement_pct)}
                      </Typography>
                    </TableCell>
                    <TableCell align="right">
                      {label.cohens_kappa != null
                        ? label.cohens_kappa.toFixed(3)
                        : "—"}
                    </TableCell>
                    <TableCell align="right">
                      {label.disagreement_count > 0 ? (
                        <Link
                          id={`disagreement-trigger-${id}`}
                          component="button"
                          variant="body2"
                          onClick={(e) => handleOpenPopover(e, id)}
                          aria-label={`${label.disagreement_count} disagreements for ${label.label_name}`}
                          aria-haspopup="dialog"
                          aria-expanded={
                            popoverOpen && selection.labelId === id
                              ? "true"
                              : "false"
                          }
                          aria-controls={
                            popoverOpen && selection.labelId === id
                              ? "disagreement-popover"
                              : undefined
                          }
                          sx={{
                            fontWeight: "fontWeightSemiBold",
                            color: "primary.main",
                            textDecoration: "underline",
                            "&:hover": {
                              color: "primary.dark",
                            },
                          }}
                        >
                          {label.disagreement_count}
                        </Link>
                      ) : (
                        label.disagreement_count ?? 0
                      )}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </TableContainer>
        </Box>
      )}

      {/* Annotator Pairs */}
      {pairs.length > 0 && (
        <Box>
          <Typography variant="subtitle2" sx={{ mb: 1 }}>
            Annotator Pair Agreement
          </Typography>
          <TableContainer>
            <Table size="small">
              <TableHead>
                <TableRow>
                  <TableCell>Annotator 1</TableCell>
                  <TableCell>Annotator 2</TableCell>
                  <TableCell align="right">Agreement</TableCell>
                  <TableCell align="right">Comparisons</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {pairs.map((pair, i) => (
                  <TableRow key={i}>
                    <TableCell>{pair.annotator_1_id}</TableCell>
                    <TableCell>{pair.annotator_2_id}</TableCell>
                    <TableCell align="right">
                      <Typography
                        color={getAgreementColor(pair.agreement_pct)}
                        fontWeight="fontWeightSemiBold"
                      >
                        {formatPct(pair.agreement_pct)}
                      </Typography>
                    </TableCell>
                    <TableCell align="right">
                      {pair.total_comparisons ?? 0}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </TableContainer>
        </Box>
      )}

      <Popover
        open={popoverOpen}
        anchorEl={popoverOpen ? selection.anchorEl : null}
        disableRestoreFocus={Boolean(selection && !hasSelectedItems)}
        onClose={handleClosePopover}
        anchorOrigin={{
          vertical: "bottom",
          horizontal: "right",
        }}
        transformOrigin={{
          vertical: "top",
          horizontal: "right",
        }}
        PaperProps={{
          id: "disagreement-popover",
          role: "dialog",
          "aria-modal": true,
          "aria-labelledby": "disagreement-popover-title",
          sx: {
            p: 2,
            width: 240,
            maxHeight: 300,
            display: "flex",
            flexDirection: "column",
            boxShadow: (theme) => theme.shadows[8],
          },
        }}
      >
        {selectedLabel && (
          <>
            <Typography
              id="disagreement-popover-title"
              variant="subtitle2"
              title={selectedLabel.label_name}
              sx={{
                mb: 1.5,
                fontWeight: "fontWeightSemiBold",
                overflowWrap: "anywhere",
                flexShrink: 0,
                display: "-webkit-box",
                WebkitBoxOrient: "vertical",
                WebkitLineClamp: 3,
                overflow: "hidden",
              }}
            >
              Disagreed Items: {selectedLabel.label_name}
            </Typography>
            <Stack spacing={1} sx={{ overflowY: "auto", flexGrow: 1 }}>
              {(selectedLabel.disagreement_items ?? []).map((itemId) => (
                <Link
                  key={itemId}
                  component="button"
                  variant="body2"
                  onClick={() => {
                    // Conflicting answers are only shown side by side in
                    // review mode, so anyone who can view submissions opens
                    // the item there; annotator-only members annotate.
                    const mode = canViewSubmissions
                      ? WORKSPACE_MODES.REVIEW
                      : WORKSPACE_MODES.ANNOTATE;
                    navigate(
                      `${paths.dashboard.annotations.annotate(queueId)}?itemId=${itemId}&mode=${mode}`,
                    );
                    handleClosePopover();
                  }}
                  sx={{
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "space-between",
                    textAlign: "left",
                    py: 0.5,
                    px: 1,
                    borderRadius: 0.5,
                    width: "100%",
                    textDecoration: "none",
                    color: "primary.main",
                    "&:hover": {
                      backgroundColor: "action.hover",
                      textDecoration: "underline",
                    },
                  }}
                >
                  <span>Item #{shortId(itemId)}</span>
                  <Iconify icon="eva:arrow-ios-forward-fill" width={16} />
                </Link>
              ))}
            </Stack>
            {selectedLabel.disagreement_count >
              (selectedLabel.disagreement_items ?? []).length && (
              <Typography
                variant="caption"
                color="text.secondary"
                sx={{ mt: 1.5, display: "block", textAlign: "center" }}
              >
                +{" "}
                {selectedLabel.disagreement_count -
                  (selectedLabel.disagreement_items ?? []).length}{" "}
                more disagreements
              </Typography>
            )}
          </>
        )}
      </Popover>
    </Box>
  );
}

QueueAgreementTab.propTypes = {
  queueId: PropTypes.string.isRequired,
  canViewSubmissions: PropTypes.bool,
};
