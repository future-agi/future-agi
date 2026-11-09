// R4 (lane M): the dataset list's modality icons and `Scenarios` chip. The
// icons come from the dataset's column types (image, audio, document,
// video); a scenarios dataset adds the call icon and its kind chip.
import React, { useMemo } from "react";
import PropTypes from "prop-types";
import { Box, Stack } from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import useDatasetColumnsLite from "./useDatasetColumnsLite";

export const MODALITY_ICONS = [
  {
    kind: "image",
    types: ["image", "images"],
    icon: "mdi:image-outline",
    label: "Images",
  },
  { kind: "audio", types: ["audio"], icon: "mdi:waveform", label: "Audio" },
  {
    kind: "document",
    types: ["document", "file"],
    icon: "mdi:file-document-outline",
    label: "Documents",
  },
  {
    kind: "video",
    types: ["video"],
    icon: "mdi:video-outline",
    label: "Video",
  },
  { kind: "call", types: ["call"], icon: "mdi:phone-in-talk", label: "Calls" },
];

/** Which modalities a column list holds (`[{kind, icon, label, columns}]`). */
export function modalitiesOf(columns, { kind } = {}) {
  const list = MODALITY_ICONS.map((m) => ({
    ...m,
    columns: (columns ?? [])
      .filter((c) => m.types.includes(String(c?.data_type ?? "").toLowerCase()))
      .map((c) => c.name),
  })).filter((m) => m.columns.length);
  if (kind === "scenarios" && !list.some((m) => m.kind === "call"))
    list.push({
      ...MODALITY_ICONS.find((m) => m.kind === "call"),
      columns: [],
    });
  return list;
}

export function ScenariosChip() {
  return (
    <CustomTooltip
      show
      size="small"
      arrow
      title="Scenarios: each row is one scenario played by one caller persona; running it on a voice agent makes one call per row."
    >
      <Box
        component="span"
        data-kind-chip="scenarios"
        sx={{
          display: "inline-flex",
          alignItems: "center",
          height: 18,
          px: 0.75,
          borderRadius: 0.5,
          bgcolor: (t) => `${t.palette.primary.main}1F`,
          // Light: the dark purple reads at 6:1 on the tint (primary.main
          // was 3.9:1, a11y #14); dark mode's primary is already light.
          color: (t) =>
            t.palette.mode === "light"
              ? t.palette.primary.dark
              : t.palette.primary.main,
          typography: "s3",
          fontWeight: 500,
          flexShrink: 0,
          whiteSpace: "nowrap",
        }}
      >
        Scenarios
      </Box>
    </CustomTooltip>
  );
}

export default function DatasetModalityIcons({ datasetId, kind }) {
  const { data: columns } = useDatasetColumnsLite(datasetId, {
    enabled: Boolean(datasetId),
  });
  const mods = useMemo(() => modalitiesOf(columns, { kind }), [columns, kind]);
  if (!mods.length) return null;
  return (
    <Stack
      direction="row"
      alignItems="center"
      gap={0.25}
      data-modality-icons={mods.map((m) => m.kind).join(" ")}
      sx={{ flexShrink: 0, color: "text.secondary" }}
    >
      {mods.map((m) => (
        <CustomTooltip
          key={m.kind}
          show
          size="small"
          arrow
          title={
            m.columns.length
              ? `${m.label} · ${m.columns.join(", ")}`
              : `${m.label} · one call per row when run on a voice agent`
          }
        >
          <Box
            component="span"
            aria-label={m.label}
            sx={{ display: "inline-flex" }}
          >
            <Iconify icon={m.icon} width={15} />
          </Box>
        </CustomTooltip>
      ))}
    </Stack>
  );
}

DatasetModalityIcons.propTypes = {
  datasetId: PropTypes.string,
  kind: PropTypes.string,
};
