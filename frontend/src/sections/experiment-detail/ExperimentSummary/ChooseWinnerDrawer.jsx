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
import { useExperimentDetail } from "src/api/develop/runs";
import { opsWeightsAvailable, weightsToSend } from "./chooseWinnerOps";

// With no saved weights, the dataset's default evals (`Use in new
// experiments`, in that order) start highest: 8, 7, 6 …; other evals and the
// ops variables start at 1. Direction comes from the metric definitions.
export const getDefaultValue = (evalsList, oldData, defaultEvalNames = []) => {
  const obj = { response_time: 1, total_tokens: 1, completion_tokens: 1 };
  const fresh = !Object.keys(oldData ?? {}).length;

  evalsList.forEach(({ name }) => {
    const rank = defaultEvalNames.indexOf(name);
    obj[name] = fresh && rank >= 0 ? Math.max(2, 8 - rank) : 1;
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

const ChooseWinnerDrawerChild = ({
  onClose,
  experimentId,
  evalsList,
  oldData,
  defaultEvalNames,
  opsAvailable = true,
}) => {
  const theme = useTheme();
  const { control, handleSubmit } = useForm({
    defaultValues: getDefaultValue(evalsList, oldData, defaultEvalNames),
  });

  const queryClient = useQueryClient();

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
        experiment_name: data?.data?.result?.experiment_name,
        experiment_details: variables,
      });
      enqueueSnackbar("Winner chosen successfully", { variant: "success" });
      onClose();
      queryClient.invalidateQueries({
        queryKey: ["experiment-summary", experimentId],
      });
      queryClient.invalidateQueries({ queryKey: ["develop", "analysis"] });
    },
  });

  return (
    <form
      style={{ height: "100%" }}
      onSubmit={handleSubmit((values) =>
        mutate(weightsToSend(values, opsAvailable)),
      )}
    >
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
          {defaultEvalNames?.length > 0 && (
            <Typography typography="s2" color="text.secondary">
              Weights start from this dataset&apos;s default evals (Use in new
              experiments). Each eval&apos;s direction is the one the verdict
              and win rate use.
            </Typography>
          )}
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
          {opsAvailable ? (
            <>
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
            </>
          ) : (
            <Typography
              typography="s2"
              color="text.secondary"
              data-testid="choose-winner-no-ops"
            >
              Ranked on evals only: agent, code and harness runs in this
              experiment record no response time or token counts to weigh.
            </Typography>
          )}
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
  experimentId: PropTypes.string.isRequired,
  evalsList: PropTypes.array,
  oldData: PropTypes.object,
  defaultEvalNames: PropTypes.arrayOf(PropTypes.string),
  opsAvailable: PropTypes.bool,
};

const ChooseWinnerDrawer = ({
  open,
  onClose,
  evalsList,
  defaultEvalNames,
  experimentId: parentExperimentId,
}) => {
  const { experimentId: routeExperimentId } = useParams();
  // The individual Summary resolves its parent from dataset metadata; its
  // route has only individualExperimentId. Use one ID for reads, save and refresh.
  const experimentId = parentExperimentId ?? routeExperimentId;
  const [isLoading, setIsLoading] = useState(null);
  const { data: experiment } = useExperimentDetail(experimentId, {
    enabled: Boolean(open),
  });
  const opsAvailable = opsWeightsAvailable(experiment?.runs);

  const { data, isFetching } = useQuery({
    queryKey: ["get-choose-winner", experimentId],
    queryFn: () =>
      axios.get(endpoints.develop.experiment.comparison(experimentId)),
    select: (data) => data.data?.result,
    enabled: Boolean(open && experimentId),
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
      open={Boolean(open && oldData)}
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
          experimentId={experimentId}
          evalsList={evalsList?.filter(
            (item) =>
              !item.name.includes("-reason") && !item.name.includes("_reason"),
          )}
          oldData={oldData}
          defaultEvalNames={defaultEvalNames}
          opsAvailable={opsAvailable}
        />
      ) : (
        <ChooseWinnerSkeleton />
      )}
    </Drawer>
  );
};

ChooseWinnerDrawer.propTypes = {
  experimentId: PropTypes.string,
  open: PropTypes.bool,
  onClose: PropTypes.func,
  evalsList: PropTypes.array,
  defaultEvalNames: PropTypes.arrayOf(PropTypes.string),
};

export default ChooseWinnerDrawer;
