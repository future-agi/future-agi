import PropTypes from "prop-types";
import { useMemo, useRef, useState } from "react";
import { alpha } from "@mui/material/styles";
import { Box, Button, ButtonBase, InputBase, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { LogoTile } from "../../environments/LandingBits";
import { PANEL_SX, focusRing } from "../../environments/landingStyles";
import { Detection } from "../../environments/ConnectDirectory";

/**
 * Every way to connect an agent — asked as one question first.
 *
 * It opens on "where does your agent run?": one card per group with its
 * options in it, so the first click is the choice itself (feedback, Oct
 * 2026: 46 equal rows left people unsure what to do; a card that only opened
 * a list was a click too many). Each option says the step it starts and has
 * Connect; a card with more than four says "Show all N". Code lists where
 * the code lives — the framework is worked out from the code, so frameworks
 * fold under one line, with our SDK as the other way in.
 *
 * The box above does two jobs: type a name and every way in is searched;
 * paste a repo, an ID, a URL or a prompt and it says what that is and opens
 * the right setup. Examples under it show what can be pasted.
 */
export default function ConnectCatalog({ groups, pickedId, onPick, detect, onPaste, emptyHint, onFallback, compact = false, pasteHint = "a repo, an ID, a URL or a prompt", examples = [] }) {
  const [query, setQuery] = useState("");
  /* Cards showing every option, not just their first few. */
  const [expanded, setExpanded] = useState({});
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
  /* A recognised paste isn't a search. */
  const searching = !!q && !found;
  const matches = useMemo(() => (searching
    ? items.filter((it) => `${it.name} ${it.need} ${it.next || ""} ${it.group.title}`.toLowerCase().includes(q))
    : []), [items, q, searching]);
  /* A framework searched for by name: it's read from the code, so the
     repository it lives in comes first, our SDK after. */
  const frameworkHit = matches.find((it) => it.fold && it.kind === "framework") || null;
  const hosts = frameworkHit ? items.filter((it) => it.group.id === frameworkHit.group.id && !it.fold) : [];

  const onKey = (e) => {
    if (e.key === "Escape") { setQuery(""); return; }
    if (e.key !== "Enter") return;
    if (canPaste) paste();
    else if (matches.length === 1) onPick(matches[0]);
  };
  const fill = (value) => { setQuery(value); inputRef.current?.focus(); };

  return (
    <Stack spacing={1.5}>
      {/* ── the one box ── */}
      <Box>
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
            placeholder={detect ? `Paste ${pasteHint} — or search ${total} ways to connect` : `Search ${total} ways to connect`}
            inputProps={{ "aria-label": detect ? "Paste a repository, ID, URL or prompt, or search ways to connect" : "Search ways to connect", spellCheck: false }}
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
        {/* What a paste can be — one click fills the box. */}
        {!query && detect && examples.length > 0 && (
          <Stack direction="row" alignItems="center" flexWrap="wrap" columnGap={0.5} rowGap={0.25} sx={{ mt: 0.75 }}>
            <Typography sx={{ typography: "s3", color: "text.subtitle", mr: 0.25 }}>Try pasting</Typography>
            {examples.map((ex) => (
              <ButtonBase
                key={ex.id} onClick={() => fill(ex.value)}
                sx={{ px: 0.75, py: 0.25, borderRadius: 0.75, gap: 0.5, typography: "s3", color: "text.secondary", "&:hover": { color: "text.primary", bgcolor: (t) => alpha(t.palette.text.primary, 0.05) }, "&.Mui-focusVisible": focusRing }}
              >
                {ex.label}
              </ButtonBase>
            ))}
          </Stack>
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

      {searching ? (
        /* ── search: every way in that matches ── */
        <Box sx={{ ...PANEL_SX, overflow: "hidden" }}>
          {!matches.length ? (
            <Stack alignItems="center" spacing={1} sx={{ py: 5, px: 2, textAlign: "center" }}>
              <Typography sx={{ typography: "s1", fontWeight: 600 }}>{`Nothing matches “${query.trim()}”`}</Typography>
              {emptyHint && <Typography sx={{ typography: "s2", color: "text.secondary" }}>{emptyHint}</Typography>}
              {onFallback && (
                <Button size="small" variant="outlined" onClick={onFallback} sx={{ typography: "s2", fontWeight: 600, color: "text.primary", borderColor: "divider", mt: 0.5 }}>
                  Connect an agent endpoint
                </Button>
              )}
            </Stack>
          ) : (
            <>
              {frameworkHit && (
                <Box sx={{ borderBottom: "1px solid", borderColor: "divider" }}>
                  <Section title={`${frameworkHit.name} is read from your code`} reach="Connect the repository it's in — we work out the framework." compact={compact}>
                    {hosts.map((it) => <CatalogRow key={it.id} item={it} selected={pickedId === it.id} onPick={onPick} />)}
                  </Section>
                </Box>
              )}
              <Section title={frameworkHit ? "Or connect through our SDK" : `${matches.length} ${matches.length === 1 ? "match" : "matches"}`} compact={compact}>
                {matches.map((it) => <CatalogRow key={it.id} item={it} showGroup={!frameworkHit} selected={pickedId === it.id} onPick={onPick} />)}
              </Section>
            </>
          )}
        </Box>
      ) : (
        /* ── where does it run? — each card carries its own options ── */
        <Stack spacing={1}>
          <Typography sx={{ typography: "s2", fontWeight: 700 }}>
            {found ? (found.blocked ? "Pick where your agent runs" : "Not what you meant? Pick where your agent runs") : "Where does your agent run today?"}
          </Typography>
          <Box
            sx={(t) => ({
              display: "grid", gap: 1, gridTemplateColumns: "1fr", alignItems: "start",
              [t.breakpoints.up("sm")]: { gridTemplateColumns: "1fr 1fr" },
              [t.breakpoints.up(compact ? "lg" : "md")]: { gridTemplateColumns: "repeat(3, 1fr)" },
            })}
          >
            {groups.map((g) => (
              <GroupCard
                key={g.id} group={g} pickedId={pickedId} onPick={onPick}
                expanded={!!expanded[g.id]} onExpand={(on) => setExpanded((cur) => ({ ...cur, [g.id]: on }))}
              />
            ))}
          </Box>
        </Stack>
      )}
    </Stack>
  );
}

ConnectCatalog.propTypes = {
  groups: PropTypes.array.isRequired,
  pickedId: PropTypes.string,
  onPick: PropTypes.func.isRequired,
  /* Reads a paste (repo, ID, URL, prompt) — `onPaste(choice, prefill, meta)` opens its setup. */
  detect: PropTypes.func,
  onPaste: PropTypes.func,
  emptyHint: PropTypes.node,
  /* Offered when a search finds nothing. */
  onFallback: PropTypes.func,
  /* Inside a drawer: fewer columns until the screen is wide. */
  compact: PropTypes.bool,
  /* What can be pasted, as the placeholder says it. */
  pasteHint: PropTypes.string,
  /* Paste examples under the box: { id, label, value }. */
  examples: PropTypes.array,
};

/* A group's heading and its rows, two columns when there's room. */
function Section({ title, reach, compact, children }) {
  return (
    <Box>
      <Stack
        direction={{ xs: "column", sm: "row" }} alignItems={{ sm: "baseline" }} spacing={{ xs: 0.25, sm: 1.25 }}
        sx={{ px: 2, pt: 1.75, pb: 0.75 }}
      >
        <Typography component="h3" sx={{ typography: "s1", fontWeight: 700, flexShrink: 0 }}>{title}</Typography>
        {reach && <Typography sx={{ typography: "s2", color: "text.secondary", minWidth: 0 }}>{reach}</Typography>}
      </Stack>
      <Box
        sx={(t) => ({
          display: "grid", gridTemplateColumns: "1fr", px: 1, pb: 1,
          [t.breakpoints.up(compact ? "lg" : "md")]: { gridTemplateColumns: "1fr 1fr", columnGap: 1 },
        })}
      >
        {children}
      </Box>
    </Box>
  );
}
Section.propTypes = { title: PropTypes.string, reach: PropTypes.string, compact: PropTypes.bool, children: PropTypes.node };

/* The options a card shows before "Show all". */
const FIRST = 4;

/*
  One answer to "where does it run", with its options in it — one click on
  GitHub or Vapi opens the setup. What you get is said once, on the card.
*/
function GroupCard({ group, pickedId, onPick, expanded, onExpand }) {
  const card = group.card || {};
  const options = group.items.filter((it) => !it.fold);
  const folded = group.items.filter((it) => it.fold && it.kind === "framework");
  const shown = expanded ? options : options.slice(0, FIRST);
  const hidden = options.length - FIRST;
  return (
    <Box sx={{ ...PANEL_SX, overflow: "hidden" }}>
      <Box sx={{ px: 1.75, pt: 1.5, pb: 0.75 }}>
        <Typography component="h3" sx={{ typography: "s1", fontWeight: 700 }}>{card.title || group.title}</Typography>
        <Typography sx={{ typography: "s3", color: "text.secondary", mt: 0.25 }}>{card.gets || group.reach}</Typography>
      </Box>
      <Stack sx={{ px: 0.75, pb: 0.75 }}>
        {shown.map((it) => <CatalogRow key={it.id} item={it} selected={pickedId === it.id} onPick={onPick} dense />)}
      </Stack>
      {hidden > 0 && (
        <ButtonBase
          onClick={() => onExpand(!expanded)}
          sx={{ width: "100%", justifyContent: "flex-start", gap: 0.5, px: 1.75, py: 1, borderTop: "1px solid", borderColor: "divider", typography: "s3", fontWeight: 600, color: "text.secondary", "&:hover": { color: "text.primary" }, "&.Mui-focusVisible": focusRing }}
        >
          <Iconify icon={expanded ? "eva:arrow-ios-upward-fill" : "eva:arrow-ios-downward-fill"} width={14} />
          {expanded ? "Show fewer" : `Show all ${options.length}`}
        </ButtonBase>
      )}
      {/* Frameworks are worked out from the code — one line, and our SDK as the other way. */}
      {folded.length > 0 && (
        <Box sx={{ px: 1.75, py: 1, borderTop: "1px solid", borderColor: "divider" }}>
          <Typography sx={{ typography: "s3", color: "text.secondary" }}>
            {`We work out the framework — ${folded.slice(0, 3).map((it) => it.name).join(", ")} and ${folded.length - 3} more.`}
          </Typography>
          <ButtonBase
            onClick={() => onPick(folded[0])}
            sx={{ mt: 0.25, borderRadius: 0.5, typography: "s3", fontWeight: 600, color: "text.primary", "&:hover": { textDecoration: "underline" }, "&.Mui-focusVisible": focusRing }}
          >
            Connect through our SDK instead
          </ButtonBase>
        </Box>
      )}
    </Box>
  );
}
GroupCard.propTypes = {
  group: PropTypes.object.isRequired, pickedId: PropTypes.string, onPick: PropTypes.func.isRequired,
  expanded: PropTypes.bool, onExpand: PropTypes.func.isRequired,
};

/* One way in: what it is, the step it starts, and Connect. */
function CatalogRow({ item, selected, onPick, showGroup, dense = false }) {
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
        "&:hover .cat-connect": { borderColor: "text.primary" },
        "&.Mui-focusVisible": focusRing,
        "&.Mui-disabled": { opacity: 0.55 },
      }}
    >
      <LogoTile logo={item.logo} icon={item.icon} muted={inert} size={dense ? 28 : 32} />
      <Box sx={{ flex: 1, minWidth: 0 }}>
        <Typography noWrap sx={{ typography: "s2_1", fontWeight: 600, color: inert ? "text.secondary" : "text.primary" }}>{item.name}</Typography>
        <Typography noWrap sx={{ typography: "s3", color: "text.secondary" }}>
          {inert ? "Coming soon" : [showGroup && (item.group?.card?.title || item.group?.title), item.next || item.need].filter(Boolean).join(" · ")}
        </Typography>
      </Box>
      {/* Looks like a button; the whole row is the click. */}
      {!inert && (
        <Box
          className="cat-connect"
          sx={{
            flexShrink: 0, display: "inline-flex", alignItems: "center", gap: 0.5,
            px: 1.25, height: 28, borderRadius: 0.75, border: "1px solid", borderColor: "divider",
            typography: "s3", fontWeight: 700, color: "text.primary", transition: "border-color .12s ease",
          }}
        >
          {selected ? <Iconify icon="solar:check-circle-linear" width={14} /> : null}
          {selected ? "Selected" : "Connect"}
        </Box>
      )}
    </ButtonBase>
  );
}
CatalogRow.propTypes = { item: PropTypes.object.isRequired, selected: PropTypes.bool, onPick: PropTypes.func.isRequired, showGroup: PropTypes.bool, dense: PropTypes.bool };
