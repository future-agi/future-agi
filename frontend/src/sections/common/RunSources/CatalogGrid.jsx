// Level 2 · the catalog behind a card (lane Ra MUST 2): group search,
// group headers, tiles (4 columns at 960 px, 3 at 720), arrow keys move,
// `Enter` picks. Harness lists what the user already has first.
import React, { useMemo, useRef, useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Chip,
  InputAdornment,
  Link,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import ConnectionPill from "src/sections/common/Connect/ConnectionPill";
import {
  GROUP_LABELS,
  catalogEntry,
  entriesFor,
  heldEntriesFor,
} from "./catalog";
import { LOADING_AVAILABILITY } from "./runSourceAvailability";
import CatalogTile from "./CatalogTile";

const norm = (s) => String(s ?? "").toLowerCase();
const matches = (e, q) =>
  !q ||
  [
    e.name,
    e.short,
    ...(e.aliases ?? []),
    ...(e.languages ?? []),
    e.instrument?.pip,
    e.instrument?.npm,
  ]
    .map(norm)
    .some((s) => s.includes(q));

/**
 * Groups `[{id, label, entries, saved?}]` for a card (pure): every source the
 * launch shows behind it, whatever the server's report says (the tiles grey
 * the ones it cannot run, CatalogTile), then its held sources (greyed with
 * "Not available in this release", never offered).
 */
export function catalogGroups(
  card,
  { query = "", savedHarness = [], lang = null } = {},
) {
  const q = norm(query).trim();
  // IT-R2 G1 P5: `All · Python · TypeScript` on Your code.
  const inLang = (e) =>
    !lang || (e.languages ?? ["python"]).map(norm).includes(lang);
  const by = (group) =>
    entriesFor(card).filter(
      (e) => e.group === group && matches(e, q) && inLang(e),
    );
  if (card === "code")
    return [
      {
        id: "python_auto",
        label: GROUP_LABELS.python_auto,
        entries: by("python_auto"),
      },
      {
        id: "ts_auto",
        label: GROUP_LABELS.ts_auto,
        // IT-R1 G1 P1: LangChain is listed once (its tile reads `Py · TS`).
        entries: by("ts_auto"),
      },
      {
        id: "own_tracing",
        label: GROUP_LABELS.own_tracing,
        entries: by("own_tracing"),
      },
    ].filter((g) => g.entries.length);
  if (card === "harness")
    // IT-R1 G1 P7: a connected worker is an option of its catalog entry
    // (`Claude Code · connected on alice@mbp-14`), never a second tile;
    // workers of a harness the catalog does not list keep their own group.
    return [
      {
        id: "harness",
        label: "Agent harnesses",
        hideStatus: true,
        entries: by("harness").map((e) => ({
          ...e,
          worker: savedHarness.find((s) => s.agent?.entry_id === e.id) ?? null,
        })),
      },
      {
        id: "workers",
        label: "Other harness workers",
        saved: savedHarness.filter(
          (s) =>
            !catalogEntry(s.agent?.entry_id) &&
            (!q || norm(s.agent.name).includes(q)),
        ),
      },
    ].filter((g) => g.entries?.length || g.saved?.length);
  if (card === "running")
    return [
      { id: "endpoint", label: "Endpoint", entries: by("endpoint") },
      {
        id: "running",
        label: "Already running",
        // Traces I already have, Upload results: listed, greyed.
        entries: [
          ...by("running"),
          ...heldEntriesFor(card).filter((e) => matches(e, q)),
        ],
      },
    ].filter((g) => g.entries.length);
  return [];
}

export default function CatalogGrid({
  card,
  query,
  onQuery,
  searchRef,
  onPickEntry,
  onPickSaved,
  savedHarness = [],
  environment = null,
  availability = LOADING_AVAILABILITY,
}) {
  const gridRef = useRef(null);
  const [lang, setLang] = useState(null);
  const groups = useMemo(
    () =>
      catalogGroups(card, {
        query,
        savedHarness,
        lang: card === "code" ? lang : null,
      }),
    [card, query, savedHarness, lang],
  );

  // Arrow keys move between tiles (roving focus over the rendered order).
  const onArrow = (e) => {
    if (!["ArrowRight", "ArrowLeft", "ArrowDown", "ArrowUp"].includes(e.key))
      return;
    const tiles = [
      ...(gridRef.current?.querySelectorAll('[data-tile="1"]') ?? []),
    ];
    const i = tiles.indexOf(document.activeElement);
    if (i === -1) return;
    e.preventDefault();
    const cols = Math.max(
      1,
      Math.round(
        (gridRef.current?.clientWidth ?? 720) /
          ((tiles[0]?.offsetWidth ?? 220) + 8),
      ),
    );
    const step = {
      ArrowRight: 1,
      ArrowLeft: -1,
      ArrowDown: cols,
      ArrowUp: -cols,
    }[e.key];
    tiles[Math.min(tiles.length - 1, Math.max(0, i + step))]?.focus();
  };

  const footer =
    card === "code"
      ? "Don't see yours? Pick Custom Python or Custom TypeScript. Any function that takes a row and returns an output works."
      : null;

  return (
    <Stack spacing={1.25} data-testid={`catalog-${card}`}>
      <Stack direction="row" alignItems="center" gap={1}>
        {card === "code" ? (
          <Stack
            direction="row"
            gap={0.5}
            role="group"
            aria-label="Language"
            data-testid="catalog-lang"
            sx={{ flexShrink: 0 }}
          >
            {[
              [null, "All"],
              ["python", "Python"],
              ["typescript", "TypeScript"],
            ].map(([v, label]) => (
              <Chip
                key={label}
                size="small"
                label={label}
                clickable
                color={lang === v ? "primary" : "default"}
                variant={lang === v ? "filled" : "outlined"}
                onClick={() => setLang(v)}
                aria-pressed={lang === v}
                sx={{ typography: "s2" }}
              />
            ))}
          </Stack>
        ) : null}
        <TextField
          inputRef={searchRef}
          size="small"
          value={query}
          onChange={(e) => onQuery(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              // The first tile that can be picked (refused ones are greyed).
              const first = gridRef.current?.querySelector(
                '[data-tile="1"]:not([aria-disabled="true"])',
              );
              if (first) {
                e.preventDefault();
                first.click();
              }
            }
            if (e.key === "ArrowDown") {
              e.preventDefault();
              gridRef.current?.querySelector('[data-tile="1"]')?.focus();
            }
          }}
          placeholder="Search this list"
          inputProps={{ "aria-label": "Search this list" }}
          InputProps={{
            startAdornment: (
              <InputAdornment position="start">
                <Iconify
                  icon="mdi:magnify"
                  width={18}
                  sx={{ color: "text.disabled" }}
                />
              </InputAdornment>
            ),
          }}
          sx={{ flex: 1, "& .MuiOutlinedInput-root": { height: 34 } }}
        />
      </Stack>
      <Stack spacing={1.25} ref={gridRef} onKeyDown={onArrow}>
        {groups.map((g) => (
          <Stack key={g.id} spacing={0.75}>
            <Typography
              typography="s3"
              color="text.secondary"
              fontWeight="fontWeightMedium"
            >
              {g.label}
            </Typography>
            <Box
              sx={{
                display: "grid",
                gridTemplateColumns: "repeat(auto-fill, minmax(208px, 1fr))",
                gap: 1,
              }}
            >
              {(g.saved ?? []).map((s) => {
                const entry =
                  catalogEntry(s.agent?.entry_id) ??
                  catalogEntry("custom_command");
                const name = s.agent.name;
                return (
                  <CatalogTile
                    key={s.id ?? s.agent?.id}
                    entry={{
                      ...entry,
                      name,
                      languages: [],
                      badge: null,
                    }}
                    onPick={() => onPickSaved?.(s)}
                    extra={
                      s.machine ? (
                        <ConnectionPill
                          machineId={s.machine.id}
                          agentId={s.agent?.id}
                          size="small"
                        />
                      ) : s.version ? (
                        <Typography
                          component="span"
                          typography="s3"
                          color="text.secondary"
                          noWrap
                        >
                          {s.version}
                        </Typography>
                      ) : null
                    }
                  />
                );
              })}
              {(g.entries ?? []).map((e) => (
                <CatalogTile
                  key={`${g.id}-${e.id}`}
                  entry={e}
                  environment={card === "harness" ? environment : null}
                  hideStatus={g.hideStatus}
                  availability={availability}
                  onPick={() => onPickEntry(e.id)}
                  extra={
                    e.worker ? (
                      <Link
                        component="button"
                        type="button"
                        typography="s3"
                        noWrap
                        data-testid={`tile-worker-${e.id}`}
                        onClick={(ev) => {
                          ev.stopPropagation();
                          onPickSaved?.(e.worker);
                        }}
                        title={`Run on ${e.worker.machine?.name ?? "your connected machine"} (live connection)`}
                        sx={{ minWidth: 0 }}
                      >
                        {`connected on ${e.worker.machine?.name ?? "your machine"}`}
                      </Link>
                    ) : null
                  }
                />
              ))}
            </Box>
          </Stack>
        ))}
        {!groups.length ? (
          <Typography typography="s2" color="text.secondary">
            {`No match for "${query.trim()}" here. Clear the search to see every way to run.`}
          </Typography>
        ) : null}
      </Stack>
      {footer ? (
        <Typography typography="s2" color="text.secondary">
          {footer}
        </Typography>
      ) : null}
    </Stack>
  );
}

CatalogGrid.propTypes = {
  card: PropTypes.string.isRequired,
  query: PropTypes.string,
  onQuery: PropTypes.func.isRequired,
  searchRef: PropTypes.object,
  onPickEntry: PropTypes.func.isRequired,
  onPickSaved: PropTypes.func,
  savedHarness: PropTypes.array,
  /** dataset.environment: harness tiles print Level 3's default command. */
  environment: PropTypes.object,
  /** The server's run-source report (useRunSourceAvailability). */
  availability: PropTypes.object,
};
