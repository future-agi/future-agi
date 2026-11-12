import React, { useEffect, useMemo, useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Dialog,
  DialogContent,
  DialogTitle,
  IconButton,
  InputAdornment,
  Link,
  Stack,
  Tab,
  Tabs,
  TextField,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import {
  GROUPS,
  GROUP_TITLES,
  SURFACES,
  SURFACE_TITLES,
  shortcutRows,
} from "src/utils/keymap";
import { SHORTCUT_SHEET_ATTR } from "src/utils/keyboard-guards";
import Term from "src/sections/common/ux/Term";
import { glossaryEntry, searchGlossary } from "src/sections/common/ux/glossary";
import {
  isGlossaryShown,
  isLaunchBuild,
  isSurfaceShown,
} from "src/launch/launchSwitch";
import {
  liveActionsFor,
  unavailableFor,
} from "src/sections/common/ux/useShortcuts";

/** A key cap. */
export const KeyCap = ({ children, sx }) => (
  <Box
    component="kbd"
    sx={{
      display: "inline-flex",
      alignItems: "center",
      justifyContent: "center",
      minWidth: 22,
      height: 22,
      px: 0.75,
      border: "1px solid",
      borderColor: "divider",
      borderBottomWidth: 2,
      borderRadius: 0.5,
      bgcolor: "background.neutral",
      typography: "s2",
      fontFamily: "inherit",
      fontWeight: 500,
      color: "text.primary",
      whiteSpace: "nowrap",
      ...sx,
    }}
  >
    {children}
  </Box>
);

KeyCap.propTypes = { children: PropTypes.node, sx: PropTypes.object };

const norm = (s) => String(s ?? "").toLowerCase();

const rowMatches = (row, q) =>
  !q ||
  norm(row.description).includes(q) ||
  row.caps.some((c) => norm(c) === q || norm(c).includes(q)) ||
  norm(row.action).includes(q);

function Section({ title, rows }) {
  if (!rows.length) return null;
  return (
    <Box sx={{ mb: 2 }}>
      <Typography
        typography="s2"
        fontWeight="fontWeightSemiBold"
        color="text.primary"
        sx={{ mb: 0.5 }}
      >
        {title}
      </Typography>
      {GROUPS.map((g) => {
        const inGroup = rows.filter((r) => r.group === g);
        if (!inGroup.length) return null;
        return (
          <Box key={g} sx={{ mb: 1 }}>
            <Typography
              typography="s3"
              color="text.disabled"
              sx={{ textTransform: "uppercase", letterSpacing: 0.4 }}
            >
              {GROUP_TITLES[g]}
            </Typography>
            <Stack
              divider={
                <Box sx={{ borderTop: "1px solid", borderColor: "divider" }} />
              }
            >
              {inGroup.map((r) => (
                <Stack
                  key={r.action}
                  direction="row"
                  alignItems="center"
                  justifyContent="space-between"
                  gap={2}
                  sx={{ py: 0.75, ...(r.unavailable ? { opacity: 0.55 } : {}) }}
                  data-shortcut={r.action}
                  data-unavailable={r.unavailable ? "true" : undefined}
                >
                  <Stack
                    direction="row"
                    alignItems="baseline"
                    gap={1}
                    sx={{ minWidth: 0 }}
                  >
                    <Typography
                      typography="s1"
                      color={r.unavailable ? "text.secondary" : "text.primary"}
                    >
                      {r.description}
                    </Typography>
                    {r.unavailable ? (
                      <Typography
                        typography="s3"
                        color="text.secondary"
                        sx={{ whiteSpace: "nowrap" }}
                      >
                        {r.unavailable}
                      </Typography>
                    ) : null}
                  </Stack>
                  <Stack
                    direction="row"
                    alignItems="center"
                    gap={0.5}
                    sx={{ flexShrink: 0 }}
                  >
                    {r.caps.map((cap, i) => (
                      <React.Fragment key={cap}>
                        {i > 0 && (
                          <Typography typography="s3" color="text.disabled">
                            or
                          </Typography>
                        )}
                        <KeyCap>{cap}</KeyCap>
                      </React.Fragment>
                    ))}
                  </Stack>
                </Stack>
              ))}
            </Stack>
          </Box>
        );
      })}
    </Box>
  );
}
Section.propTypes = { title: PropTypes.string, rows: PropTypes.array };

function ShortcutsTab({ surface, query }) {
  const q = norm(query).trim();
  const sections = useMemo(() => {
    const first = surface && surface !== "global" ? [surface] : [];
    const order = [...first, "global"];
    // L6 launch switch: other screens' sections only where they ship.
    const others = q
      ? SURFACES.filter(
          (s) =>
            !order.includes(s) &&
            s !== "experiments" &&
            shortcutRows(s).length > 0,
        )
      : [];
    return [...order, ...others].map((s) => {
      const off = unavailableFor(s);
      // R4 (D25): only actions with a live handler on this screen; one that
      // needs context is greyed with its reason; the rest are hidden.
      const live = liveActionsFor(s);
      return {
        surface: s,
        title: SURFACE_TITLES[s] ?? s,
        rows: shortcutRows(s)
          .filter((r) => !live || live.has(r.action) || off[r.action])
          .filter((r) => rowMatches(r, q))
          .map((r) =>
            off[r.action] ? { ...r, unavailable: off[r.action] } : r,
          ),
      };
    });
  }, [surface, q]);
  const empty = sections.every((s) => !s.rows.length);
  return (
    <Box>
      {sections.map((s) => (
        <Section key={s.surface} title={s.title} rows={s.rows} />
      ))}
      {empty && (
        <Typography typography="s1" color="text.secondary" sx={{ py: 2 }}>
          {`No shortcut matches “${query}”. Try a key (J) or a word (${
            // L6: there is no baseline in a launch build.
            isLaunchBuild() ? "filter" : "baseline"
          }).`}
        </Typography>
      )}
    </Box>
  );
}
ShortcutsTab.propTypes = { surface: PropTypes.string, query: PropTypes.string };

function GlossaryTab({ query }) {
  // L6: a launch build keeps only the terms of shown surfaces.
  const hits = searchGlossary(query).filter(({ key }) => isGlossaryShown(key));
  if (!hits.length)
    return (
      <Typography typography="s1" color="text.secondary" sx={{ py: 2 }}>
        No term matches “{query}”.
      </Typography>
    );
  return (
    <Stack
      divider={<Box sx={{ borderTop: "1px solid", borderColor: "divider" }} />}
    >
      {hits.map(({ key, entry }) => (
        <Box key={key} sx={{ py: 1 }} data-glossary={key}>
          <Stack direction="row" alignItems="baseline" gap={1}>
            <Typography
              typography="s1"
              fontWeight="fontWeightSemiBold"
              color="text.primary"
            >
              <Term k={key}>{entry.label}</Term>
            </Typography>
            {entry.docs && (
              <Link
                href={entry.docs}
                target="_blank"
                rel="noopener noreferrer"
                typography="s3"
                underline="hover"
              >
                Docs ↗
              </Link>
            )}
          </Stack>
          <Typography typography="s2" color="text.secondary">
            {entry.short}
          </Typography>
        </Box>
      ))}
    </Stack>
  );
}
GlossaryTab.propTypes = { query: PropTypes.string };

// Example words for the glossary search, in this order; the hint names only
// terms this build lists.
const GLOSSARY_HINT_KEYS = [
  "passHatK",
  "baseline",
  "flaky",
  "wrapLines",
  "matchedByMeaning",
];

/** The glossary search placeholder: up to three listed terms as examples. */
function glossaryPlaceholder() {
  const words = GLOSSARY_HINT_KEYS.filter((key) => isGlossaryShown(key))
    .map((key) => glossaryEntry(key)?.label?.toLowerCase())
    .filter(Boolean)
    .slice(0, 3);
  return words.length ? `Search terms (${words.join(", ")}…)` : "Search terms";
}

/**
 * ShortcutSheet — the global `?` sheet (R3 G0 §B5, RV#4): the current
 * surface first, then Global; groups Navigate / View / Act; a search box
 * (searching also lists other screens); a `Glossary` tab with the GLOSSARY
 * entries this build lists. Opened by `?` anywhere (GlobalKeyboard), by Cmd+K
 * `Keyboard shortcuts`, or by a screen (Row view, drawer, review).
 *
 * Props (frozen; R3 additions optional):
 * @param {boolean} props.open
 * @param {Function} props.onClose
 * @param {string} [props.surface]  keymap surface listed first
 * @param {"shortcuts"|"glossary"} [props.tab]  initial tab
 * @param {string} [props.query]  initial search
 */
const ShortcutSheet = ({
  open,
  onClose,
  surface = "row",
  tab: initialTab = "shortcuts",
  query: initialQuery = "",
}) => {
  // L6 launch switch: no Glossary tab where the glossary is hidden.
  const glossaryShown = isSurfaceShown("palette.glossary");
  const firstTab = glossaryShown ? initialTab : "shortcuts";
  const [tabState, setTab] = useState(firstTab);
  const tab = glossaryShown ? tabState : "shortcuts";
  const [query, setQuery] = useState(initialQuery);
  useEffect(() => {
    if (open) {
      setTab(firstTab);
      setQuery(initialQuery);
    }
  }, [open, firstTab, initialQuery]);

  return (
    <Dialog
      open={open}
      onClose={onClose}
      maxWidth="sm"
      fullWidth
      PaperProps={{
        [SHORTCUT_SHEET_ATTR]: "",
        // L6: a launch build hides the Glossary tab, so the name drops it.
        "aria-label": glossaryShown
          ? "Keyboard shortcuts and glossary"
          : "Keyboard shortcuts",
        sx: { height: "min(720px, 80vh)" },
      }}
    >
      <DialogTitle sx={{ pb: 0, pr: 1.5 }}>
        <Stack direction="row" alignItems="center">
          <Typography
            typography="m3"
            fontWeight="fontWeightSemiBold"
            sx={{ flex: 1 }}
          >
            {tab === "glossary" ? "Glossary" : "Keyboard shortcuts"}
          </Typography>
          <IconButton size="small" aria-label="Close" onClick={onClose}>
            <Iconify icon="mdi:close" width={18} />
          </IconButton>
        </Stack>
        <Tabs
          value={tab}
          onChange={(_, v) => setTab(v)}
          sx={{ minHeight: 36, mt: 0.5 }}
        >
          <Tab value="shortcuts" label="Shortcuts" sx={{ minHeight: 36 }} />
          {glossaryShown ? (
            <Tab value="glossary" label="Glossary" sx={{ minHeight: 36 }} />
          ) : null}
        </Tabs>
        <TextField
          autoFocus
          fullWidth
          size="small"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder={
            tab === "glossary"
              ? glossaryPlaceholder()
              : "Search shortcuts (a key or a word)"
          }
          inputProps={{
            "aria-label":
              tab === "glossary" ? "Search terms" : "Search shortcuts",
          }}
          InputProps={{
            startAdornment: (
              <InputAdornment position="start">
                <Iconify icon="mdi:magnify" width={18} />
              </InputAdornment>
            ),
          }}
          sx={{ my: 1.5 }}
        />
      </DialogTitle>
      <DialogContent sx={{ pt: 0 }}>
        {tab === "glossary" ? (
          <GlossaryTab query={query} />
        ) : (
          <ShortcutsTab surface={surface} query={query} />
        )}
      </DialogContent>
      <Stack
        direction="row"
        alignItems="center"
        justifyContent="space-between"
        sx={{
          px: 3,
          py: 1.25,
          borderTop: "1px solid",
          borderColor: "divider",
        }}
      >
        <Typography typography="s2" color="text.secondary">
          Letter keys work unless you are typing in a field.
        </Typography>
        <Stack direction="row" alignItems="center" gap={0.75}>
          <Typography typography="s2" color="text.secondary">
            Press
          </Typography>
          <KeyCap>?</KeyCap>
          <Typography typography="s2" color="text.secondary">
            anytime
          </Typography>
        </Stack>
      </Stack>
    </Dialog>
  );
};

ShortcutSheet.propTypes = {
  open: PropTypes.bool.isRequired,
  onClose: PropTypes.func.isRequired,
  surface: PropTypes.string,
  tab: PropTypes.oneOf(["shortcuts", "glossary"]),
  query: PropTypes.string,
};

export default ShortcutSheet;
