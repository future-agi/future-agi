import { Box, Typography } from "@mui/material";
import PropTypes from "prop-types";
import HelperText from "../../Common/HelperText";
import HeadingAndSubHeading from "src/components/HeadingAndSubheading/HeadingAndSubheading";
import { FormSearchSelectFieldControl } from "src/components/FromSearchSelectField";
import FormTextFieldV2 from "src/components/FormTextField/FormTextFieldV2";
import SecretSelect from "src/sections/common/SecretSelect/SecretSelect";
import EmbeddingConfigField from "./EmbeddingConfigField";
import { RetrievalFormItemWrapper } from "./RetrievalComponents";

const VectorStoreForm = ({ allColumns, control, provider, requiresApiKey }) => (
  <Box sx={{ display: "flex", flexDirection: "column", gap: 2 }}>
    <HeadingAndSubHeading
      heading={
        <FormSearchSelectFieldControl
          fullWidth
          label="Column"
          size="small"
          control={control}
          fieldName="columnId"
          options={allColumns?.map((column) => ({ label: column.headerName, value: column.field }))}
        />
      }
      subHeading={<Typography typography="s2" color="text.primary">Query to send to the vector database</Typography>}
    />
    <Box sx={{ display: "flex", flexDirection: "column", border: "2px solid", borderColor: "background.neutral", borderRadius: "8px", "& > *:last-child": { borderBottom: "none" } }}>
      {requiresApiKey && (
        <RetrievalFormItemWrapper>
          <SecretSelect label={`${provider} API Key`} control={control} fieldName="apiKey" fullWidth size="small" helperText={<HelperText text={`API key for authenticating with ${provider}`} />} />
        </RetrievalFormItemWrapper>
      )}
      <RetrievalFormItemWrapper>
        <FormTextFieldV2 label={`${provider} URL`} size="small" control={control} fieldName="url" fullWidth helperText={<HelperText text="Connection URL for the vector database" />} />
      </RetrievalFormItemWrapper>
      <RetrievalFormItemWrapper>
        <FormTextFieldV2 label="Collection Name" size="small" control={control} fieldName="collectionName" fullWidth helperText={<HelperText text="Collection or table to search" />} />
      </RetrievalFormItemWrapper>
      <RetrievalFormItemWrapper>
        <FormTextFieldV2 label="Number of chunks to fetch" size="small" control={control} fieldName="topK" type="number" fieldType="number" fullWidth />
      </RetrievalFormItemWrapper>
      <RetrievalFormItemWrapper><EmbeddingConfigField control={control} /></RetrievalFormItemWrapper>
      <RetrievalFormItemWrapper>
        <FormTextFieldV2 label="Key to extract" size="small" control={control} fieldName="key" fullWidth helperText={<HelperText text="Optional field to return from each match" />} />
      </RetrievalFormItemWrapper>
      <RetrievalFormItemWrapper>
        <FormTextFieldV2 label="Vector column" size="small" control={control} fieldName="queryKey" fullWidth helperText={<HelperText text="Vector field name; used by pgvector" />} />
      </RetrievalFormItemWrapper>
      <RetrievalFormItemWrapper>
        <FormTextFieldV2 label="Vector Length" size="small" control={control} fieldName="vectorLength" type="number" fieldType="number" fullWidth />
      </RetrievalFormItemWrapper>
      <RetrievalFormItemWrapper>
        <FormTextFieldV2 label="Concurrency" size="small" control={control} fieldName="concurrency" type="number" fieldType="number" fullWidth />
      </RetrievalFormItemWrapper>
    </Box>
  </Box>
);

VectorStoreForm.propTypes = {
  allColumns: PropTypes.array,
  control: PropTypes.object,
  provider: PropTypes.string.isRequired,
  requiresApiKey: PropTypes.bool,
};

export default VectorStoreForm;
