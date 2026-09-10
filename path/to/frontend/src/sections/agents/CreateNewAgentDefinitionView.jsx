import React from 'react';
import { FormTextFieldV2 } from 'components/FormTextFieldV2';
import { zodResolver } from 'zod-form-resolver';
import CreateAgentDefinitionBaseValidationSchema from './common';

const CreateNewAgentDefinitionView = () => {
  const { register, control } = zodResolver(CreateAgentDefinitionBaseValidationSchema);

  return (
    <div>
      <FormTextFieldV2
        control={control}
        fieldName="agentName"
        label="Agent name"
        required
        trim
      />
      <FormTextFieldV2
        control={control}
        fieldName="description"
        label="Description"
        required
        trim
      />
    </div>
  );
};

export default CreateNewAgentDefinitionView;