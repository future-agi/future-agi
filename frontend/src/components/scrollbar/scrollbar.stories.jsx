import React from "react";
import Scrollbar from "./scrollbar";

export default {
  title: "Components/Scrollbar",
  component: Scrollbar,
};

const Template = (args) => (
  <Scrollbar {...args}>
    <div>
      <h2>Content</h2>
      <p>This is a scrollable area. Scroll to see more content.</p>
      <p>Lorem ipsum dolor sit amet, consectetur adipiscing elit.</p>
      <p>
        Quisque sollicitudin, sapien vel cursus hendrerit, odio augue
        sollicitudin tortor.
      </p>
      <p>Fusce nec sapien id leo posuere auctor non nec orci.</p>
      <p>
        Nulla facilisi. Vivamus scelerisque, enim in varius tincidunt, eros nisl
        fermentum dui.
      </p>
      <p>Curabitur consequat ligula non risus euismod scelerisque.</p>
      <p>Donec non suscipit libero, eget pharetra leo.</p>
      <p>Donec non suscipit libero, eget pharetra leo.</p>
      <p>Donec non suscipit libero, eget pharetra leo.</p>
      <p>Donec non suscipit libero, eget pharetra leo.</p>
      <p>Donec non suscipit libero, eget pharetra leo.</p>
    </div>
  </Scrollbar>
);

export const Default = Template.bind({});
Default.args = {
  sx: { width: "100%", height: "300px" },
};
