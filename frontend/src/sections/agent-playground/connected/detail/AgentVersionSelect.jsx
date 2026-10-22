import PropTypes from "prop-types";
import React from "react";
import { Box, MenuItem, Select, Skeleton } from "@mui/material";
import { MONO, ago } from "./format";
import usePinnedVersions from "./usePinnedVersions";
import { LineSkeleton } from "./bits";

const loadingRows = (n) =>
  Array.from({ length: n }, (_, i) => (
    // eslint-disable-next-line react/no-array-index-key
    <MenuItem
      key={`loading-${i}`}
      disabled
      data-testid="version-option-skeleton"
      sx={{ typography: "s2" }}
    >
      <LineSkeleton width={26} sx={{ mr: 1 }} />
      <LineSkeleton width={52} />
    </MenuItem>
  ));

/**
 * Picks the version the tabs show. Versions come from the paginated list and
 * load as the menu scrolls; a selected version that isn't on the first page
 * stays pinned at the top.
 */
export default function AgentVersionSelect({ agentId, value, onChange }) {
  const {
    versions,
    selected,
    isLoading,
    hasNextPage,
    isFetchingNextPage,
    fetchNextPage,
  } = usePinnedVersions(agentId, value);

  const onMenuScroll = (e) => {
    const el = e.currentTarget;
    if (
      el.scrollTop + el.clientHeight >= el.scrollHeight - 5 &&
      hasNextPage &&
      !isFetchingNextPage
    ) {
      fetchNextPage();
    }
  };

  return (
    <Select
      size="small"
      value={value}
      onChange={(e) => onChange(e.target.value)}
      renderValue={() => {
        if (!selected) return "";
        return selected.isActive
          ? `${selected.label} · active`
          : `Viewing ${selected.label}`;
      }}
      sx={{ height: 32, typography: "s2", fontWeight: 600, minWidth: 120 }}
      // Long histories scroll inside the menu instead of running off the page.
      MenuProps={{
        PaperProps: { sx: { maxHeight: 320 }, onScroll: onMenuScroll },
      }}
    >
      {/* Keeps the selected value valid for Select before any page arrives. */}
      {!selected && <MenuItem value={value} sx={{ display: "none" }} />}
      {versions.map((v) => (
        <MenuItem key={v.id} value={v.id} sx={{ typography: "s2" }}>
          <Box
            component="span"
            sx={{ fontFamily: MONO, fontWeight: 600, mr: 1 }}
          >
            {v.label}
          </Box>
          <Box component="span" sx={{ color: "text.subtitle" }}>
            {[v.isActive ? "active" : null, ago(v.createdAt)]
              .filter(Boolean)
              .join(" · ")}
          </Box>
        </MenuItem>
      ))}
      {isLoading && loadingRows(3)}
      {isFetchingNextPage && loadingRows(2)}
    </Select>
  );
}

AgentVersionSelect.propTypes = {
  agentId: PropTypes.string.isRequired,
  value: PropTypes.string.isRequired,
  onChange: PropTypes.func.isRequired,
};
