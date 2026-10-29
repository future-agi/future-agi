import EmptyContent from "src/components/empty-content";
import PropTypes from "prop-types";
import { useMemo, useState } from "react";
import { alpha } from "@mui/material/styles";
import {
  Box,
  Button,
  IconButton,
  InputBase,
  MenuItem,
  Select,
  Stack,
  Tooltip,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import {
  SANDBOX_TYPE,
  WORLD_TYPES,
  domainLabelOf,
  filterWorlds,
  prebuiltWorlds,
  sandboxMatches,
  worldTotal,
} from "./landingData";
import { SaasSandboxCard } from "./SaasSandbox";
import { QuietTag, WorldCard } from "./LandingBits";
import { AGENT_TYPES } from "./connectOptions";
import WorldDrawer from "./WorldDrawer";
import CopyCommandButton from "../components/CopyCommandButton";
import { CATALOGUE_CLI, READINESS } from "../_mock/catalogue";

/*
  Every prebuilt world, filterable. A world is the world only — open one to
  see what's in it, pick the agent it's for, and build that agent's
  environment from it. Worlds for Coming-soon agent types are listed, inert.
  `bare` drops the page heading — the Build environment drawer's own header
  says "Prebuilt worlds" and carries the way back.
*/
export default function PrebuiltWorldsView({ onBack, bare = false }) {
  const worlds = useMemo(() => prebuiltWorlds(), []);
  const [query, setQuery] = useState("");
  const [type, setType] = useState("all");
  const [domain, setDomain] = useState("all");
  /* Ready, Coming soon (starter cases not proved), Retired or Withdrawn. */
  const [readiness, setReadiness] = useState("all");
  const [open, setOpen] = useState(null);
  const shown = useMemo(
    () => filterWorlds(worlds, { query, type, domain, readiness }),
    [worlds, query, type, domain, readiness],
  );
  const readinessCount = (id) =>
    worlds.filter((w) => (w.readiness || "ready") === id).length;
  const domains = useMemo(
    () =>
      [...new Set(worlds.map((w) => w.domain))].sort((a, b) =>
        domainLabelOf(a).localeCompare(domainLabelOf(b)),
      ),
    [worlds],
  );
  /* The SaaS sandbox is one more world, pinned first under "Any agent". */
  const total = worldTotal();
  const withSandbox =
    (readiness === "all" || readiness === "ready") &&
    sandboxMatches({ query, type, domain });
  const countOf = (id) =>
    id === "all"
      ? total
      : id === SANDBOX_TYPE.id
        ? 1
        : worlds.filter((w) => w.type === id).length;
  const types = [
    { id: "all", label: "All types" },
    { ...SANDBOX_TYPE, icon: "solar:widget-4-linear" },
    ...WORLD_TYPES.map((t) => ({
      ...t,
      icon: AGENT_TYPES.find((a) => a.id === t.id)?.icon,
    })),
  ].filter((t) => countOf(t.id) > 0);
  const listed = type === SANDBOX_TYPE.id ? [] : shown;
  /* Agent types that are Coming soon — their worlds can't be built yet. */
  const soonTypes = useMemo(
    () => new Set(WORLD_TYPES.filter((t) => t.soon).map((t) => t.id)),
    [],
  );
  const grid = {
    display: "grid",
    gap: 1.5,
    /* minmax(0, …) so a card's one-line footer truncates instead of widening its column. */
    gridTemplateColumns: {
      xs: "minmax(0, 1fr)",
      sm: "repeat(2, minmax(0, 1fr))",
      lg: "repeat(3, minmax(0, 1fr))",
    },
  };
  const selectSx = {
    height: 34,
    flexShrink: 0,
    "& .MuiSelect-select": {
      typography: "s2",
      fontWeight: 600,
      py: 0.75,
      display: "flex",
      alignItems: "center",
      gap: 0.75,
    },
  };

  return (
    <Box sx={{ pb: 4 }}>
      {/* Back sits beside the title, as on the other Environments sub-pages. */}
      {!bare && (
        <Stack
          direction="row"
          alignItems="flex-start"
          spacing={1.5}
          sx={{ mb: 2 }}
        >
          <Tooltip arrow title="Back to build environment">
            <IconButton
              size="small"
              onClick={onBack}
              aria-label="Back to build environment"
              sx={{ mt: 0.25 }}
            >
              <Iconify icon="solar:alt-arrow-left-linear" width={20} />
            </IconButton>
          </Tooltip>
          <Box minWidth={0}>
            <Typography
              component="h2"
              sx={{ typography: "m2", fontWeight: 600 }}
            >
              Prebuilt worlds
              <Box
                component="span"
                sx={{ color: "text.secondary", fontWeight: 500, ml: 0.75 }}
              >
                {total}
              </Box>
            </Typography>
            <Typography sx={{ typography: "s2", color: "text.subtitle" }}>
              Tables, tools and rules, as designed. Try one now with its sample
              agent, or use it with one of your agents.
            </Typography>
          </Box>
        </Stack>
      )}

      {/* Wraps rather than widening the page when the drawer is narrow. */}
      <Stack
        direction={{ xs: "column", md: "row" }}
        spacing={1.25}
        useFlexGap
        flexWrap="wrap"
        alignItems={{ md: "center" }}
        sx={{ mb: 2, minWidth: 0 }}
      >
        <Box
          sx={{
            display: "flex",
            alignItems: "center",
            gap: 0.75,
            px: 1.25,
            height: 34,
            width: { xs: "100%", md: 260 },
            border: "1px solid",
            borderColor: "divider",
            borderRadius: 1,
            "&:focus-within": {
              borderColor: (t) => alpha(t.palette.text.primary, 0.4),
            },
          }}
        >
          <Iconify
            icon="solar:magnifer-linear"
            width={14}
            sx={{ color: "text.disabled" }}
          />
          <InputBase
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search tasks, domains or tools"
            inputProps={{ "aria-label": "Search worlds" }}
            sx={{
              typography: "s2",
              flex: 1,
              "& input::placeholder": { color: "text.disabled", opacity: 1 },
            }}
          />
        </Box>

        {/* Type and domain as two menus, so the row never wraps. */}
        <Select
          size="small"
          value={type}
          onChange={(e) => setType(e.target.value)}
          inputProps={{ "aria-label": "Type" }}
          sx={{ ...selectSx, minWidth: 160 }}
        >
          {types.map((t) => (
            <MenuItem
              key={t.id}
              value={t.id}
              sx={{ typography: "s2", gap: 0.75 }}
            >
              {t.icon && (
                <Iconify
                  icon={t.icon}
                  width={14}
                  sx={{ color: "text.secondary" }}
                />
              )}
              {t.label}
              {t.soon && <QuietTag>Coming soon</QuietTag>}
              <Box
                component="span"
                sx={{
                  ml: "auto",
                  pl: 1.5,
                  color: "text.disabled",
                  fontVariantNumeric: "tabular-nums",
                }}
              >
                {countOf(t.id)}
              </Box>
            </MenuItem>
          ))}
        </Select>

        <Select
          size="small"
          value={domain}
          onChange={(e) => setDomain(e.target.value)}
          inputProps={{ "aria-label": "Domain" }}
          sx={{ ...selectSx, minWidth: 150 }}
        >
          <MenuItem value="all" sx={{ typography: "s2" }}>
            All domains
          </MenuItem>
          {domains.map((d) => (
            <MenuItem key={d} value={d} sx={{ typography: "s2" }}>
              {domainLabelOf(d)}
            </MenuItem>
          ))}
        </Select>

        <Select
          size="small"
          value={readiness}
          onChange={(e) => setReadiness(e.target.value)}
          inputProps={{ "aria-label": "Readiness" }}
          sx={{ ...selectSx, minWidth: 150 }}
        >
          <MenuItem value="all" sx={{ typography: "s2" }}>
            Any readiness
          </MenuItem>
          {Object.entries(READINESS).map(([id, r]) => (
            <MenuItem key={id} value={id} sx={{ typography: "s2", gap: 0.75 }}>
              {r.label}
              <Box
                component="span"
                sx={{
                  ml: "auto",
                  pl: 1.5,
                  color: "text.disabled",
                  fontVariantNumeric: "tabular-nums",
                }}
              >
                {readinessCount(id)}
              </Box>
            </MenuItem>
          ))}
        </Select>

        <Box flex={1} />
        <Typography
          sx={{
            typography: "s2",
            color: "text.secondary",
            fontVariantNumeric: "tabular-nums",
            flexShrink: 0,
          }}
        >
          {`${listed.length + (withSandbox ? 1 : 0)} of ${total} worlds`}
        </Typography>
        {/* The same list from the CLI. */}
        <CopyCommandButton command={CATALOGUE_CLI} label="CLI" />
      </Stack>

      {listed.length || withSandbox ? (
        <Box sx={grid}>
          {withSandbox && <SaasSandboxCard />}
          {listed.map((w) => (
            <WorldCard
              key={w.id}
              world={w}
              onOpen={setOpen}
              selected={open?.id === w.id}
              soon={soonTypes.has(w.type)}
            />
          ))}
        </Box>
      ) : (
        <EmptyContent
          title="No world matches"
          sx={{ height: "auto", flexGrow: 0 }}
          action={
            <Button
              variant="outlined"
              color="inherit"
              size="small"
              onClick={() => {
                setQuery("");
                setType("all");
                setDomain("all");
                setReadiness("all");
              }}
            >
              Clear filters
            </Button>
          }
        />
      )}

      <WorldDrawer
        world={open}
        onClose={() => setOpen(null)}
        onOpenWorld={(id) =>
          setOpen(worlds.find((w) => w.catalogueId === id) || null)
        }
      />
    </Box>
  );
}
PrebuiltWorldsView.propTypes = {
  onBack: PropTypes.func.isRequired,
  bare: PropTypes.bool,
};
