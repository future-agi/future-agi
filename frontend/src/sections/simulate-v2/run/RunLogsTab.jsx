import PropTypes from "prop-types";
import { useMemo, useState } from "react";
import { Button, MenuItem, Select, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import LogViewer from "../components/logs/LogViewer";
import useLogClock from "../components/logs/useLogClock";
import { runLogOf } from "../_mock/sandboxLogs";

const HOST_LINE = {
  sandbox: "Your agent ran in this sandbox, next to the worlds.",
  platform: (h) =>
    `Your agent runs on ${h.label}. Its own logs stay there; this covers the worlds and every call to it.`,
  endpoint: (h) =>
    `Your agent runs on ${h.label}. This covers the worlds and every call we made to it.`,
  builtin: "Your agent runs in Future AGI on our model.",
};

/**
 * The run page's Logs tab: the run sandbox's log, narrowed to one world or
 * one trial when asked. A trial opens its drawer from here.
 */
export default function RunLogsTab({ run, agent, tasks, onOpenTrial }) {
  const live = run?.status === "running";
  const now = useLogClock(live, 2000);
  const log = useMemo(
    () => runLogOf({ run, agent, tasks, now }),
    [run, agent, tasks, now],
  );
  const [world, setWorld] = useState("all");
  const [trial, setTrial] = useState("all");

  const worlds = useMemo(() => {
    const set = new Set();
    Object.values(log?.trialSpans || {}).forEach((s) => set.add(s.world));
    return [...set].sort();
  }, [log]);
  const trials = useMemo(
    () =>
      (tasks || []).map((t) => ({
        key: `${t.id}#0`,
        id: t.id,
        title: t.title || t.name || t.id,
      })),
    [tasks],
  );

  const shown = useMemo(() => {
    if (!log || (world === "all" && trial === "all")) return log;
    return {
      ...log,
      steps: log.steps.map((s) => ({
        ...s,
        lines: s.lines.filter(
          (l) =>
            (world === "all" || !l.world || l.world === world) &&
            (trial === "all" ||
              !l.trialKey ||
              l.trialKey.startsWith(`${trial}#`)),
        ),
      })),
    };
  }, [log, world, trial]);

  const hostLine = log?.host ? HOST_LINE[log.host.kind] : null;

  return (
    <Stack spacing={1.5}>
      <Stack
        direction="row"
        alignItems="center"
        spacing={1}
        flexWrap="wrap"
        useFlexGap
      >
        <Typography
          sx={{
            typography: "s2",
            color: "text.secondary",
            flex: 1,
            minWidth: 240,
          }}
        >
          {typeof hostLine === "function" ? hostLine(log.host) : hostLine}
        </Typography>
        {worlds.length > 1 && (
          <Select
            size="small"
            value={world}
            onChange={(e) => setWorld(e.target.value)}
            sx={{ typography: "s2", height: 32 }}
          >
            <MenuItem value="all" sx={{ typography: "s2" }}>
              All worlds
            </MenuItem>
            {worlds.map((w) => (
              <MenuItem key={w} value={w} sx={{ typography: "s2" }}>
                World {w}
              </MenuItem>
            ))}
          </Select>
        )}
        <Select
          size="small"
          value={trial}
          onChange={(e) => setTrial(e.target.value)}
          sx={{ typography: "s2", height: 32, maxWidth: 280 }}
        >
          <MenuItem value="all" sx={{ typography: "s2" }}>
            All scenarios
          </MenuItem>
          {trials.map((t) => (
            <MenuItem key={t.id} value={t.id} sx={{ typography: "s2" }}>
              {t.title}
            </MenuItem>
          ))}
        </Select>
        {trial !== "all" && (
          <Button
            size="small"
            variant="outlined"
            color="inherit"
            onClick={() => onOpenTrial?.(trial)}
            endIcon={<Iconify icon="solar:arrow-right-up-linear" width={14} />}
          >
            Open scenario
          </Button>
        )}
      </Stack>
      <LogViewer
        log={shown}
        maxHeight="calc(100vh - 360px)"
        emptyText="This run hasn't started a sandbox yet."
      />
    </Stack>
  );
}

RunLogsTab.propTypes = {
  run: PropTypes.object,
  agent: PropTypes.object,
  tasks: PropTypes.array,
  onOpenTrial: PropTypes.func,
};
