import React, { useEffect } from "react";
import PropTypes from "prop-types";
import { MenuItem, Select, Stack, Typography } from "@mui/material";
import { useWatch } from "react-hook-form";
import { CLASSIC_EXPERIMENT_COPY as COPY } from "src/launch/classicExperimentCopy";
import { useExperimentForm } from "./ExperimentFormContext";

// The legacy prompt runner matches variables to dataset column names. Do not
// expose DSX sample/row/split/version scope, constant/JSON mappings or previews.
export default function ClassicExperimentData({ control, setValue }) {
  const { mappingColumns = [], userColumns = [] } = useExperimentForm();
  const cards = useWatch({ control, name: "promptConfig" }) ?? [];
  const columnId = useWatch({ control, name: "columnId" });
  useEffect(() => {
    cards.forEach((card, index) => {
      const variableMapping = Object.fromEntries(
        (card.variables ?? []).flatMap((variable) => {
          const col = mappingColumns.find((c) => c.name === variable);
          return col ? [[variable, col.id]] : [];
        }),
      );
      if (
        JSON.stringify(card.variableMapping ?? {}) !==
        JSON.stringify(variableMapping)
      )
        setValue(`promptConfig.${index}.variableMapping`, variableMapping);
    });
  }, [cards, mappingColumns, setValue]);
  return (
    <Stack spacing={1.5}>
      <Typography>{COPY.referenceColumn}</Typography>
      <Select
        size="small"
        value={columnId ?? ""}
        inputProps={{ "aria-label": COPY.referenceColumn }}
        onChange={(e) =>
          setValue("columnId", e.target.value || null, { shouldDirty: true })
        }
      >
        <MenuItem value="">{COPY.noReference}</MenuItem>
        {userColumns.map((c) => (
          <MenuItem key={c.id} value={c.id}>
            {c.name}
          </MenuItem>
        ))}
      </Select>
      {cards.map((card, index) => (
        <Stack key={card._itemId ?? index} spacing={0.5}>
          <Typography>{card.name}</Typography>
          <Typography typography="s2">{COPY.inputsLabel}</Typography>
          {(card.variables ?? []).length ? (
            card.variables.map((variable) => (
              <Typography key={variable} typography="s2">
                {variable}
                {mappingColumns.some((c) => c.name === variable)
                  ? null
                  : ` — ${COPY.inputMissing}`}
              </Typography>
            ))
          ) : (
            <Typography typography="s2">{COPY.noInputs}</Typography>
          )}
        </Stack>
      ))}
    </Stack>
  );
}
ClassicExperimentData.propTypes = {
  control: PropTypes.object,
  setValue: PropTypes.func,
};
