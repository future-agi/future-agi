import { useState } from "react";
import PropTypes from "prop-types";
import { Box, Collapse, Link, Stack, Typography } from "@mui/material";

import Iconify from "src/components/iconify";

import { failureHeadline } from "./failureWords";
import { readable } from "./harnessShared";

const recordedLine = (failure) =>
  [failure.domain, failure.code].filter(Boolean).map(readable).join(" · ");

export default function RunFailure({ failure, dense }) {
  const [open, setOpen] = useState(false);
  const headline = failureHeadline(failure);
  const text = dense ? "caption" : "body2";

  if (!headline) {
    return (
      <>
        <Typography variant={dense ? "caption" : "subtitle2"}>
          {recordedLine(failure)}
        </Typography>
        {failure.message && (
          <Typography variant={text} component="div">
            {failure.message}
          </Typography>
        )}
      </>
    );
  }

  return (
    <Stack spacing={0.25} sx={{ minWidth: 0 }}>
      <Typography variant={dense ? "caption" : "subtitle2"}>
        {headline}
      </Typography>
      <Link
        component="button"
        type="button"
        variant="caption"
        underline="hover"
        color="text.secondary"
        aria-expanded={open}
        onClick={() => setOpen((wasOpen) => !wasOpen)}
        sx={{
          display: "inline-flex",
          alignItems: "center",
          gap: 0.25,
          alignSelf: "flex-start",
        }}
      >
        Details
        <Iconify
          icon={
            open ? "solar:alt-arrow-up-linear" : "solar:alt-arrow-down-linear"
          }
          width={14}
        />
      </Link>
      <Collapse in={open} unmountOnExit>
        <Box sx={{ pl: 1, borderLeft: 2, borderColor: "divider" }}>
          <Typography variant="caption" component="div" color="text.secondary">
            {recordedLine(failure)}
            {failure.cause ? ` · ${failure.cause}` : ""}
          </Typography>
          {failure.message && (
            <Typography
              variant="caption"
              component="div"
              color="text.secondary"
              sx={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}
            >
              {failure.message}
            </Typography>
          )}
        </Box>
      </Collapse>
    </Stack>
  );
}

RunFailure.propTypes = {
  failure: PropTypes.shape({
    domain: PropTypes.string,
    code: PropTypes.string,
    cause: PropTypes.string,
    message: PropTypes.string,
  }).isRequired,
  dense: PropTypes.bool,
};
