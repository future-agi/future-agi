import React, { useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  Drawer,
  IconButton,
  Stack,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import ExperimentReviewSummary from "src/sections/develop-detail/Review/ExperimentReviewSummary";

/**
 * `Review summary` in the annotate header of an experiment review (W4-3,
 * W4-4): the same summary the All Done screen shows (results, corrections,
 * outside reviewers), open at any point of the review.
 */
export default function ReviewSummaryButton({ queueId, datasetId, from }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <Button
        size="small"
        variant="outlined"
        color="inherit"
        onClick={() => setOpen(true)}
        startIcon={<Iconify icon="mdi:chart-box-outline" width={16} />}
        sx={{ borderRadius: 0.75, fontWeight: 700, flexShrink: 0 }}
      >
        Review summary
      </Button>
      <Drawer
        anchor="right"
        open={open}
        onClose={() => setOpen(false)}
        PaperProps={{ sx: { width: 720, maxWidth: "100vw" } }}
      >
        <Stack direction="row" alignItems="center" sx={{ px: 2, py: 1.25 }}>
          <Typography
            typography="m3"
            fontWeight="fontWeightSemiBold"
            sx={{ flex: 1 }}
          >
            Review summary
          </Typography>
          <IconButton
            size="small"
            aria-label="Close"
            onClick={() => setOpen(false)}
          >
            <Iconify icon="mdi:close" width={18} />
          </IconButton>
        </Stack>
        <Box sx={{ px: 2, pb: 2, overflow: "auto" }}>
          {open ? (
            <ExperimentReviewSummary
              queueId={queueId}
              datasetId={datasetId}
              from={from}
            />
          ) : null}
        </Box>
      </Drawer>
    </>
  );
}

ReviewSummaryButton.propTypes = {
  queueId: PropTypes.string.isRequired,
  datasetId: PropTypes.string,
  from: PropTypes.string,
};
