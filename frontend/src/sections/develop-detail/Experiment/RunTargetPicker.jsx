import React, { useEffect, useMemo, useRef, useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  InputAdornment,
  MenuItem,
  Popover,
  Skeleton,
  TextField,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { usePromptExecutions } from "src/api/develop/prompt";
import { useDebounce } from "src/hooks/use-debounce";
import { useGetListOfAgents } from "src/api/experiment/use-get-agents";
import { PROMPT_CONFIG_TYPE } from "./constants";
import { popoverPlacement, scrollBoundsOf } from "./popoverPlacement";

// Estimated heights for the placement (search box, one two-line item).
const SEARCH_H = 46;
const ITEM_H = 54;

/**
 * Picks one saved prompt or agent graph for a new run card (`+ Add run →
 * Prompt / Agent`). One click adds the run; versions and models are chosen
 * on the card. The popover stays inside the drawer body: below the card
 * when it fits, else above, its list scrolling inside a capped height (it
 * never runs over the drawer footer). The owner closes it on a failed
 * Next (ConfigureStepLLM `failedNext`), so errors never sit behind it.
 */
const RunTargetPicker = ({ anchorEl, type, onPick, onClose }) => {
  const open = Boolean(anchorEl);
  const [search, setSearch] = useState("");
  const q = useDebounce(search, 300);
  const isPrompt = type === PROMPT_CONFIG_TYPE.PROMPT;
  const prompts = usePromptExecutions(open && isPrompt, q, "chat");
  const agents = useGetListOfAgents(open && !isPrompt, q, [], false);
  const query = isPrompt ? prompts : agents;

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
  const count = query.isPending ? 3 : Math.max(1, items.length);
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
        ) : items.length === 0 ? (
          <Typography
            typography="s2"
            color="text.secondary"
            sx={{ px: 1.5, py: 1 }}
          >
            {search
              ? "No matches"
              : isPrompt
                ? "No saved prompts yet. Create one in Prompts."
                : "No agents yet. Create one in Agent Playground."}
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
};

export default RunTargetPicker;
