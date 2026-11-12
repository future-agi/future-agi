import { Popover } from "@mui/material";
import { useQuery } from "@tanstack/react-query";
import PropTypes from "prop-types";
import React from "react";
import ModelOptionsItems from "src/components/custom-model-options/ModelOptionsItems.jsx";
import axios, { endpoints } from "../../../utils/axios";
import _ from "lodash";

export default function ModelParamsPopover({
  anchorEl,
  setAnchorEl,
  fieldName,
  control,
  modelParams,
}) {
  const { data: responseSchema } = useQuery({
    queryKey: ["response-schema"],
    queryFn: () => axios.get(endpoints.develop.runPrompt.responseSchema),
    select: (d) => d.data?.results,
    staleTime: 1 * 60 * 1000, // 1 min stale time
  });

  return (
    <Popover
      open={Boolean(anchorEl)}
      anchorEl={anchorEl}
      onClose={() => setAnchorEl(null)}
      // options-truth L1 G5: stays inside the viewport.
      anchorOrigin={{ vertical: "bottom", horizontal: "right" }}
      transformOrigin={{ vertical: "top", horizontal: "right" }}
      marginThreshold={16}
      PaperProps={{
        sx: {
          p: 1,
          width: "min(584px, calc(100vw - 32px))",
          maxHeight: "calc(100vh - 32px)",
          overflowY: "auto",
          borderRadius: "4px",
        },
      }}
    >
      <ModelOptionsItems
        control={control}
        fieldNamePrefix={fieldName}
        setDisabledClickOutside={() => {}}
        responseSchema={responseSchema}
        responseFormat={modelParams?.responseFormat ?? []}
        items={
          // The worker now resolves all sampling params, not only temperature
          // and max tokens. Show the values the request actually sends.
          modelParams?.sliders?.map((item) => {
            const id = _.camelCase(item?.label);
            // Max tokens moves in steps of 50 (never 1 at a time).
            const tokens = /tokens/i.test(id);
            return {
              ...item,
              label: _.startCase(item?.label),
              id,
              ...(tokens
                ? { step: Math.max(50, Number(item?.step) || 0) }
                : {}),
            };
          }) ?? []
        }
        booleans={modelParams?.booleans}
        dropdowns={modelParams?.dropdowns}
        // L5 G1: no reasoning toggle on an experiment card. The run engine
        // has no reasoning span, so the toggle would change nothing.
      />
    </Popover>
  );
}

ModelParamsPopover.propTypes = {
  setAnchorEl: PropTypes.func,
  anchorEl: PropTypes.any,
  fieldName: PropTypes.string,
  control: PropTypes.object,
  modelParams: PropTypes.object,
};
