import React, { useCallback, useId, useMemo, useRef, useState } from "react";
import PropTypes from "prop-types";
import { Box, Skeleton, Stack, Typography } from "@mui/material";
import { useNavigate, useParams } from "react-router";
import { DataTable, DataTablePagination } from "src/components/data-table";
import ErrorState from "src/sections/common/ux/ErrorState";
import { ROW_HEIGHT, TABLE_HEADER_HEIGHT } from "src/theme/sizes";
import { trackEvent, Events } from "src/utils/Mixpanel";
import ExperimentTabHeader from "./ExperimentTabHeader";
import {
  PAGE_SIZE,
  classicExperimentPath,
  useClassicExperimentColumns,
} from "./classicExperimentColumns";
import { OTHER_EXPERIMENTS_COPY as COPY } from "./otherExperimentsCopy";

// The Experiments list's search, done here on the rows already read: the
// text is split on spaces and commas, and a name matches when it contains
// every word, in any order, ignoring case (the list's server search did the
// same on the name).
const searchWords = (text) =>
  text
    .toLowerCase()
    .split(/[\s,]+/)
    .filter(Boolean);
const nameMatches = (row, words) => {
  const name = String(row.name ?? "").toLowerCase();
  return words.every((word) => name.includes(word));
};

const Heading = ({ id }) => (
  <Box sx={{ minWidth: 0 }}>
    <Typography
      id={id}
      component="h3"
      typography="s1"
      fontWeight="fontWeightSemiBold"
      color="text.primary"
    >
      {COPY.heading}
    </Typography>
    <Typography typography="s2" color="text.secondary">
      {COPY.line}
    </Typography>
  </Box>
);

Heading.propTypes = { id: PropTypes.string };

const Loading = () => (
  <Stack
    data-testid="other-experiments-loading"
    aria-busy="true"
    spacing={1}
    sx={{ px: 1.5, pt: 1.5, pb: 1.5 }}
  >
    <Skeleton width={180} />
    <Skeleton width="42%" />
    <Box sx={{ border: "1px solid", borderColor: "divider", borderRadius: 1 }}>
      <Skeleton variant="rectangular" height={TABLE_HEADER_HEIGHT.default} />
      {[0, 1, 2].map((i) => (
        <Stack
          key={i}
          direction="row"
          alignItems="center"
          gap={2}
          sx={{
            height: ROW_HEIGHT.default,
            px: 1.5,
            borderTop: "1px solid",
            borderColor: "divider",
          }}
        >
          <Skeleton variant="rounded" width={18} height={18} />
          <Skeleton width="30%" />
          <Skeleton width="12%" />
          <Skeleton width="8%" />
          <Skeleton width="8%" />
          <Skeleton width="14%" />
        </Stack>
      ))}
    </Box>
  </Stack>
);

/**
 * The dataset's experiments that open in the original results view, under
 * the runs view of the Experiments tab: the Experiments list's own table,
 * columns, row actions and selection actions, 20 rows a page. A row opens the
 * experiment at the page the list always opened.
 *
 * Its selection stays here: the runs view's selection bar acts on runs. Its
 * Search field filters the rows by name, as the Experiments list's did.
 */
export default function OtherExperimentsGroup({ other }) {
  const { dataset } = useParams();
  const navigate = useNavigate();
  const headingId = useId();
  const [page, setPage] = useState(0);
  const [search, setSearch] = useState("");
  const [rowSelection, setRowSelection] = useState({});
  const [selected, setSelected] = useState([]);
  const { rows: allRows, refetch } = other;
  const columns = useClassicExperimentColumns(refetch);
  const words = useMemo(() => searchWords(search), [search]);
  const rows = useMemo(
    () =>
      words.length ? allRows.filter((row) => nameMatches(row, words)) : allRows,
    [allRows, words],
  );
  const onSearch = useCallback((text) => {
    setSearch(text);
    setPage(0);
  }, []);

  const clearSelection = useCallback(() => {
    setRowSelection({});
    setSelected([]);
  }, []);
  // The selection actions refresh and clear through the handle the list gives
  // them (refreshExperimentTab / clearRowSelection).
  const handle = useRef(null);
  handle.current = {
    refreshExperimentTab: () => {
      refetch();
      clearSelection();
    },
    clearRowSelection: clearSelection,
  };

  const lastPage = Math.max(0, Math.ceil(rows.length / PAGE_SIZE) - 1);
  const shownPage = Math.min(page, lastPage);
  const pageRows = useMemo(
    () => rows.slice(shownPage * PAGE_SIZE, (shownPage + 1) * PAGE_SIZE),
    [rows, shownPage],
  );

  const onSelectionChange = useCallback((selection, selectedRows) => {
    setRowSelection(selection);
    setSelected(selectedRows);
  }, []);

  const openRow = useCallback(
    (row) => {
      trackEvent(Events.expSelected, {
        datasetId: row.dataset,
        experimentName: row.name,
      });
      navigate(classicExperimentPath(row, dataset));
    },
    [dataset, navigate],
  );

  if (other.isPending) return <Loading />;
  if (other.isError && other.count === null) {
    return (
      <Box
        component="section"
        aria-label={COPY.heading}
        data-testid="other-experiments-error"
        sx={{ px: 1.5, pb: 1.5 }}
      >
        <ErrorState
          thing={COPY.loadThing}
          error={other.error}
          onRetry={() => refetch()}
          compact
        />
      </Box>
    );
  }
  if (!allRows.length) return null;

  return (
    <Box
      component="section"
      aria-labelledby={headingId}
      data-testid="other-experiments"
      sx={{
        flexShrink: 0,
        display: "flex",
        flexDirection: "column",
        px: 1.5,
        pt: 1.5,
        pb: 1,
        borderTop: "1px solid",
        borderColor: "divider",
      }}
    >
      <Heading id={headingId} />
      <Box sx={{ mx: -1 }}>
        <ExperimentTabHeader
          tabRef={handle}
          experimentSearch={search}
          setExperimentSearch={onSearch}
          rowSelected={selected}
          setRowSelected={setSelected}
          compareUnavailableReason={COPY.compareReason}
        />
      </Box>
      <Box
        sx={{
          mt: 1,
          display: "flex",
          height:
            TABLE_HEADER_HEIGHT.default +
            Math.max(pageRows.length, 1) * ROW_HEIGHT.default +
            2,
        }}
      >
        <DataTable
          columns={columns}
          data={pageRows}
          rowCount={rows.length}
          enableSelection
          rowSelection={rowSelection}
          selectionScope={[dataset, shownPage, PAGE_SIZE]}
          onRowSelectionChange={onSelectionChange}
          onRowClick={openRow}
          getRowId={(row) => row.id}
          ariaLabel={COPY.heading}
          emptyMessage={COPY.noMatch}
        />
      </Box>
      <DataTablePagination
        page={shownPage}
        pageSize={PAGE_SIZE}
        total={rows.length}
        onPageChange={setPage}
        pageSizeOptions={[PAGE_SIZE]}
      />
    </Box>
  );
}

OtherExperimentsGroup.propTypes = {
  other: PropTypes.shape({
    rows: PropTypes.array.isRequired,
    count: PropTypes.number,
    isPending: PropTypes.bool,
    isError: PropTypes.bool,
    error: PropTypes.any,
    refetch: PropTypes.func.isRequired,
  }).isRequired,
};
