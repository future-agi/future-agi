import PropTypes from "prop-types";
import { useMemo, useRef, useState } from "react";
import { alpha } from "@mui/material/styles";
import { Box, Button, ButtonBase, InputBase, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { LogoTile } from "../../environments/LandingBits";
import { PANEL_SX, focusRing } from "../../environments/landingStyles";
import { Detection } from "../../environments/ConnectDirectory";
import ChipCard from "../../environments/ChipCard";

/**
 * Every way to connect an agent, as one searchable list.
 *
 * One box does both jobs: type a name and the list narrows; paste a repo,
 * an ID, a URL or a prompt and it says what that is and opens the right
 * setup. Chips narrow by where the agent lives. Nothing is folded behind
 * "N more" — the whole list scrolls, and every row says the same three
 * things: what it is, what you'll need, and how a fix comes back.
 */
export default function ConnectCatalog({ groups, popularIds = [], pickedId, onPick, detect, onPaste, emptyHint, onFallback, compact = false, initialFilter = "all" }) {
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState(initialFilter);
  const inputRef = useRef(null);

  const items = useMemo(() => groups.flatMap((g) => g.items.map((it) => ({ ...it, group: g }))), [groups]);
  const total = items.length;
  /* A paste is recognised; a name being typed is just a search. */
  const found = useMemo(() => {
    const hit = detect && query.trim() ? detect(query) : null;
    return hit && hit.kind !== "unknown" ? hit : null;
  }, [detect, query]);
  const canPaste = !!found && !found.blocked && !!found.choice;
  const paste = () => {
    if (canPaste) onPaste(found.choice, found.prefill, { name: found.label, detail: found.detail, logo: found.logo, icon: found.icon });
  };

  const q = query.trim().toLowerCase();
  const matches = useMemo(() => (q && !found
    ? items.filter((it) => `${it.name} ${it.need} ${it.group.title}`.toLowerCase().includes(q))
    : []), [items, q, found]);

  /* What the list shows: search results; or one group; or Popular, then every group. */
  const sections = useMemo(() => {
    if (q) return found ? [] : [{ id: "results", title: `${matches.length} ${matches.length === 1 ? "match" : "matches"}`, items: matches }];
    if (filter === "popular") return [{ id: "popular", title: "Popular", items: popularIds.map((id) => items.find((it) => it.id === id)).filter(Boolean) }];
    if (filter !== "all") return groups.filter((g) => g.id === filter).map((g) => ({ ...g, items: g.items.map((it) => ({ ...it, group: g })) }));
    const popular = popularIds.map((id) => items.find((it) => it.id === id)).filter(Boolean);
    return [
      ...(popular.length ? [{ id: "popular", title: "Popular", reach: "Where most agents come from.", items: popular }] : []),
      ...groups.map((g) => ({ ...g, items: g.items.map((it) => ({ ...it, group: g })) })),
    ];
  }, [q, found, matches, filter, groups, items, popularIds]);

  const onKey = (e) => {
    if (e.key === "Escape") { setQuery(""); return; }
    if (e.key !== "Enter") return;
    if (canPaste) paste();
    else if (matches.length === 1) onPick(matches[0]);
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
          placeholder={detect ? `Search ${total} ways to connect — or paste a repo, an ID, a URL or a prompt` : `Search ${total} ways to connect`}
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

      {/* ── what a paste was read as, or the chips ── */}
      {found ? (
        <Stack
          direction="row" alignItems="center" spacing={1.5}
          sx={{ ...PANEL_SX, px: 2, py: 1.5 }}
        >
          <Box flex={1} minWidth={0}><Detection found={found} /></Box>
          {canPaste && (
            <Button
              variant="contained" color="primary" onClick={paste}
              endIcon={<Iconify icon="solar:arrow-right-linear" width={14} />}
              sx={{ typography: "s2", fontWeight: 700, flexShrink: 0 }}
            >
              Continue
            </Button>
          )}
        </Stack>
      ) : !q && (
        <Box sx={{ display: "flex", flexWrap: "wrap", gap: 0.75 }}>
          <ChipCard label={<Count label="All" n={total} />} on={filter === "all"} onClick={() => setFilter("all")} />
          {popularIds.length > 0 && <ChipCard label="Popular" on={filter === "popular"} onClick={() => setFilter("popular")} />}
          {groups.map((g) => (
            <ChipCard key={g.id} label={<Count label={g.title} n={g.items.length} />} on={filter === g.id} onClick={() => setFilter(g.id)} />
          ))}
        </Box>
      )}

      {/* ── the list ── */}
      {!found && (
        <Box sx={{ ...PANEL_SX, overflow: "hidden" }}>
          {q && !matches.length ? (
            <Stack alignItems="center" spacing={1} sx={{ py: 5, px: 2, textAlign: "center" }}>
              <Typography sx={{ typography: "s1", fontWeight: 600 }}>{`Nothing matches “${query.trim()}”`}</Typography>
              {emptyHint && <Typography sx={{ typography: "s2", color: "text.secondary" }}>{emptyHint}</Typography>}
              {onFallback && (
                <Button size="small" variant="outlined" onClick={onFallback} sx={{ typography: "s2", fontWeight: 600, color: "text.primary", borderColor: "divider", mt: 0.5 }}>
                  Connect an agent endpoint
                </Button>
              )}
            </Stack>
          ) : sections.map((sec, i) => (
            <Box key={sec.id} sx={{ borderTop: i ? "1px solid" : "none", borderColor: "divider" }}>
              <Stack
                direction={{ xs: "column", sm: "row" }} alignItems={{ sm: "baseline" }} spacing={{ xs: 0.25, sm: 1.25 }}
                sx={{ px: 2, pt: 1.75, pb: 0.75 }}
              >
                <Typography component="h3" sx={{ typography: "s1", fontWeight: 700, flexShrink: 0 }}>{sec.title}</Typography>
                {sec.reach && <Typography sx={{ typography: "s2", color: "text.secondary", minWidth: 0 }}>{sec.reach}</Typography>}
              </Stack>
              <Box
                sx={(t) => ({
                  display: "grid", gridTemplateColumns: "1fr", px: 1, pb: 1,
                  [t.breakpoints.up(compact ? "lg" : "md")]: { gridTemplateColumns: "1fr 1fr", columnGap: 1 },
                })}
              >
                {sec.items.map((it) => (
                  <CatalogRow key={`${sec.id}-${it.id}`} item={it} showGroup={sec.id === "results" || sec.id === "popular"} selected={pickedId === it.id} onPick={onPick} />
                ))}
              </Box>
            </Box>
          ))}
        </Box>
      )}
    </Stack>
  );
}

ConnectCatalog.propTypes = {
  groups: PropTypes.array.isRequired,
  /* Item ids shown first, as Popular, when nothing is searched. */
  popularIds: PropTypes.array,
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
  /* Which chip it opens on — "popular" where the list shares a page with other ways in. */
  initialFilter: PropTypes.string,
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

/* One way in: what it is, what it needs, and how a fix comes back. */
function CatalogRow({ item, selected, onPick, showGroup }) {
  const inert = !!(item.soon || item.later);
  return (
    <ButtonBase
      disabled={inert}
      onClick={() => onPick(item)}
      aria-pressed={selected}
      sx={{
        width: "100%", justifyContent: "flex-start", textAlign: "left",
        gap: 1.25, px: 1, py: 1, borderRadius: 1,
        bgcolor: (t) => (selected ? alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.08 : 0.05) : "transparent"),
        transition: "background-color .12s ease",
        "&:hover": { bgcolor: (t) => alpha(t.palette.text.primary, selected ? 0.09 : 0.045) },
        "&:hover .cat-go": { opacity: 1, transform: "translateX(0)" },
        "&.Mui-focusVisible": focusRing,
        "&.Mui-disabled": { opacity: 0.55 },
      }}
    >
      <LogoTile logo={item.logo} icon={item.icon} muted={inert} />
      <Box sx={{ flex: 1, minWidth: 0 }}>
        <Typography noWrap sx={{ typography: "s2_1", fontWeight: 600, color: inert ? "text.secondary" : "text.primary" }}>{item.name}</Typography>
        <Typography noWrap sx={{ typography: "s3", color: "text.secondary" }}>
          {inert ? "Coming soon" : [showGroup && item.group?.title, item.need].filter(Boolean).join(" · ")}
        </Typography>
      </Box>
      {!inert && item.fix && (
        <Typography noWrap sx={{ typography: "s3", color: "text.subtitle", flexShrink: 0, display: { xs: "none", sm: "block" } }}>
          {`Fix: ${item.fix}`}
        </Typography>
      )}
      {!inert && (
        <Iconify
          className="cat-go"
          icon={selected ? "solar:check-circle-linear" : "solar:alt-arrow-right-linear"}
          width={14}
          sx={{
            color: "text.secondary", flexShrink: 0,
            opacity: selected ? 1 : 0, transform: selected ? "none" : "translateX(-3px)",
            transition: "opacity .15s cubic-bezier(.16,1,.3,1), transform .15s cubic-bezier(.16,1,.3,1)",
          }}
        />
      )}
    </ButtonBase>
  );
}
CatalogRow.propTypes = { item: PropTypes.object.isRequired, selected: PropTypes.bool, onPick: PropTypes.func.isRequired, showGroup: PropTypes.bool };
