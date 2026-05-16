import { Button, Popover, Stack, Typography } from "@mui/material";
import React from "react";
import PropTypes from "prop-types";
import { useForm } from "react-hook-form";
import { FormSelectField } from "src/components/FormSelectField";
import { AdvanceNumberFilterOperators } from "src/utils/constants";
import NumberQuickFilterValue from "./NumberQuickFilterValue";
import { zodResolver } from "@hookform/resolvers/zod";
import { NumberQuickFilterValidationSchema } from "./validation";
import { avoidDuplicateFilterSet } from "../../common";

const NumberQuickFilterPopoverChild = ({
  filterData,
  onClose,
  setFilters,
  setFilterOpen,
}) => {
  const { control, handleSubmit } = useForm({
    defaultValues: {
      operator: "equals",
      value1: filterData?.value,
      value2: 0,
    },
    resolver: zodResolver(NumberQuickFilterValidationSchema),
  });

  const onSubmit = (data) => {
    const filter = filterData.filter;
    filter.filter_config.filter_value = [data.value1, data.value2];
    filter.filter_config.filter_op = data.operator;

    setFilterOpen(true);
    setFilters((prev) => avoidDuplicateFilterSet(prev, filter));
    onClose();
  };

  return (
    <form onSubmit={handleSubmit(onSubmit)}>
      <Stack gap={1}>
        <Stack direction="row" gap={2} alignItems="center">
          <Typography typography="s3" sx={{ whiteSpace: "nowrap" }}>
            Where score is
          </Typography>
          <FormSelectField
            control={control}
            fieldName="operator"
            options={AdvanceNumberFilterOperators}
            label="Operator"
            size="small"
            fullWidth
          />
        </Stack>
        <NumberQuickFilterValue control={control} />
        <Button size="small" variant="outlined" color="primary" type="submit">
          Apply
        </Button>
      </Stack>
    </form>
  );
};

NumberQuickFilterPopoverChild.propTypes = {
  filterData: PropTypes.object,
  onClose: PropTypes.func,
  setFilters: PropTypes.func,
  setFilterOpen: PropTypes.func,
};

const NumberQuickFilterPopover = ({
  open,
  filterData,
  onClose,
  setFilters,
  setFilterOpen,
}) => {
  return (
    <Popover
      anchorReference="anchorPosition"
      anchorPosition={filterData?.filterAnchor}
      open={open}
      onClose={onClose}
      anchorOrigin={{
        vertical: "top",
        horizontal: "left",
      }}
      transformOrigin={{
        vertical: "bottom",
        horizontal: "right",
      }}
      elevation={10}
      sx={{
        "& .MuiPaper-root": {
          borderRadius: "8px",
          padding: "12px",
          border: "1px solid",
          borderColor: "divider",
          minWidth: "250px",
        },
      }}
    >
      <NumberQuickFilterPopoverChild
        filterData={filterData}
        onClose={onClose}
        setFilters={setFilters}
        setFilterOpen={setFilterOpen}
      />
    </Popover>
  );
};

NumberQuickFilterPopover.propTypes = {
  open: PropTypes.bool,
  filterData: PropTypes.object,
  onClose: PropTypes.func,
  setFilters: PropTypes.func,
  setFilterOpen: PropTypes.func,
};

export default NumberQuickFilterPopover;
