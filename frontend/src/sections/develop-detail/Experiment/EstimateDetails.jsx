import React, { useId, useRef } from "react";
import PropTypes from "prop-types";
import { Box, Button, Collapse, Stack } from "@mui/material";
import Iconify from "src/components/iconify";
import CostEstimateLine, { SimpleEstimate } from "./CostEstimateLine";

/**
 * A2-13: the last step's ONE estimate. The footer reads the plain sentence
 * (`About $0.42 and 6 minutes for 30 rows × 3 runs`) and a `Details` toggle;
 * the breakdown (CostEstimateLine: calls, judges, carry-over, the cheaper
 * split) opens in place right above the footer, with G's InlineDetail look
 * (2 px left rule, 8 px indent; Esc closes and returns focus to `Details`).
 *
 * Local wrapper (gap recorded): G's InlineDetail renders its detail under
 * its summary; here the summary sits in the footer row and the detail above
 * it, so the toggle and the region are two pieces sharing one id.
 */
export function EstimateSummary({
  control,
  open,
  onToggle,
  controlsId,
  btnRef,
}) {
  return (
    <Stack
      direction="row"
      alignItems="center"
      gap={0.75}
      sx={{ flex: 1, minWidth: 0 }}
    >
      <Box sx={{ minWidth: 0, flexShrink: 1, display: "flex" }}>
        <SimpleEstimate control={control} />
      </Box>
      <Button
        ref={btnRef}
        size="small"
        aria-expanded={open}
        aria-controls={controlsId}
        data-testid="estimate-details-toggle"
        onClick={onToggle}
        endIcon={
          <Iconify
            icon={open ? "mdi:chevron-down" : "mdi:chevron-up"}
            width={16}
          />
        }
        sx={{
          typography: "s2",
          minWidth: 0,
          px: 0.75,
          flexShrink: 0,
          whiteSpace: "nowrap",
          "& .MuiButton-endIcon": { ml: 0.25 },
        }}
      >
        {open ? "Hide details" : "Details"}
      </Button>
    </Stack>
  );
}

EstimateSummary.propTypes = {
  control: PropTypes.object.isRequired,
  open: PropTypes.bool,
  onToggle: PropTypes.func.isRequired,
  controlsId: PropTypes.string,
  btnRef: PropTypes.object,
};

export function EstimateBreakdown({
  control,
  setValue,
  open,
  onClose,
  id,
  btnRef,
}) {
  return (
    <Collapse in={open} unmountOnExit timeout={120}>
      <Box
        id={id}
        role="region"
        aria-label="Cost and time estimate details"
        data-testid="estimate-details"
        onKeyDown={(e) => {
          if (e.key !== "Escape") return;
          e.stopPropagation();
          e.preventDefault();
          onClose();
          btnRef?.current?.focus();
        }}
        sx={{
          ml: 1,
          mb: 1,
          pl: 1,
          pr: 6,
          borderLeft: "2px solid",
          borderColor: "divider",
          "& [data-testid='cost-estimate']": { pt: 0 },
        }}
      >
        <CostEstimateLine control={control} setValue={setValue} />
      </Box>
    </Collapse>
  );
}

EstimateBreakdown.propTypes = {
  control: PropTypes.object.isRequired,
  setValue: PropTypes.func.isRequired,
  open: PropTypes.bool,
  onClose: PropTypes.func.isRequired,
  id: PropTypes.string,
  btnRef: PropTypes.object,
};

/** `{open, toggle, close, id, btnRef}` shared by the two pieces. */
export function useEstimateDetails() {
  const [open, setOpen] = React.useState(false);
  const id = useId();
  const btnRef = useRef(null);
  return {
    open,
    toggle: () => setOpen((o) => !o),
    close: () => setOpen(false),
    id,
    btnRef,
  };
}
