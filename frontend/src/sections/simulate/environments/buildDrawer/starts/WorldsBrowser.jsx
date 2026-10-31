import PropTypes from "prop-types";
import { useMemo, useState } from "react";
import { alpha } from "@mui/material/styles";
import {
  Box,
  Button,
  InputBase,
  MenuItem,
  Select,
  Stack,
  Typography,
} from "@mui/material";
import EmptyContent from "src/components/empty-content";
import Iconify from "src/components/iconify";
import { OUTLINED_BTN_SX, T13 } from "../drawerStyles";
import {
  WORLD_TYPES,
  domainLabelOf,
  filterWorlds,
  isSoonWorld,
} from "../worlds";
import { SANDBOX_APPS } from "./sandboxApps";
import { QuietTag } from "./startBits";
import { SaasSandboxCard, WorldCard } from "./worldCards";

const SANDBOX_TYPE = {
  id: "any",
  label: "Any agent",
  icon: "solar:widget-4-linear",
};

// The sandbox is one more world, pinned first, when nothing narrows it out.
function sandboxMatches({ query, type, domain }) {
  if (domain !== "all" || (type !== "all" && type !== SANDBOX_TYPE.id)) {
    return false;
  }
  const q = query.trim().toLowerCase();
  return (
    !q ||
    `saas sandbox any agent ${SANDBOX_APPS.map((a) => a.name).join(" ")}`
      .toLowerCase()
      .includes(q)
  );
}

const selectSx = {
  height: 32,
  flexShrink: 0,
  ...T13,
  "& .MuiSelect-select": {
    ...T13,
    fontWeight: 600,
    py: 0.75,
    display: "flex",
    alignItems: "center",
    gap: 0.75,
  },
};

/**
 * Every prebuilt world, filterable, with the SaaS sandbox pinned first. A
 * world opens its setup; a computer use world is listed but can't be built
 * for yet. The drawer's header names the view and carries the way back.
 */
export default function WorldsBrowser({ worlds, onOpenWorld, onOpenSandbox }) {
  const [query, setQuery] = useState("");
  const [type, setType] = useState("all");
  const [domain, setDomain] = useState("all");
  const shown = useMemo(
    () => filterWorlds(worlds, { query, type, domain }),
    [worlds, query, type, domain],
  );
  const domains = useMemo(
    () =>
      [...new Set(worlds.map((w) => w.domain))].sort((a, b) =>
        domainLabelOf(a).localeCompare(domainLabelOf(b)),
      ),
    [worlds],
  );
  const total = worlds.length + 1;
  const withSandbox = sandboxMatches({ query, type, domain });
  const countOf = (id) =>
    id === "all"
      ? total
      : id === SANDBOX_TYPE.id
        ? 1
        : worlds.filter((w) => w.type === id).length;
  const types = [
    { id: "all", label: "All types" },
    SANDBOX_TYPE,
    ...WORLD_TYPES,
  ].filter((t) => countOf(t.id) > 0);
  const listed = type === SANDBOX_TYPE.id ? [] : shown;
  const clear = () => {
    setQuery("");
    setType("all");
    setDomain("all");
  };

  return (
    <Box sx={{ pb: 4 }}>
      <Stack direction="row" spacing={1.25} alignItems="center" sx={{ mb: 2 }}>
        <Stack
          direction="row"
          alignItems="center"
          spacing={0.75}
          sx={{
            px: 1.25,
            height: 32,
            width: 260,
            flexShrink: 0,
            border: "1px solid",
            borderColor: "divider",
            borderRadius: "6px",
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
            placeholder="Search worlds"
            inputProps={{ "aria-label": "Search worlds" }}
            sx={{ ...T13, flex: 1 }}
          />
        </Stack>
        <Select
          size="small"
          value={type}
          onChange={(e) => setType(e.target.value)}
          inputProps={{ "aria-label": "Type" }}
          sx={{ ...selectSx, minWidth: 160 }}
        >
          {types.map((t) => (
            <MenuItem key={t.id} value={t.id} sx={{ ...T13, gap: 0.75 }}>
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
          <MenuItem value="all" sx={T13}>
            All domains
          </MenuItem>
          {domains.map((d) => (
            <MenuItem key={d} value={d} sx={T13}>
              {domainLabelOf(d)}
            </MenuItem>
          ))}
        </Select>
        <Box flex={1} />
        <Typography
          sx={{
            ...T13,
            color: "text.secondary",
            fontVariantNumeric: "tabular-nums",
            flexShrink: 0,
          }}
        >
          {`${listed.length + (withSandbox ? 1 : 0)} of ${total} worlds`}
        </Typography>
      </Stack>

      {listed.length || withSandbox ? (
        <Box
          component="ul"
          aria-label="Prebuilt worlds"
          sx={{
            listStyle: "none",
            m: 0,
            p: 0,
            display: "grid",
            gap: 1.5,
            gridTemplateColumns: "repeat(3, minmax(0, 1fr))",
          }}
        >
          {withSandbox && <SaasSandboxCard onOpen={onOpenSandbox} />}
          {listed.map((w) => (
            <WorldCard
              key={w.id}
              world={w}
              onOpen={onOpenWorld}
              soon={isSoonWorld(w)}
            />
          ))}
        </Box>
      ) : (
        <EmptyContent
          title="No world matches"
          sx={{ height: "auto", flexGrow: 0, py: 3 }}
          action={
            <Button
              variant="outlined"
              onClick={clear}
              sx={{ ...OUTLINED_BTN_SX, mt: 1.5 }}
            >
              Clear filters
            </Button>
          }
        />
      )}
    </Box>
  );
}
WorldsBrowser.propTypes = {
  worlds: PropTypes.array.isRequired,
  onOpenWorld: PropTypes.func.isRequired,
  onOpenSandbox: PropTypes.func.isRequired,
};
