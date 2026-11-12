import React, { useEffect, useMemo, useRef, useState } from "react";
import PropTypes from "prop-types";
import {
  Alert,
  Box,
  Button,
  InputAdornment,
  MenuItem,
  Popover,
  Skeleton,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { usePromptExecutions } from "src/api/develop/prompt";
import { useDebounce } from "src/hooks/use-debounce";
import { useGetListOfAgents } from "src/api/experiment/use-get-agents";
import { paths } from "src/routes/paths";
import { PLAYGROUND_AGENTS_PATH } from "src/sections/agent-playground/agentsTabs";
import { PROMPT_CONFIG_TYPE } from "./constants";
import { popoverPlacement, scrollBoundsOf } from "./popoverPlacement";

// Estimated heights for the placement (search box, one two-line item).
const SEARCH_H = 46;
const ITEM_H = 54;

// An empty catalogue says where to make one (FR-EXPERIMENTS-01). The page
// opens in a new tab so the open experiment draft is kept; Refresh list then
// reads the catalogue again and the new item is picked here.
// JRN-EXP-NO-PROMPT-DEADEND: when the wizard can take a prompt written in
// place (`onWrite`), that comes first; Prompts stays one click away.
const WRITE_COPY = {
  hint: "Write one here without leaving this experiment. It's saved to Prompts.",
  write: "Write a prompt",
  open: "Open Prompts",
};
const COPY = {
  prompt: {
    empty: "No saved prompts",
    hint: "Create and save one in Prompts, then refresh this list. It opens in a new tab, so this experiment stays open.",
    href: paths.dashboard.workbench,
    create: "Create a prompt",
    failed: "Couldn't load your prompts.",
  },
  agent: {
    empty: "No saved agents",
    hint: "Build and save one in Agent Playground, then refresh this list. It opens in a new tab, so this experiment stays open.",
    href: PLAYGROUND_AGENTS_PATH,
    create: "Create an agent",
    failed: "Couldn't load your agents.",
  },
};

/**
 * Picks one saved prompt or agent graph for a new run card (`+ Add run →
 * Prompt / Agent`). One click adds the run; versions and models are chosen
 * on the card. The popover stays inside the drawer body: below the card
 * when it fits, else above, its list scrolling inside a capped height (it
 * never runs over the drawer footer). The owner closes it on a failed
 * Next (ConfigureStepLLM `failedNext`), so errors never sit behind it.
 * A failed read shows an error with Retry, never the empty-catalogue copy.
 * With `onWrite`, an empty prompt catalogue offers `Write a prompt`, which
 * hands the run to the wizard (FirstPromptComposer) and closes.
 */
const RunTargetPicker = ({ anchorEl, type, onPick, onClose, onWrite }) => {
  const open = Boolean(anchorEl);
  const [search, setSearch] = useState("");
  const q = useDebounce(search, 300);
  const isPrompt = type === PROMPT_CONFIG_TYPE.PROMPT;
  const canWrite = isPrompt && typeof onWrite === "function";
  const prompts = usePromptExecutions(open && isPrompt, q, "chat");
  const agents = useGetListOfAgents(open && !isPrompt, q, [], false);
  const query = isPrompt ? prompts : agents;
  const copy = isPrompt ? COPY.prompt : COPY.agent;

  const items = useMemo(() => {
    const pages = query.data?.pages ?? [];
    return pages.flatMap((p) =>
      isPrompt ? p?.results ?? [] : p?.graphs ?? p?.results ?? [],
    );
  }, [query.data, isPrompt]);

  const close = () => {
    setSearch("");
    onClose?.();
  };

  // Room inside the drawer body, measured from the anchor at open and again
  // when the list length changes (MUI re-positions through `action`).
  const action = useRef(null);
  const failed = !query.isPending && query.isError && items.length === 0;
  // The empty-catalogue guide (copy and two actions) needs about three
  // rows of room, or its actions would scroll out of the capped list; the
  // Write a prompt action takes one more.
  const guide = !query.isPending && !failed && items.length === 0 && !search;
  const guideRows = canWrite ? 4 : 3;
  const count = query.isPending
    ? 3
    : guide
      ? guideRows
      : Math.max(1, items.length);
  const fit = useMemo(() => {
    if (!anchorEl?.getBoundingClientRect) return null;
    return popoverPlacement(
      anchorEl.getBoundingClientRect(),
      scrollBoundsOf(anchorEl),
      SEARCH_H + count * ITEM_H + 8,
    );
  }, [anchorEl, count]);
  const above = fit?.place === "above";
  useEffect(() => {
    if (open) action.current?.updatePosition?.();
  }, [open, count, fit?.maxHeight]);

  return (
    <Popover
      open={open}
      anchorEl={anchorEl}
      onClose={close}
      action={action}
      anchorOrigin={{
        vertical: above ? "top" : "bottom",
        horizontal: "right",
      }}
      transformOrigin={{
        vertical: above ? "bottom" : "top",
        horizontal: "right",
      }}
      disableAutoFocus
      slotProps={{
        paper: {
          "data-testid": "run-target-picker",
          sx: {
            mt: above ? -0.5 : 0.5,
            width: 300,
            maxHeight: fit?.maxHeight ?? 360,
            display: "flex",
            flexDirection: "column",
            border: "1px solid",
            borderColor: "divider",
            borderRadius: 1,
          },
        },
      }}
    >
      <Box sx={{ p: 1, pb: 0.5, flexShrink: 0 }}>
        <TextField
          fullWidth
          autoFocus
          size="small"
          placeholder={isPrompt ? "Search prompts" : "Search agents"}
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          InputProps={{
            startAdornment: (
              <InputAdornment position="start">
                <Iconify
                  icon="eva:search-fill"
                  width={16}
                  sx={{ color: "text.disabled" }}
                />
              </InputAdornment>
            ),
          }}
          sx={{ "& .MuiOutlinedInput-root": { fontSize: 13, height: 30 } }}
        />
      </Box>
      <Box
        sx={{ flex: 1, minHeight: 0, overflowY: "auto", py: 0.5 }}
        onScroll={(e) => {
          const el = e.currentTarget;
          if (
            el.scrollTop + el.clientHeight >= el.scrollHeight - 5 &&
            query.hasNextPage &&
            !query.isFetchingNextPage
          )
            query.fetchNextPage();
        }}
      >
        {query.isPending ? (
          [0, 1, 2].map((i) => (
            <Box key={i} sx={{ px: 1.5, py: 0.75 }}>
              <Skeleton variant="rectangular" height={18} width="70%" />
            </Box>
          ))
        ) : failed ? (
          <Alert
            severity="error"
            action={
              <Button
                color="inherit"
                size="small"
                disabled={Boolean(query.isFetching)}
                onClick={() => query.refetch?.()}
              >
                Retry
              </Button>
            }
            sx={{ mx: 1, my: 0.5 }}
          >
            {copy.failed}
          </Alert>
        ) : guide ? (
          <Box sx={{ px: 1.5, py: 1 }}>
            <Typography typography="s2" fontWeight="fontWeightMedium">
              {copy.empty}
            </Typography>
            <Typography
              typography="s3"
              color="text.secondary"
              sx={{ mt: 0.25 }}
            >
              {canWrite ? WRITE_COPY.hint : copy.hint}
            </Typography>
            {canWrite ? (
              <Button
                size="small"
                variant="contained"
                color="primary"
                data-testid="run-target-write"
                startIcon={
                  <Iconify icon="solar:pen-new-square-linear" width={14} />
                }
                onClick={() => {
                  onWrite();
                  close();
                }}
                sx={{ mt: 1 }}
              >
                {WRITE_COPY.write}
              </Button>
            ) : null}
            <Stack
              direction="row"
              alignItems="center"
              flexWrap="wrap"
              gap={0.5}
              sx={{ mt: 0.75 }}
            >
              <Button
                size="small"
                href={copy.href}
                target="_blank"
                rel="noopener noreferrer"
                endIcon={<Iconify icon="mdi:arrow-top-right" width={14} />}
                sx={{ color: "status.info.fg", typography: "s2" }}
              >
                {canWrite ? WRITE_COPY.open : copy.create}
              </Button>
              <Button
                size="small"
                color="inherit"
                disabled={Boolean(query.isFetching)}
                onClick={() => query.refetch?.()}
                startIcon={<Iconify icon="mdi:refresh" width={14} />}
                sx={{ typography: "s2" }}
              >
                {query.isFetching ? "Refreshing…" : "Refresh list"}
              </Button>
            </Stack>
          </Box>
        ) : items.length === 0 ? (
          <Typography
            typography="s2"
            color="text.secondary"
            sx={{ px: 1.5, py: 1 }}
          >
            No matches
          </Typography>
        ) : (
          items.map((item) => (
            <MenuItem
              key={item.id}
              dense
              onClick={() => {
                onPick(item);
                close();
              }}
              sx={{ px: 1.5, py: 0.75, gap: 1, alignItems: "flex-start" }}
            >
              <Box sx={{ minWidth: 0, flex: 1 }}>
                <Typography
                  typography="s1"
                  fontWeight="fontWeightMedium"
                  noWrap
                >
                  {item.name}
                </Typography>
                <Typography typography="s3" color="text.secondary" noWrap>
                  {isPrompt
                    ? ["Prompt", item.model ?? item.model_detail?.model_name]
                        .filter(Boolean)
                        .join(" · ")
                    : [
                        `${item.node_count ?? 0} nodes`,
                        item.active_version_number != null &&
                          `active v${item.active_version_number}`,
                      ]
                        .filter(Boolean)
                        .join(" · ")}
                </Typography>
              </Box>
            </MenuItem>
          ))
        )}
      </Box>
    </Popover>
  );
};

RunTargetPicker.propTypes = {
  anchorEl: PropTypes.any,
  type: PropTypes.oneOf(["prompt", "agent"]),
  onPick: PropTypes.func.isRequired,
  onClose: PropTypes.func,
  onWrite: PropTypes.func,
};

export default RunTargetPicker;
