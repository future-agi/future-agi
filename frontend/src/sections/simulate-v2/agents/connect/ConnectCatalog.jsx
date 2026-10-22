import PropTypes from "prop-types";
import { useEffect, useMemo, useRef, useState } from "react";
import { alpha } from "@mui/material/styles";
import { Box, Button, ButtonBase, InputBase, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { LogoTile } from "../../environments/LandingBits";
import { PANEL_SX, focusRing } from "../../environments/landingStyles";
import { Detection } from "../../environments/ConnectDirectory";
import ChipCard from "../../environments/ChipCard";
import { highlightOf, searchCatalog } from "./catalogSearch";

/**
 * Every way to connect an agent, as one searchable list.
 *
 * One box does both jobs: type a name and the list narrows; paste a repo,
 * an ID, a URL or a prompt and it says what that is and opens the right
 * setup — with the full list still below, in case it read the paste wrong.
 * Chips narrow by where the agent lives. Nothing is folded behind
 * "N more" — the whole list scrolls, and every row says what it is and the
 * step it starts, with Connect beside it; how a fix comes back is said once
 * per group.
 */
export default function ConnectCatalog({ groups, pickedId, onPick, detect, onPaste, emptyHint, onFallback, compact = false, showAll = true, pasteHint = "a repo, an ID, a URL or a prompt", ready = {}, onBuild = null }) {
  const [query, setQuery] = useState("");
  /* Without an All chip, it opens on the first group. */
  const [filter, setFilter] = useState(showAll ? "all" : groups[0]?.id);
  const inputRef = useRef(null);

  const items = useMemo(() => groups.flatMap((g) => g.items.map((it) => ({ ...it, group: g }))), [groups]);
  const total = items.length;
  /* A paste is recognised; a name being typed is just a search. */
  const found = useMemo(() => {
    const hit = detect && query.trim() ? detect(query) : null;
    return hit && hit.kind !== "unknown" ? hit : null;
  }, [detect, query]);
  const canPaste = !!found && !found.blocked && !!found.choice;
  const meta = found && { name: found.label, detail: found.detail, logo: found.logo, icon: found.icon };
  const paste = (way = found) => {
    if (canPaste) onPaste(way.choice, way.prefill, meta);
  };

  const q = query.trim().toLowerCase();
  /* A recognised paste isn't a search — the list stays whole under it. */
  const searching = !!q && !found;
  /* Forgiving: case, spaces and punctuation don't matter, and keywords count
     (LangChain finds LangGraph) — best matches first (catalogSearch). */
  const matches = useMemo(() => (searching ? searchCatalog(items, query) : []), [items, query, searching]);
  /* Results stay under the group they belong to, in the tabs' order. */
  const resultGroups = useMemo(() => groups
    .map((g) => ({ ...g, id: `results-${g.id}`, title: g.title, count: 0, items: matches.filter((it) => it.group.id === g.id) }))
    .filter((g) => g.items.length), [groups, matches]);
  /* The order arrow keys walk: the results as shown. */
  const ordered = useMemo(() => resultGroups.flatMap((g) => g.items), [resultGroups]);
  const [active, setActive] = useState(0);
  useEffect(() => { setActive(0); }, [query]);
  /* "/" anywhere on the page jumps to the box. */
  useEffect(() => {
    const onSlash = (e) => {
      if (e.key !== "/" || e.metaKey || e.ctrlKey || e.altKey) return;
      const el = document.activeElement;
      if (el && (el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.isContentEditable)) return;
      e.preventDefault();
      inputRef.current?.focus();
    };
    window.addEventListener("keydown", onSlash);
    return () => window.removeEventListener("keydown", onSlash);
  }, []);

  /* What the list shows: search results by group; or one chip's items; or every group. */
  const sections = useMemo(() => {
    if (searching) return resultGroups;
    if (filter !== "all") return groups.filter((g) => g.id === filter).map((g) => ({ ...g, items: g.items.map((it) => ({ ...it, group: g })) }));
    /* All is every group, each way in listed once. */
    return groups.map((g) => ({ ...g, items: g.items.map((it) => ({ ...it, group: g })) }));
  }, [searching, resultGroups, filter, groups]);

  const onKey = (e) => {
    if (e.key === "Escape") { setQuery(""); return; }
    /* Up and down walk the results; Enter connects the one marked. */
    if (searching && ordered.length && (e.key === "ArrowDown" || e.key === "ArrowUp")) {
      e.preventDefault();
      setActive((i) => (e.key === "ArrowDown" ? Math.min(i + 1, ordered.length - 1) : Math.max(i - 1, 0)));
      return;
    }
    if (e.key !== "Enter") return;
    if (canPaste) paste();
    else if (ordered[active]) onPick(ordered[active]);
  };

  return (
    <Stack spacing={1.5}>
      {/* ── the one box ── */}
      <Box
        sx={{
          display: "flex", alignItems: "center", gap: 1, px: 1.5, height: 44,
          border: "1px solid", borderRadius: 1,
          borderColor: (t) => (found?.warn ? alpha(t.palette.error.main, 0.55) : t.palette.divider),
          bgcolor: "background.paper",
          transition: "border-color .12s ease",
          "&:focus-within": { borderColor: (t) => (found?.warn ? t.palette.error.main : alpha(t.palette.text.primary, 0.45)) },
        }}
      >
        <Iconify icon="solar:magnifer-linear" width={16} sx={{ color: "text.disabled", flexShrink: 0 }} />
        <InputBase
          inputRef={inputRef}
          fullWidth
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={onKey}
          placeholder={detect ? `Search ${total} ways to connect — or paste ${pasteHint}` : `Search ${total} ways to connect`}
          endAdornment={!query ? <Box component="kbd" sx={{ px: 0.75, py: 0.125, mr: 0.5, borderRadius: 0.5, border: "1px solid", borderColor: "divider", typography: "s3", color: "text.disabled", fontFamily: "inherit" }}>/</Box> : null}
          inputProps={{ "aria-label": detect ? "Search ways to connect, or paste a repository, ID, URL or prompt" : "Search ways to connect", spellCheck: false }}
          sx={{
            typography: "s1",
            fontFamily: found && found.kind !== "describe" ? "ui-monospace, SFMono-Regular, Menlo, monospace" : undefined,
            "& input::placeholder": { color: "text.disabled", opacity: 1 },
          }}
        />
        {query && (
          <ButtonBase onClick={() => { setQuery(""); inputRef.current?.focus(); }} aria-label="Clear" sx={{ borderRadius: 0.75, p: 0.25, color: "text.disabled", "&:hover": { color: "text.primary" }, "&.Mui-focusVisible": focusRing }}>
            <Iconify icon="eva:close-fill" width={16} />
          </ButtonBase>
        )}
      </Box>

      {/* ── what a paste was read as ── */}
      {found && (
        <Stack
          direction={{ xs: "column", sm: "row" }} alignItems={{ sm: "center" }} spacing={1.5}
          sx={{ ...PANEL_SX, px: 2, py: 1.5 }}
        >
          <Box flex={1} minWidth={0}><Detection found={found} /></Box>
          {canPaste && (
            <Stack direction="row" spacing={1} sx={{ flexShrink: 0 }}>
              {(found.alternatives || []).map((alt) => (
                <Button
                  key={alt.label} variant="outlined" onClick={() => paste(alt)}
                  sx={{ typography: "s2", fontWeight: 600, color: "text.primary", borderColor: "divider" }}
                >
                  {alt.label}
                </Button>
              ))}
              <Button
                variant="contained" color="primary" onClick={() => paste()}
                endIcon={<Iconify icon="solar:arrow-right-linear" width={14} />}
                sx={{ typography: "s2", fontWeight: 700 }}
              >
                {found.action || "Continue"}
              </Button>
            </Stack>
          )}
        </Stack>
      )}

      {/* ── the chips — under a paste, the way out if it was read wrong ── */}
      {!searching && (
        <Stack spacing={0.75}>
          {found && (
            <Typography sx={{ typography: "s2", color: "text.secondary" }}>
              {found.blocked ? "Pick where the agent runs:" : "Not what you meant? Pick where it runs:"}
            </Typography>
          )}
          <Stack direction={{ xs: "column", md: "row" }} alignItems={{ md: "center" }} spacing={{ xs: 1, md: 2 }}>
            <Box sx={{ display: "flex", flexWrap: "wrap", gap: 0.75, flex: 1, minWidth: 0 }}>
              {showAll && <ChipCard label={<Count label="All" n={total} />} on={filter === "all"} onClick={() => setFilter("all")} />}
              {groups.map((g) => (
                <ChipCard key={g.id} label={<Count label={g.title} n={g.items.length} />} on={filter === g.id} onClick={() => setFilter(g.id)} />
              ))}
            </Box>
            {/* Nothing to connect: the agent can start here instead. */}
            {onBuild && !found && (
              <Typography sx={{ typography: "s2", color: "text.secondary", flexShrink: 0 }}>
                {"No agent yet? "}
                <ButtonBase
                  onClick={onBuild}
                  sx={{ typography: "s2", fontWeight: 700, color: "text.primary", verticalAlign: "baseline", borderRadius: 0.5, "&:hover": { textDecoration: "underline" }, "&.Mui-focusVisible": focusRing }}
                >
                  Build one in Future AGI
                </ButtonBase>
                {" — write its prompt, pick a model, add tools."}
              </Typography>
            )}
          </Stack>
        </Stack>
      )}

      {/* ── the list: one panel per part, each with its own header ── */}
      {searching && !matches.length ? (
        <Box sx={{ ...PANEL_SX, overflow: "hidden" }}>
          <Stack alignItems="center" spacing={1} sx={{ py: 5, px: 2, textAlign: "center" }}>
            <Typography sx={{ typography: "s1", fontWeight: 600 }}>{`Nothing matches “${query.trim()}”`}</Typography>
            {emptyHint && <Typography sx={{ typography: "s2", color: "text.secondary" }}>{emptyHint}</Typography>}
            {onFallback && (
              <Button size="small" variant="outlined" onClick={onFallback} sx={{ typography: "s2", fontWeight: 600, color: "text.primary", borderColor: "divider", mt: 0.5 }}>
                Connect an agent endpoint
              </Button>
            )}
          </Stack>
        </Box>
      ) : sections.map((sec) => {
        const parted = sec.sections && !searching;
        const grid = (rows, columns, rowProps = () => ({})) => (
          <Box
            sx={(t) => ({
              display: "grid", gridTemplateColumns: "1fr", px: 1, py: 1,
              [t.breakpoints.up(compact ? "lg" : "md")]: { gridTemplateColumns: `repeat(${columns || 2}, 1fr)`, columnGap: 4 },
            })}
          >
            {rows.map((it) => (
              <CatalogRow key={`${sec.id}-${it.id}`} item={it} ready={ready[it.id]} selected={pickedId === it.id} onPick={onPick} {...rowProps(it)} />
            ))}
          </Box>
        );
        /* The tab names the group, and each panel says how it comes back —
           nothing sits between the tabs and the panels. A group in parts
           (Code: a repository, our SDK, or neither) is a panel per part. */
        if (parted) {
          return (
            <Stack key={sec.id} spacing={1.25}>
              {Object.entries(sec.sections).map(([key, part]) => {
                const rows = sec.items.filter((it) => it.section === key);
                if (!rows.length) return null;
                return (
                  <PartPanel key={key} title={part.title} reach={part.reach}>
                    {part.dropzone
                      ? <DropZone item={rows[0]} onPick={onPick} />
                      : grid(rows, part.columns, (it) => ({ sub: it.short, single: !!it.short && !ready[it.id] }))}
                  </PartPanel>
                );
              })}
            </Stack>
          );
        }
        const rows = grid(sec.items, 2, (it) => ({ active: searching && ordered[active]?.id === it.id, query: searching ? query : "" }));
        /* Searching spans the groups, so each result group is named. */
        if (searching) {
          return (
            <PartPanel
              key={sec.id} heading reach={sec.reach}
              title={<>{sec.title}<Box component="span" sx={{ fontWeight: 400, color: "text.secondary", ml: 0.75 }}>{sec.items.length}</Box></>}
            >
              {rows}
            </PartPanel>
          );
        }
        return <Box key={sec.id} sx={{ ...PANEL_SX, overflow: "hidden" }}>{rows}</Box>;
      })}
    </Stack>
  );
}

ConnectCatalog.propTypes = {
  /* Rows whose account is already connected, by row id: { line } — said on the row (connectedAccounts.js). */
  ready: PropTypes.object,
  /* Opens Build in Future AGI — for someone with no agent to connect. */
  onBuild: PropTypes.func,
  groups: PropTypes.array.isRequired,
  pickedId: PropTypes.string,
  onPick: PropTypes.func.isRequired,
  /* Reads a paste (repo, ID, URL, prompt) — `onPaste(choice, prefill, meta)` opens its setup. */
  detect: PropTypes.func,
  onPaste: PropTypes.func,
  emptyHint: PropTypes.node,
  /* Offered when a search finds nothing. */
  onFallback: PropTypes.func,
  /* Inside a drawer: two columns only on wide screens. */
  compact: PropTypes.bool,
  /* An All chip listing every group at once — off where the list shares a drawer with other ways in. */
  showAll: PropTypes.bool,
  /* What can be pasted, as the placeholder says it. */
  pasteHint: PropTypes.string,
};

function Count({ label, n }) {
  return (
    <>
      {`${label} `}
      <Box component="span" sx={{ fontWeight: 400, color: "text.secondary", fontVariantNumeric: "tabular-nums" }}>{n}</Box>
    </>
  );
}
Count.propTypes = { label: PropTypes.string, n: PropTypes.number };

/* One part of the list: its name and what it needs in a header row, then its rows. */
function PartPanel({ title, reach, heading = false, children }) {
  return (
    <Box sx={{ ...PANEL_SX, overflow: "hidden" }}>
      <Stack
        direction={{ xs: "column", sm: "row" }} alignItems={{ sm: "baseline" }} spacing={{ xs: 0.25, sm: 1.25 }}
        sx={{ px: 2, py: 1.25, borderBottom: "1px solid", borderColor: "divider" }}
      >
        <Typography component={heading ? "h3" : "h4"} sx={{ typography: heading ? "s1" : "s2", fontWeight: 700, flexShrink: 0 }}>{title}</Typography>
        {reach && <Typography sx={{ typography: heading ? "s2" : "s3", color: "text.secondary", minWidth: 0 }}>{reach}</Typography>}
      </Stack>
      {children}
    </Box>
  );
}
PartPanel.propTypes = { title: PropTypes.node, reach: PropTypes.string, heading: PropTypes.bool, children: PropTypes.node };

/*
  Upload, as what it is: a place to drop a folder or a .zip. Dropping (or
  Upload) opens the upload setup with the files already in it.
*/
function DropZone({ item, onPick }) {
  const [over, setOver] = useState(false);
  const inputRef = useRef(null);
  const take = (list) => onPick({ ...item, initial: { ...(item.initial || {}), files: Array.from(list || []).map((f) => ({ name: f.name, size: f.size })) } });
  return (
    <Box
      onDragOver={(e) => { e.preventDefault(); setOver(true); }}
      onDragLeave={() => setOver(false)}
      onDrop={(e) => { e.preventDefault(); setOver(false); if (e.dataTransfer?.files?.length) take(e.dataTransfer.files); }}
      sx={{
        /* The panel's body — the panel is its outline; dragging over it tints. */
        px: 2, py: 1.5,
        display: "flex", alignItems: "center", gap: 1.5,
        outline: over ? "1px dashed" : "none", outlineColor: "text.primary", outlineOffset: -6,
        bgcolor: (t) => (over ? alpha(t.palette.text.primary, 0.05) : "transparent"),
        transition: "background-color .12s ease",
      }}
    >
      <LogoTile icon={item.icon} />
      <Box flex={1} minWidth={0}>
        <Typography sx={{ typography: "s2_1", fontWeight: 600 }}>{over ? "Drop to upload" : "Drop a folder or a .zip here"}</Typography>
        <Typography sx={{ typography: "s3", color: "text.secondary" }}>Up to 200 MB · built in our sandbox for every run</Typography>
      </Box>
      <input ref={inputRef} type="file" hidden multiple accept=".zip,.tar,.gz,.tgz,.py,.ts,.js,.json,.yaml,.yml,.md" onChange={(e) => { if (e.target.files?.length) take(e.target.files); e.target.value = ""; }} />
      <Button
        size="small" variant="outlined" onClick={() => inputRef.current?.click()}
        startIcon={<Iconify icon="solar:upload-minimalistic-linear" width={14} />}
        sx={{ typography: "s3", fontWeight: 700, color: "text.primary", borderColor: "divider", height: 28, flexShrink: 0, "&:hover": { borderColor: "text.primary", bgcolor: "transparent" } }}
      >
        Upload
      </Button>
    </Box>
  );
}
DropZone.propTypes = { item: PropTypes.object.isRequired, onPick: PropTypes.func.isRequired };

/* One way in: what it is, the step it starts, and Connect. */
function CatalogRow({ item, selected, onPick, showGroup, sub, single = false, ready = null, active = false, query = "" }) {
  const inert = !!(item.soon || item.later);
  const rowRef = useRef(null);
  /* The row the arrow keys are on stays in view. */
  useEffect(() => { if (active) rowRef.current?.scrollIntoView({ block: "nearest" }); }, [active]);
  const hit = highlightOf(item.name, query);
  return (
    <ButtonBase
      ref={rowRef}
      disabled={inert}
      onClick={() => onPick(item)}
      aria-pressed={selected}
      data-active={active || undefined}
      sx={{
        width: "100%", justifyContent: "flex-start", textAlign: "left",
        gap: 1.25, px: 1, py: single ? 0.75 : 1, borderRadius: 1,
        bgcolor: (t) => (selected ? alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.08 : 0.05) : "transparent"),
        transition: "background-color .12s ease",
        "&:hover": { bgcolor: (t) => alpha(t.palette.text.primary, selected ? 0.09 : 0.045) },
        "&:hover .cat-connect, &.Mui-focusVisible .cat-connect, &[data-active] .cat-connect": { borderColor: "text.primary" },
        "&[data-active]": { bgcolor: (t) => alpha(t.palette.text.primary, 0.06) },
        "&.Mui-focusVisible": focusRing,
        "&.Mui-disabled": { opacity: 0.55 },
      }}
    >
      <LogoTile logo={item.logo} icon={item.icon} muted={inert} />
      <Box sx={{ flex: 1, minWidth: 0 }}>
        <Typography noWrap sx={{ typography: "s2_1", fontWeight: 600, color: inert ? "text.secondary" : "text.primary" }}>
          {hit ? (
            <>
              {item.name.slice(0, hit[0])}
              <Box component="mark" sx={{ bgcolor: (t) => alpha(t.palette.text.primary, 0.14), color: "inherit", borderRadius: 0.25 }}>{item.name.slice(hit[0], hit[1])}</Box>
              {item.name.slice(hit[1])}
            </>
          ) : item.name}
        </Typography>
        {!single && (
        <Typography noWrap sx={{ typography: "s3", color: "text.secondary" }}>
          {inert ? "Coming soon" : ready ? (
            /* Its account is connected already: say what's waiting there. */
            <>
              <Box component="span" sx={{ display: "inline-block", width: 6, height: 6, borderRadius: "50%", bgcolor: "#16A34A", mr: 0.75, verticalAlign: "middle" }} />
              {ready.line}
            </>
          ) : sub || [showGroup && item.group?.title, item.next || item.need].filter(Boolean).join(" · ")}
        </Typography>
        )}
      </Box>
      {/* Looks like a button; the whole row is the click. */}
      {!inert && (
        <Box
          className="cat-connect"
          sx={{
            flexShrink: 0, display: "inline-flex", alignItems: "center", gap: 0.5,
            px: 1.25, height: 28, borderRadius: 0.75, border: "1px solid", borderColor: "divider",
            typography: "s3", fontWeight: 700, color: "text.primary",
            transition: "border-color .12s ease",
          }}
        >
          {selected && <Iconify icon="solar:check-circle-linear" width={14} />}
          {selected ? "Selected" : "Connect"}
        </Box>
      )}
    </ButtonBase>
  );
}
CatalogRow.propTypes = {
  item: PropTypes.object.isRequired, selected: PropTypes.bool, onPick: PropTypes.func.isRequired, showGroup: PropTypes.bool,
  /* In place of the next step — where the part's heading already says it. */
  sub: PropTypes.string,
  /* Name only — a framework under "Through our SDK" needs no second line. */
  single: PropTypes.bool,
  /* Its account is connected: { line } replaces the next step. */
  ready: PropTypes.object,
  /* The row the arrow keys are on, while searching. */
  active: PropTypes.bool,
  /* The search, to mark in the name. */
  query: PropTypes.string,
};
