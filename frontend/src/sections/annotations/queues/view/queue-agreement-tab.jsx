import PropTypes from "prop-types";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  Box,
  Card,
  CardContent,
  CircularProgress,
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
import { useQueueAgreement } from "src/api/annotation-queues/annotation-queues";
import { paths } from "src/routes/paths";
import Iconify from "src/components/iconify";

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

export default function QueueAgreementTab({ queueId }) {
  const navigate = useNavigate();
  const { data: agreement, isLoading } = useQueueAgreement(queueId);
  const [anchorEl, setAnchorEl] = useState(null);
  const [selectedLabel, setSelectedLabel] = useState(null);

  const handleOpenPopover = (event, label) => {
    setAnchorEl(event.currentTarget);
    setSelectedLabel(label);
  };

  const handleClosePopover = () => {
    setAnchorEl(null);
    setSelectedLabel(null);
  };

  const popoverOpen = Boolean(anchorEl);

  if (isLoading) {
    return (
      <Box sx={{ display: "flex", justifyContent: "center", py: 8 }}>
        <CircularProgress />
      </Box>
    );
  }

  if (!agreement) return null;

  const { overall_agreement, labels, annotator_pairs } = agreement;
  const overallAgreement = overall_agreement;
  const labelEntries = Object.entries(labels || {});
  const pairs = annotator_pairs || [];

  return (
    <Box sx={{ p: 3 }}>
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
            <Table size="small">
              <TableHead>
                <TableRow>
                  <TableCell>Label</TableCell>
                  <TableCell>Type</TableCell>
                  <TableCell align="right">Agreement</TableCell>
                  <TableCell align="right">Cohen&apos;s Kappa</TableCell>
                  <TableCell align="right">Disagreements</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {labelEntries.map(([id, label]) => (
                  <TableRow key={id}>
                    <TableCell>{label.label_name}</TableCell>
                    <TableCell>
                      <Typography variant="caption">
                        {label.label_type}
                      </Typography>
                    </TableCell>
                    <TableCell align="right">
                      <Typography
                        color={getAgreementColor(label.agreement_pct)}
                        fontWeight={600}
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
                          onClick={(e) => handleOpenPopover(e, label)}
                          aria-haspopup="true"
                          aria-expanded={
                            popoverOpen && selectedLabel === label
                              ? "true"
                              : "false"
                          }
                          aria-controls={
                            popoverOpen && selectedLabel === label
                              ? "disagreement-popover"
                              : undefined
                          }
                          sx={{
                            fontWeight: 600,
                            color: "primary.main",
                            textDecoration: "underline",
                            cursor: "pointer",
                            border: "none",
                            background: "none",
                            padding: 0,
                            fontFamily: "inherit",
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
                        fontWeight={600}
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
        id="disagreement-popover"
        open={popoverOpen}
        anchorEl={anchorEl}
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
            <Typography variant="subtitle2" sx={{ mb: 1.5, fontWeight: 600 }}>
              Disagreed Items: {selectedLabel.label_name}
            </Typography>
            <Stack spacing={1} sx={{ overflowY: "auto", flexGrow: 1 }}>
              {(selectedLabel.disagreement_items ?? []).map((itemId) => (
                <Link
                  key={itemId}
                  component="button"
                  variant="body2"
                  onClick={() => {
                    navigate(
                      `${paths.dashboard.annotations.annotate(queueId)}?itemId=${itemId}&mode=review`
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
                    backgroundColor: "transparent",
                    border: "none",
                    cursor: "pointer",
                    "&:hover": {
                      backgroundColor: "action.hover",
                      textDecoration: "underline",
                    },
                  }}
                >
                  <span>Item #{itemId}</span>
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
};
