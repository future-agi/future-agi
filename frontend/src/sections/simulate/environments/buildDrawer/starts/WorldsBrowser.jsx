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
import ContentSkeleton from "src/components/content-skeleton";
import EmptyContent from "src/components/empty-content";
import ErrorState from "src/components/error-state";
import Iconify from "src/components/iconify";
import { errorMessage } from "src/pages/dashboard/harness/harnessShared";
import { OUTLINED_BTN_SX, T13 } from "../drawerStyles";
import { WORLD_TYPES, filterWorlds } from "../worlds";
import { QuietTag } from "./startBits";
import ReadyEnvironmentCard from "./ReadyEnvironmentCard";

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
 * Every ready environment, filterable. One
 * opens its panel. The drawer's header names the view and carries the way back.
 */
export default function WorldsBrowser({
  worlds,
  isLoading = false,
  error = null,
  onRetry,
  onOpenWorld,
}) {
  const [query, setQuery] = useState("");
  const [type, setType] = useState("all");
  const [domain, setDomain] = useState("all");
  const shown = useMemo(
    () => filterWorlds(worlds, { query, type, domain }),
    [worlds, query, type, domain],
  );
  const domains = useMemo(
    () =>
      [...new Map(worlds.map((w) => [w.domain, w.domainLabel]))].sort(
        ([, a], [, b]) => a.localeCompare(b),
      ),
    [worlds],
  );
  const total = worlds.length;
  const countOf = (id) =>
    id === "all" ? total : worlds.filter((w) => w.type === id).length;
  const types = [{ id: "all", label: "All types" }, ...WORLD_TYPES].filter(
    (t) => t.id === "all" || countOf(t.id) !== 0,
  );
  const listed = shown;
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
            placeholder="Search environments"
            inputProps={{ "aria-label": "Search environments" }}
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
          {domains.map(([d, label]) => (
            <MenuItem key={d} value={d} sx={T13}>
              {label}
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
          {isLoading || error
            ? null
            : `${listed.length} of ${total} environments`}
        </Typography>
      </Stack>

      {isLoading ? (
        <ContentSkeleton variant="panel" label="Loading ready environments" />
      ) : error ? (
        <ErrorState
          title="Couldn't load the ready environments"
          detail={errorMessage(error)}
          onRetry={onRetry}
          sx={{ height: "auto", flexGrow: 0, py: 3 }}
        />
      ) : listed.length ? (
        <Box
          component="ul"
          aria-label="Ready environments"
          sx={{
            listStyle: "none",
            m: 0,
            p: 0,
            display: "grid",
            gap: 1.5,
            gridTemplateColumns: "repeat(3, minmax(0, 1fr))",
          }}
        >
          {listed.map((w) => (
            <ReadyEnvironmentCard key={w.id} world={w} onOpen={onOpenWorld} />
          ))}
        </Box>
      ) : (
        <EmptyContent
          title="No environment matches"
          sx={{ height: "auto", flexGrow: 0, py: 3 }}
          action={
            <Button
              size="small"
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
  isLoading: PropTypes.bool,
  error: PropTypes.object,
  onRetry: PropTypes.func,
  onOpenWorld: PropTypes.func.isRequired,
};
