import { Box, Typography } from "@mui/material";
import PropTypes from "prop-types";
import React from "react";
import { useWatch } from "react-hook-form";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import RequestBody from "src/sections/develop-detail/AddColumn/AddColumnApiCall/RequestBody";

const CreateDescriptions = ({ fields, control }) => {
  const columns = useWatch({
    name: "columns",
    control,
  });

  const allColumn = React.useMemo(
    () => columns?.map((column) => ({ headerName: column?.name })) || [],
    [columns],
  );

  const formattedValueReason = (field) => {
    return (
      <Box
        sx={{
          width: "250px",
          padding: "8px",
          display: "flex",
          flexDirection: "column",
          gap: "4px",
        }}
      >
        <Box sx={{ display: "flex", justifyContent: "space-between" }}>
          <Typography
            variant="s1"
            fontWeight={"fontWeightRegular"}
            color="text.secondary"
          >
            Column Type:
          </Typography>
          <Typography
            variant="s1"
            fontWeight={"fontWeightMedium"}
            color="text.primary"
            sx={{ width: "8ch" }}
          >
            {field.data_type}
          </Typography>
        </Box>
        {field.property
          .filter(
            (item) => item.type === "min_length" || item.type === "max_length",
          )
          ?.map((item, index) => {
            return (
              <Box
                key={index}
                sx={{ display: "flex", justifyContent: "space-between" }}
              >
                <Typography
                  variant="s1"
                  fontWeight={"fontWeightRegular"}
                  color="text.secondary"
                >
                  {item.type}:
                </Typography>
                <Typography
                  variant="s1"
                  fontWeight={"fontWeightMedium"}
                  color="text.primary"
                  sx={{ width: "8ch" }}
                >
                  {item.value}
                </Typography>
              </Box>
            );
          })}
      </Box>
    );
  };

  return (
    <Box sx={{}}>
      <Box sx={{ display: "flex", flexDirection: "column", gap: "20px" }}>
        {fields?.map((field, ind) => {
          return (
            <Box key={ind} sx={{ overflowX: "hidden" }}>
              {columns[ind]?.name && (
                <Box
                  sx={{ display: "flex", alignItems: "center", gap: "12px" }}
                >
                  <Typography
                    fontWeight={"fontWeightSemiBold"}
                    color="text.primary"
                    variant="s1"
                    sx={{ alignItems: "center" }}
                  >
                    Column {ind + 1}:
                  </Typography>
                  <Typography
                    color="text.primary"
                    variant="s1"
                    fontWeight="fontWeightRegular"
                  >
                    {columns[ind]?.name}
                  </Typography>
                  <CustomTooltip
                    show={true}
                    title={formattedValueReason(field)}
                    arrow
                    sx={{ display: "flex", marginTop: "2px" }}
                  >
                    <Iconify
                      icon="material-symbols:info-outline-rounded"
                      color="text.secondary"
                      width={15}
                      height={15}
                    />
                  </CustomTooltip>
                </Box>
              )}
              <Box sx={{ marginX: "-16px", marginTop: "8px" }}>
                <RequestBody
                  control={control}
                  contentFieldName={`columns.${ind}.description`}
                  allColumns={allColumn.filter((_, i) => i < ind)}
                  placeholder={
                    "Describe the values you want in this column, use {{ to access other columns"
                  }
                  showHelper={false}
                  sx={{
                    borderRadius: "4px",
                  }}
                />
              </Box>
            </Box>
          );
        })}
      </Box>
    </Box>
  );
};

export default CreateDescriptions;

CreateDescriptions.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  columns: PropTypes.array,
  control: PropTypes.any,
  fields: PropTypes.array,
  allColumn: PropTypes.array,
};
