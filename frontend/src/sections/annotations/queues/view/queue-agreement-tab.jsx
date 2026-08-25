import PropTypes from "prop-types";
import { useMemo, useState } from "react";
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
import {
  useAnnotationQueueDetail,
  useQueueAgreement,
} from "src/api/annotation-queues/annotation-queues";
import { useAuthContext } from "src/auth/hooks";
import { paths } from "src/routes/paths";
import Iconify from "src/components/iconify";
import {
  QUEUE_ROLES,
  hasQueueRole,
  shortId,
} from "../constants";
import { resolveQueueItemWorkspaceMode } from "../annotate/annotation-view-mode";

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
  const { user } = useAuthContext();
  const currentUserId = user?.id ? String(user.id) : null;
  const { data: queue } = useAnnotationQueueDetail(queueId);
  const { data: agreement, isLoading } = useQueueAgreement(queueId);
  const [anchorEl, setAnchorEl] = useState(null);
  const [selectedLabelId, setSelectedLabelId] = useState(null);

  const myQueueMembership = useMemo(() => {
    if (!queue || !user) return null;
    if (Array.isArray(queue.viewer_roles) && queue.viewer_roles.length > 0) {
      return { role: queue.viewer_role, roles: queue.viewer_roles };
    }
    const annotators = queue.annotators || [];
    return annotators.find((a) => String(a.user_id) === currentUserId) || null;
  }, [queue, user, currentUserId]);

  const isManager = hasQueueRole(myQueueMembership, QUEUE_ROLES.MANAGER);
  const canAnnotateQueue =
    hasQueueRole(myQueueMembership, QUEUE_ROLES.ANNOTATOR) || isManager;
  const canViewSubmissions =
    hasQueueRole(myQueueMembership, QUEUE_ROLES.REVIEWER) || isManager;

  const handleOpenPopover = (event, labelId) => {
    setAnchorEl(event.currentTarget);
    setSelectedLabelId(labelId);
  };

  const handleClosePopover = () => {
    setAnchorEl(null);
    setSelectedLabelId(null);
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
  const selectedLabel = selectedLabelId ? labels?.[selectedLabelId] : null;

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
                          aria-haspopup="true"
                          aria-expanded={
                            popoverOpen && selectedLabelId === id
                              ? "true"
                              : "false"
                          }
                          aria-controls={
                            popoverOpen && selectedLabelId === id
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
        id="disagreement-popover"
        aria-labelledby={
          selectedLabelId ? `disagreement-trigger-${selectedLabelId}` : undefined
        }
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
            <Typography
              variant="subtitle2"
              sx={{ mb: 1.5, fontWeight: "fontWeightSemiBold" }}
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
                    const mode = resolveQueueItemWorkspaceMode({
                      canViewSubmissions,
                      canAnnotate: canAnnotateQueue,
                    });
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
};
