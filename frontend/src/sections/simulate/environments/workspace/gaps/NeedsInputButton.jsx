import PropTypes from "prop-types";
import { useMemo, useState } from "react";
import { enqueueSnackbar } from "notistack";
import { Box, Button } from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import {
  SAMPLE_CREDENTIALS,
  SAMPLE_GAPS,
  SAMPLE_GAP_SUITE,
  SAMPLE_SIT_OUT,
} from "src/api/simulate-environments/_fixtures/detail/gaps";
import SideDrawer from "../../components/SideDrawer";
import useUiSample from "../../hooks/useUiSample";
import SetupGapsDrawer, { gapMeter } from "./SetupGapsDrawer";

export const UI_ONLY_SAVED = "UI only. Nothing was saved.";

// The header's Needs input inbox: an icon with the count of what's open, red
// only when something blocks a run. Opens Vel's setup-gaps drawer. The API has
// no setup questions yet, so the count shows only with ?uiSample=gaps.
export default function NeedsInputButton({ onGo }) {
  const sample = useUiSample("gaps");
  const [open, setOpen] = useState(false);
  const [answers, setAnswers] = useState({});

  const gaps = useMemo(
    () =>
      sample
        ? SAMPLE_GAPS.map((g) => {
            const a = answers[g.id];
            return a
              ? {
                  ...g,
                  status: a.status,
                  answered: a.value ?? g.answered,
                  by: "you",
                }
              : g;
          })
        : [],
    [sample, answers],
  );
  const meter = gapMeter(gaps);
  const openCount = meter.blocking + meter.optional;

  const onAnswer = (id, status, value) => {
    if (status === "reopen")
      setAnswers((prev) => {
        const next = { ...prev };
        delete next[id];
        return next;
      });
    else if (status !== "credential")
      setAnswers((prev) => ({ ...prev, [id]: { status, value } }));
    enqueueSnackbar(UI_ONLY_SAVED, { variant: "info" });
  };

  const tip = openCount
    ? `Needs input · ${meter.blocking ? `${meter.blocking} blocking, ` : ""}${meter.optional} optional`
    : "Needs input · nothing open";

  return (
    <>
      <CustomTooltip show size="small" arrow title={tip}>
        <Button
          size="small"
          variant="outlined"
          aria-label="Needs input"
          onClick={() => setOpen(true)}
          startIcon={<Iconify icon="solar:inbox-linear" width={16} />}
          sx={{
            whiteSpace: "nowrap",
            color: "text.primary",
            borderColor: "divider",
            minWidth: 0,
            // Icon only when nothing is open, so the button hugs it.
            ...(!openCount && { px: 0.875 }),
            "& .MuiButton-startIcon": { mr: openCount ? 0.75 : 0, ml: 0 },
          }}
        >
          {openCount > 0 && (
            <Box
              component="span"
              sx={{
                px: 0.625,
                minWidth: 18,
                height: 18,
                borderRadius: "4px",
                display: "inline-flex",
                alignItems: "center",
                justifyContent: "center",
                typography: "s3",
                fontWeight: 600,
                fontVariantNumeric: "tabular-nums",
                color: meter.blocking ? "error.main" : "text.secondary",
                bgcolor: meter.blocking ? "error.lighter" : "action.hover",
              }}
            >
              {openCount}
            </Box>
          )}
        </Button>
      </CustomTooltip>
      <SideDrawer
        open={open}
        onClose={() => setOpen(false)}
        width={{ xs: "100%", md: 640 }}
        hideClose
      >
        {open && (
          <SetupGapsDrawer
            gaps={gaps}
            credentials={sample ? SAMPLE_CREDENTIALS : []}
            suiteTotal={SAMPLE_GAP_SUITE.total}
            sitOut={sample ? SAMPLE_SIT_OUT : 0}
            onAnswer={onAnswer}
            onGo={onGo}
            onClose={() => setOpen(false)}
          />
        )}
      </SideDrawer>
    </>
  );
}

NeedsInputButton.propTypes = { onGo: PropTypes.func };
