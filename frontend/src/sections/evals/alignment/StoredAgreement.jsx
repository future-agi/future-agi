import PropTypes from "prop-types";
import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import {
  Alert,
  Box,
  Button,
  CircularProgress,
  MenuItem,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  TextField,
  Typography,
} from "@mui/material";
import { useOrganization } from "src/contexts/OrganizationContext";
import { useWorkspace } from "src/contexts/WorkspaceContext";
import axios, { endpoints } from "src/utils/axios";
import { isSurfaceShown } from "src/launch/launchSwitch";
import { kappaText, pctText } from "./agreementModel";

export default function StoredAgreement({ templateId }) {
  const [search, setSearch] = useSearchParams();
  const [source, setSource] = useState(() => search.get("source"));
  const clearSelection = () => {
    const next = new URLSearchParams(search);
    for (const field of ["source", "dataset", "label"]) next.delete(field);
    setSource(null);
    setSearch(next);
  };
  const { currentOrganizationId } = useOrganization();
  const { currentWorkspaceId } = useWorkspace();
  const params = Object.fromEntries(
    Object.entries({
      source,
      dataset_id: search.get("dataset"),
      label_id: search.get("label"),
    }).filter(([, value]) => value),
  );
  const { data, isPending, isError, isFetching, refetch } = useQuery({
    queryKey: [
      "stored-agreement",
      currentOrganizationId,
      currentWorkspaceId,
      templateId,
      params,
    ],
    queryFn: async () =>
      (
        await axios.get(endpoints.develop.w7.agreement(templateId), {
          params,
          headers: {
            "X-Organization-Id": currentOrganizationId,
            "X-Workspace-Id": currentWorkspaceId,
          },
        })
      ).data.result,
    enabled: Boolean(
      templateId &&
        currentOrganizationId &&
        currentWorkspaceId &&
        isSurfaceShown("evals.agreement"),
    ),
    staleTime: 0,
  });
  if (!isSurfaceShown("evals.agreement")) return null;
  if (isPending)
    return (
      <Box sx={{ p: 3 }}>
        <CircularProgress aria-label="Loading agreement" size={24} />
      </Box>
    );
  if (isError)
    return (
      <Alert
        severity="error"
        action={
          <Stack direction="row">
            {(source || search.get("dataset") || search.get("label")) && (
              <Button onClick={clearSelection}>Choose a source</Button>
            )}
            <Button onClick={() => refetch()}>Try again</Button>
          </Stack>
        }
      >
        Could not load agreement. No saved report is being shown.
      </Alert>
    );
  const report = data?.report;
  return (
    <Stack gap={2} sx={{ p: 2, minWidth: 0 }} data-testid="w7-agreement-tab">
      <Stack direction="row" justifyContent="space-between" alignItems="center">
        <Typography variant="h6">Agreement with reviewers</Typography>
        <Button onClick={() => refetch()} disabled={isFetching}>
          Refresh
        </Button>
      </Stack>
      <Typography color="text.secondary" variant="body2">
        Saved dataset eval results compared with human labels. No judge calls or
        rescoring. Each row uses the latest vote per reviewer: a strict majority
        for categories, or the median for numeric labels. Ties, multiple-choice
        labels, missing or ambiguously encoded results and failed cells are
        excluded. Numeric labels and scores must use the same scale; no
        conversion is applied.
      </Typography>
      {!data?.sources?.length ? (
        <Typography>No saved dataset results have human labels yet.</Typography>
      ) : (
        <>
          <TextField
            select
            label="Human label source"
            value={report?.key ?? ""}
            onChange={(event) => setSource(event.target.value)}
            fullWidth
          >
            {data.sources.map((item) => (
              <MenuItem
                key={item.key}
                value={item.key}
              >{`${item.dataset_name} · ${item.binding_name} · ${item.label_name}`}</MenuItem>
            ))}
          </TextField>
          {!report && (
            <Alert severity="info">
              Choose a label source to view its report. The first source is too
              large to calculate automatically.
            </Alert>
          )}
          {report && (
            <>
              <Typography variant="h6" data-testid="w7-headline">
                {report.numeric
                  ? `Spearman ρ ${kappaText(report.spearman)} · mean absolute difference ${kappaText(report.mad)} · n ${report.n}`
                  : `Agreement ${pctText(report.accuracy)} · κ ${kappaText(report.kappa)} · n ${report.n}`}
              </Typography>
              <Typography color="text.secondary" variant="body2">
                {report.excluded} labelled rows excluded from this report.
              </Typography>
              {!report.numeric && (
                <Box sx={{ overflowX: "auto" }}>
                  <Table
                    size="small"
                    aria-label="Agreement per label"
                    data-testid="w7-per-label"
                  >
                    <TableHead>
                      <TableRow>
                        {[
                          "Label",
                          "Precision",
                          "Recall",
                          "F1",
                          "Reviewer rows",
                        ].map((label) => (
                          <TableCell key={label}>{label}</TableCell>
                        ))}
                      </TableRow>
                    </TableHead>
                    <TableBody>
                      {report.per_label.map((item) => (
                        <TableRow key={item.label}>
                          <TableCell component="th" scope="row">
                            {item.label}
                          </TableCell>
                          <TableCell>{pctText(item.precision)}</TableCell>
                          <TableCell>{pctText(item.recall)}</TableCell>
                          <TableCell>{kappaText(item.f1)}</TableCell>
                          <TableCell>{item.support}</TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </Box>
              )}
              <Typography variant="subtitle1">
                Disagreements (up to 100 rows)
              </Typography>
              <Box sx={{ overflowX: "auto" }}>
                <Table
                  size="small"
                  aria-label="Stored disagreements"
                  data-testid="w7-disagreements"
                >
                  <TableHead>
                    <TableRow>
                      <TableCell>Row</TableCell>
                      <TableCell>Human label</TableCell>
                      <TableCell>Stored judge result</TableCell>
                    </TableRow>
                  </TableHead>
                  <TableBody>
                    {report.disagreements.map((item) => (
                      <TableRow key={item.row_id}>
                        <TableCell>{item.row_id}</TableCell>
                        <TableCell>{String(item.human)}</TableCell>
                        <TableCell>{String(item.judge)}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </Box>
            </>
          )}
        </>
      )}
    </Stack>
  );
}
StoredAgreement.propTypes = { templateId: PropTypes.string.isRequired };
