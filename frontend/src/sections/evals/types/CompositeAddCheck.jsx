// W7 A1 (W7-5): Add check — a task-suite check as a composite child. Lists
// the check keys the workspace's task suites carry (each suite's `checks`
// column); a check child reads the try's own check result, free (no judge
// call). Shown only when a task suite has checks.
import React, { useMemo, useState } from "react";
import PropTypes from "prop-types";
import { useQueries } from "@tanstack/react-query";
import { Button, ListItemText, Menu, MenuItem } from "@mui/material";
import Iconify from "src/components/iconify";
import axios, { endpoints } from "src/utils/axios";
import { useDevelopDatasetList } from "src/api/develop/develop-detail";
import {
  addCheck,
  suiteCheckKeysOf,
} from "src/sections/common/EvalTypes/compositeModel";

export default function CompositeAddCheck({
  rows,
  onChildrenChange,
  disabled,
}) {
  const [anchor, setAnchor] = useState(null);
  const { data: datasets = [] } = useDevelopDatasetList();
  const suites = useMemo(
    () => (datasets ?? []).filter((d) => d.kind === "task_suite"),
    [datasets],
  );
  const tables = useQueries({
    queries: suites.map((s) => ({
      queryKey: ["evals", "composite-suite-checks", s.datasetId],
      queryFn: () =>
        axios.get(endpoints.develop.getDatasetDetail(s.datasetId), {
          params: { page_size: 500, current_page_index: 0 },
        }),
      select: (res) =>
        suiteCheckKeysOf(
          res.data?.result?.table ?? [],
          res.data?.result?.column_config ?? [],
          s.name,
        ),
      staleTime: 60000,
    })),
  });
  const keys = useMemo(() => {
    const seen = new Map();
    tables.forEach((t) =>
      (t.data ?? []).forEach((k) => {
        if (!seen.has(k.key)) seen.set(k.key, k);
      }),
    );
    return [...seen.values()];
  }, [tables]);
  const taken = new Set(
    rows.filter((r) => r.kind === "check").map((r) => r.check_key),
  );
  if (!keys.length) return null;
  return (
    <>
      <Button
        variant="outlined"
        size="small"
        startIcon={<Iconify icon="mdi:clipboard-check-outline" width={16} />}
        onClick={(e) => setAnchor(e.currentTarget)}
        disabled={disabled}
        sx={{
          mt: 0.5,
          alignSelf: "flex-start",
          textTransform: "none",
          fontSize: "12px",
        }}
      >
        Add check
      </Button>
      <Menu
        anchorEl={anchor}
        open={Boolean(anchor)}
        onClose={() => setAnchor(null)}
      >
        {keys.map((k) => (
          <MenuItem
            key={k.key}
            disabled={taken.has(k.key)}
            onClick={() => {
              onChildrenChange(addCheck(rows, { key: k.key, name: k.name }));
              setAnchor(null);
            }}
          >
            <ListItemText
              primary={k.name}
              secondary={`${k.key} · ${k.suite} · ${k.rows} task${k.rows === 1 ? "" : "s"}`}
              primaryTypographyProps={{ fontSize: "13px" }}
              secondaryTypographyProps={{ fontSize: "11px" }}
            />
          </MenuItem>
        ))}
      </Menu>
    </>
  );
}

CompositeAddCheck.propTypes = {
  rows: PropTypes.array.isRequired,
  onChildrenChange: PropTypes.func.isRequired,
  disabled: PropTypes.bool,
};
