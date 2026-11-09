import React, { useCallback, useState } from "react";
import { Button, Stack } from "@mui/material";
import Iconify from "src/components/iconify";
import { useRunConfigs } from "src/api/develop/r2";
import { planRescore, useRescore } from "src/sections/common/Runs/useRescore";
import { rescoreOnVersionTargets } from "./rescoreOnVersionModel";

/**
 * HO results R6 (W4-3 + W1-2): after Save corrections makes vN, the runs
 * still scored on an older version can be re-scored on vN through the one
 * re-score door (useRescore: the confirm with the judge's cost, a child run
 * per run, the flipped rows). `offer(number)` resolves to the toast's
 * action props and keeps the door (`Re-score 3 runs on v7`, shown under the
 * block as `line`) only when the server's plan of that very request says it
 * scores on vN (planRescore); otherwise, or when no run needs it, `{}` and
 * no door (a door whose every confirm would refuse is never shown).
 */
export default function useRescoreOnVersion(datasetId) {
  const r1 = useRunConfigs(datasetId);
  const rescore = useRescore({ datasetId });
  const { request } = rescore;
  const [door, setDoor] = useState(null);
  const offer = useCallback(
    async (number) => {
      setDoor(null);
      const t = rescoreOnVersionTargets(r1.data, number);
      if (!t) return {};
      const ask = {
        runs: t.runs,
        evalIdsOf: () => t.evalIds,
        evalNames: t.evalNames,
        datasetVersion: number,
        title: () => t.title,
      };
      const plan = await planRescore(ask).catch(() => null);
      if (!plan || plan.error) return {};
      const next = {
        label: t.label,
        // Done once: after the re-score the door goes (a second press would
        // re-score the same runs again).
        run: () =>
          request(ask).then((res) => {
            if (res && !res.cancelled) setDoor(null);
            return res;
          }),
      };
      setDoor(next);
      return {
        action: () => (
          <Button size="small" color="inherit" onClick={next.run}>
            {next.label}
          </Button>
        ),
      };
    },
    [r1.data, request],
  );
  const line = door ? (
    <Stack
      direction="row"
      alignItems="center"
      sx={{ px: 1.5, py: 1, borderTop: "1px solid", borderColor: "divider" }}
    >
      <Button
        size="small"
        variant="outlined"
        startIcon={<Iconify icon="mdi:refresh" width={16} />}
        onClick={door.run}
        data-testid="corrections-rescore"
      >
        {door.label}
      </Button>
    </Stack>
  ) : null;
  return { offer, line, dialog: rescore.dialog };
}
