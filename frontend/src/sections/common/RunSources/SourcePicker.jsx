// The run-source picker (lane Ra MUST 1–3): Level 1 cards → Level 2
// catalog → Level 3 config, with a breadcrumb `Add a run › Your code ›
// LangGraph` (`Backspace` / `alt+←` / `‹`), keys `1–6`, `/` search and the
// URL (`src`, `entry`, `mode`, `form`, `view`) as the view state. Prompt /
// Agent graph / HTTP / traces / upload hand back to R3's cards
// (`onAddCard`); code and harness open Level 3 and come back as a
// draft (`onAddR4`).
import React, { useEffect, useMemo, useRef, useState } from "react";
import PropTypes from "prop-types";
import { Box, IconButton, Link, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import KeyHintTooltip from "src/sections/common/ux/KeyHintTooltip";
import useShortcuts from "src/sections/common/ux/useShortcuts";
import { useViewUrlState } from "src/sections/common/ux/viewState";
import {
  cardUnavailableReason,
  catalogEntry,
  entriesFor,
  isOffered,
  runSourceCard,
  sourceAvailable,
  stepCardsFor,
  stepCardShown,
} from "./catalog";
import { useRunSourceAvailability } from "./useRunSourceAvailability";
import { isInBlockingDialog } from "src/utils/keyboard-guards";
import { openShortcutSheet } from "src/sections/common/ux/useShortcuts";
import SourceCards, { ConnectedNowStrip } from "./SourceCards";
import CatalogGrid from "./CatalogGrid";
import SourceConfig from "./SourceConfig";
import { usePickerUi } from "./pickerUi";
import {
  connectedNowOf,
  findAgent,
  opensLevel3,
  pickCard,
  pickEntry,
  pickerBack,
  pickerFromParams,
  pickerToParams,
  usableAgents,
} from "./sourceModel";

const URL_DEFAULTS = {
  new_experiment: null,
  source: null,
  agent: null,
  src: null,
  entry: null,
  mode: null,
  form: null,
  view: null,
};

// Run kinds R3's cards still own (unchanged behaviour).
// Entry kinds whose card is an already-running source (step 1 `running`).
const RUNNING_ENTRY_KINDS = ["http", "a2a", "mcp_tool", "traces", "upload"];

const R3_TYPES = {
  prompt: "prompt",
  agent: "agent",
  http: "http",
  // These card types stay gated by the server's report before selection.
  a2a: "a2a",
  mcp_tool: "mcp_tool",
  traces: "traces",
  upload: "external",
};

const NO_MACHINES = Object.freeze([]);

// The one source behind a card when the launch shows exactly one there and
// the server runs it; null when the card lists more (refused ones included).
function loneEntryOf(cardId, availability) {
  const shown = entriesFor(cardId);
  const runs = entriesFor(cardId, availability);
  return shown.length === 1 && runs.length === 1 ? runs[0] : null;
}

export default function SourcePicker({
  dataset,
  datasetKind,
  rowCount,
  rowNoun = "row",
  runLabel,
  reserved,
  openedAtMs,
  onAddCard,
  onAddR4,
  onOpenResults,
  editing,
  onCancelEdit,
  tries,
  onTries,
  recommended = null,
}) {
  const [url, setUrl] = useViewUrlState(URL_DEFAULTS);
  // The server's report: which sources this workspace can create and run.
  const availability = useRunSourceAvailability();
  // options-truth L7b G4 (mock:connect-endpoints): connecting a machine is
  // absent, so the drawer reads no connected machines (no 5 s poll of
  // GET connect/machines/): no `connected now` strip, no saved workers.
  const machines = NO_MACHINES;
  const state = useMemo(() => {
    const parsed = pickerFromParams(url);
    // options-truth L3 G8 P1 (entry:deep-link): an `agent=` that is a
    // connected agent of another entry than `entry=` is ignored.
    const deepAgent =
      parsed.agentId && parsed.entryId
        ? findAgent(machines, parsed.agentId)
        : null;
    const linked =
      deepAgent?.agent?.entry_id && deepAgent.agent.entry_id !== parsed.entryId
        ? { ...parsed, agentId: null }
        : parsed;
    // A link to an unknown catalog opens on step 1. Already running always
    // opens: its tiles show, greyed with the reason where the server cannot
    // run them.
    const s =
      linked.level === 2 && !runSourceCard(linked.card, availability)
        ? { ...linked, level: 1, card: null }
        : linked;
    if (editing && s.level !== 3)
      return {
        ...s,
        level: 3,
        card: null,
        entryId: editing.entryId,
        mode: editing.mode,
        form: editing.form,
        view: editing.view ?? "cli",
      };
    return s;
  }, [url, editing, machines, availability]);
  const write = (next) =>
    setUrl({
      ...pickerToParams(next),
      source: null,
      agent: next.agentId ?? null,
      new_experiment: url.new_experiment,
    });
  const setLevel = usePickerUi((s) => s.setLevel);
  useEffect(() => {
    setLevel(state.level);
    return () => setLevel(0);
  }, [state.level, setLevel]);

  const [query, setQuery] = useState("");
  const [dismissed, setDismissed] = useState(false);
  const searchRef = useRef(null);
  useEffect(() => setQuery(""), [state.level, state.card]);
  // 3 × 2 cards when the content is ≥ 600 px (IT-R2 G1 P5: all six fit at
  // 1280 × 720), 2 × 3 below.
  const rootRef = useRef(null);
  const [wide, setWide] = useState(true);
  useEffect(() => {
    const el = rootRef.current;
    if (!el || typeof ResizeObserver === "undefined") return undefined;
    const ro = new ResizeObserver(([e]) => setWide(e.contentRect.width >= 600));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const savedHarness = usableAgents(machines, {}).filter(
    (x) => x.agent.kind === "harness",
  );
  const connected = dismissed
    ? null
    : connectedNowOf(machines, { datasetKind });

  const goEntry = (entryId, extra = {}) => {
    // Child callbacks cannot bypass visibility or the server's report.
    if (!isOffered(entryId)) return;
    const e = catalogEntry(entryId);
    if (!e || !sourceAvailable(e.kind, availability)) return;
    if (!opensLevel3(entryId)) {
      onAddCard(R3_TYPES[e.kind] ?? e.kind);
      return;
    }
    write({
      ...pickEntry(state, entryId),
      agentId: extra.agentId ?? null,
    });
  };
  const goCard = (cardId, el) => {
    if (!stepCardShown(cardId)) return;
    // Greyed when the server runs none of the card's sources here.
    const card = runSourceCard(cardId, availability);
    if (!card || cardUnavailableReason(card, availability)) return;
    if (cardId === "prompt" || cardId === "agent") {
      onAddCard(R3_TYPES[cardId], el);
      return;
    }
    // One source behind the card, and it runs: pick it, without a list of
    // one. With any refused source behind it the list opens, so its greyed
    // tiles (and their reasons) stay in view.
    const lone = loneEntryOf(cardId, availability);
    if (lone) {
      goEntry(lone.id);
      return;
    }
    write(pickCard(state, cardId));
  };
  // options-truth L1 B: a deep link to an entry that has a card instead of
  // a Level 3 (`entry=a2a`, `entry=mcp_tool`, `entry=http`, …) opens that
  // card once, then drops the entry key.
  // The add waits for the drawer's form to settle (as `firstRunType` does).
  const deepEntryDone = useRef(null);
  // A link to a source waits for the server's yes (it can arrive later).
  const deepEntryRuns = sourceAvailable(
    catalogEntry(url.entry)?.kind,
    availability,
  );
  useEffect(() => {
    const raw = url.entry;
    // dataset integration FE-0: a deep link to an absent source (upload, traces, a
    // framework) adds nothing; the picker stays on step 1.
    const e = raw && isOffered(raw) ? catalogEntry(raw) : null;
    if (
      !e ||
      !deepEntryRuns ||
      opensLevel3(raw) ||
      deepEntryDone.current === raw ||
      usePickerUi.getState().deepEntry === raw
    )
      return undefined;
    const t = setTimeout(() => {
      deepEntryDone.current = raw;
      onAddCard(R3_TYPES[e.kind] ?? e.kind);
      // options-truth L3 G8 P1: an already-running card (endpoint, A2A,
      // MCP tool, traces, upload) keeps `entry=` so the link reproduces
      // it on reload; pickerUi remembers it was added once.
      const keep = RUNNING_ENTRY_KINDS.includes(e.kind);
      if (keep) usePickerUi.getState().setDeepEntry(raw);
      setUrl({ ...url, entry: keep ? raw : null, src: null });
    }, 400);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [url.entry, deepEntryRuns]);
  // A door outside the drawer (the kinds menu, the Experiments tab) opens
  // Already running by its card (`src=running`). With one source behind it,
  // and the server runs it, add that run, as picking the card here does,
  // instead of a list of one (loneEntryOf: never past a greyed tile). A deep link (`entry=`) keeps its path above;
  // like it, the add waits for the drawer's form to settle.
  const lone =
    state.level === 2 && !editing && !url.entry
      ? loneEntryOf(state.card, availability)
      : null;
  const loneId = lone && !opensLevel3(lone.id) ? lone.id : null;
  useEffect(() => {
    if (!loneId) return undefined;
    const t = setTimeout(() => {
      write({ level: 1 });
      goEntry(loneId);
    }, 400);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loneId]);
  const back = () => {
    if (editing && state.level === 3) {
      onCancelEdit?.();
      return;
    }
    write(pickerBack(state));
  };

  // `/` focuses search on Levels 1–2 (the `search` action is not on the
  // newExperiment surface, so the picker listens itself while open).
  useEffect(() => {
    if (state.level >= 3) return undefined;
    const onKey = (e) => {
      if (e.key !== "/" || e.metaKey || e.ctrlKey || e.altKey) return;
      const t = e.target;
      const typing =
        t?.isContentEditable ||
        ["INPUT", "TEXTAREA", "SELECT"].includes(t?.tagName);
      if (typing || !searchRef.current) return;
      e.preventDefault();
      searchRef.current.focus();
    };
    document.addEventListener("keydown", onKey, true);
    return () => document.removeEventListener("keydown", onKey, true);
  }, [state.level]);

  const pickByKey = (key) => {
    const { cards } = stepCardsFor(datasetKind, recommended, availability);
    const card = cards[Number(key) - 1];
    // A held card takes no key: its number passes through untouched, so
    // other listeners still get it.
    if (!card || card.held) return false;
    goCard(
      card.id,
      document.querySelector(`[data-testid="source-card-${card.id}"]`),
    );
    return true;
  };
  useShortcuts("newExperiment", {
    pickSource:
      state.level === 1 && !query ? (e) => pickByKey(e.key) : undefined,
    catalogBack: state.level > 1 ? back : undefined,
  });

  // L7c g2 (picker:keys-breadcrumb): the drawer is a dialog, and focus sits
  // on it when it opens, where the app's single-key shortcuts stand aside
  // (isInBlockingDialog). Inside the drawer that holds this picker, and not
  // in a field or a menu, the picker's own keys still work: 1–3, Backspace
  // or Alt+←, and ? for the shortcut sheet.
  const keysRef = useRef(null);
  keysRef.current = { state, query, pickByKey, back };
  useEffect(() => {
    const onKey = (e) => {
      if (e.defaultPrevented || e.isComposing || e.metaKey || e.ctrlKey) return;
      const t = e.target;
      const root = rootRef.current;
      if (!t || !root || t === document.body) return;
      if (!isInBlockingDialog(t)) return;
      const dialog = t.closest?.('[role="dialog"]');
      if (!dialog || !dialog.contains(root)) return;
      if (
        t.isContentEditable ||
        ["INPUT", "TEXTAREA", "SELECT"].includes(t.tagName) ||
        t.closest?.('[role="menu"], [role="listbox"], [role="combobox"]')
      )
        return;
      const k = keysRef.current;
      if (/^[1-9]$/.test(e.key) && !e.altKey) {
        if (k.state.level === 1 && !k.query && k.pickByKey(e.key))
          e.preventDefault();
        return;
      }
      if (
        (e.key === "Backspace" && !e.altKey) ||
        (e.key === "ArrowLeft" && e.altKey)
      ) {
        if (k.state.level > 1) {
          e.preventDefault();
          k.back();
        }
        return;
      }
      if (e.key === "?" && !e.altKey) {
        e.preventDefault();
        openShortcutSheet({ surface: "newExperiment" });
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);

  const card = runSourceCard(state.card);
  const entry = catalogEntry(state.entryId);
  const crumbs = [
    {
      label: "Add a run",
      go: state.level > 1 ? () => write({ level: 1 }) : null,
    },
    card
      ? {
          label: card.title,
          go: state.level > 2 ? () => write(pickCard(state, card.id)) : null,
        }
      : null,
    state.level === 3 && entry ? { label: entry.name, go: null } : null,
  ].filter(Boolean);

  return (
    <Stack
      ref={rootRef}
      spacing={1.25}
      data-testid="source-picker"
      data-level={state.level}
    >
      {state.level > 1 ? (
        <Stack
          direction="row"
          alignItems="center"
          gap={0.5}
          data-testid="picker-breadcrumb"
        >
          <KeyHintTooltip title="Back one level" action="catalogBack">
            <IconButton
              size="small"
              onClick={back}
              aria-label="Back one level"
              sx={{ p: 0.25 }}
            >
              <Iconify icon="mdi:chevron-left" width={20} />
            </IconButton>
          </KeyHintTooltip>
          {crumbs.map((c, i) => (
            <React.Fragment key={c.label}>
              {i ? (
                <Typography typography="s2" color="text.disabled">
                  ›
                </Typography>
              ) : null}
              {c.go ? (
                <Link
                  component="button"
                  type="button"
                  typography="s2"
                  color="text.secondary"
                  onClick={c.go}
                  underline="hover"
                >
                  {c.label}
                </Link>
              ) : (
                <Typography typography="s2" fontWeight="fontWeightMedium">
                  {c.label}
                </Typography>
              )}
            </React.Fragment>
          ))}
        </Stack>
      ) : null}
      {state.level === 1 ? (
        <SourceCards
          datasetKind={datasetKind}
          recommended={recommended}
          wide={wide}
          query={query}
          onQuery={setQuery}
          searchRef={searchRef}
          onPickCard={goCard}
          onPickEntry={(id) => goEntry(id)}
          availability={availability}
          connected={connected}
          connectedStrip={
            connected ? (
              <ConnectedNowStrip
                connected={connected}
                rowCount={rowCount}
                rowNoun={rowNoun}
                onDismiss={() => setDismissed(true)}
                onRun={() =>
                  goEntry(connected.agent.entry_id, {
                    arrivedForm: "connect",
                    agentId: connected.agent.id,
                  })
                }
              />
            ) : null
          }
        />
      ) : null}
      {state.level === 2 && card ? (
        <CatalogGrid
          card={card.id}
          environment={dataset?.environment ?? null}
          query={query}
          onQuery={setQuery}
          searchRef={searchRef}
          onPickEntry={(id) => goEntry(id)}
          onPickSaved={(s) =>
            goEntry(s.agent.entry_id ?? "custom_command", {
              arrivedForm: "connect",
              agentId: s.agent.id,
            })
          }
          savedHarness={savedHarness}
          availability={availability}
        />
      ) : null}
      {state.level === 3 && entry ? (
        <Box>
          <SourceConfig
            key={`${entry.id}`}
            state={state}
            onState={(patch) => write({ ...state, ...patch })}
            onChangeSource={() => write(pickCard(state, state.card ?? "code"))}
            dataset={dataset}
            runLabel={runLabel}
            reserved={reserved}
            openedAtMs={openedAtMs}
            machines={machines}
            initialDraft={editing}
            rowCount={rowCount}
            rowNoun={rowNoun}
            onOpenResults={onOpenResults}
            tries={tries}
            onTries={onTries}
            onAdd={(draft) => {
              onAddR4(draft);
              setUrl({
                src: null,
                entry: null,
                mode: null,
                form: null,
                view: null,
                source: null,
                agent: null,
              });
            }}
          />
        </Box>
      ) : null}
    </Stack>
  );
}

SourcePicker.propTypes = {
  dataset: PropTypes.object,
  datasetKind: PropTypes.string,
  rowCount: PropTypes.number,
  rowNoun: PropTypes.string,
  runLabel: PropTypes.string,
  reserved: PropTypes.object,
  openedAtMs: PropTypes.number,
  onAddCard: PropTypes.func.isRequired,
  onAddR4: PropTypes.func.isRequired,
  onOpenResults: PropTypes.func,
  editing: PropTypes.object,
  onCancelEdit: PropTypes.func,
  tries: PropTypes.number,
  onTries: PropTypes.func,
  /** Additive (IT-R2 G1 P5): `{card, reason}` from context. */
  recommended: PropTypes.object,
};
