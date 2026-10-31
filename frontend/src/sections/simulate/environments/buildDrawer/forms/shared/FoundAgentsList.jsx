import PropTypes from "prop-types";
import { useMemo, useRef, useState } from "react";
import { alpha } from "@mui/material/styles";
import { Box, ButtonBase, InputBase, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { T12, T13, focusRing } from "../../drawerStyles";
import { MONO } from "../formBits";
import SampleTag from "./SampleTag";

/* Past this many rows the list scrolls in its own frame. */
const SCROLL_FROM = 6;

/* A scanned agent: searched by name, framework, file and prompts. */
const repoSearchOf = (a) =>
  `${a.name} ${a.framework} ${a.sdk || ""} ${a.entry} ${(a.prompts || []).join(" ")}`;
function repoLineOf(a) {
  const prompts = (a.prompts || []).length;
  return (
    <>
      {`${a.framework} · `}
      <Box component="span" sx={{ fontFamily: MONO }}>
        {a.entry}
      </Box>
      {` · ${a.tools} tool${a.tools === 1 ? "" : "s"} · ${prompts} prompt file${prompts === 1 ? "" : "s"}`}
    </>
  );
}

/**
 * The agents a repository scan found, or a platform account lists, to pick
 * the one being connected: a search box, then one radio row each. Past a
 * handful of rows it scrolls in its own frame, and the chosen agent stays
 * named above the list when a search hides its row. `sample` marks the list
 * as sample data.
 */
export default function FoundAgentsList({
  agents,
  agentId,
  onPick,
  heading,
  searchHint = "name, framework or file",
  searchOf = repoSearchOf,
  describe = repoLineOf,
  sample = false,
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
      <Stack
        direction="row"
        alignItems="center"
        spacing={0.75}
        sx={{ mb: 0.5 }}
      >
        <Typography sx={{ ...T12, fontWeight: 600 }}>
          {heading ||
            (agents.length === 1
              ? "We found 1 agent"
              : `We found ${agents.length} agents. Which one do you mean?`)}
        </Typography>
        {sample && <SampleTag />}
      </Stack>

      <Box
        sx={{
          display: "flex",
          alignItems: "center",
          gap: 1,
          px: 1.25,
          height: 32,
          mb: 0.75,
          border: "1px solid",
          borderColor: "divider",
          borderRadius: "6px",
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
              : `Search ${agents.length} agents by ${searchHint}`
          }
          inputProps={{
            "aria-label": "Search the agents found",
            spellCheck: false,
          }}
          sx={{
            ...T12,
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

      {chosenHidden && (
        <Typography sx={{ ...T12, color: "text.secondary", mb: 0.75 }}>
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
          role="radiogroup"
          aria-label="Agents found"
          spacing={0.75}
          sx={
            many
              ? {
                  maxHeight: "min(420px, 50vh)",
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
              describe={describe}
            />
          ))}
        </Stack>
      ) : (
        <Box
          sx={{
            px: 1.5,
            py: 1.25,
            borderRadius: "6px",
            bgcolor: "background.neutral",
          }}
        >
          <Typography
            sx={{ ...T12, color: "text.secondary" }}
          >{`No agent here matches “${query.trim()}”.`}</Typography>
        </Box>
      )}

      {q && shown.length > 0 && (
        <Typography
          sx={{
            ...T12,
            color: "text.secondary",
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
  onPick: PropTypes.func.isRequired,
  heading: PropTypes.string,
  searchHint: PropTypes.string,
  searchOf: PropTypes.func,
  describe: PropTypes.func,
  sample: PropTypes.bool,
};

/* One found agent: its name, then what it is. One agent per connection, so a radio. */
function AgentRow({ agent: a, on, onPick, describe }) {
  return (
    <ButtonBase
      role="radio"
      aria-checked={on}
      aria-label={a.name}
      onClick={() => onPick(a)}
      sx={{
        display: "flex",
        alignItems: "center",
        gap: 1.25,
        px: 1.5,
        py: 1,
        textAlign: "left",
        borderRadius: "6px",
        flexShrink: 0,
        border: "1px solid",
        borderColor: on ? "text.primary" : "divider",
        bgcolor: (t) =>
          on ? alpha(t.palette.text.primary, 0.08) : "transparent",
        "&.Mui-focusVisible": focusRing,
      }}
    >
      <Iconify
        icon={on ? "mdi:radiobox-marked" : "mdi:radiobox-blank"}
        width={18}
        sx={{ color: on ? "text.primary" : "text.disabled", flexShrink: 0 }}
      />
      <Box flex={1} minWidth={0}>
        <Typography noWrap sx={{ ...T13, fontWeight: 500 }}>
          {a.name}
        </Typography>
        <Typography noWrap sx={{ ...T12, color: "text.secondary" }}>
          {describe(a)}
        </Typography>
      </Box>
    </ButtonBase>
  );
}
AgentRow.propTypes = {
  agent: PropTypes.object.isRequired,
  on: PropTypes.bool,
  onPick: PropTypes.func.isRequired,
  describe: PropTypes.func.isRequired,
};
