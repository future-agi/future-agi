// LensLab: every MediaValue size for the lab row's outputs (and the row's
// media inputs), so each size is walked on real data.
import React from "react";
import PropTypes from "prop-types";
import { Box, Stack, Typography } from "@mui/material";
import MediaValue from "src/sections/common/Media/MediaValue";
import { callOf, mediaRefOf } from "src/sections/common/Media/mediaRef";
import { personaOf } from "src/sections/common/Media/mediaModel";
import LaneLabel from "./LaneLabel";

const SIZES = ["cell", "row", "card"];

// TranscriptView's audio store is one per page, so the lab shows one
// CallView (the first lane's `row`); the other call lanes show cell sizes.
function SizeRow({ title, name, value, sizes = SIZES }) {
  return (
    <Stack
      gap={1}
      sx={{ py: 1.5, borderTop: "1px solid", borderColor: "divider" }}
      data-lab-sizes={name}
    >
      <Box sx={{ typography: "s2", fontWeight: 600 }}>{title}</Box>
      <Box
        sx={{
          display: "grid",
          gridTemplateColumns: "220px 1fr 1fr",
          gap: 2,
          alignItems: "start",
        }}
      >
        {sizes.map((s) => (
          <Stack key={s} gap={0.5} sx={{ minWidth: 0 }}>
            <Typography typography="s3" color="text.disabled">
              {s === "compact"
                ? 'size="cell" compact · 72 px Matrix'
                : `size="${s}"${s === "cell" ? " · 32 px" : ""}`}
            </Typography>
            <Box
              sx={
                s === "cell" || s === "compact"
                  ? {
                      height: 32,
                      display: "flex",
                      alignItems: "center",
                      px: 1,
                      border: "1px solid",
                      borderColor: "divider",
                      borderRadius: 0.5,
                      minWidth: 0,
                      overflow: "hidden",
                    }
                  : { minWidth: 0 }
              }
            >
              {s === "compact" ? (
                <MediaValue value={value} size="cell" compact />
              ) : (
                <MediaValue value={value} size={s} dense={s === "cell"} />
              )}
            </Box>
          </Stack>
        ))}
      </Box>
    </Stack>
  );
}

SizeRow.propTypes = {
  title: PropTypes.node,
  name: PropTypes.string,
  value: PropTypes.any,
  sizes: PropTypes.arrayOf(PropTypes.string),
};

export default function LabMediaSizes({ lanes, row }) {
  const inputs = Object.entries(row?.inputs ?? {}).filter(
    ([, v]) => mediaRefOf(v) || personaOf(v),
  );
  return (
    <Stack sx={{ maxWidth: 1200 }} data-lab-media-sizes="">
      <Typography
        typography="m3"
        fontWeight="fontWeightSemiBold"
        sx={{ mt: 1 }}
      >
        Every MediaValue size
      </Typography>
      {lanes.slice(0, 2).map((l, i) => (
        <SizeRow
          key={l.runId}
          title={<LaneLabel lane={l} dense />}
          name={l.label}
          value={l.value}
          sizes={
            callOf(l.value)
              ? i === 0
                ? ["cell", "compact", "row"]
                : ["cell", "compact"]
              : SIZES
          }
        />
      ))}
      {inputs.slice(0, 4).map(([name, v]) => (
        <SizeRow key={name} name={name} title={`Input · ${name}`} value={v} />
      ))}
    </Stack>
  );
}

LabMediaSizes.propTypes = {
  lanes: PropTypes.array.isRequired,
  row: PropTypes.object,
};
