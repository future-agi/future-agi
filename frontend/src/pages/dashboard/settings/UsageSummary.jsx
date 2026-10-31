import React, { useEffect, useRef, useState } from "react";
import { Helmet } from "react-helmet-async";
import Typography from "@mui/material/Typography";
import { Box, CircularProgress, IconButton } from "@mui/material";
import { BarChart, Bar, XAxis, YAxis, Tooltip, CartesianGrid } from "recharts";
import axios, { endpoints } from "src/utils/axios";
import { trackEvent, Events } from "src/utils/Mixpanel";
import { useForm } from "react-hook-form";
import { FormSearchSelectFieldControl } from "src/components/FromSearchSelectField";

const UsageSummary = () => {
  const [isLoading, setIsLoading] = useState(false);

  // Form setup
  const { control } = useForm({
    defaultValues: {
      selectedOptions: "dataset_evaluation",
    },
  });

  // Options for the select field
  const options = [
    { value: "dataset_evaluation", label: "Evaluations" },
    { value: "experiment_evaluation", label: "Experiment Evaluations" },
    { value: "optimisation_evaluation", label: "Optimisation Evaluations" },
    { value: "dataset_optimization", label: "Prompt Optimisation" },
    { value: "dataset_protect", label: "Protect" },
  ];

  // Definition object for the FormSearchSelectFieldControl
  const definition = {
    propertyName: "Select Type",
    multiSelect: false,
  };

  const constructDisplayString = (date) => {
    const currentDate = new Date();
    const year = date.getFullYear();
    const month = date.toLocaleString("default", { month: "long" }).slice(0, 3);

    // Check if the month is the current month
    if (
      date.getMonth() === currentDate.getMonth() &&
      year === currentDate.getFullYear()
    ) {
      return `${month} 1,${year} - ${month} ${currentDate.toLocaleDateString("en-US", { day: "numeric" })},${year}`;
    } else {
      return `${month} 1,${year} - ${month} 31,${year}`;
    }
  };

  const getMonthString = (date) => {
    const month = date.toLocaleString("default", { month: "long" }).slice(0, 3);
    return month;
  };

  const [selectedMonth, setSelectedMonth] = useState(new Date());
  const [displayString, setDisplayString] = useState(
    constructDisplayString(selectedMonth),
  );

  const [selectedValue, setSelectedValue] = useState("dataset_evaluation");

  // New state for bar chart data
  const [barChartData, setBarChartData] = useState(
    Array.from({ length: 30 }, (_, i) => ({
      name: "Nov " + (i + 1).toString(),
      value: 0,
    })),
  );

  const handleGetAPICallCount = async () => {
    // console.log("in handleGetAPICallCount : ", selectedMonth);
    setIsLoading(true);
    try {
      const response = await axios.get(endpoints.stripe.getAPICallCount, {
        params: {
          year: selectedMonth.getFullYear(),
          month: selectedMonth.getMonth() + 1,
          api_call_type: selectedValue,
        },
      });
      // iterate through all the integers present in the response.data?.result?.data
      // it is given they will be in consective
      const keys = Object.keys(response.data?.result?.data);
      // console.log("keys : ", keys.length);

      setBarChartData(
        Array.from({ length: keys.length }, (_, i) => ({
          name: getMonthString(selectedMonth) + " " + (i + 1).toString(),
          value: response.data?.result?.data?.[String(i + 1)] || 0,
        })),
      );
    } catch (error) {
      // console.log("error : ", error);
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    handleGetAPICallCount();
  }, [selectedMonth, selectedValue]);

  const chartContainerRef = useRef(null);
  const [chartWidth, setChartWidth] = useState(0);

  useEffect(() => {
    const updateChartWidth = () => {
      if (chartContainerRef.current) {
        setChartWidth(chartContainerRef.current.offsetWidth * 0.9);
      }
    };

    updateChartWidth(); // Set initial width
    window.addEventListener("resize", updateChartWidth);
    return () => window.removeEventListener("resize", updateChartWidth);
  }, []);

  const handlePreviousMonth = () => {
    trackEvent(Events.previousMonthUsage);
    setSelectedMonth((prevMonth) => {
      const newMonth = new Date(prevMonth.setMonth(prevMonth.getMonth() - 1));
      setDisplayString(constructDisplayString(newMonth));
      return newMonth;
    });
  };

  const handleNextMonth = () => {
    trackEvent(Events.nextMonthUsage);
    // make sure the month is not greater than the current month
    // if year is not equal to current year, or month is greater than current month, do nothing
    if (
      selectedMonth.getMonth() < new Date().getMonth() ||
      selectedMonth.getFullYear() < new Date().getFullYear()
    ) {
      setSelectedMonth((prevMonth) => {
        const newMonth = new Date(prevMonth.setMonth(prevMonth.getMonth() + 1));
        setDisplayString(constructDisplayString(newMonth));
        return newMonth;
      });
    }
  };

  const handleSelectChange = (event) => {
    setSelectedValue(event.target.value);
    trackEvent(Events.chooseSummaryVIew);
  };

  return (
    <>
      <Helmet>
        <title>Usage Summary</title>
      </Helmet>
      <Box>
        <Box>
          <Typography
            sx={{
              typography: "m2",
              fontWeight: "fontWeightSemiBold",
              color: "text.primary",
            }}
          >
            Usage Summary
          </Typography>
          <Typography
            sx={{
              typography: "s1",
              fontWeight: "fontWeightRegular",
              color: "text.primary",
              marginTop: (theme) => theme.spacing(0.5),
            }}
          >
            View your manage usage summary
          </Typography>
        </Box>

        <Typography
          variant="body2"
          sx={{
            fontWeight: "600",
            textAlign: "left",
            marginBottom: "20px",
            marginTop: "10px",
            color: "text.primary",
          }}
        >
          Usage Breakdown (Token Count)
        </Typography>

        <div
          style={{
            border: "1px solid lightgray",
            borderRadius: "12px",
            padding: "10px",
          }}
          ref={chartContainerRef}
        >
          <div
            style={{
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
              marginBottom: "10px",
              width: "100%",
            }}
          >
            <div
              style={{
                display: "flex",
                alignItems: "center",
                marginLeft: "20px",
              }}
            >
              <IconButton
                size="small"
                aria-label="Previous month"
                onClick={handlePreviousMonth}
              >
                &lt;
              </IconButton>
              <Typography
                variant="body2"
                style={{
                  margin: "0 2px 0 2px",
                  width: "180px",
                  textAlign: "center",
                  fontWeight: "600",
                }}
              >
                {displayString}
              </Typography>
              <IconButton
                size="small"
                aria-label="Next month"
                onClick={handleNextMonth}
              >
                &gt;
              </IconButton>
              {isLoading && (
                <CircularProgress size={20} style={{ marginLeft: "10px" }} />
              )}
            </div>

            <div style={{ marginRight: "50px" }}>
              <FormSearchSelectFieldControl
                label={definition?.propertyName}
                size="small"
                control={control}
                sx={{ maxWidth: "220px", width: "100%" }}
                fieldName="selectedOptions"
                options={options}
                onChange={handleSelectChange}
              />
            </div>
          </div>

          <div
            style={{
              border: "none",
              borderRadius: "12px",
              padding: "10px",
            }}
          >
            <BarChart width={chartWidth} height={300} data={barChartData}>
              <CartesianGrid strokeDasharray="3 3" vertical={false} />
              {/* <YAxis
            label={{ value: 'Token Count', angle: -90, position: 'insideLeft', offset: 10 }} // {{ edit_1 }}
          /> */}
              <XAxis
                dataKey="name"
                stroke=""
                tick={{ fontSize: "0.75rem", fontWeight: 400 }}
                ticks={[
                  barChartData[0]?.name,
                  barChartData[7]?.name,
                  barChartData[14]?.name,
                  barChartData[21]?.name,
                  barChartData[28]?.name,
                ].filter(Boolean)}
              />
              <YAxis
                stroke=""
                tick={{ fontSize: "0.75rem", fontWeight: 400 }}
                tickFormatter={(value) =>
                  value > 1000 ? `${(value / 1000).toFixed(1)}k` : `${value}`
                }
              />
              <Tooltip />
              {/* eslint-disable-next-line no-restricted-syntax -- ds: data colour */}
              <Bar dataKey="value" fill="#D3B2E2" radius={[3, 3, 0, 0]} />
            </BarChart>
          </div>
        </div>
      </Box>
    </>
  );
};

export default UsageSummary;
