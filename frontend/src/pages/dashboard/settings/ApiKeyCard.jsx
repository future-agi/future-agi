import React, { useEffect, useState } from "react";
import PropTypes from "prop-types";
import { Button, Input, Typography } from "@mui/material";
import axios, { endpoints } from "src/utils/axios";
import { enqueueSnackbar } from "notistack";

// const ApiKeyCard = ({ apiKeyName, icon, apiKeyString }) => {
const ApiKeyCard = ({ apiCardData, refreshData, setRefreshData }) => {
  const [inputValue, setInputValue] = useState("");

  const handleSaveApiKey = async () => {
    const response = await axios.post(endpoints.settings.apiKeys, {
      provider: apiCardData.name,
      key: inputValue,
    });

    if (response.status === 200) {
      setRefreshData((prev) => !prev);
      enqueueSnackbar("API key saved successfully", { variant: "success" });
    } else {
      enqueueSnackbar("Failed to save API key", { variant: "error" });
    }
  };

  useEffect(() => {
    setInputValue(apiCardData.keyString);
  }, [refreshData, apiCardData]);

  return (
    <div style={styles.card}>
      <div style={styles.topPart}>
        <img
          src={apiCardData.icon} // Use the icon prop
          alt={`${apiCardData.name} Icon`} // Use the apiKeyName for alt text
          style={styles.icon}
        />
        <Typography
          variant="subtitle2"
          sx={{ ...styles.label, fontSize: "1.0rem", fontWeight: 500 }}
        >
          {apiCardData.name} {/* Use the apiKeyName prop */}
        </Typography>
      </div>
      <div style={styles.bottomPart}>
        <Input
          placeholder="Enter your API key"
          value={inputValue} // Use the apiKeyString prop
          onChange={(e) => {
            setInputValue(e.target.value);
          }} // Add onChange handler
          style={styles.input} // Use custom styles
        />
        <Button
          variant="contained"
          color="primary"
          sx={{ ...styles.saveButton }}
          onClick={() => {
            handleSaveApiKey();
          }}
        >
          Save
        </Button>
      </div>
    </div>
  );
};

// PropTypes validation
ApiKeyCard.propTypes = {
  apiKeyName: PropTypes.string.isRequired, // Validate apiKeyName
  icon: PropTypes.string.isRequired, // Validate icon
  apiKeyString: PropTypes.string.isRequired, // Validate apiKeyString
  apiCardData: PropTypes.object.isRequired, // Validate apiCardData
  refreshData: PropTypes.bool.isRequired, // Validate refreshData
  setRefreshData: PropTypes.func.isRequired, // Validate setRefreshData
};

const styles = {
  card: {
    borderRadius: "8px",
    border: "1px solid var(--border-default)",
    overflow: "hidden",
    display: "flex",
    flexDirection: "column",
    height: "120px", // Adjust height as needed
    width: "400px", // Adjust width as needed
    marginBottom: "20px",
  },
  topPart: {
    display: "flex",
    alignItems: "center",
    justifyContent: "flex-start",
    padding: "10px",
    backgroundColor: "var(--bg-neutral)",
    flex: "0 0 40%", // 40% height
    borderBottom: "0px solid var(--border-default)",
  },
  bottomPart: {
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
    padding: "10px",
    flex: "0 0 60%", // 60% height
    marginRight: "10px",
  },
  icon: {
    width: "20px", // Adjust icon size as needed
    height: "20px",
    marginRight: "10px",
    marginLeft: "10px",
  },
  label: {
    fontWeight: "600",
  },
  input: {
    flex: "0 0 75%", // Set width to 20%
    height: "40px", // Set a fixed height for the Button
    marginBottom: "40px",
    border: "none", // Add border to mimic TextField
    boxSizing: "border-box",
    padding: "10px",
    borderRadius: "8px",
    outline: "none",
  },
  saveButton: {
    flex: "0 0 20%", // Set width to 20%
    height: "40px", // Set a fixed height for the Button
    marginBottom: "40px",
  },
};

export default ApiKeyCard;
