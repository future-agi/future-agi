import React from 'react';
import { FormTextFieldV2 } from 'components/FormTextFieldV2';
import { zodResolver } from 'zod-form-resolver';
import ScenarioCreateBaseValidationSchema from './common';

const CreateScenarioView = () => {
  const { register, control } = zodResolver(ScenarioCreateBaseValidationSchema);

  return (
    <div>
      <FormTextFieldV2
        control={control}
        fieldName="name"
        label="Scenario name"
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

export default CreateScenarioView;