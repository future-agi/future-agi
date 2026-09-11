import CustomBreadcrumbs from "./custom-breadcrumbs";
import { MemoryRouter } from "react-router-dom";

const meta = {
  component: CustomBreadcrumbs,
  title: "UI Components/CustomBreadcrumbs",
  decorators: [
    (Story) => (
      <MemoryRouter>
        <Story />
      </MemoryRouter>
    ),
  ],
};

export default meta;

const Template = (args) => {
  return <CustomBreadcrumbs {...args} />;
};

export const Default = Template.bind({});

Default.args = {
  heading: "Custom Breadcrumbs",
  links: [
    { name: "Home", href: "/" },
    { name: "Dashboard", href: "/dashboard" },
    { name: "Settings", href: "/settings" },
  ],
  moreLink: ["https://www.example.com", "https://www.example2.com"],
  activeLast: true,
};

export const WithoutHeading = Template.bind({});

WithoutHeading.args = {
  links: [
    { name: "Home", href: "/" },
    { name: "Dashboard", href: "/dashboard" },
    { name: "Settings", href: "/settings" },
  ],
  moreLink: ["https://www.example.com", "https://www.example2.com"],
  activeLast: true,
};

export const WithAction = Template.bind({});

WithAction.args = {
  heading: "Custom Breadcrumbs",
  links: [
    { name: "Home", href: "/" },
    { name: "Dashboard", href: "/dashboard" },
    { name: "Settings", href: "/settings" },
  ],
  moreLink: ["https://www.example.com", "https://www.example2.com"],
  action: <button> Action Button </button>,
  activeLast: true,
};

export const WithoutMoreLink = Template.bind({});

WithoutMoreLink.args = {
  heading: "Custom Breadcrumbs",
  links: [
    { name: "Home", href: "/" },
    { name: "Dashboard", href: "/dashboard" },
    { name: "Settings", href: "/settings" },
  ],
  activeLast: true,
};
