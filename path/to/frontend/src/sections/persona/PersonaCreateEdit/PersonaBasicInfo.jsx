import React from 'react';
import { FormTextFieldV2 } from 'components/FormTextFieldV2';
import { zodResolver } from 'zod-form-resolver';
import PersonCreateBaseValidationSchema from './common';

const PersonaBasicInfo = () => {
  const { register, control } = zodResolver(PersonCreateBaseValidationSchema);

  return (
    <div>
      <FormTextFieldV2
        control={control}
        fieldName="name"
        label="Persona name"
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

export default PersonaBasicInfo;