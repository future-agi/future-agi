import LinkifiedTypography from "./LinkifiedTypography";

const meta = {
  component: LinkifiedTypography,
  title: "Components/LinkifiedTypography", // Add this line to define the category in Storybook
  argTypes: {
    text: { control: "text" }, // Allows changing the `text` prop directly from the Storybook UI
  },
};

export default meta;

export const Default = {
  args: {
    text: "Check out https://www.example.com for more information!",
  },
};

export const WithLongText = {
  args: {
    text: "Here's a longer sentence with multiple links like https://www.google.com and http://www.github.com, just to test how it handles longer texts.",
  },
};

export const WithDifferentLinks = {
  args: {
    text: "Learn more at https://www.openai.com or https://www.microsoft.com.",
  },
};
