import RadioField from "./RadioField";
import { useForm } from "react-hook-form";

const meta = {
  component: RadioField,
  title: "UI Components/RadioField",
};

export default meta;

const Template = (args) => {
  const { control } = useForm();
  return <RadioField {...args} control={control} />;
};

export const Default = Template.bind({});

Default.args = {
  fieldName: "example",
  label: "Example Radio Field",
  options: [
    { label: "Option 1", value: "option1" },
    { label: "Option 2", value: "option2" },
  ],
  optionDirection: "column",
};

export const RowDirection = Template.bind({});

RowDirection.args = {
  fieldName: "example",
  label: "Example Radio Field",
  options: [
    { label: "Option 1", value: "option1" },
    { label: "Option 2", value: "option2" },
  ],
  optionDirection: "row",
};
