import RenderFileIcons from "./RenderFileIcons";

const meta = {
  title: "Components/RenderFileIcons",
  component: RenderFileIcons,
  tags: ["autodocs"], // Optional, helps with Docs tab generation
  args: {
    fileType: "document.pdf", // Default args for the story
  },
  argTypes: {
    fileType: {
      control: "text",
      description: "File name or extension used to render the correct icon",
    },
  },
};

export default meta;

export const Default = {
  args: {
    fileType: "document.pdf",
  },
};

export const ImageFile = {
  args: {
    fileType: "photo.jpg",
  },
};

export const UnknownExtension = {
  args: {
    fileType: "file.unknownext",
  },
};

export const NoExtension = {
  args: {
    fileType: "filename",
  },
};

export const NullFileType = {
  args: {
    fileType: null,
  },
};
