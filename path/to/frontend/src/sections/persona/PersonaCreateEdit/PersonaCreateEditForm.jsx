import React from 'react';
import { Form } from 'react-hook-form';
import { zodResolver } from 'zod-form-resolver';
import PersonCreateBaseValidationSchema from './common';

const PersonaCreateEditForm = () => {
  const { register, control, handleSubmit } = zodResolver(PersonCreateBaseValidationSchema);

  const onSubmit = async (data) => {
    try {
      const payload = {
        name: data.name,
        description: data.description,
      };
      // API call to create persona
    } catch (error) {
      console.error(error);
    }
  };

  return (
    <Form onSubmit={handleSubmit(onSubmit)}>
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
      <button type="submit">Save</button>
    </Form>
  );
};

export default PersonaCreateEditForm;