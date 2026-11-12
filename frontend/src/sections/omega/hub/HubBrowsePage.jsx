import React, { useEffect, useRef, useState } from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Collapse from "@mui/material/Collapse";
import InputAdornment from "@mui/material/InputAdornment";
import Link from "@mui/material/Link";
import Pagination from "@mui/material/Pagination";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";
import { useSearchParams } from "react-router-dom";

import { RouterLink } from "src/routes/components";
import { OMEGA_PART } from "src/sections/omega/shell/rollout";
import { useOmegaPart } from "src/sections/omega/shell/useOmegaPart";

import { DISCORD_URL, hubPaths } from "./constants";
import {
  useHubEntries,
  useHubEntry,
  useHubPicks,
  useHubRules,
  useHubTags,
} from "./api/queries";
import { EntryGrid, EntryGridSkeleton } from "./components/EntryCard";
import { TryOneNow } from "./components/EntryCommands";
import FiltersBar, { ActiveFilters, SortSelect } from "./components/FiltersBar";
import HubFrame, { hubPageQuery } from "./components/HubFrame";
import HubIcon from "./components/HubIcon";
import HubNav from "./components/HubNav";
import { HubEmpty, HubError } from "./components/HubStates";
import PicksStrip from "./components/PicksStrip";
import RegistryEmpty from "./components/RegistryEmpty";
import ReviewMeans from "./components/ReviewMeans";
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
const FILTER_KEYS = ["q", "kind", "tier", "noKey", "tag", "vendor"];
// 48 is a whole number of rows at 4, 3, 2 and 1 columns, and holds today's
// catalogue on one page (the API allows up to 60).
const PAGE_SIZE = 48;
// A search applies once typing pauses, as well as on Enter.
const SEARCH_PAUSE_MS = 350;

// The Hub's own first-run entry, offered first in "Try one now" when this
// registry lists it (useStarterEntry below says what is offered when not).
const STARTER = { publisher: "future-agi", name: "first-agent" };

const HEADLINE = "Working agents you can run in one command";
const HEADLINE_NO_CLI = "Working agents, teams and tools for Omega";
// What a visitor gets, first; how an entry got here is one quiet line under
// it, with the rules file's "What review means" behind a toggle.
const BROWSE_DESCRIPTION =
  "Ready-to-run agents, teams, apps, workflows, MCP servers, skills and plugins for Omega. Open one to see what it does and what it needs.";
const TRUST_LINE =
  "Every version is checked automatically and approved by a Future AGI maintainer.";
const SEARCH_COVERS =
  "Search looks at each entry's title, tagline, tags, publisher and README.";
const TRY_TAGS = "Try a shorter word, or one of these tags.";
const TRY_WORD = "Try a shorter word.";
const TRY_FEWER = "Try a shorter word, or remove a filter.";
const ONE_FILTER =
  "No listed entry matches the filter shown above. Remove it to see every entry.";
const ALL_FILTERS =
  "No listed entry matches every filter shown above. Remove one, or clear them all.";
const MEANS_ID = "hub-review-means-browse";
// With a first entry to try, the headline shares its row with that box.
const HERO_WITH_TRY = "minmax(0, 1fr) minmax(320px, 400px)";

const KBD_SX = {
  px: 0.75,
  borderRadius: 0.5,
  border: "1px solid",
  borderColor: "divider",
  color: "text.secondary",
  fontFamily: "inherit",
  typography: "s2",
  lineHeight: "18px",
};

// The "/" hint is left out on a page as narrow as a phone's, which has no
// key to press.
const HINT_SX = {
  [hubPageQuery("(max-width: 559.98px)")]: { display: "none" },
};

function readParams(search) {
  return Object.fromEntries(
    PARAM_KEYS.map((key) => [key, search.get(key) || undefined]),
  );
}

const isKeylessAgent = (entry) => entry.kind === "agent" && entry.noKeyNeeded;

// The entry "Try one now" offers: the Hub's own first agent when this
// registry lists it, else this week's first maintainer pick, else the first
// listed agent that runs without a key. The last two are entries the page
// has already loaded, with their real commands, so no other request is made.
// `listed` is the unfiltered list (empty while a search or filter is on, and
// while the next answer loads).
// Once one is chosen it stays for the visit, so the box never changes or
// goes away as the list is searched or filtered.
function useStarterEntry({ starter, picks, listed }) {
  const chosen = useRef(null);
  if (!chosen.current) {
    if (starter.data?.ref) {
      chosen.current = starter.data;
    } else if (!starter.isLoading && !picks.isLoading) {
      const pick = picks.data?.find((entry) => entry?.ref);
      chosen.current = pick || listed.find(isKeylessAgent) || null;
    }
  }
  return chosen.current;
}

// The trust sentence, once, and "What review means" from GET /rules, word
// for word (ReviewMeans). Escape closes it and leaves focus on the toggle.
function TrustLine() {
  const rules = useHubRules();
  const [open, setOpen] = useState(false);
  const toggle = useRef(null);
  const hasMeans = Boolean(rules.data?.reviewMeans?.length);
  const onKeyDown = (event) => {
    if (event.key !== "Escape" || !open) return;
    setOpen(false);
    toggle.current?.focus();
  };
  return (
    <Box onKeyDown={onKeyDown} sx={{ mt: 1.25 }}>
      <Box
        sx={{
          display: "flex",
          alignItems: "center",
          flexWrap: "wrap",
          columnGap: 0.5,
        }}
      >
        <Typography typography="s1" color="text.secondary">
          {TRUST_LINE}
        </Typography>
        {hasMeans && (
          <Button
            ref={toggle}
            size="small"
            variant="text"
            aria-expanded={open ? "true" : "false"}
            aria-controls={open ? MEANS_ID : undefined}
            onClick={() => setOpen((value) => !value)}
            sx={{ minWidth: 0, px: 0.5, py: 0, typography: "s1" }}
          >
            What review means
          </Button>
        )}
      </Box>
      {hasMeans && (
        <Collapse in={open} unmountOnExit id={MEANS_ID}>
          <Box
            sx={{
              mt: 1,
              p: 1.5,
              maxWidth: 720,
              border: "1px solid",
              borderColor: "divider",
              borderRadius: 1,
              bgcolor: "background.neutral",
            }}
          >
            <ReviewMeans rules={rules.data} dense />
          </Box>
        </Collapse>
      )}
    </Box>
  );
}

// The no-match state's words: what was asked for, what to try next, and the
// name of the way back. `others` are the filters set besides the search.
function noMatchCopy(query, others, tagCount) {
  if (query) {
    let next = TRY_WORD;
    if (others.length) next = TRY_FEWER;
    else if (tagCount) next = TRY_TAGS;
    return {
      title: `Nothing matches “${query}”`,
      description: `${SEARCH_COVERS} ${next}`,
      clear: others.length ? "Clear filters" : "Clear search",
    };
  }
  if (others.length === 1) {
    return {
      title: "Nothing matches this filter",
      description: ONE_FILTER,
      clear: "Clear filter",
    };
  }
  return {
    title: "Nothing matches these filters",
    description: ALL_FILTERS,
    clear: "Clear filters",
  };
}

// No entry matches: what was asked for, the way back, and the way to add
// what is missing. A search on its own also gets a few of the Hub's own tags
// to try (GET /tags). With another filter set, a tag would keep that filter
// and lead to another empty page, so none are offered; the chips above the
// list remove one filter at a time.
function NoMatch({ params, onClear, onTag }) {
  const tags = useHubTags();
  const query = params.q || "";
  const others = FILTER_KEYS.filter((key) => key !== "q" && params[key]);
  const suggest = Boolean(query) && others.length === 0;
  const shown = suggest
    ? (tags.data || [])
        .map((row) => row.tag)
        .filter((tag) => tag && tag !== query)
        .slice(0, 5)
    : [];
  const copy = noMatchCopy(query, others, shown.length);
  return (
    <HubEmpty
      icon={null}
      titleComponent="h3"
      title={copy.title}
      description={copy.description}
      action={
        <Box
          sx={{
            display: "flex",
            flexDirection: "column",
            alignItems: "center",
            gap: 1.5,
          }}
        >
          {shown.length > 0 && (
            <Box
              role="group"
              aria-label="Tags to search for"
              sx={{
                display: "flex",
                flexWrap: "wrap",
                justifyContent: "center",
                gap: 0.75,
              }}
            >
              {shown.map((tag) => (
                <Button
                  key={tag}
                  size="small"
                  variant="outlined"
                  color="inherit"
                  onClick={() => onTag(tag)}
                  sx={{ borderColor: "divider" }}
                >
                  {tag}
                </Button>
              ))}
            </Box>
          )}
          <Button size="small" variant="outlined" onClick={onClear}>
            {copy.clear}
          </Button>
          <Typography typography="s2" color="text.secondary">
            {"Not on the Hub? "}
            <Link component={RouterLink} href={hubPaths.publish}>
              Publish your own
            </Link>
          </Typography>
        </Box>
      }
    />
  );
}

NoMatch.propTypes = {
  params: PropTypes.object.isRequired,
  onClear: PropTypes.func.isRequired,
  onTag: PropTypes.func.isRequired,
};

// The Hub home (SPEC §7.3): what you get and one command to try, then one
// control row (search, kind, trust, "Runs without a key", sort) with the
// count and the active filters under it, the maintainer picks ("Start
// here"), and the other entries, by the page's own width.
export default function HubBrowsePage() {
  const [search, setSearch] = useSearchParams();
  const cliReleased = useOmegaPart(OMEGA_PART.HUB_CLI);
  const installPath = useInstallPath();
  const searchBox = useRef(null);
  // An old ?sort=installs link reads the default order while nothing can be
  // installed (lib/installs.js).
  const params = { ...readParams(search) };
  params.sort = offeredSort(params.sort, installPath);
  const [query, setQuery] = useState(params.q || "");
  const filtered = FILTER_KEYS.some((key) => Boolean(params[key]));

  // The box follows the URL (a shared link, Clear all) but keeps what is
  // being typed, a trailing space included.
  useEffect(
    () =>
      setQuery((typed) =>
        typed.trim() === (params.q || "") ? typed : params.q || "",
      ),
    [params.q],
  );

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
  const latestUpdate = useRef(update);
  latestUpdate.current = update;
  useEffect(() => {
    const typed = query.trim();
    if (typed === (params.q || "")) return undefined;
    const timer = setTimeout(
      () => latestUpdate.current({ q: typed || null }),
      SEARCH_PAUSE_MS,
    );
    return () => clearTimeout(timer);
  }, [query, params.q]);

  // Every filter off; the chosen sort stays.
  const clearAll = () =>
    update(Object.fromEntries(FILTER_KEYS.map((key) => [key, null])));
  const focusSearch = () => searchBox.current?.focus();
  const searchFor = (text) => {
    setQuery(text);
    update({ q: text });
    focusSearch();
  };

  const entries = useHubEntries({ ...params, page_size: PAGE_SIZE });
  const picks = useHubPicks();
  const starter = useHubEntry(STARTER.publisher, STARTER.name);
  const results = entries.data?.results || [];
  const pages = Math.max(1, Math.ceil((entries.data?.count || 0) / PAGE_SIZE));
  // False while the list query shows the last answer as a placeholder for
  // the next one (keepPreviousData).
  const settled = !entries.isPlaceholderData;
  const starterEntry = useStarterEntry({
    starter,
    picks,
    listed: filtered || !settled ? [] : results,
  });
  // The entry to try is undecided until the first agent's answer and the
  // picks are in, and on the home the first list too (the last fallback
  // reads it).
  const starterPending =
    !starterEntry &&
    (starter.isLoading || picks.isLoading || (!filtered && entries.isLoading));
  const starterRef = starterEntry?.ref;
  // Start here shows this week's picks, without the entry Try one now
  // offers, and the grid below leaves out both, so on the home no entry is
  // drawn twice, whatever the registry holds. A search or a filter lists
  // every match the server counted.
  const pickList = filtered
    ? []
    : (picks.data || []).filter((pick) => pick.ref !== starterRef);
  const starterPick = picks.data?.find((pick) => pick.ref === starterRef);
  const drawnAbove = new Set(pickList.map((pick) => pick.ref));
  if (!filtered && starterRef) drawnAbove.add(starterRef);
  const rest = results.filter((entry) => !drawnAbove.has(entry.ref));
  const heroColumns =
    starterEntry || starterPending ? HERO_WITH_TRY : "minmax(0, 1fr)";
  // Until the picks and the entry to try are known, the list and Start here
  // wait with them and the list has no heading: no card is drawn and then
  // taken out, and "All entries" never turns into "More entries" in front
  // of the visitor.
  const picksPending = !filtered && picks.isLoading;
  const listWaits = picksPending || starterPending;
  let listTitle = "All entries";
  if (filtered) listTitle = "Results";
  else if (pickList.length) listTitle = "More entries";

  // The last answer is never shown as the next one's: the count waits, and
  // the last cards stay, dimmed and marked busy, only while the visitor
  // refines the same view. Coming from the home to a search (or back), or
  // from an empty answer, the list shows placeholders, so no empty state,
  // count or card speaks for a search whose answer has not arrived.
  const shownView = useRef(filtered);
  if (settled && entries.data) shownView.current = filtered;
  const newView = !settled && shownView.current !== filtered;

  const searchField = (
    <TextField
      size="small"
      type="search"
      placeholder="Search entries, tags and publishers"
      value={query}
      onChange={(event) => setQuery(event.target.value)}
      onKeyDown={(event) => {
        if (event.key !== "Escape" || !query) return;
        event.preventDefault();
        setQuery("");
        update({ q: null });
      }}
      inputRef={searchBox}
      inputProps={{
        "aria-label": "Search the Hub",
        "aria-keyshortcuts": "/",
        "data-testid": "hub-search",
      }}
      InputProps={{
        startAdornment: (
          <InputAdornment position="start">
            <HubIcon name="search" size={16} />
          </InputAdornment>
        ),
        endAdornment: (
          <InputAdornment position="end" sx={HINT_SX}>
            <Box component="kbd" aria-hidden="true" sx={KBD_SX}>
              /
            </Box>
          </InputAdornment>
        ),
      }}
      sx={{
        flex: "1 1 240px",
        minWidth: 0,
        // Escape and the search chip clear the field, so the browser's own
        // clear button is left out; the "/" hint steps aside while the field
        // has focus, where a "/" is typed as text.
        "& input::-webkit-search-cancel-button": {
          WebkitAppearance: "none",
          display: "none",
        },
        "&:focus-within kbd": { visibility: "hidden" },
        // On a narrow page the search takes the first line and the other
        // controls share the next, rather than leaving one on a line alone.
        [hubPageQuery("(max-width: 879.98px)")]: { flexBasis: "100%" },
      }}
    />
  );

  let list = null;
  let stale = false;
  if (entries.isLoading || listWaits) {
    list = <EntryGridSkeleton />;
  } else if (entries.isError) {
    list = (
      <HubError
        title="The Hub entries did not load"
        error={entries.error}
        onRetry={() => entries.refetch()}
      />
    );
  } else if (!settled && (newView || results.length === 0)) {
    list = <EntryGridSkeleton />;
  } else if (filtered && results.length === 0) {
    list = <NoMatch params={params} onClear={clearAll} onTag={searchFor} />;
  } else if (rest.length > 0) {
    list = <EntryGrid entries={rest} />;
    stale = !settled;
  }
  const registryEmpty =
    settled &&
    !filtered &&
    !entries.isLoading &&
    !entries.isError &&
    !results.length;
  const listHeading = !listWaits && !entries.isError && Boolean(list);

  return (
    <HubFrame documentTitle="Hub" maxWidth={1400} testId="hub-browse">
      <HubNav />
      <Box
        component="section"
        aria-labelledby="hub-browse-title"
        sx={{
          display: "grid",
          gap: 2,
          alignItems: "start",
          gridTemplateColumns: "minmax(0, 1fr)",
          pt: 1,
          [hubPageQuery("(min-width: 960px)")]: {
            gridTemplateColumns: heroColumns,
            columnGap: 4,
          },
        }}
      >
        <Box sx={{ minWidth: 0 }}>
          <Typography
            id="hub-browse-title"
            component="h1"
            typography="l3"
            fontWeight="fontWeightSemiBold"
            color="text.primary"
          >
            {cliReleased ? HEADLINE : HEADLINE_NO_CLI}
          </Typography>
          <Typography
            typography="s1_2"
            color="text.secondary"
            sx={{ mt: 0.75, maxWidth: 640 }}
          >
            {BROWSE_DESCRIPTION}
          </Typography>
          <TrustLine />
        </Box>
        <TryOneNow
          entry={starterEntry}
          why={starterPick?.pick?.why}
          loading={starterPending}
        />
      </Box>
      <Box
        component="form"
        role="search"
        aria-label="Find entries"
        onSubmit={(event) => {
          event.preventDefault();
          update({ q: query.trim() || null });
        }}
        sx={{ display: "flex", flexDirection: "column", gap: 1, mt: 1 }}
      >
        <FiltersBar
          params={params}
          facets={entries.data?.facets}
          onChange={update}
          search={searchField}
          end={
            /* Without ?sort the server picks the order (New until anything is counted) and says which. */
            <SortSelect
              value={params.sort || entries.data?.sort}
              onChange={(sort) => update({ sort })}
              relevance={Boolean(params.q)}
            />
          }
        />
        <ActiveFilters
          params={params}
          count={entries.isError || !settled ? undefined : entries.data?.count}
          pending={!settled}
          onChange={update}
          onClearAll={clearAll}
          onDone={focusSearch}
        />
      </Box>
      {!filtered && (
        <PicksStrip
          picks={pickList}
          loading={picks.isLoading || starterPending}
          error={picks.isError ? picks.error : null}
          onRetry={() => picks.refetch()}
        />
      )}
      {registryEmpty ? (
        <RegistryEmpty />
      ) : (
        <Box
          component="section"
          aria-labelledby={listHeading ? "hub-list-title" : undefined}
          aria-busy={stale || undefined}
          data-testid="hub-list"
          sx={{ display: "flex", flexDirection: "column", gap: 1.5, mt: 1 }}
        >
          {listHeading && (
            <Typography
              id="hub-list-title"
              component="h2"
              typography="m2"
              fontWeight="fontWeightSemiBold"
              color="text.primary"
            >
              {listTitle}
            </Typography>
          )}
          {list && (
            <Box sx={{ opacity: stale ? 0.6 : 1, transition: "opacity 120ms" }}>
              {list}
            </Box>
          )}
          {!listWaits && pages > 1 && (
            <Box sx={{ display: "flex", justifyContent: "center" }}>
              <Pagination
                count={pages}
                page={Number(params.page) || 1}
                onChange={(_, page) => update({ page: String(page) })}
                size="small"
              />
            </Box>
          )}
        </Box>
      )}
      <Typography typography="s1" color="text.secondary" sx={{ mt: 1 }}>
        {"Ask about an entry or about publishing on the "}
        <Link
          href={DISCORD_URL}
          target="_blank"
          rel="noopener noreferrer"
          data-testid="hub-discord-link"
        >
          Future AGI Discord
        </Link>
        {"."}
      </Typography>
    </HubFrame>
  );
}
