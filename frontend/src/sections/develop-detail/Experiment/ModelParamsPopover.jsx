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
      anchorOrigin={{ vertical: "bottom", horizontal: "right" }}
      PaperProps={{
        sx: {
          p: 1,
          width: 584,
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
          modelParams?.sliders?.map((item) => ({
            ...item,
            label: _.startCase(item?.label),
            id: _.camelCase(item?.label),
          })) ?? []
        }
        booleans={modelParams?.booleans}
        dropdowns={modelParams?.dropdowns}
        reasoning={modelParams?.reasoning}
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
