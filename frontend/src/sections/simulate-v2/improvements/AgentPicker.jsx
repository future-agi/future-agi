import PropTypes from "prop-types";
import { useEffect, useMemo, useRef, useState } from "react";
import {
  Box,
  ClickAwayListener,
  Divider,
  InputAdornment,
  Paper,
  Popper,
  Stack,
  TextField,
  Tooltip,
  Typography,
} from "@mui/material";
import { useTheme } from "@mui/material/styles";
import Iconify from "src/components/iconify";
import { clickable, focusRing } from "../components/clickable";

const TYPE_ICON = {
  Voice: "solar:phone-calling-rounded-linear",
  Chat: "solar:chat-round-linear",
};

/**
 * The agent to improve — the same two-column picker as the dashboards'
 * filter attributes (search on top, categories on the left, rows on the
 * right): agent types first, then where each agent lives, so a long list is
 * one click and a scan, not a scroll.
 *
 *   choices: [{ key, name, type: "Voice" | "Chat", source, version, envs, off, offReason }]
 *
 * `locked` keeps the same field but fixes it to `value` — Debug failures
 * improves the agent its run tested — and says why on hover.
 */
export default function AgentPicker({
  choices,
  value,
  onChange,
  locked = null,
  emptyText = null,
  emptyAction = null,
}) {
  const theme = useTheme();
  const anchor = useRef(null);
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState("");
  const [category, setCategory] = useState("all");
  useEffect(() => {
    if (open) setSearch("");
  }, [open]);

  const sources = useMemo(
    () => [...new Set(choices.map((c) => c.source).filter(Boolean))].sort(),
    [choices],
  );
  const inCategory = (c, key) =>
    key === "all" || c.type === key || `source:${c.source}` === key;
  const countOf = (key) => choices.filter((c) => inCategory(c, key)).length;
  const q = search.trim().toLowerCase();
  const shown = choices.filter(
    (c) =>
      inCategory(c, category) &&
      (!q ||
        `${c.name} ${c.source} ${c.type} ${c.version}`
          .toLowerCase()
          .includes(q)),
  );
  const picked = choices.find((c) => c.key === value) || null;

  const categories = [
    { key: "all", label: "All", icon: "solar:widget-4-linear" },
    ...["Voice", "Chat"]
      .filter((t) => countOf(t))
      .map((t) => ({ key: t, label: t, icon: TYPE_ICON[t] })),
  ];
  const pick = (c) => {
    onChange(c.key);
    setOpen(false);
  };

  if (locked) {
    return (
      <Tooltip arrow placement="bottom-start" title={locked}>
        <Stack
          direction="row"
          alignItems="center"
          spacing={1}
          tabIndex={0}
          aria-disabled="true"
          sx={{
            ...focusRing,
            px: 1.5,
            height: 40,
            borderRadius: 1,
            border: "1px solid",
            borderColor: "divider",
            bgcolor: "action.hover",
          }}
        >
          {picked && (
            <Iconify
              icon={TYPE_ICON[picked.type]}
              width={16}
              sx={{ color: "text.secondary", flexShrink: 0 }}
            />
          )}
          <Typography noWrap sx={{ typography: "s2", flex: 1, minWidth: 0 }}>
            {picked?.name || "—"}
          </Typography>
          {picked?.version && (
            <Typography
              sx={{
                typography: "s2",
                color: "text.tertiary",
                flexShrink: 0,
                fontFamily: "ui-monospace, Menlo, monospace",
              }}
            >
              {picked.version}
            </Typography>
          )}
          <Iconify
            icon="solar:lock-keyhole-minimalistic-linear"
            width={14}
            sx={{ color: "text.disabled", flexShrink: 0 }}
          />
        </Stack>
      </Tooltip>
    );
  }

  return (
    <>
      {/* The field: what's picked, or a prompt — the picker opens under it. */}
      <Stack
        ref={anchor}
        direction="row"
        alignItems="center"
        spacing={1}
        {...clickable(() => setOpen((o) => !o), { expanded: open })}
        sx={{
          ...focusRing,
          px: 1.5,
          height: 40,
          borderRadius: 1,
          cursor: "pointer",
          border: "1px solid",
          borderColor: open ? "text.primary" : "divider",
          "&:hover": { borderColor: open ? "text.primary" : "text.disabled" },
        }}
      >
        {picked && (
          <Iconify
            icon={TYPE_ICON[picked.type]}
            width={16}
            sx={{ color: "text.secondary", flexShrink: 0 }}
          />
        )}
        <Typography
          noWrap
          sx={{
            typography: "s2",
            flex: 1,
            minWidth: 0,
            color: picked ? "text.primary" : "text.disabled",
          }}
        >
          {picked ? picked.name : "Choose an agent"}
        </Typography>
        <Iconify
          icon={
            open ? "solar:alt-arrow-up-linear" : "solar:alt-arrow-down-linear"
          }
          width={14}
          sx={{ color: "text.subtitle", flexShrink: 0 }}
        />
      </Stack>

      <Popper
        open={open}
        anchorEl={anchor.current}
        placement="bottom-start"
        sx={{ zIndex: 1300 }}
      >
        <ClickAwayListener
          onClickAway={(e) => {
            if (!anchor.current?.contains(e.target)) setOpen(false);
          }}
        >
          <Paper
            elevation={8}
            onKeyDown={(e) => {
              if (e.key === "Escape") setOpen(false);
            }}
            sx={{
              width: anchor.current?.offsetWidth || 600,
              maxHeight: 440,
              mt: 0.5,
              display: "flex",
              flexDirection: "column",
            }}
          >
            <Box sx={{ p: 1.5 }}>
              <TextField
                size="small"
                fullWidth
                autoFocus
                placeholder="Search agents..."
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && shown.length === 1 && !shown[0].off)
                    pick(shown[0]);
                }}
                InputProps={{
                  startAdornment: (
                    <InputAdornment position="start">
                      <Iconify
                        icon="solar:magnifier-linear"
                        width={16}
                        sx={{ color: "text.disabled" }}
                      />
                    </InputAdornment>
                  ),
                  endAdornment: (
                    <InputAdornment position="end">
                      <Typography
                        variant="caption"
                        sx={{ color: "text.disabled", fontSize: 12 }}
                      >{`${shown.length} agents`}</Typography>
                    </InputAdornment>
                  ),
                }}
              />
            </Box>
            <Divider />
            <Box
              sx={{
                display: "flex",
                flex: 1,
                minHeight: 0,
                overflow: "hidden",
              }}
            >
              {/* Types, then sources — each with its count. */}
              <Box
                sx={{
                  width: 190,
                  flexShrink: 0,
                  borderRight: `1px solid ${theme.palette.divider}`,
                  overflow: "auto",
                  py: 0.5,
                }}
              >
                {categories.map((cat) => (
                  <RailItem
                    key={cat.key}
                    cat={cat}
                    on={category === cat.key}
                    count={countOf(cat.key)}
                    onClick={() => setCategory(cat.key)}
                  />
                ))}
                {sources.length > 1 && (
                  <>
                    <Typography
                      sx={{
                        px: 2,
                        pt: 1.25,
                        pb: 0.5,
                        typography: "s3",
                        fontWeight: 600,
                        color: "text.disabled",
                      }}
                    >
                      Source
                    </Typography>
                    {sources.map((src) => (
                      <RailItem
                        key={src}
                        on={category === `source:${src}`}
                        count={countOf(`source:${src}`)}
                        cat={{
                          key: `source:${src}`,
                          label: src,
                          icon: "solar:link-round-linear",
                        }}
                        onClick={() => setCategory(`source:${src}`)}
                      />
                    ))}
                  </>
                )}
              </Box>
              <Box
                sx={{
                  flex: 1,
                  minWidth: 0,
                  overflow: "auto",
                  maxHeight: 340,
                  py: 0.5,
                }}
              >
                {shown.map((c) => (
                  <Box
                    key={c.key}
                    {...clickable(c.off ? undefined : () => pick(c), {
                      disabled: c.off,
                      pressed: c.key === value,
                    })}
                    title={c.off ? c.offReason : undefined}
                    sx={{
                      display: "flex",
                      alignItems: "center",
                      gap: 1,
                      px: 1.5,
                      py: 0.75,
                      mx: 0.5,
                      borderRadius: 1,
                      cursor: c.off ? "default" : "pointer",
                      opacity: c.off ? 0.45 : 1,
                      bgcolor:
                        c.key === value ? "action.selected" : "transparent",
                      "&:hover": {
                        bgcolor: c.off
                          ? "transparent"
                          : c.key === value
                            ? "action.selected"
                            : "action.hover",
                      },
                      ...focusRing,
                    }}
                  >
                    <Iconify
                      icon={TYPE_ICON[c.type]}
                      width={16}
                      sx={{ color: "text.disabled", flexShrink: 0 }}
                    />
                    <Typography
                      variant="body2"
                      title={c.name}
                      sx={{
                        fontSize: "13px",
                        flex: 1,
                        minWidth: 0,
                        overflow: "hidden",
                        textOverflow: "ellipsis",
                        whiteSpace: "nowrap",
                      }}
                    >
                      {c.name}
                    </Typography>
                    {c.off ? (
                      <Typography
                        sx={{
                          typography: "s2",
                          color: "text.tertiary",
                          flexShrink: 0,
                        }}
                      >
                        {c.offReason || "Unavailable"}
                      </Typography>
                    ) : (
                      <>
                        <Typography
                          sx={{
                            typography: "s2",
                            color: "text.tertiary",
                            flexShrink: 0,
                            fontFamily: "ui-monospace, Menlo, monospace",
                          }}
                        >
                          {c.version}
                        </Typography>
                        <Typography
                          sx={{
                            typography: "s2",
                            color: "text.tertiary",
                            flexShrink: 0,
                          }}
                        >
                          {c.envs
                            ? `${c.envs} ${c.envs === 1 ? "env" : "envs"}`
                            : "No environment yet"}
                        </Typography>
                      </>
                    )}
                  </Box>
                ))}
                {shown.length === 0 && (
                  <Box sx={{ p: 3, textAlign: "center" }}>
                    <Typography variant="body2" color="text.disabled">
                      {choices.length
                        ? "No agent matches"
                        : emptyText || "No agents to pick from."}
                    </Typography>
                    {/* Searched and it isn't there: bring it in from here. */}
                    {emptyAction && (
                      <Typography
                        component="button"
                        type="button"
                        onClick={() => {
                          setOpen(false);
                          emptyAction.onClick();
                        }}
                        sx={{
                          ...focusRing,
                          mt: 0.75,
                          p: 0,
                          border: 0,
                          bgcolor: "transparent",
                          cursor: "pointer",
                          typography: "s2",
                          fontWeight: 500,
                          color: "text.primary",
                          textDecoration: "underline",
                          textUnderlineOffset: "3px",
                        }}
                      >
                        {emptyAction.label}
                      </Typography>
                    )}
                  </Box>
                )}
              </Box>
            </Box>
          </Paper>
        </ClickAwayListener>
      </Popper>
    </>
  );
}

AgentPicker.propTypes = {
  choices: PropTypes.arrayOf(
    PropTypes.shape({
      key: PropTypes.string.isRequired,
      name: PropTypes.string.isRequired,
      type: PropTypes.oneOf(["Voice", "Chat"]).isRequired,
      source: PropTypes.string,
      version: PropTypes.string,
      envs: PropTypes.number,
      off: PropTypes.bool,
      offReason: PropTypes.string,
    }),
  ).isRequired,
  value: PropTypes.string,
  onChange: PropTypes.func,
  /* Why the agent can't be changed here — shown on hover. */
  locked: PropTypes.string,
  /* Why there are no choices at all — the caller knows what it left out. */
  emptyText: PropTypes.string,
  /* A link under "No agent matches", e.g. Connect a new agent. */
  emptyAction: PropTypes.shape({
    label: PropTypes.string.isRequired,
    onClick: PropTypes.func.isRequired,
  }),
};

function RailItem({ cat, on, count, onClick }) {
  return (
    <Box
      {...clickable(onClick, { pressed: on })}
      sx={{
        display: "flex",
        alignItems: "center",
        gap: 1,
        px: 1.5,
        minHeight: 28,
        mx: 0.5,
        cursor: "pointer",
        borderRadius: "6px",
        bgcolor: on ? "action.selected" : "transparent",
        "&:hover": { bgcolor: on ? "action.selected" : "action.hover" },
        ...focusRing,
      }}
    >
      <Iconify
        icon={cat.icon}
        width={16}
        sx={{ color: on ? "text.primary" : "text.secondary", flexShrink: 0 }}
      />
      <Typography
        variant="body2"
        noWrap
        sx={{
          fontSize: "13px",
          flex: 1,
          minWidth: 0,
          fontWeight: on ? 500 : 400,
          color: on ? "text.primary" : "text.secondary",
        }}
      >
        {cat.label}
      </Typography>
      <Typography
        variant="caption"
        sx={{ color: "text.disabled", fontSize: 12 }}
      >
        {count}
      </Typography>
    </Box>
  );
}
RailItem.propTypes = {
  cat: PropTypes.object.isRequired,
  on: PropTypes.bool,
  count: PropTypes.number,
  onClick: PropTypes.func.isRequired,
};
