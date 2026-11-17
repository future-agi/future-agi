import { Box, Typography } from "@mui/material";
import React from "react";
import HelperText from "../../Common/HelperText";
import PropTypes from "prop-types";
import SecretSelect from "src/sections/common/SecretSelect/SecretSelect";
import { RetrievalFormItemWrapper } from "./RetrievalComponents";
import EmbeddingConfigField from "./EmbeddingConfigField";
import HeadingAndSubHeading from "src/components/HeadingAndSubheading/HeadingAndSubheading";
import { FormSearchSelectFieldControl } from "src/components/FromSearchSelectField";
import FormTextFieldV2 from "src/components/FormTextField/FormTextFieldV2";
import { vectorStoreAuthHelp } from "./retrievalCopy";

const QdrantForm = ({ allColumns, control }) => {
  return (
    <Box sx={{ display: "flex", flexDirection: "column", gap: 2 }}>
      <HeadingAndSubHeading
        heading={
          <FormSearchSelectFieldControl
            fullWidth
            label="Column"
            size="small"
            control={control}
            fieldName="columnId"
            options={allColumns?.map((column) => ({
              label: column.headerName,
              value: column.field,
            }))}
          />
        }
        subHeading={
          <Typography typography={"s2"} color={"text.primary"}>
            Query to send to the vector database
          </Typography>
        }
      />
      <SecretSelect
        label="Qdrant API Key"
        control={control}
        fieldName="apiKey"
        fullWidth
        helperText={
          <HelperText text={vectorStoreAuthHelp("Qdrant", "collection")} />
        }
        size="small"
      />
      <Box
        sx={{
          display: "flex",
          flexDirection: "column",
          border: "2px solid",
          borderColor: "background.neutral",
          borderRadius: "8px",
          "& > *:last-child": {
            borderBottom: "none",
          },
        }}
      >
        <RetrievalFormItemWrapper>
          <FormTextFieldV2
            label="Qdrant URL"
            size="small"
            placeholder="Enter qdrant url"
            control={control}
            fieldName="url"
            fullWidth
            helperText={
              <HelperText text="URL of the Qdrant instance. Include the port as well in the URL" />
            }
          />
        </RetrievalFormItemWrapper>
        <RetrievalFormItemWrapper>
          <FormTextFieldV2
            label="Collection Name"
            size="small"
            placeholder="Enter collection name"
            control={control}
            fieldName="collectionName"
            fullWidth
            helperText={
              <HelperText text="Name of the Qdrant collection to query" />
            }
          />
        </RetrievalFormItemWrapper>

        <RetrievalFormItemWrapper>
          <FormTextFieldV2
            label="Number of chunks to fetch"
            size="small"
            placeholder="Enter topK"
            control={control}
            fieldName="topK"
            type="number"
            fieldType="number"
            fullWidth
            helperText={
              <HelperText text="The number of top matching vectors to fetch" />
            }
          />
        </RetrievalFormItemWrapper>
        <RetrievalFormItemWrapper>
          <EmbeddingConfigField control={control} />
        </RetrievalFormItemWrapper>
        <RetrievalFormItemWrapper>
          <FormTextFieldV2
            label="Key to extract"
            size="small"
            placeholder="Enter key"
            control={control}
            fieldName="key"
            fullWidth
            helperText={
              <HelperText text="The payload key to read from each Qdrant point" />
            }
          />
        </RetrievalFormItemWrapper>
        <RetrievalFormItemWrapper>
          <FormTextFieldV2
            label="Vector Length"
            size="small"
            placeholder="Enter vector length"
            control={control}
            fieldName="vectorLength"
            type="number"
            fieldType="number"
            fullWidth
            helperText={<HelperText text="Length of Vector" />}
          />
        </RetrievalFormItemWrapper>
        <RetrievalFormItemWrapper>
          <FormTextFieldV2
            label="Concurrency"
            size="small"
            control={control}
            placeholder="Enter concurrency"
            fieldName="concurrency"
            type="number"
            fieldType="number"
            fullWidth
            helperText={
              <HelperText text="Number of rows to process concurrently. If not set, will use our own system configuration" />
            }
          />
        </RetrievalFormItemWrapper>
      </Box>
    </Box>
  );
};

QdrantForm.propTypes = {
  allColumns: PropTypes.array,
  control: PropTypes.object,
};

export default QdrantForm;
