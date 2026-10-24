import PropTypes from "prop-types";
import { useMemo, useRef, useState } from "react";
import { alpha } from "@mui/material/styles";
import {
  Box,
  ButtonBase,
  InputBase,
  Radio,
  Stack,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { clickable, focusRing } from "../../components/clickable";

const MONO = "ui-monospace, Menlo, monospace";
/* Past this many rows the list scrolls in its own frame. */
const SCROLL_FROM = 6;

/**
 * The agents a repository scan found — or a platform account lists — to
 * choose the one being connected. The same list in both places: search,
 * radio rows, ones already in Agents marked "Connected · Open".
 *
 * A monorepo can hold dozens, so the list always has a search box (name,
 * framework, file or folder), and past a handful of rows it scrolls inside
 * its own frame instead of stretching the drawer. The chosen agent stays named above the list even
 * when a search hides its row, so a choice is never lost from view.
 */
export default function FoundAgentsList({
  agents,
  agentId,
  onPick,
  connectedOf,
  onOpenConnected,
  heading,
  searchHint = "name, framework or file",
  searchOf = repoSearchOf,
  describe = repoLineOf,
}) {
  const [query, setQuery] = useState("");
  const inputRef = useRef(null);
  const many = agents.length >= SCROLL_FROM;
  const q = query.trim().toLowerCase();
  const shown = useMemo(
    () =>
      q ? agents.filter((a) => searchOf(a).toLowerCase().includes(q)) : agents,
    [agents, q, searchOf],
  );
  const chosen = agents.find((a) => a.id === agentId) || null;
  const chosenHidden = !!chosen && !shown.some((a) => a.id === chosen.id);

  const onKey = (e) => {
    if (e.key === "Escape") setQuery("");
    if (e.key === "Enter" && shown.length === 1) onPick(shown[0]);
  };

  return (
    <Box>
      <Typography sx={{ typography: "s3", fontWeight: 600, mb: 0.5 }}>
        {heading ||
          (agents.length === 1
            ? "We found 1 agent"
            : `We found ${agents.length} agents — which one do you mean?`)}
      </Typography>

      <Box
        sx={{
          display: "flex",
          alignItems: "center",
          gap: 1,
          px: 1.25,
          height: 36,
          mb: 0.75,
          border: "1px solid",
          borderColor: "divider",
          borderRadius: 1,
          bgcolor: "background.paper",
          "&:focus-within": {
            borderColor: (t) => alpha(t.palette.text.primary, 0.45),
          },
        }}
      >
        <Iconify
          icon="solar:magnifer-linear"
          width={16}
          sx={{ color: "text.disabled", flexShrink: 0 }}
        />
        <InputBase
          inputRef={inputRef}
          fullWidth
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={onKey}
          placeholder={
            agents.length === 1
              ? `Search by ${searchHint}`
              : `Search ${agents.length} agents — ${searchHint}`
          }
          inputProps={{
            "aria-label": "Search the agents found",
            spellCheck: false,
          }}
          sx={{
            typography: "s2",
            "& input::placeholder": { color: "text.disabled", opacity: 1 },
          }}
        />
        {query && (
          <ButtonBase
            onClick={() => {
              setQuery("");
              inputRef.current?.focus();
            }}
            aria-label="Clear"
            sx={{
              borderRadius: "4px",
              p: 0.25,
              color: "text.disabled",
              "&:hover": { color: "text.primary" },
              "&.Mui-focusVisible": focusRing,
            }}
          >
            <Iconify icon="solar:close-linear" width={16} />
          </ButtonBase>
        )}
      </Box>

      {/* The choice, kept in view while a search hides its row. */}
      {chosenHidden && (
        <Typography
          sx={{ typography: "s3", color: "text.secondary", mb: 0.75 }}
        >
          {"Chosen: "}
          <Box component="span" sx={{ color: "text.primary", fontWeight: 600 }}>
            {chosen.name}
          </Box>
          {chosen.entry && (
            <Box
              component="span"
              sx={{ fontFamily: MONO }}
            >{` · ${chosen.entry}`}</Box>
          )}
        </Typography>
      )}

      {shown.length ? (
        <Stack
          spacing={0.75}
          /* Tall enough to compare most of a monorepo at once; capped by the window. */
          sx={
            many
              ? {
                  maxHeight: "min(600px, 58vh)",
                  overflowY: "auto",
                  pr: 0.5,
                  mr: -0.5,
                }
              : undefined
          }
        >
          {shown.map((a) => (
            <AgentRow
              key={a.id}
              agent={a}
              on={a.id === agentId}
              onPick={onPick}
              connected={connectedOf?.(a) || null}
              onOpenConnected={onOpenConnected}
              describe={describe}
            />
          ))}
        </Stack>
      ) : (
        <Box
          sx={{
            px: 1.5,
            py: 1.25,
            borderRadius: 1,
            bgcolor: "background.neutral",
          }}
        >
          <Typography
            sx={{ typography: "s2", color: "text.secondary" }}
          >{`No agent here matches “${query.trim()}”.`}</Typography>
        </Box>
      )}

      {q && shown.length > 0 && (
        <Typography
          sx={{
            typography: "s3",
            color: "text.subtitle",
            mt: 0.75,
            fontVariantNumeric: "tabular-nums",
          }}
        >
          {`${shown.length} of ${agents.length}`}
        </Typography>
      )}
    </Box>
  );
}

FoundAgentsList.propTypes = {
  agents: PropTypes.array.isRequired,
  agentId: PropTypes.string,
  /* Called with the agent picked. */
  onPick: PropTypes.func.isRequired,
  /* The agent in Agents a found one already is, or null — it's opened, not connected twice. */
  connectedOf: PropTypes.func,
  onOpenConnected: PropTypes.func,
  /* The line over the list, in place of "We found N agents". */
  heading: PropTypes.string,
  /* What the search box says it searches, what it searches, and each row's second line. */
  searchHint: PropTypes.string,
  searchOf: PropTypes.func,
  describe: PropTypes.func,
};

/* A scanned agent: searched by name, framework, file and prompts; described by framework · entry · tools · prompt files. */
const repoSearchOf = (a) =>
  `${a.name} ${a.framework} ${a.sdk || ""} ${a.entry} ${(a.prompts || []).join(" ")}`;
function repoLineOf(a, connected) {
  return (
    <>
      {`${a.framework} · `}
      <Box component="span" sx={{ fontFamily: MONO }}>
        {a.entry}
      </Box>
      {connected ? " · already in Agents" : ""}
      {` · ${a.tools} tool${a.tools === 1 ? "" : "s"} · ${(a.prompts || []).length} prompt file${(a.prompts || []).length === 1 ? "" : "s"}`}
    </>
  );
}

/* One found agent: its name, then framework · entry file · tools · prompt
   files. One that's already in Agents can't be connected again — it says so
   and opens instead. */
function AgentRow({
  agent: a,
  on,
  onPick,
  connected,
  onOpenConnected,
  describe,
}) {
  return (
    <Stack
      direction="row"
      alignItems="center"
      spacing={1.25}
      {...(connected
        ? {}
        : clickable(() => onPick(a), { role: "radio", checked: on }))}
      sx={{
        ...focusRing,
        px: 1.5,
        py: 1,
        borderRadius: 1,
        cursor: connected ? "default" : "pointer",
        flexShrink: 0,
        border: "1px solid",
        borderColor: on ? "text.primary" : "divider",
        bgcolor: on ? "action.selected" : "transparent",
      }}
    >
      {/* One agent per connection, so a radio. The row is the target; the radio says which one is chosen. */}
      {connected ? (
        <Iconify
          icon="solar:check-circle-linear"
          width={20}
          sx={{ color: "text.secondary", flexShrink: 0 }}
        />
      ) : (
        <Radio
          size="small"
          checked={on}
          tabIndex={-1}
          inputProps={{ "aria-label": `Choose ${a.name}` }}
          sx={{
            p: 0,
            flexShrink: 0,
            color: "text.disabled",
            "&.Mui-checked": { color: "text.primary" },
          }}
        />
      )}
      <Box flex={1} minWidth={0}>
        <Typography noWrap sx={{ typography: "s2_1", fontWeight: 500 }}>
          {a.name}
        </Typography>
        <Typography noWrap sx={{ typography: "s3", color: "text.subtitle" }}>
          {describe(a, connected)}
        </Typography>
      </Box>
      {connected && onOpenConnected && (
        <ButtonBase
          onClick={() => onOpenConnected(connected)}
          sx={{
            px: 1,
            height: 26,
            borderRadius: "4px",
            border: "1px solid",
            borderColor: "divider",
            typography: "s3",
            fontWeight: 600,
            flexShrink: 0,
            "&.Mui-focusVisible": focusRing,
          }}
        >
          Connected · Open
        </ButtonBase>
      )}
    </Stack>
  );
}
AgentRow.propTypes = {
  agent: PropTypes.object.isRequired,
  on: PropTypes.bool,
  onPick: PropTypes.func.isRequired,
  connected: PropTypes.object,
  onOpenConnected: PropTypes.func,
  describe: PropTypes.func.isRequired,
};
