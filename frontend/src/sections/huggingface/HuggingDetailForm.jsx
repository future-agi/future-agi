import { LoadingButton } from "@mui/lab";
import { Box, Button, Stack, Typography, useTheme } from "@mui/material";
import PropTypes from "prop-types";
import React from "react";
import { DatasetInfo, FaceBadge } from "./huggingFaceStyle";
import Iconify from "src/components/iconify";
import { trackEvent, Events, PropertyName } from "src/utils/Mixpanel";
import LinkifiedTypography from "src/components/LinkifiedTypography/LinkifiedTypography";
import { FormSearchSelectFieldControl } from "src/components/FromSearchSelectField";
import FormTextFieldV2 from "src/components/FormTextField/FormTextFieldV2";

const HuggingDetailForm = ({
  subsetOptions,
  splitOptions,
  huggingFaceDetail,
  onSubmit,
  onClose,
  isLoadingCreateDataset,
  showNameField,
  control,
  huggingFaceDatasetConfigError,
}) => {
  const theme = useTheme();

  return (
    <Stack flexDirection="column" p="16px" height="100%">
      <Typography variant="m3" fontWeight={500} color="text.primary" mb="29px">
        Hugging Face Dataset
      </Typography>
      <DatasetInfo>
        <Stack
          direction="row"
          alignItems="center"
          justifyContent={"space-between"}
          paddingTop={"3px"}
          paddingBottom={"11px"}
          paddingLeft={"14px"}
          paddingRight={"11px"}
          sx={{ borderBottom: `1px solid ${theme.palette.divider}` }}
        >
          <Typography
            variant="s1"
            color="text.primary"
            fontWeight="fontWeightMedium"
          >
            Dataset Information
          </Typography>
          <Box sx={{ display: "flex", alignItems: "center", gap: "5px" }}>
            <Iconify
              icon="material-symbols:download"
              sx={{ color: "text.disabled" }}
            />
            <Typography
              component="span"
              sx={{
                color: "text.disabled",
                textAlign: "center",
                fontSize: "10px",
              }}
            >
              {huggingFaceDetail?.downloads}
            </Typography>
            <Iconify
              icon="weui:like-outlined"
              sx={{ color: "text.disabled" }}
            />
            <Typography
              variant="span"
              component="span"
              sx={{
                color: "text.disabled",
                textAlign: "center",
                fontSize: "10px",
              }}
            >
              {huggingFaceDetail?.likes}
            </Typography>
          </Box>
        </Stack>
        <Stack
          direction="row"
          alignItems="center"
          justifyContent={"space-between"}
          paddingTop={"8px"}
          paddingBottom={"14px"}
          paddingLeft={"14px"}
          paddingRight={"11px"}
          sx={{ borderBottom: `1px solid ${theme.palette.divider}` }}
        >
          <Typography
            variant="s1"
            color="text.primary"
            fontWeight="fontWeightMedium"
          >
            Dataset
          </Typography>
          <Typography
            variant="p"
            component="span"
            sx={{
              color: "text.disabled",
              textAlign: "center",
              fontWeight: "500",
              fontSize: "12px",
              textDecoration: "underline",
            }}
          >
            {huggingFaceDetail?.name}
          </Typography>
        </Stack>
        <Stack
          direction="row"
          alignItems="center"
          justifyContent={"space-between"}
          paddingTop={"10px"}
          paddingLeft={"14px"}
          paddingRight={"11px"}
          paddingBottom={"14px"}
          sx={{ borderBottom: `1px solid ${theme.palette.divider}` }}
        >
          <Typography
            variant="s1"
            color="text.primary"
            fontWeight="fontWeightMedium"
          >
            Author
          </Typography>
          <FaceBadge color={"#2F7CF7"} bgcolor={"#2F7CF729"}>
            {huggingFaceDetail?.author}
          </FaceBadge>
        </Stack>
        <Stack
          paddingTop={"10px"}
          paddingBottom={"14px"}
          paddingLeft={"14px"}
          paddingRight={"11px"}
        >
          <Typography
            variant="s1"
            color="text.primary"
            fontWeight="fontWeightMedium"
          >
            Description{" "}
          </Typography>
          <Typography
            sx={{
              color: "text.disabled",
              fontWeight: "400",
              fontSize: "14px",
              pt: "6px",
            }}
          >
            {huggingFaceDetail?.description}
          </Typography>
        </Stack>
      </DatasetInfo>
      <Typography
        variant="s1"
        fontWeight="fontWeightMedium"
        color="text.primary"
        mb="15px"
      >
        Ingest Dataset
      </Typography>
      <Box
        sx={{
          flex: 1,
          display: "flex",
          gap: 2,
          flexDirection: "column",
          paddingY: 1,
        }}
      >
        {showNameField && (
          <Box display="flex" flexDirection="column">
            <FormTextFieldV2
              disabled={huggingFaceDatasetConfigError?.length}
              control={control}
              size="small"
              fieldName="name"
              label="Name"
              placeholder="Enter name"
              variant="outlined"
              sx={{ mb: 0.5 }}
              margin="none"
              required
            />
            <Typography
              color="text.secondary"
              variant="s3"
              fontWeight="fontWeightMedium"
              sx={{ mt: 0, mb: 2 }}
            >
              Name your dataset
            </Typography>
          </Box>
        )}

        <Box display="flex" flexDirection="column">
          <FormSearchSelectFieldControl
            fullWidth
            control={control}
            fieldName="huggingface_dataset_config"
            size="small"
            label="Subset"
            options={subsetOptions}
            disabled={huggingFaceDatasetConfigError?.length}
            sx={{ mb: 0.5 }}
            required
          />
          <Typography
            color="text.secondary"
            variant="s3"
            fontWeight="fontWeightMedium"
            sx={{ mt: 0, mb: 2 }}
          >
            Select which subset of the dataset you want to import
          </Typography>
        </Box>

        <Box display="flex" flexDirection="column">
          <FormSearchSelectFieldControl
            fullWidth
            control={control}
            fieldName="huggingface_dataset_split"
            size="small"
            label="Split"
            options={splitOptions}
            disabled={huggingFaceDatasetConfigError?.length}
            sx={{ mb: 0.5 }}
            required
          />
          <Typography
            color="text.secondary"
            variant="s3"
            fontWeight="fontWeightMedium"
            sx={{ mt: 0, mb: 2 }}
          >
            Select the data split you want to import
          </Typography>
        </Box>

        {huggingFaceDatasetConfigError && (
          <LinkifiedTypography
            text={huggingFaceDatasetConfigError}
            color={"red.500"}
            variant="s2"
            fontWeight={"fontWeightRegular"}
          />
        )}

        <Box display="flex" flexDirection="column">
          <FormTextFieldV2
            disabled={huggingFaceDatasetConfigError?.length}
            control={control}
            fieldName="num_rows"
            size="small"
            label="No. of rows"
            variant="outlined"
            fullWidth
            isSpinnerField
            placeholder="Enter number of rows"
            fieldType="number"
            required
          />
          <Typography
            color="text.secondary"
            variant="s3"
            fontWeight="fontWeightMedium"
            sx={{ mt: 0, mb: 2 }}
          >
            Choose the number of rows to import
          </Typography>
        </Box>
      </Box>
      <Box
        sx={{
          display: "flex",
          gap: 2,
          width: "100%",
          paddingBottom: "16px",
          marginTop: "auto",
        }}
      >
        <Button
          onClick={() => {
            onClose();
          }}
          fullWidth
          variant="outlined"
        >
          Cancel
        </Button>
        <LoadingButton
          disabled={huggingFaceDatasetConfigError?.length}
          fullWidth
          variant="contained"
          color="primary"
          loading={isLoadingCreateDataset}
          onClick={() => {
            trackEvent(Events.dataAddSuccessfull, {
              [PropertyName.method]: " add using import from hugging face",
            });
            onSubmit();
          }}
        >
          Start Experimenting
        </LoadingButton>
      </Box>
    </Stack>
  );
};

HuggingDetailForm.propTypes = {
  subsetOptions: PropTypes.array,
  splitOptions: PropTypes.array,
  huggingFaceDetail: PropTypes.object,
  onSubmit: PropTypes.func,
  onClose: PropTypes.func,
  isLoadingCreateDataset: PropTypes.bool,
  showNameField: PropTypes.bool,
  control: PropTypes.object.isRequired,
  huggingFaceDatasetConfigError: PropTypes.string,
};

export default HuggingDetailForm;
