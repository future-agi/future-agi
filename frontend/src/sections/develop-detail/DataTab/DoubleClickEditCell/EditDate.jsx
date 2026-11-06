import React, { useState } from "react";
import {
  Box,
  DialogActions,
  IconButton,
  Menu,
  MenuItem,
  Typography,
} from "@mui/material";
import { DateCalendar } from "@mui/x-date-pickers";
import { LoadingButton } from "@mui/lab";
import { DemoContainer, DemoItem } from "@mui/x-date-pickers/internals/demo";
import { LocalizationProvider } from "@mui/x-date-pickers/LocalizationProvider";
import { AdapterDateFns } from "@mui/x-date-pickers/AdapterDateFns";
import { TimeClock } from "@mui/x-date-pickers/TimeClock";
import PropTypes from "prop-types";
import Iconify from "src/components/iconify";

const formatDate = (date) => {
  const pad = (num) => String(num).padStart(2, "0");
  const year = date.getFullYear();
  const month = pad(date.getMonth() + 1);
  const day = pad(date.getDate());
  const hours = pad(date.getHours());
  const minutes = pad(date.getMinutes());
  const seconds = pad(date.getSeconds());
  return `${month}/${day}/${year} ${hours}:${minutes}:${seconds}`;
};

const formatHours = (hours) => (hours % 12 === 0 ? 12 : hours % 12); // Convert to 12-hour format
const isPM = (hours) => hours >= 12;

const EditDate = ({ params, onClose, onCellValueChanged }) => {
  const convertedDate = new Date(params?.value);
  const [selectedDate, setSelectedDate] = useState(convertedDate);
  const [error, setError] = useState("");
  const [ampm, setAmPm] = useState(isPM(selectedDate.getHours()) ? "PM" : "AM");
  const [anchorEl, setAnchorEl] = useState(null);
  const [timeView, setTimeView] = useState("hours");

  const handleClose = () => {
    onClose();
  };

  const handleDateChange = (date) => {
    setSelectedDate((prev) => {
      const updatedDate = new Date(date);
      updatedDate.setHours(prev.getHours(), prev.getMinutes(), 0);
      return updatedDate;
    });
  };

  const handleTimeChange = (newTime) => {
    setSelectedDate(newTime);
  };

  const handleAmPmClick = (event) => {
    setAnchorEl(event.currentTarget);
  };

  const handleAmPmSelect = (value) => {
    const hours = selectedDate.getHours();
    if (value === "AM" && hours >= 12) {
      selectedDate.setHours(hours - 12);
    } else if (value === "PM" && hours < 12) {
      selectedDate.setHours(hours + 12);
    }
    setSelectedDate(new Date(selectedDate)); // Update state
    setAmPm(value);
    setAnchorEl(null);
  };

  const increaseMinutes = () => {
    setSelectedDate((prevDate) => {
      const updatedDate = new Date(prevDate);
      updatedDate.setMinutes(updatedDate.getMinutes() + 1);
      return updatedDate;
    });
  };

  const decreaseMinutes = () => {
    setSelectedDate((prevDate) => {
      const updatedDate = new Date(prevDate);
      updatedDate.setMinutes(updatedDate.getMinutes() - 1);
      return updatedDate;
    });
  };

  const handleHourClick = () => {
    setTimeView("hours");
  };

  const onSubmit = (e) => {
    e.preventDefault();
    try {
      const formattedDate = formatDate(selectedDate);
      onCellValueChanged({ ...params, newValue: formattedDate });
      handleClose();
    } catch (err) {
      setError(err.message);
    }
  };

  return (
    <Box
      sx={{
        display: "flex",
        justifyContent: "center",
        flexDirection: "column",
        paddingBottom: "-200px",
        marginX: "15px",
        marginBottom: "-10px",
      }}
      component="form"
      onSubmit={onSubmit}
    >
      <Box
        sx={{
          display: "flex",
          justifyContent: "space-between",
          gap: 2,
          height: "400px",
        }}
      >
        <Box
          sx={{
            backgroundColor: "var(--bg-paper)",
            width: "55%",
          }}
        >
          <LocalizationProvider dateAdapter={AdapterDateFns}>
            <DateCalendar value={selectedDate} onChange={handleDateChange} />
          </LocalizationProvider>
        </Box>

        <Box
          sx={{
            backgroundColor: "var(--bg-paper)",
            width: "45%",
            display: "flex",
            flexDirection: "column",
            alignItems: "center",
            paddingTop: "45px",
          }}
        >
          <Box
            sx={{
              display: "flex",
              flexDirection: "row",
              justifyContent: "space-evenly",
              width: "100%",
              height: "20%",
              alignItems: "center",
            }}
          >
            {/* Hour Display */}
            <Box onClick={handleHourClick} sx={{ cursor: "pointer" }}>
              <Typography fontWeight={500} color="text.primary" fontSize="24px">
                {String(formatHours(selectedDate.getHours())).padStart(2, "0")}
              </Typography>
            </Box>

            {/* Minute Display with Arrows */}
            <Box
              sx={{
                display: "flex",
                flexDirection: "column",
                alignItems: "center",
              }}
            >
              <IconButton onClick={increaseMinutes}>
                <Iconify
                  icon="solar:alt-arrow-up-linear"
                  width="30px"
                  sx={{ color: "var(--text-primary)" }}
                />
              </IconButton>
              <Typography
                fontWeight={500}
                color="text.disabled"
                fontSize="24px"
              >
                {String(selectedDate.getMinutes()).padStart(2, "0")}
              </Typography>
              <IconButton onClick={decreaseMinutes}>
                <Iconify
                  icon="solar:alt-arrow-down-linear"
                  width="30px"
                  sx={{ color: "var(--text-primary)" }}
                />
              </IconButton>
            </Box>

            {/* AM/PM Dropdown */}
            <Box
              sx={{
                width: "92px",
                height: "40px",
                backgroundColor: "var(--bg-paper)",
                border: "2px solid var(--border-default)",
                borderRadius: "8px",
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
              }}
            >
              <Typography
                fontWeight={400}
                color="var(--text-disabled)"
                fontSize="13px"
                marginLeft="10px"
              >
                {ampm}
              </Typography>
              <IconButton onClick={handleAmPmClick}>
                <Iconify
                  icon="solar:alt-arrow-down-linear"
                  width="24px"
                  sx={{ color: "var(--text-muted)" }}
                />
              </IconButton>

              <Menu
                anchorEl={anchorEl}
                open={Boolean(anchorEl)}
                onClose={() => setAnchorEl(null)}
                sx={{ zIndex: "9999" }}
                onMouseDown={(event) => event.stopPropagation()}
              >
                <MenuItem onClick={() => handleAmPmSelect("AM")}>AM</MenuItem>
                <MenuItem onClick={() => handleAmPmSelect("PM")}>PM</MenuItem>
              </Menu>
            </Box>
          </Box>

          {/* TimeClock */}
          <Box
            sx={{
              display: "flex",
              justifyContent: "center",
              alignItems: "center",
              marginTop: "20px",
            }}
          >
            <LocalizationProvider dateAdapter={AdapterDateFns}>
              <DemoContainer components={["TimeClock", "TimeClock"]}>
                <DemoItem>
                  <TimeClock
                    value={selectedDate}
                    onChange={handleTimeChange}
                    ampm
                    view={timeView}
                  />
                </DemoItem>
              </DemoContainer>
            </LocalizationProvider>
          </Box>
        </Box>
      </Box>

      {error && <Typography color="error">{error}</Typography>}

      <DialogActions sx={{ marginRight: "-15px", marginTop: "-10px" }}>
        <LoadingButton
          onClick={handleClose}
          sx={{
            backgroundColor: "action.selected",
            width: "90px",
          }}
        >
          Cancel
        </LoadingButton>
        <LoadingButton
          variant="contained"
          color="primary"
          type="submit"
          sx={{ width: "90px" }}
        >
          Save
        </LoadingButton>
      </DialogActions>
    </Box>
  );
};

EditDate.propTypes = {
  params: PropTypes.object,
  onClose: PropTypes.func,
  onCellValueChanged: PropTypes.func,
};

export default EditDate;
