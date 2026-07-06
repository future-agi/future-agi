import React, {
  useMemo,
  useCallback,
  useState,
  useEffect,
  useRef,
} from "react";
import { Drawer, Box, IconButton, Typography, Button } from "@mui/material";
import Iconify from "src/components/iconify";
import { useWorkbenchMetrics } from "../context/WorkbenchMetricsContext";
import { getRandomId } from "src/utils/utils";
import { buildFilterDefinitions, defaultFilterBase } from "../common";
import ComplexFilter from "src/components/ComplexFilter/ComplexFilter";

const MetricFilterDrawer = React.memo(() => {
  const {
    isFilterDrawerOpen,
    setIsFilterDrawerOpen,
    columns,
    filters,
    setFilters,
    activeTab,
  } = useWorkbenchMetrics();

  const getDefaultFilter = () => [{ ...defaultFilterBase, id: getRandomId() }];

  const prevTabRef = useRef(activeTab);

  const [filterDefs, setFilterDefs] = useState([]);

  const [tempFilters, setTempFilters] = useState(() =>
    filters?.length ? filters : getDefaultFilter(),
  );
  const [tempFilterDefs, setTempFilterDefs] = useState([]);

  useEffect(() => {
    if (columns?.length) {
      setFilterDefs(buildFilterDefinitions(columns, activeTab === "Metrics"));
    }
  }, [columns, activeTab]);

  useEffect(() => {
    if (prevTabRef.current !== activeTab) {
      setFilters(getDefaultFilter());
      prevTabRef.current = activeTab;
    }
  }, [activeTab, setFilters]);

  useEffect(() => {
    if (!isFilterDrawerOpen) return;

    // Quick filters are appended API-shaped (no `_meta.parentProperty`/`id`) and
    // sit alongside the empty default draft. Drop empty drafts and rebuild the
    // property link from `column_id` so each applied filter shows as a real,
    // editable row instead of a blank "Select Option".
    const hydrated = (filters || [])
      .filter((f) => f?.column_id)
      .map((f) => {
        if (f._meta?.parentProperty && f.id) return f;
        const def = filterDefs.find(
          (d) => d.propertyId === f.column_id || d.propertyName === f.column_id,
        );
        return {
          ...f,
          id: f.id || getRandomId(),
          _meta: {
            ...f._meta,
            parentProperty:
              f._meta?.parentProperty ||
              def?.propertyId ||
              def?.propertyName ||
              f.column_id,
          },
        };
      });

    setTempFilters(hydrated.length ? hydrated : getDefaultFilter());
    setTempFilterDefs(filterDefs);
  }, [isFilterDrawerOpen, filters, filterDefs]);

  const handleClose = useCallback(() => {
    setIsFilterDrawerOpen(false);
  }, [setIsFilterDrawerOpen]);

  const handleApplyFilters = useCallback(() => {
    const isEmpty = (v) => v === "" || v === undefined || v === null;
    const validFilters = tempFilters.filter((f) => {
      const value = f?.filter_config?.filter_value;
      return Array.isArray(value)
        ? f?.column_id && value.length > 0 && !value.some(isEmpty)
        : f?.column_id && !isEmpty(value);
    });

    setFilters(validFilters.length ? validFilters : getDefaultFilter());
    handleClose();
  }, [tempFilters, setFilters, handleClose]);

  const hasValidFilters = useMemo(() => {
    return tempFilters.some((f) => {
      const value = f?.filter_config?.filter_value;
      return (
        f?.column_id ||
        (value !== "" && !(Array.isArray(value) && value.length === 0))
      );
    });
  }, [tempFilters]);

  const handleCancel = useCallback(() => {
    handleClose();
  }, [handleClose]);

  const resetFiltersAndClose = () => {
    setFilters(getDefaultFilter());
    handleClose();
  };

  const handleAddFilter = useCallback(() => {
    setTempFilters((prev) => [
      ...(prev?.length ? prev : []),
      { ...defaultFilterBase, id: getRandomId() },
    ]);
  }, []);

  const drawerStyles = useMemo(
    () => ({
      height: "100vh",
      position: "fixed",
      borderRadius: "0px !important",
      backgroundColor: "background.paper",
      width: "clamp(560px, 46vw, 720px)",
      display: "flex",
      flexDirection: "column",
    }),
    [],
  );

  const drawerContentStyles = useMemo(
    () => ({
      padding: 2,
      flex: 1,
      display: "flex",
      height: "100%",
      flexDirection: "column",
    }),
    [],
  );

  const closeButtonStyles = useMemo(
    () => ({
      position: "absolute",
      top: "10px",
      right: "10px",
      color: "text.primary",
      zIndex: 10,
    }),
    [],
  );

  const actionButtonStyles = useMemo(
    () => ({
      pt: 2,
      display: "flex",
      gap: 2,
      justifyContent: "flex-end",
      borderTop: "1px solid",
      borderColor: "background.neutral",
      mt: "auto",
    }),
    [],
  );

  const scrollableContentStyles = useMemo(
    () => ({
      flex: 1,
      overflowY: "auto",
      pr: 1, // Padding for scrollbar
      display: "flex",
      flexDirection: "column",
      // Restyle ComplexFilter into stacked cards via its stable class hooks
      // (className="cf-cards") rather than positional selectors.
      "& .cf-cards .cf-rows": {
        border: "none",
        p: 0,
        gap: 3,
      },
      "& .cf-cards .cf-row": {
        position: "relative",
        border: "1px solid",
        borderColor: "divider",
        borderRadius: "10px",
        px: 1.5,
        pt: 2,
        pb: 1.25,
        flexWrap: "wrap",
        rowGap: 1,
      },
      "& .cf-cards .cf-row:not(:first-of-type)::before": {
        content: '"AND"',
        position: "absolute",
        top: "-18px",
        left: "2px",
        fontSize: "11px",
        fontWeight: 600,
        letterSpacing: "0.5px",
        color: "text.disabled",
      },
      "& .cf-cards .cf-row__controls": {
        flexWrap: "nowrap",
        alignItems: "center",
        gap: 1,
        minWidth: 0,
        "& > *": { minWidth: 0 },
        "& > .MuiTypography-root": { flexShrink: 0, minWidth: "max-content" },
        "& > :first-child": { flex: "1 1 auto", maxWidth: "240px" },
        "& > :last-child": { flexShrink: 0 },
      },
      "& .cf-cards .cf-row__add": {
        display: "none", // FilterRow's inline add — replaced by the button below
      },
    }),
    [],
  );

  return (
    <Drawer
      anchor="right"
      open={isFilterDrawerOpen}
      onClose={handleClose}
      variant="persistent"
      PaperProps={{ sx: drawerStyles }}
      ModalProps={{
        BackdropProps: { style: { backgroundColor: "transparent" } },
      }}
    >
      <Box sx={drawerContentStyles}>
        <IconButton onClick={handleClose} sx={closeButtonStyles}>
          <Iconify icon="akar-icons:cross" />
        </IconButton>

        <Box
          display="flex"
          justifyContent="space-between"
          alignItems="center"
          mb={2}
        >
          <Typography variant="h6">Filters</Typography>
        </Box>
        <Box sx={scrollableContentStyles}>
          <ComplexFilter
            className="cf-cards"
            filters={tempFilters}
            setFilters={setTempFilters}
            filterDefinition={tempFilterDefs}
            defaultFilter={defaultFilterBase}
            onClose={resetFiltersAndClose}
          />
          <Button
            fullWidth
            variant="outlined"
            startIcon={<Iconify icon="ic:round-plus" />}
            onClick={handleAddFilter}
            sx={{
              mt: 3,
              py: 1,
              borderStyle: "dashed",
              borderColor: "divider",
              color: "text.secondary",
              textTransform: "none",
              "&:hover": {
                borderStyle: "dashed",
                borderColor: "text.disabled",
                backgroundColor: "action.hover",
              },
            }}
          >
            Add Filter
          </Button>
        </Box>
        <Box sx={actionButtonStyles}>
          <Button
            variant="outlined"
            aria-label="cancel"
            size="small"
            onClick={handleCancel}
            sx={{ width: 140, px: 1, borderColor: "text.disabled" }}
          >
            Cancel
          </Button>
          <Button
            variant="contained"
            aria-label="apply-filters"
            color="primary"
            size="small"
            onClick={handleApplyFilters}
            sx={{ width: 140, px: 1 }}
            disabled={!hasValidFilters}
          >
            Apply Filters
          </Button>
        </Box>
      </Box>
    </Drawer>
  );
});

MetricFilterDrawer.displayName = "MetricFilterDrawer";

export default MetricFilterDrawer;
