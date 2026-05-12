import {
  Box,
  Button,
  Chip,
  FormControl,
  InputLabel,
  MenuItem,
  Select,
} from "@mui/material";
import React, { useMemo, useState } from "react";
import Iconify from "src/components/iconify/iconify";
import PropTypes from "prop-types";
import { useController, useFieldArray } from "react-hook-form";
import { FormHelperText } from "@mui/material";
import CustomTooltip from "src/components/tooltip/CustomTooltip";

export const DatasetSelector = ({ datasetOptions, control }) => {
  const [selected, setSelected] = useState({
    selectedEnv: null,
    selectedVersion: null,
  });

  const { fields, append, remove } = useFieldArray({
    control,
    name: "datasets",
  });

  const disableAddDataset = useMemo(() => {
    let disableAddDataset = false;

    if (selected.selectedEnv && selected.selectedVersion) {
      if (
        fields.find(
          (fld) =>
            fld.environment === selected.selectedEnv &&
            fld.modelVersion === selected.selectedVersion,
        )
      ) {
        disableAddDataset = true;
      }
    }

    return disableAddDataset;
  }, [selected, fields]);

  const { fieldState } = useController({
    name: "datasets",
    control,
  });

  const environmentOptions = useMemo(() => {
    if (!datasetOptions) return [];

    const set = new Set(datasetOptions.map((o) => o.environment));

    return Array.from(set);
  }, [datasetOptions]);

  const versionOptions = useMemo(() => {
    if (!datasetOptions) return [];
    if (selected.selectedEnv) {
      return datasetOptions.reduce((arr, { environment, version }) => {
        if (environment === selected.selectedEnv && !arr.includes(version)) {
          arr.push(version);
        }
        return arr;
      }, []);
    } else {
      const set = new Set(datasetOptions.map((o) => o.version));
      return Array.from(set);
    }
  }, [datasetOptions, selected]);

  const setEnv = (e) => {
    const newEnv = e.target.value;
    setSelected(() => ({
      selectedEnv: newEnv,
      selectedVersion: "",
    }));
  };

  const setVersion = (e) => {
    const newVersion = e.target.value;

    setSelected((ex) => ({
      ...ex,
      selectedVersion: newVersion,
    }));
  };

  const addButtonActive = selected.selectedEnv && selected.selectedVersion;

  const renderChips = () => {
    if (!fields?.length) return <></>;

    return (
      <Box sx={{ display: "flex", gap: "16px", overflowX: "auto" }}>
        {fields?.map(({ id }, index) => (
          <ControlledChip
            key={id}
            index={index}
            control={control}
            remove={remove}
          />
        ))}
      </Box>
    );
  };

  const isError = Boolean(fieldState?.error?.message);

  return (
    <Box sx={{ display: "flex", flexDirection: "column", gap: "23px" }}>
      <Box>
        <Box sx={{ display: "flex", gap: 2 }}>
          <Box sx={{ display: "flex", gap: "6px", flex: 1 }}>
            <FormControl fullWidth size="small">
              <InputLabel>Environment</InputLabel>
              <Select
                value={selected?.selectedEnv}
                label="Environment"
                placeholder="Select Environment"
                onChange={setEnv}
                MenuProps={{
                  sx: {
                    maxHeight: "280px",
                  },
                }}
              >
                {environmentOptions?.map((val) => (
                  <MenuItem key={val} value={val}>
                    {val}
                  </MenuItem>
                ))}
              </Select>
            </FormControl>
            <CustomTooltip
              show={!selected.selectedEnv}
              placement="bottom"
              arrow
              title="Select an environment first"
            >
              <FormControl fullWidth size="small">
                <InputLabel>Version</InputLabel>
                <Select
                  value={selected?.selectedVersion}
                  label="Version"
                  onChange={setVersion}
                  MenuProps={{
                    sx: {
                      maxHeight: "280px",
                    },
                  }}
                  disabled={!selected.selectedEnv}
                >
                  {versionOptions?.map((ver) => (
                    <MenuItem key={ver} value={ver}>
                      {ver}
                    </MenuItem>
                  ))}
                </Select>
              </FormControl>
            </CustomTooltip>
          </Box>
          <Button
            variant="contained"
            color="primary"
            disabled={!addButtonActive || disableAddDataset}
            onClick={() => {
              append({
                environment: selected.selectedEnv,
                modelVersion: selected.selectedVersion,
              });
              setSelected({
                selectedEnv: null,
                selectedVersion: null,
              });
            }}
            sx={{
              "& .MuiButton-startIcon": {
                margin: 0,
              },
            }}
            startIcon={<Iconify icon="ic:round-plus" />}
          ></Button>
        </Box>
        {isError && (
          <FormHelperText error={true}>
            {fieldState?.error?.message}
          </FormHelperText>
        )}
      </Box>
      {renderChips()}
    </Box>
  );
};

const ControlledChip = ({ control, index, remove }) => {
  const { field } = useController({
    name: `datasets.${index}`,
    control,
  });

  return (
    <Chip
      color="primary"
      variant="soft"
      label={`${field.value.environment} | ${field.value.modelVersion}`}
      onDelete={() => remove(index)}
    />
  );
};

ControlledChip.propTypes = {
  control: PropTypes.object,
  index: PropTypes.number,
  remove: PropTypes.func,
};

DatasetSelector.propTypes = {
  datasetOptions: PropTypes.object,
  control: PropTypes.object,
};
