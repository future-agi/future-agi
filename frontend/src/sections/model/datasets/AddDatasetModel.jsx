import {
  Box,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Divider,
  IconButton,
  Link,
  Paper,
  Radio,
  RadioGroup,
  styled,
  Typography,
  useTheme,
} from "@mui/material";
import React from "react";
import Iconify from "src/components/iconify";
import PropTypes from "prop-types";
import { PYTHON_DOCUMENTATION_URL } from "src/utils/constant";

const StyledRadio = styled(Radio)(({ theme }) => ({
  "& .MuiSvgIcon-root": {
    fontSize: 24,
  },
  color: theme.palette.text.disabled,
  "&.Mui-checked": {
    color: theme.palette.secondary.light,
  },
}));

const AddDatasetModel = ({ open, onClose, onAddDataset }) => {
  const [value, setValue] = React.useState(null);

  const handleChange = (event) => {
    setValue(event.target.value);
  };

  const onCloseClick = () => {
    setValue(null);
    onClose();
  };

  const renderInfo = () => {
    if (value === "addFromSdk") {
      return (
        <InfoBox
          title="Uploading Dataset"
          text="You can upload the datasets to our system by adding SDK. View the documentation by clicking link below."
          externalLinkLabel="SDK upload guide "
          href={PYTHON_DOCUMENTATION_URL}
        />
      );
    } else if (value === "addFromConnector") {
      return (
        <InfoBox
          title="Uploading Dataset"
          text="You can sync your data from your database to our system. To sync your database click on the below link"
          externalLinkLabel="Use connectors"
          href="/dashboard/sync"
        />
      );
    }
  };

  return (
    <Dialog
      open={open}
      onClose={onCloseClick}
      maxWidth={false}
      PaperProps={{ sx: { maxWidth: 560 } }}
      fullWidth
    >
      <DialogTitle
        sx={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
        }}
      >
        Add Dataset
        <IconButton onClick={onCloseClick}>
          <Iconify icon="solar:close-linear" />
        </IconButton>
      </DialogTitle>
      <DialogContent
        sx={{
          display: "flex",
          flexDirection: "column",
          gap: "20px",
          paddingBottom: value === "addFromExisting" ? 0 : "20px",
        }}
      >
        <RadioGroup
          aria-labelledby="demo-controlled-radio-buttons-group"
          name="controlled-radio-buttons-group"
          value={value}
          onChange={handleChange}
          sx={{ display: "flex", flexDirection: "column", gap: "20px" }}
        >
          <Box
            sx={{ display: "flex", alignItems: "flex-start" }}
            component="label"
          >
            <StyledRadio value="addFromSdk" />
            <OptionText
              title="Add dataset using SDK"
              subTitle="Add SDK to import your data to our system"
            />
          </Box>
          <Box sx={{ display: "flex", alignItems: "flex-start" }}>
            <StyledRadio value="addFromExisting" />
            <OptionText
              title="Add from existing dataset"
              subTitle="Choose from the existing datasets in our system to create a new dataset"
            />
          </Box>
          <Box
            sx={{ display: "flex", alignItems: "flex-start" }}
            component="label"
          >
            <StyledRadio value="addFromConnector" />
            <OptionText
              title="Add from connectors"
              subTitle="Sync your data from your database to our system"
            />
          </Box>
        </RadioGroup>
        {renderInfo()}
      </DialogContent>
      {value === "addFromExisting" && (
        <DialogActions>
          <Button
            color="primary"
            variant="contained"
            onClick={() => {
              setValue(null);
              onAddDataset();
            }}
          >
            Add dataset
          </Button>
        </DialogActions>
      )}
    </Dialog>
  );
};

const OptionText = ({ title, subTitle }) => {
  const theme = useTheme();

  return (
    <Box sx={{ display: "flex", flexDirection: "column", gap: 0.5 }}>
      <Typography variant="body2" color={theme.palette.text.primary}>
        {title}
      </Typography>
      <Box sx={{ display: "flex", gap: 1, alignItems: "flex-start" }}>
        <Iconify
          icon="solar:info-circle-bold"
          sx={{ width: "14px", flexShrink: 0 }}
          color="text.secondary"
        />
        <Typography variant="caption" color="text.secondary">
          {subTitle}
        </Typography>
      </Box>
    </Box>
  );
};

OptionText.propTypes = {
  title: PropTypes.string,
  subTitle: PropTypes.string,
};

const InfoBox = ({ title, text, externalLinkLabel, href }) => {
  const theme = useTheme();

  return (
    <Paper
      sx={{ width: "100%", background: theme.palette.background.paper }}
      elevation={2}
    >
      <Typography fontSize="13px" fontWeight={600} sx={{ padding: "18px" }}>
        {title}
      </Typography>
      <Divider />
      <Box
        sx={{
          padding: "18px",
          gap: 1,
          display: "flex",
          flexDirection: "column",
        }}
      >
        <Typography variant="caption" color="text.secondary">
          {text}
        </Typography>
        <Link
          href={href}
          variant="caption"
          sx={{ display: "flex", alignItems: "center", gap: 1 }}
        >
          {externalLinkLabel}{" "}
          <Iconify icon="solar:arrow-right-up-linear" sx={{ width: "16px" }} />
        </Link>
      </Box>
    </Paper>
  );
};

InfoBox.propTypes = {
  title: PropTypes.string,
  text: PropTypes.string,
  externalLinkLabel: PropTypes.string,
  href: PropTypes.string,
};

AddDatasetModel.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  onAddDataset: PropTypes.func,
};

export default AddDatasetModel;
