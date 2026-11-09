import React, { useMemo, useState } from "react";
import PropTypes from "prop-types";
import { Box, Button, Menu, Stack, Typography } from "@mui/material";
import { useLocation, useNavigate } from "react-router-dom";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import { iterateHref } from "src/sections/common/Analysis/analysisLinks";
import { isolateOptions } from "./isolate";

/**
 * `Isolate ▾` (R2-SPEC §3.3.3): one line per changed axis. An existing run
 * that changes only that axis → its effect + `Compare`; else `Run it`
 * (≈ cost · time) → the Iterate dock with that single change.
 */
const IsolateMenu = ({ baseline, candidate, runs, readOnly }) => {
  const [anchor, setAnchor] = useState(null);
  const navigate = useNavigate();
  const { pathname, search } = useLocation();
  const { options } = useMemo(
    () => isolateOptions(baseline, candidate, runs),
    [baseline, candidate, runs],
  );
  if (!options.length) return null;
  const compare = (run) => {
    const params = new URLSearchParams(search);
    params.set("runs", [baseline.run_id, run.run_id].join(","));
    params.set("baseline", baseline.run_id);
    params.delete("cand");
    const base = pathname.includes("/compare/")
      ? pathname
      : `/dashboard/develop/${baseline.dataset_id}/compare/data`;
    navigate(`${base}?${params.toString()}`);
  };
  const runIt = (opt) => {
    const value =
      opt.axis === "model"
        ? candidate.config?.model?.id
        : opt.axis === "prompt"
          ? candidate.config?.prompt?.version
          : null;
    navigate(
      iterateHref(`${pathname}${search}`, {
        runId: baseline.run_id,
        scope: "all",
        change: value ? `${opt.axis}:${value}` : opt.axis,
      }),
    );
  };
  return (
    <>
      <Button
        size="small"
        variant="outlined"
        onClick={(e) => setAnchor(e.currentTarget)}
        endIcon={<Iconify icon="mdi:chevron-down" width={16} />}
        sx={{
          height: 24,
          px: 1,
          typography: "s2",
          flexShrink: 0,
          borderColor: "divider",
          color: "text.primary",
        }}
      >
        Isolate
      </Button>
      <Menu
        anchorEl={anchor}
        open={Boolean(anchor)}
        onClose={() => setAnchor(null)}
        slotProps={{ paper: { sx: { width: 460, p: 0.5 } } }}
      >
        {options.map((opt) => (
          <Stack
            key={opt.axis}
            direction="row"
            alignItems="flex-start"
            gap={1}
            sx={{
              px: 1.25,
              py: 1,
              "&:not(:last-of-type)": {
                borderBottom: "1px solid",
                borderColor: "divider",
              },
            }}
          >
            <Box sx={{ flex: 1, minWidth: 0 }}>
              <Typography typography="s2">{opt.text}</Typography>
              {opt.action === "run" && (
                <Typography typography="s3" color="text.secondary">
                  {opt.estimateText}
                </Typography>
              )}
            </Box>
            {opt.action === "compare" ? (
              <Button
                size="small"
                variant="outlined"
                onClick={() => {
                  setAnchor(null);
                  compare(opt.run);
                }}
                sx={{ flexShrink: 0 }}
              >
                Compare
              </Button>
            ) : (
              <CustomTooltip
                show={readOnly}
                size="small"
                placement="top"
                arrow
                title="Editors can start runs"
              >
                <span>
                  <Button
                    size="small"
                    variant="contained"
                    disabled={readOnly}
                    onClick={() => {
                      setAnchor(null);
                      runIt(opt);
                    }}
                    sx={{ flexShrink: 0, whiteSpace: "nowrap" }}
                  >
                    Run it
                  </Button>
                </span>
              </CustomTooltip>
            )}
          </Stack>
        ))}
      </Menu>
    </>
  );
};

IsolateMenu.propTypes = {
  baseline: PropTypes.object,
  candidate: PropTypes.object,
  runs: PropTypes.array,
  readOnly: PropTypes.bool,
};

export default IsolateMenu;
