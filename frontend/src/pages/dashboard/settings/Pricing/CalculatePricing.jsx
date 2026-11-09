import {
  Box,
  Button,
  ButtonBase,
  Divider,
  InputAdornment,
  List,
  ListItem,
  Typography,
  useTheme,
} from "@mui/material";
import React, { useMemo } from "react";
import { useForm } from "react-hook-form";
import { FormCheckboxField } from "src/components/FormCheckboxField/FormCheckboxField";
import { FormSearchSelectFieldControl } from "src/components/FromSearchSelectField";
import RadioField from "src/components/RadioField/RadioField";
import { useQuery } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import PropTypes from "prop-types";
import { FUTUREAGI_LLM_MODELS } from "src/sections/common/EvaluationDrawer/validation";
import { formatNumberSystem } from "src/utils/utils";
import {
  annualDiscount,
  concurrentDropdown,
  evaluationDropdown,
  simulationDropdown,
  syntheticDropdown,
  traceDropdown,
} from "./constant";
import SvgColor from "src/components/svg-color";
import { HubspotMeetingModalWrapper } from "./HubspotMeetingModalWrapper";
const enterPriseFeatures = [
  "Unlimited everything",
  "Dedicated support engineer",
  "Advanced security & compliance (SOC2, HIPAA)",
  "24/7 premium support",
  "On premise and Custom deployments",
  "Advanced reporting & analytics",
  "SLA guarantees (private slack channel)",
];

const CalculatePricing = ({ redirect, setMeetModalOpen, meetModalOpen }) => {
  const theme = useTheme();
  const { data } = useQuery({
    queryKey: ["get-latest-prices"],
    queryFn: () => axios.get(endpoints.settings.getLatestPrices),
    select: (d) => d?.data?.result,
  });
  const {
    control,
    watch,
    reset,
    formState: { isDirty },
  } = useForm({
    defaultValues: {
      planTier: "Free",
      model: "turing_large",
      billingFrequent: 1,
      traces: 0,
      evaluations: 0,
      errorLocalizer: false,
      syntheticData: 0,
      simulation: 0,
      conCurrentCall: 0,
    },
  });

  const formState = watch();

  const resetCalculate = () => {
    reset({
      planTier: "Free",
      model: "turing_large",
      billingFrequent: 1,
      traces: 0,
      evaluations: 0,
      errorLocalizer: false,
      syntheticData: 0,
      simulation: 0,
      conCurrentCall: 0,
    });
  };
  const planType = watch("planTier");

  const {
    traces,
    evaluations,
    syntheticData,
    simulation,
    totalWithoutDiscount,
  } = useMemo(() => {
    const {
      traces,
      evaluations,
      errorLocalizer,
      syntheticData,
      simulation,
      conCurrentCall,
      billingFrequent,
      model,
    } = formState;
    const reservedDiscount = 0.2; // 20% reserved discount
    const billingMode = billingFrequent;
    const evaluation1K =
      data?.[
        model == "turing_large"
          ? "turingLarge1K"
          : model == "turing_small"
            ? "turingSmall1K"
            : "turingFlash1K"
      ];

    const tracesAmount = traces * (data?.trace1M || 0) * billingMode;
    const evaluationsAmount = evaluations * (evaluation1K || 0) * billingMode;
    const errorLocalizerAmount = errorLocalizer
      ? evaluations * (data?.explanation1K || 0) * billingMode
      : 0;
    const syntheticDataAmount =
      syntheticData * (data?.synthetic1K || 0) * 4 * 200 * billingMode;
    const simulationAmount = simulation * (data?.voice1K || 0) * billingMode;
    const conCurrentCallCmount = conCurrentCall * 0 * billingMode;

    // Getting the reserved volume discount for each type.
    const reservedTraceDiscount =
      traces >= 100 ? tracesAmount * reservedDiscount : 0;
    const reservedEvaluationsDiscount =
      evaluations >= 5000 ? evaluationsAmount * reservedDiscount : 0;
    const reservedErrorLocalizerDiscount =
      evaluations >= 5000 ? errorLocalizerAmount * reservedDiscount : 0;
    const reservedSyntheticDataDiscount =
      syntheticData >= 250 ? syntheticDataAmount * reservedDiscount : 0;
    const reservedSimulationDiscount =
      simulation >= 50 ? simulationAmount * reservedDiscount : 0;
    const reservedConCurrentCallDiscount =
      simulation >= 50 ? conCurrentCallCmount * reservedDiscount : 0;

    return {
      traces: tracesAmount - reservedTraceDiscount,
      syntheticData: syntheticDataAmount - reservedSyntheticDataDiscount,
      evaluations:
        evaluationsAmount -
        reservedEvaluationsDiscount +
        errorLocalizerAmount -
        reservedErrorLocalizerDiscount,
      simulation:
        simulationAmount -
        reservedSimulationDiscount +
        conCurrentCallCmount -
        reservedConCurrentCallDiscount,
      totalWithoutDiscount:
        tracesAmount +
        syntheticDataAmount +
        evaluationsAmount +
        errorLocalizerAmount +
        simulationAmount +
        conCurrentCallCmount,
    };
  }, [formState, data]);
  const totalAfterDiscount = traces + evaluations + syntheticData + simulation;
  return (
    <Box
      padding={3.5}
      textAlign={"right"}
      sx={{
        borderRadius: 2,
        backgroundColor: "background.paper",
        border: "1px solid",
        borderColor: "border.default",
      }}
    >
      <Button
        variant="outlined"
        size="small"
        disabled={!isDirty || planType === "Enterprise"}
        onClick={resetCalculate}
        sx={{ mt: -1.5, mb: 1.5 }}
      >
        Clear All
      </Button>

      <Box display="flex" minHeight={"900px"} gap={3} textAlign={"left"}>
        <Box flex={1} display="flex" flexDirection={"column"} gap={2}>
          <Box display="flex" gap={10} sx={{ marginBottom: "20px" }}>
            <RadioField
              required={false}
              label="Plan Tier"
              control={control}
              fieldName={"planTier"}
              optionColor={"text.primary"}
              labelColor="text.primary"
              groupSx={{ padding: 0, marginLeft: -1 }}
              options={[
                { label: "Free", value: "Free" },
                { label: "Growth", value: "Growth" },
                { label: "Enterprise", value: "Enterprise" },
              ]}
            />
            <RadioField
              disabled={planType === "Enterprise"}
              {...(planType === "Enterprise" && { value: "" })}
              required={false}
              label="Billing Frequent"
              control={control}
              fieldName={"billingFrequent"}
              optionColor={"text.primary"}
              labelColor="text.primary"
              groupSx={{ padding: 0, marginLeft: -1 }}
              options={[
                { label: "Monthly", value: 1 },
                { label: "Yearly", value: 12 },
              ]}
            />
          </Box>
          <Divider orientation="horizontal" />
          {planType === "Enterprise" ? (
            <Box
              sx={{
                display: "flex",
                flexDirection: "column",
                alignItems: "center",
                justifyContent: "center",
                my: "auto",
              }}
            >
              <Typography sx={{ fontWeight: 600, fontSize: "16px" }}>
                Enterprise Plan
              </Typography>
              <Typography
                sx={{ fontWeight: 400, fontSize: "13px", maxWidth: "401px" }}
              >
                Work with our sales team to design the perfect plan for your
                business. Ready to begin? Click on{" "}
                <ButtonBase
                  component="span"
                  onClick={() => {
                    setMeetModalOpen(true);
                  }}
                  sx={{
                    color: "primary.main",
                    font: "inherit",
                    fontWeight: 500,
                    verticalAlign: "baseline",
                  }}
                >
                  Talk to Sales
                </ButtonBase>{" "}
                to continue
              </Typography>
            </Box>
          ) : (
            <>
              <Box>
                <Box display="flex" justifyContent={"space-between"} mb={0.5}>
                  <Typography
                    typography={"m2"}
                    fontWeight={"fontWeightSemiBold"}
                  >
                    Observe Traces
                  </Typography>
                  <Typography
                    typography={"m2"}
                    fontWeight={"fontWeightSemiBold"}
                  >
                    Cost
                  </Typography>
                </Box>
                <Typography typography={"s1"} fontWeight={"fontWeightRegular"}>
                  Expected volume of Observe traces
                </Typography>
                <Box display="flex" justifyContent={"space-between"} mt={1}>
                  <FormSearchSelectFieldControl
                    showClear={false}
                    control={control}
                    sx={{ width: "215px" }}
                    fieldName={"traces"}
                    size={"small"}
                    placeholder={"No. of Traces"}
                    options={traceDropdown}
                  />
                  <Typography
                    typography={"m2"}
                    fontWeight={"fontWeightSemiBold"}
                  >
                    $
                    {formatNumberSystem(
                      traces +
                        (formState.billingFrequent == 12 ? traces * -0.1 : 0),
                    )}
                  </Typography>
                </Box>
              </Box>
              <Divider orientation="horizontal" />
              <Box>
                <Box display="flex" justifyContent={"space-between"} mb={0.5}>
                  <Typography
                    typography={"m2"}
                    fontWeight={"fontWeightSemiBold"}
                  >
                    Evaluations
                  </Typography>
                </Box>
                <Typography typography={"s1"} fontWeight={"fontWeightRegular"}>
                  Expected monthly Evaluations run on platform
                </Typography>
                <Box display="flex" justifyContent={"space-between"} mt={1}>
                  <FormSearchSelectFieldControl
                    showClear={false}
                    sx={{ width: "215px" }}
                    control={control}
                    options={FUTUREAGI_LLM_MODELS.filter(
                      (model) =>
                        model.label !== "PROTECT_FLASH" &&
                        model.label !== "PROTECT",
                    ).map((model) => {
                      return {
                        ...model,
                        component: (
                          <Box
                            sx={{
                              padding: (theme) => theme.spacing(0.75, 1),
                            }}
                          >
                            <Box
                              display={"flex"}
                              flexDirection={"row"}
                              alignItems={"center"}
                              gap={"8px"}
                            >
                              <img
                                src={"/favicon/logo.svg"}
                                style={{
                                  height: theme.spacing(2),
                                  width: theme.spacing(2),
                                }}
                              />
                              <Typography
                                typography="s1"
                                fontWeight={"fontWeightMedium"}
                                color={"text.primary"}
                              >
                                {model.label}
                              </Typography>
                            </Box>
                          </Box>
                        ),
                      };
                    })}
                    InputProps={{
                      startAdornment: (
                        <InputAdornment position="start">
                          <img
                            src={"/favicon/logo.svg"}
                            style={{
                              height: theme.spacing(2),
                              width: theme.spacing(2),
                            }}
                          />
                        </InputAdornment>
                      ),
                    }}
                    error={""}
                    fieldName={"model"}
                    label={"Language Model"}
                    size={"small"}
                  />
                  <Typography
                    typography={"m2"}
                    fontWeight={"fontWeightSemiBold"}
                  >
                    $
                    {formatNumberSystem(
                      evaluations +
                        (formState.billingFrequent == 12
                          ? evaluations * -0.1
                          : 0),
                    )}
                  </Typography>
                </Box>
                <Box display="flex" justifyContent={"space-between"} mt={1}>
                  <FormSearchSelectFieldControl
                    showClear={false}
                    control={control}
                    sx={{ width: "215px" }}
                    fieldName={"evaluations"}
                    size={"small"}
                    placeholder={"No. of Datapoints"}
                    options={evaluationDropdown}
                  />
                </Box>
                <FormCheckboxField
                  label="Error Localizer ( Include cost of error localization for each evaluation)"
                  control={control}
                  fieldName="errorLocalizer"
                  labelPlacement="end"
                  helperText=""
                  defaultValue={false}
                  checkboxSx={{ marginLeft: "-10px" }}
                />
              </Box>
              <Divider orientation="horizontal" />
              <Box>
                <Box display="flex" justifyContent={"space-between"} mb={0.5}>
                  <Typography
                    typography={"m2"}
                    fontWeight={"fontWeightSemiBold"}
                  >
                    Synthetic Data
                  </Typography>
                </Box>
                <Typography typography={"s1"} fontWeight={"fontWeightRegular"}>
                  Expected number of rows of synthetic data generated
                </Typography>
                <Box
                  display="flex"
                  justifyContent={"space-between"}
                  mt={1}
                  mb={1}
                >
                  <FormSearchSelectFieldControl
                    showClear={false}
                    control={control}
                    sx={{ width: "215px" }}
                    fieldName={"syntheticData"}
                    size={"small"}
                    placeholder={"No. of synthetic data"}
                    options={syntheticDropdown}
                  />
                  <Typography
                    typography={"m2"}
                    fontWeight={"fontWeightSemiBold"}
                  >
                    $
                    {formatNumberSystem(
                      syntheticData +
                        (formState.billingFrequent == 12
                          ? syntheticData * -0.1
                          : 0),
                    )}
                  </Typography>
                </Box>
                <Typography typography={"s1"} fontWeight={"fontWeightRegular"}>
                  Assumption: 4 Columns and each datapoint generation requires
                  200 tokens
                </Typography>
              </Box>
              <Divider orientation="horizontal" />
              <Box>
                <Box display="flex" justifyContent={"space-between"} mb={0.5}>
                  <Typography
                    typography={"m2"}
                    fontWeight={"fontWeightSemiBold"}
                  >
                    Simulation
                  </Typography>
                </Box>
                <Typography typography={"s1"} fontWeight={"fontWeightRegular"}>
                  Cost of the number of minutes used for voice simulation
                </Typography>
                <Box
                  display="flex"
                  justifyContent={"space-between"}
                  mt={1}
                  mb={1}
                >
                  <FormSearchSelectFieldControl
                    showClear={false}
                    control={control}
                    sx={{ width: "215px" }}
                    fieldName={"simulation"}
                    size={"small"}
                    placeholder={"Monthly simulation minutes"}
                    options={simulationDropdown}
                  />
                  <Typography
                    typography={"m2"}
                    fontWeight={"fontWeightSemiBold"}
                  >
                    $
                    {formatNumberSystem(
                      simulation +
                        (formState.billingFrequent == 12
                          ? simulation * -0.1
                          : 0),
                    )}
                  </Typography>
                </Box>
                <FormSearchSelectFieldControl
                  showClear={false}
                  control={control}
                  sx={{ width: "215px" }}
                  fieldName={"conCurrentCall"}
                  size={"small"}
                  placeholder={"No. of concurrent calls"}
                  options={concurrentDropdown}
                />
              </Box>
            </>
          )}
        </Box>
        <Divider orientation="vertical" flexItem />
        <Box
          padding={3}
          display="flex"
          flexDirection={"column"}
          gap={2.5}
          width="400px"
          sx={{
            borderRadius: 1,
            backgroundColor: "action.hover",
            border: "1px solid",
            borderColor: "divider",
          }}
        >
          {planType === "Enterprise" ? (
            <Box
              sx={{
                padding: 3,
                textAlign: "center",
                display: "flex",
                flexDirection: "column",
                gap: 2,
              }}
            >
              <Box>
                <Typography sx={{ fontWeight: 600, fontSize: "16px", mb: 1 }}>
                  Enterprise Plan
                </Typography>
                <Typography
                  sx={{ fontWeight: 400, fontSize: "13px", maxWidth: "337px" }}
                >
                  Flexible, custom solutions for complex teams with the
                  security, performance, and support enterprises expect
                </Typography>
              </Box>
              <Divider orientation="horizontal" flexItem />

              <List
                component="ul"
                sx={{
                  listStyleType: "disc",
                  pl: 2,
                  m: 0,
                  fontWeight: 400,
                  fontSize: "12px",
                }}
              >
                {enterPriseFeatures.map((item, index) => (
                  <ListItem
                    key={item || index}
                    component="li"
                    sx={{
                      fontSize: "13px",
                      py: 0.25,
                      display: "list-item",
                      pl: 0,
                    }}
                  >
                    {item}
                  </ListItem>
                ))}
              </List>
              <Box sx={{ display: "flex", flexDirection: "column", gap: 1.5 }}>
                <Button
                  variant="contained"
                  color="primary"
                  sx={{
                    width: "100%",
                  }}
                  onClick={() => {
                    setMeetModalOpen(true);
                  }}
                  startIcon={
                    <SvgColor
                      width={20}
                      src={"/assets/icons/ic_phone_call.svg"}
                      color="primary.contrastText"
                    />
                  }
                >
                  Talk to Sales
                </Button>
                <Box
                  sx={{
                    width: "100%",
                    px: 1.5,
                    py: 0.5,
                    backgroundColor: "status.info.bg",
                    borderRadius: "12px",
                    display: "flex",
                    flexDirection: "row",
                    alignItems: "center",
                    gap: 1,
                    height: "34px",
                  }}
                >
                  <SvgColor
                    src={"/assets/icons/ic_info.svg"}
                    sx={{
                      height: 20,
                      width: 20,
                      color: "status.info.fg",
                    }}
                  />
                  <Typography sx={{ fontWeight: 400, fontSize: "12px" }}>
                    Typical Response Time :{" "}
                    <span style={{ fontWeight: 500 }}>2 hours</span>
                  </Typography>
                </Box>
              </Box>
            </Box>
          ) : (
            <>
              <Box
                display="flex"
                flexDirection={"column"}
                alignItems={"center"}
                gap={0.5}
              >
                <Typography
                  typography={"m2"}
                  fontWeight={"fontWeightSemiBold"}
                  color="text.primary"
                >
                  Estimated{" "}
                  {formState?.billingFrequent == 1 ? "Monthly" : "Yearly"} Cost
                </Typography>

                <Box display="flex" alignItems={"flex-end"}>
                  <Typography
                    typography={"l1"}
                    fontWeight={"fontWeightSemiBold"}
                    color="primary.main"
                  >
                    $
                    {formatNumberSystem(
                      totalAfterDiscount +
                        (formState.billingFrequent == 12
                          ? totalAfterDiscount * -0.1
                          : 0),
                    )}
                  </Typography>
                  {(totalAfterDiscount !== totalWithoutDiscount ||
                    formState?.billingFrequent == 12) &&
                    totalWithoutDiscount != 0 && (
                      <>
                        <Typography
                          typography={"m2"}
                          fontWeight={"fontWeightSemiBold"}
                          color="text.primary"
                        >
                          /
                        </Typography>
                        <Typography
                          typography={"s2"}
                          fontWeight={"fontWeightSemiBold"}
                          color="text.primary"
                          sx={{ textDecoration: "line-through" }}
                        >
                          ${formatNumberSystem(totalWithoutDiscount)}
                        </Typography>
                      </>
                    )}
                </Box>

                <Button
                  size="small"
                  variant="contained"
                  color="primary"
                  onClick={redirect}
                >
                  Start Growth Plan
                </Button>
              </Box>
              <Box
                display="flex"
                flexDirection={"column"}
                alignItems={"center"}
                gap={0.5}
              >
                <Typography
                  typography={"m3"}
                  fontWeight={"fontWeightSemiBold"}
                  color="text.primary"
                >
                  Discount Applied
                </Typography>
                <Typography
                  typography={"s1"}
                  fontWeight={"fontWeightRegular"}
                  color="text.primary"
                  textAlign={"center"}
                >
                  20% reserved volume discount
                  {annualDiscount && formState.billingFrequent == 12 ? (
                    <br />
                  ) : (
                    ""
                  )}
                  {annualDiscount && formState.billingFrequent == 12
                    ? annualDiscount * 100 + "% annual discount"
                    : ""}
                </Typography>
              </Box>
              <Divider orientation="horizontal" />
              <Box>
                <Typography
                  typography={"s1"}
                  fontWeight={"fontWeightRegular"}
                  color="text.primary"
                >
                  Storage, evaluations and Synthetic data can be purchased via
                  your organization{"'"}s pay-as-you-go budget.
                </Typography>
                <List sx={{ padding: 0 }}>
                  <ListItem sx={{ padding: "0px 30px" }}>
                    <Typography
                      typography={"s1"}
                      fontWeight={"fontWeightRegular"}
                      color="text.primary"
                    >
                      Evaluations: $0.002-0.01/API
                    </Typography>
                  </ListItem>
                  <ListItem sx={{ padding: "0px 30px" }}>
                    <Typography
                      typography={"s1"}
                      fontWeight={"fontWeightRegular"}
                      color="text.primary"
                    >
                      Storage: $5/GB
                    </Typography>
                  </ListItem>
                  <ListItem sx={{ padding: "0px 30px" }}>
                    <Typography
                      typography={"s1"}
                      fontWeight={"fontWeightRegular"}
                      color="text.primary"
                    >
                      Synthetic data : $100/1M tokens
                    </Typography>
                  </ListItem>
                  <ListItem sx={{ padding: "0px 30px" }}>
                    <Typography
                      typography={"s1"}
                      fontWeight={"fontWeightRegular"}
                      color="text.primary"
                    >
                      Traces: $8/100K traces
                    </Typography>
                  </ListItem>
                  <ListItem sx={{ padding: "0px 30px" }}>
                    <Typography
                      typography={"s1"}
                      fontWeight={"fontWeightRegular"}
                      color="text.primary"
                    >
                      Voice Simulation: $0.05/min
                    </Typography>
                  </ListItem>
                  <ListItem sx={{ padding: "0px 30px" }}>
                    <Typography
                      typography={"s1"}
                      fontWeight={"fontWeightRegular"}
                      color="text.primary"
                    >
                      Chat Simulation: $0.01/turn
                    </Typography>
                  </ListItem>
                </List>
              </Box>
            </>
          )}
        </Box>
      </Box>
      <HubspotMeetingModalWrapper
        onClose={() => {
          setMeetModalOpen(false);
        }}
        open={meetModalOpen}
      />
    </Box>
  );
};

export default CalculatePricing;

CalculatePricing.propTypes = {
  redirect: PropTypes.func,
  setMeetModalOpen: PropTypes.func,
  meetModalOpen: PropTypes.bool,
};
