// The public dataset page /shared/<token> (W4-6; W4-SCOPE §6 A3): no app
// chrome, read-only, only what the link includes. Rows (search, pages of the
// server's size, a row's full values), Experiments (runs with pass rates
// and each row's answers per run), Feedback (human labels and notes). There
// are no edit, run, download or copy controls.
import {
  Box,
  Button,
  Chip,
  Divider,
  Stack,
  Tab,
  Tabs,
  TextField,
  Typography,
  useTheme,
} from "@mui/material";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import PropTypes from "prop-types";
import React, { useState } from "react";
import { Helmet } from "react-helmet-async";
import axios, { endpoints } from "src/utils/axios";
import { useDebounce } from "src/hooks/use-debounce";
import { renderAnnotationValue } from "src/sections/common/DevelopCellRenderer/AnnotationCellRenderer/renderAnnotationValue";
import {
  cellDetail,
  cellSummary,
  cellText,
  feedbackLabel,
  feedbackMeta,
  headerParts,
  metricText,
  pageInfo,
  rangeLine,
  scoredLine,
  sharedTitle,
  tabsFor,
} from "./sharedDatasetModel";

const clamp = {
  overflow: "hidden",
  textOverflow: "ellipsis",
  display: "-webkit-box",
  WebkitLineClamp: 3,
  WebkitBoxOrient: "vertical",
};

function RowsTable({ data, open, setOpen }) {
  const cols = data.columns;
  return (
    <Box sx={{ overflowX: "auto" }} data-testid="shared-rows">
      <Box
        component="table"
        sx={{
          borderCollapse: "collapse",
          width: "100%",
          "& td, & th": {
            borderBottom: 1,
            borderColor: "divider",
            p: 1,
            verticalAlign: "top",
            textAlign: "left",
          },
        }}
      >
        <thead>
          <tr>
            <Typography component="th" variant="s3" color="text.secondary">
              Row
            </Typography>
            {cols.map((c) => (
              <Typography
                component="th"
                key={c.id}
                variant="s3"
                color="text.secondary"
                sx={{ minWidth: 180 }}
              >
                {c.name}
              </Typography>
            ))}
          </tr>
        </thead>
        <tbody>
          {data.rows.items.map((r) => (
            <React.Fragment key={r.row_id}>
              <Box
                component="tr"
                onClick={() => setOpen(open === r.row_id ? null : r.row_id)}
                sx={{
                  cursor: "pointer",
                  "&:hover": { bgcolor: "action.hover" },
                }}
              >
                <Typography component="td" variant="s2">
                  {r.n}
                </Typography>
                {cols.map((c) => (
                  <Typography component="td" key={c.id} variant="s2">
                    <Box sx={clamp}>
                      {cellSummary(r.values[c.id], c.data_type)}
                    </Box>
                  </Typography>
                ))}
              </Box>
              {open === r.row_id && (
                <tr>
                  <td colSpan={cols.length + 1}>
                    <Stack
                      spacing={1}
                      sx={{ py: 1 }}
                      data-testid="shared-row-detail"
                    >
                      {cols.map((c) => (
                        <Box key={c.id}>
                          <Typography variant="s3" color="text.secondary">
                            {c.name}
                          </Typography>
                          <Typography
                            variant="s2"
                            component="pre"
                            sx={{
                              whiteSpace: "pre-wrap",
                              fontFamily: "inherit",
                              m: 0,
                            }}
                          >
                            {cellDetail(r.values[c.id], c.data_type)}
                          </Typography>
                        </Box>
                      ))}
                    </Stack>
                  </td>
                </tr>
              )}
            </React.Fragment>
          ))}
        </tbody>
      </Box>
    </Box>
  );
}
RowsTable.propTypes = {
  data: PropTypes.object.isRequired,
  open: PropTypes.string,
  setOpen: PropTypes.func.isRequired,
};

function ExperimentsTab({ data }) {
  const { runs, results } = data.experiments;
  const byId = Object.fromEntries(runs.map((r) => [r.run_id, r]));
  return (
    <Stack spacing={2} data-testid="shared-experiments">
      <Box>
        {runs.length === 0 && (
          <Typography variant="s2" color="text.secondary">
            No runs on this version.
          </Typography>
        )}
        {runs.map((r) => (
          <Stack
            key={r.run_id}
            direction="row"
            spacing={1}
            alignItems="center"
            sx={{ py: 0.75, borderBottom: 1, borderColor: "divider" }}
            data-testid="shared-run"
          >
            <Chip size="small" label={r.label} />
            <Typography variant="s2" sx={{ flex: 1 }}>
              {r.name}
            </Typography>
            {r.metrics.map((m) => (
              <Typography key={m.key} variant="s3" color="text.secondary">
                {metricText(m)}
              </Typography>
            ))}
            <Typography variant="s3" color="text.secondary">
              {scoredLine(r)}
            </Typography>
          </Stack>
        ))}
      </Box>
      {runs.length > 0 &&
        data.rows.items.map((row) => (
          <Box key={row.row_id}>
            <Typography variant="s2" fontWeight={600}>
              Row {row.n}
            </Typography>
            {(results[row.row_id] ?? []).map((x) => (
              <Stack
                key={x.run_id}
                direction="row"
                spacing={1}
                sx={{ py: 0.5 }}
              >
                <Chip
                  size="small"
                  variant="outlined"
                  label={byId[x.run_id]?.label ?? x.label}
                />
                <Typography variant="s2" sx={{ flex: 1, ...clamp }}>
                  {cellText(x.output)}
                </Typography>
                {x.evals.map((e) => (
                  <Typography key={e.name} variant="s3" color="text.secondary">
                    {`${e.name} ${e.display}`}
                  </Typography>
                ))}
              </Stack>
            ))}
          </Box>
        ))}
    </Stack>
  );
}
ExperimentsTab.propTypes = { data: PropTypes.object.isRequired };

function FeedbackTab({ data }) {
  const theme = useTheme();
  const rows = data.rows.items.filter(
    (r) => (data.feedback.by_row[r.row_id] ?? []).length,
  );
  return (
    <Stack spacing={1.5} data-testid="shared-feedback">
      {rows.length === 0 && (
        <Typography variant="s2" color="text.secondary">
          No human feedback on these rows.
        </Typography>
      )}
      {rows.map((r) => (
        <Box key={r.row_id}>
          <Typography variant="s2" fontWeight={600}>
            Row {r.n}
          </Typography>
          {data.feedback.by_row[r.row_id].map((f, i) => (
            <Stack
              key={`${r.row_id}-${i}`}
              direction="row"
              spacing={0.75}
              alignItems="center"
              flexWrap="wrap"
              data-testid="shared-feedback-entry"
            >
              <Typography variant="s2">{feedbackLabel(f)}</Typography>
              {renderAnnotationValue(f.value, theme)}
              {feedbackMeta(f) && (
                <Typography variant="s2" color="text.secondary">
                  {feedbackMeta(f)}
                </Typography>
              )}
            </Stack>
          ))}
        </Box>
      ))}
    </Stack>
  );
}
FeedbackTab.propTypes = { data: PropTypes.object.isRequired };

export default function SharedDatasetView({ token, shared }) {
  const [tab, setTab] = useState("rows");
  const [page, setPage] = useState(1);
  const [search, setSearch] = useState("");
  const [open, setOpen] = useState(null);
  const q = useDebounce(search.trim(), 300);
  const first = page === 1 && !q;
  const { data: more } = useQuery({
    queryKey: ["shared-dataset", token, page, q],
    queryFn: () =>
      axios.get(endpoints.sharedLinks.resolve(token), {
        params: { page, ...(q ? { search: q } : {}) },
      }),
    select: (res) => res.data?.data,
    enabled: !first,
    placeholderData: keepPreviousData,
    retry: false,
  });
  const data = first ? shared.data : more ?? shared.data;
  const tabs = tabsFor(data);
  const info = pageInfo(data.rows);
  return (
    <Box
      sx={{ minHeight: "100vh", bgcolor: "background.paper" }}
      data-testid="shared-dataset-view"
    >
      <Helmet>
        <title>{sharedTitle(data)}</title>
      </Helmet>
      <Box
        sx={{
          px: 3,
          py: 2,
          borderBottom: 1,
          borderColor: "divider",
          bgcolor: "background.default",
        }}
      >
        <Typography
          variant="m3"
          fontWeight={600}
          data-testid="shared-dataset-header"
        >
          {headerParts(data).join(" · ")}
        </Typography>
      </Box>
      <Box sx={{ px: 3 }}>
        <Tabs value={tab} onChange={(_, v) => setTab(v)}>
          {tabs.map((t) => (
            <Tab key={t.key} value={t.key} label={t.label} />
          ))}
        </Tabs>
        <Divider />
        <Stack direction="row" alignItems="center" spacing={2} sx={{ py: 1.5 }}>
          <TextField
            size="small"
            placeholder="Search rows"
            value={search}
            onChange={(e) => {
              setSearch(e.target.value);
              setPage(1);
            }}
            inputProps={{ "aria-label": "Search rows" }}
            sx={{ width: 320 }}
          />
          <Box sx={{ flex: 1 }} />
          <Typography
            variant="s2"
            color="text.secondary"
            data-testid="shared-range"
          >
            {rangeLine(data.rows)}
          </Typography>
          <Button
            size="small"
            disabled={data.rows.page <= 1}
            onClick={() => setPage(data.rows.page - 1)}
          >
            Previous
          </Button>
          <Button
            size="small"
            disabled={data.rows.page >= info.pages}
            onClick={() => setPage(data.rows.page + 1)}
          >
            Next
          </Button>
        </Stack>
        <Box sx={{ pb: 4 }}>
          {tab === "rows" && (
            <RowsTable data={data} open={open} setOpen={setOpen} />
          )}
          {tab === "experiments" && data.experiments && (
            <ExperimentsTab data={data} />
          )}
          {tab === "feedback" && data.feedback && <FeedbackTab data={data} />}
        </Box>
      </Box>
    </Box>
  );
}

SharedDatasetView.propTypes = {
  token: PropTypes.string.isRequired,
  shared: PropTypes.shape({ data: PropTypes.object.isRequired }).isRequired,
};
