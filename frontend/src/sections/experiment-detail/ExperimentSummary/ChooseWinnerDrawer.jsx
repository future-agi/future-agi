import { LoadingButton } from "@mui/lab";
import {
  Box,
  Button,
  Drawer,
  IconButton,
  Stack,
  Typography,
  useTheme,
} from "@mui/material";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import PropTypes from "prop-types";
import React, { useMemo, useState } from "react";
import { useForm } from "react-hook-form";
import { useParams } from "react-router";
import Iconify from "src/components/iconify";
import SliderRow from "src/sections/common/SliderRow/SliderRow";
import HelperText from "src/sections/develop-detail/Common/HelperText";
import axios, { endpoints } from "src/utils/axios";
import { enqueueSnackbar } from "src/components/snackbar";
import ChooseWinnerSkeleton from "./ChooseWinnerSkeleton";
import { trackEvent, Events } from "src/utils/Mixpanel";
import { weightSliderMarks } from "src/sections/project/ChooseWinner/common";

const getDefaultValue = (evalsList, oldData) => {
  const obj = { response_time: 1, total_tokens: 1, completion_tokens: 1 };

  evalsList.forEach(({ name }) => {
    obj[name] = 1;
  });
  Object.keys(oldData).forEach((key) => {
    if (Object.prototype.hasOwnProperty.call(obj, key)) {
      obj[key] = oldData[key];
    }
  });
  return obj;
};

const sliderRowStyles = (theme) => ({
  "& .MuiSlider-markLabel": {
    whiteSpace: "normal",
    textAlign: "center",
    maxWidth: "60px",
    transform: "unset",
    top: theme?.spacing(2),
    color: "text.disabled",
    fontWeight: "fontWeightRegular",
    ...theme?.typography["s2"],
  },
  "& .MuiSlider-markLabel[data-index='5']": {
    right: "-5px",
    left: "unset !important",
  },
  marginTop: theme.spacing(2),
});

const ChooseWinnerDrawerChild = ({ onClose, evalsList, oldData }) => {
  const theme = useTheme();
  const { control, handleSubmit } = useForm({
    defaultValues: getDefaultValue(evalsList, oldData),
  });

  const queryClient = useQueryClient();

  const { experimentId } = useParams();

  // useMemo(() => {
  //   if (oldData) {
  //     const newValue = getDefaultValue(evalsList, oldData);
  //     reset(newValue);
  //   }
  // }, [oldData]);

  const { mutate, isPending } = useMutation({
    mutationFn: (data) =>
      axios.post(
        endpoints.develop.experiment.compareExperiments(experimentId),
        { weights: data },
      ),
    onSuccess: (data, variables) => {
      trackEvent(Events.expWinnerSelect, {
        experiment_name: data?.data?.result?.experimentName,
        experiment_details: variables,
      });
      enqueueSnackbar("Winner chosen successfully", { variant: "success" });
      onClose();
      queryClient.invalidateQueries({
        queryKey: ["experiment-summary", experimentId],
      });
    },
  });

  return (
    <form style={{ height: "100%" }} onSubmit={handleSubmit(mutate)}>
      <Box
        sx={{
          padding: "20px",
          display: "flex",
          flexDirection: "column",
          gap: 2,
          height: "100%",
        }}
      >
        <Stack direction={"column"} gap={theme.spacing(0.25)}>
          <Box
            sx={{
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
            }}
          >
            <Typography
              variant="m3"
              fontWeight={"fontWeightMedium"}
              color="text.primary"
            >
              Winner Settings
            </Typography>
            <IconButton onClick={onClose} size="small">
              <Iconify icon="mingcute:close-line" />
            </IconButton>
          </Box>
          <HelperText
            text="Choose the overall importance value of the given variables below to
          determine which experiment performed the best"
          />
        </Stack>
        <Typography fontWeight={600} fontSize="14px" color="text.primary">
          Variables
        </Typography>
        <Box
          sx={{
            display: "flex",
            flexDirection: "column",
            gap: theme.spacing(6),
            flex: 1,
            overflowY: "auto",
            overflowX: "hidden",
          }}
        >
          <SliderRow
            label="Average Response Time"
            control={control}
            fieldName={`response_time`}
            min={0}
            max={10}
            step={1}
            marks={weightSliderMarks}
            sx={sliderRowStyles(theme)}
          />

          <SliderRow
            label="Completion tokens"
            control={control}
            fieldName={`completion_tokens`}
            min={0}
            max={10}
            step={1}
            marks={weightSliderMarks}
            sx={sliderRowStyles(theme)}
          />
          <SliderRow
            label="Total tokens"
            control={control}
            fieldName={`total_tokens`}
            min={0}
            max={10}
            step={1}
            marks={weightSliderMarks}
            sx={sliderRowStyles(theme)}
          />
          {evalsList.map(({ name }) => (
            <SliderRow
              key={name}
              label={name}
              control={control}
              fieldName={name}
              min={0}
              max={10}
              step={1}
              marks={weightSliderMarks}
              sx={sliderRowStyles(theme)}
            />
          ))}
        </Box>

        <Box sx={{ display: "flex", gap: 2, flexShrink: 0 }}>
          <Button onClick={onClose} fullWidth variant="outlined">
            Cancel
          </Button>
          <LoadingButton
            fullWidth
            variant="contained"
            color="primary"
            type="submit"
            loading={isPending}
          >
            Save & Run
          </LoadingButton>
        </Box>
      </Box>
    </form>
  );
};

ChooseWinnerDrawerChild.propTypes = {
  onClose: PropTypes.func,
  evalsList: PropTypes.array,
  oldData: PropTypes.object,
};

const ChooseWinnerDrawer = ({ open, onClose, evalsList }) => {
  const { experimentId } = useParams();
  const [isLoading, setIsLoading] = useState(null);

  const { data, isFetching } = useQuery({
    queryKey: ["get-choose-winner", experimentId],
    queryFn: () =>
      axios.get(endpoints.develop.experiment.comparison(experimentId)),
    select: (data) => data.data?.result,
    enabled: open,
    // staleTime: 1000,
  });

  const oldData = useMemo(() => {
    setIsLoading(true);
    if (!data) return {};
    const compare = [...(data.comparisons || [])]; // Clone the array to avoid mutation
    const newData = compare.pop()?.weights || {};
    const formattedOldData = {};
    Object.entries(newData).forEach(([key, value]) => {
      if (key === "scores" && typeof value === "object") {
        Object.entries(value).forEach(([itemName, itemValue]) => {
          formattedOldData[itemName] = itemValue;
        });
      } else {
        formattedOldData[key] = value;
      }
    });
    setIsLoading(null);
    return formattedOldData;
  }, [data]);

  return (
    <Drawer
      anchor="right"
      open={open && oldData}
      onClose={onClose}
      PaperProps={{
        sx: {
          height: "100vh",
          position: "fixed",
          zIndex: 9999,
          borderRadius: "10px",
          backgroundColor: "background.paper",
          minWidth: "550px",
        },
      }}
      ModalProps={{
        BackdropProps: {
          style: { backgroundColor: "transparent" },
        },
      }}
    >
      {!isLoading && !isFetching ? (
        <ChooseWinnerDrawerChild
          onClose={onClose}
          evalsList={evalsList?.filter(
            (item) =>
              !item.name.includes("-reason") && !item.name.includes("_reason"),
          )}
          oldData={oldData}
        />
      ) : (
        <ChooseWinnerSkeleton />
      )}
    </Drawer>
  );
};

ChooseWinnerDrawer.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  evalsList: PropTypes.array,
};

export default ChooseWinnerDrawer;
