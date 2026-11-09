import React, { useEffect } from "react";
import PropTypes from "prop-types";
import { Box, ButtonBase, IconButton, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import { runLabel } from "src/sections/common/Runs/runLabel";

/**
 * `◀ #2B · #2F · #2G ▶`: moves the right-hand side of a trace pair along a
 * run's lineage (root → iterations) while the left side stays. `[` and `]`
 * step (captured before the drawer's own trial keys while it is shown).
 *
 * @param {object[]} props.chain  runs, root first (lineageOf().chain)
 * @param {string} props.value  the run on the right
 * @param {(runId: string) => void} props.onChange
 */
const WalkChainStepper = ({ chain, value, onChange }) => {
  const i = chain.findIndex((r) => r.run_id === value);
  const prev = i > 0 ? chain[i - 1] : null;
  const next = i >= 0 && i < chain.length - 1 ? chain[i + 1] : null;

  useEffect(() => {
    const onKey = (e) => {
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      const tag = document.activeElement?.tagName;
      if (
        tag === "INPUT" ||
        tag === "TEXTAREA" ||
        document.activeElement?.isContentEditable
      )
        return;
      const target = e.key === "[" ? prev : e.key === "]" ? next : null;
      if (!target && e.key !== "[" && e.key !== "]") return;
      e.preventDefault();
      e.stopPropagation();
      if (target) onChange(target.run_id);
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [prev, next, onChange]);

  if (chain.length < 2) return null;
  return (
    <Stack
      direction="row"
      alignItems="center"
      gap={0.25}
      aria-label="Walk the chain"
      sx={{
        border: "1px solid",
        borderColor: "divider",
        borderRadius: 0.5,
        px: 0.25,
        height: 28,
      }}
    >
      <CustomTooltip
        show
        size="small"
        placement="top"
        arrow
        title="Previous in the chain ( [ )"
      >
        <span>
          <IconButton
            size="small"
            disabled={!prev}
            onClick={() => prev && onChange(prev.run_id)}
            aria-label="Previous run in the chain"
            sx={{ p: 0.25 }}
          >
            <Iconify icon="mdi:chevron-left" width={16} />
          </IconButton>
        </span>
      </CustomTooltip>
      {chain.map((r, k) => (
        <React.Fragment key={r.run_id}>
          {k > 0 && (
            <Typography typography="s3" color="text.disabled">
              ·
            </Typography>
          )}
          <ButtonBase
            onClick={() => onChange(r.run_id)}
            aria-current={r.run_id === value ? "true" : undefined}
            sx={{
              px: 0.5,
              height: 22,
              borderRadius: 0.5,
              typography: "s2",
              fontWeight: r.run_id === value ? 600 : 400,
              color: r.run_id === value ? "text.primary" : "text.secondary",
              bgcolor: r.run_id === value ? "action.selected" : "transparent",
            }}
          >
            {runLabel(r)}
          </ButtonBase>
        </React.Fragment>
      ))}
      <CustomTooltip
        show
        size="small"
        placement="top"
        arrow
        title="Next in the chain ( ] )"
      >
        <span>
          <IconButton
            size="small"
            disabled={!next}
            onClick={() => next && onChange(next.run_id)}
            aria-label="Next run in the chain"
            sx={{ p: 0.25 }}
          >
            <Iconify icon="mdi:chevron-right" width={16} />
          </IconButton>
        </span>
      </CustomTooltip>
      <Box sx={{ width: 2 }} />
    </Stack>
  );
};

WalkChainStepper.propTypes = {
  chain: PropTypes.array.isRequired,
  value: PropTypes.string,
  onChange: PropTypes.func.isRequired,
};

export default WalkChainStepper;
