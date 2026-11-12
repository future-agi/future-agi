// The header line of a scenarios dataset,
// `Scenarios · 8 scenarios × 3 personas`, counted from its rows.
import React, { useMemo } from "react";
import PropTypes from "prop-types";
import { Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import { useGetDatasetDetail } from "src/api/develop/develop-detail";
import { scenarioCountsOf } from "./scenarioCounts";

const TABLE_PARAMS = { page_size: 500, current_page_index: 0 };

export default function ScenariosLine({ datasetId }) {
  const { data } = useGetDatasetDetail(datasetId, {}, TABLE_PARAMS);
  const counts = useMemo(() => scenarioCountsOf(data), [data]);
  return (
    <CustomTooltip
      show
      size="small"
      title="A scenarios dataset: every row is one scenario played by one caller persona. Run it on a voice agent to get one call per row."
    >
      <Stack
        direction="row"
        alignItems="center"
        gap={0.5}
        data-scenarios-line=""
        sx={{ flexShrink: 0, color: "text.secondary", minWidth: 0 }}
      >
        <Iconify icon="mdi:phone-in-talk" width={16} />
        <Typography typography="s2" color="text.secondary" noWrap>
          {counts ? `Scenarios · ${counts.text}` : "Scenarios"}
        </Typography>
      </Stack>
    </CustomTooltip>
  );
}

ScenariosLine.propTypes = { datasetId: PropTypes.string.isRequired };
