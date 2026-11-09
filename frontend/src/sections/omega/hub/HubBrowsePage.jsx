import React, { useEffect, useState } from "react";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import InputAdornment from "@mui/material/InputAdornment";
import Pagination from "@mui/material/Pagination";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";
import { useSearchParams } from "react-router-dom";

import { useHubEntries, useHubPicks } from "./api/queries";
import DiscordLink from "./components/DiscordLink";
import { EntryGrid } from "./components/EntryCard";
import FiltersBar, { SortTabs } from "./components/FiltersBar";
import HubFrame from "./components/HubFrame";
import HubIcon from "./components/HubIcon";
import HubNav from "./components/HubNav";
import { HubEmpty, HubError, HubSkeletonCards } from "./components/HubStates";
import PicksStrip from "./components/PicksStrip";
import RegistryEmpty from "./components/RegistryEmpty";
import { offeredSort, useInstallPath } from "./lib/installs";

const PARAM_KEYS = [
  "q",
  "kind",
  "tier",
  "noKey",
  "tag",
  "vendor",
  "sort",
  "page",
];
const PAGE_SIZE = 24;

function readParams(search) {
  return Object.fromEntries(
    PARAM_KEYS.map((key) => [key, search.get(key) || undefined]),
  );
}

// The Hub home (SPEC §7.3): search, filters and sorts as URL state, the
// maintainer picks on top, and the reviewed entries.
export default function HubBrowsePage() {
  const [search, setSearch] = useSearchParams();
  const installPath = useInstallPath();
  // An old ?sort=installs link reads the default order while nothing can be
  // installed (lib/installs.js).
  const params = { ...readParams(search) };
  params.sort = offeredSort(params.sort, installPath);
  const [query, setQuery] = useState(params.q || "");
  const filtered = Boolean(
    params.q ||
      params.kind ||
      params.tier ||
      params.noKey ||
      params.tag ||
      params.vendor,
  );

  useEffect(() => setQuery(params.q || ""), [params.q]);

  const update = (changes) => {
    const next = new URLSearchParams(search);
    for (const [key, value] of Object.entries(changes)) {
      if (value === null || value === undefined || value === "")
        next.delete(key);
      else next.set(key, value);
    }
    if (!("page" in changes)) next.delete("page");
    setSearch(next, { replace: true });
  };

  const entries = useHubEntries({ ...params, page_size: PAGE_SIZE });
  const picks = useHubPicks();
  const results = entries.data?.results || [];
  const pages = Math.max(1, Math.ceil((entries.data?.count || 0) / PAGE_SIZE));

  return (
    <HubFrame
      title="Omega Hub"
      documentTitle="Hub"
      description="Agents, teams, apps, workflows, MCP servers, skills and plugins for Omega. Every version is checked automatically and approved by a Future AGI maintainer."
      action={<DiscordLink variant="outlined" />}
      testId="hub-browse"
    >
      <HubNav />
      <Box
        component="form"
        role="search"
        onSubmit={(event) => {
          event.preventDefault();
          update({ q: query.trim() || null });
        }}
      >
        <TextField
          fullWidth
          size="small"
          placeholder="Search entries, tags and publishers"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          inputProps={{
            "aria-label": "Search the Hub",
            "data-testid": "hub-search",
          }}
          InputProps={{
            startAdornment: (
              <InputAdornment position="start">
                <HubIcon name="search" size={16} />
              </InputAdornment>
            ),
          }}
        />
      </Box>
      <FiltersBar
        params={params}
        facets={entries.data?.facets}
        onChange={update}
      />
      {!filtered && <PicksStrip picks={picks.data} />}
      {/* Without ?sort the server picks the order (New until anything is counted, HUB-24) and says which. */}
      <SortTabs
        value={params.sort || entries.data?.sort}
        onChange={(sort) => update({ sort })}
        disabled={Boolean(params.q)}
      />
      {entries.isLoading ? (
        <HubSkeletonCards />
      ) : entries.isError ? (
        <HubError error={entries.error} onRetry={() => entries.refetch()} />
      ) : results.length === 0 ? (
        filtered ? (
          <HubEmpty
            title="Nothing matches"
            description="Try another search, or clear a filter."
            action={
              <Button
                size="small"
                variant="outlined"
                onClick={() =>
                  setSearch(new URLSearchParams(), { replace: true })
                }
              >
                Clear filters
              </Button>
            }
          />
        ) : (
          <RegistryEmpty />
        )
      ) : (
        <>
          <Typography
            typography="s2"
            color="text.secondary"
            data-testid="hub-result-count"
          >
            {`${entries.data.count} entr${entries.data.count === 1 ? "y" : "ies"}`}
          </Typography>
          <EntryGrid entries={results} />
          {pages > 1 && (
            <Box sx={{ display: "flex", justifyContent: "center" }}>
              <Pagination
                count={pages}
                page={Number(params.page) || 1}
                onChange={(_, page) => update({ page: String(page) })}
                size="small"
              />
            </Box>
          )}
        </>
      )}
    </HubFrame>
  );
}
