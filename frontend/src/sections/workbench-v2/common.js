import { PropertyName, trackEvent } from "../../utils/Mixpanel";

export const PROMPT_ITEM_TYPES = {
  FILE: "PROMPT",
  FOLDER: "FOLDER",
  TEMPLATE: "TEMPLATE",
};

export const CREATE_PROMPT_OPTIONS = [
  {
    name: "Generate with AI",
    desc: "Generate a prompt automatically",
    icon: "/assets/icons/components/ic_generate_prompt.svg", // ✨ magic-wand style icon
    id: "gen_ai",
  },
  {
    name: "Start from scratch",
    desc: "Write your prompt manually",
    icon: "/assets/icons/components/ic_polish.svg", // 📝 or pen/pencil icon
    id: "start_from_scratch",
  },
  {
    name: "Start with a template",
    desc: "Select from a library of vetted prompt templates",
    icon: "/assets/icons/ic_scratch.svg", // 📄 or layered-templates icon
    id: "start_with_template",
  },
];

export const iconStyles = {
  fileIcon: "/assets/icons/ic_prompt.svg",
  folderIcon: "/assets/icons/ic_folder.svg",
  fileColor: "text.secondary",
  folderColor: "text.secondary",
};

export const PROMPT_ICON_MAPPER = {
  PROMPT: "/assets/icons/ic_prompt.svg",
  FOLDER: "/assets/icons/ic_folder_filled.svg",
  TEMPLATE: "/assets/icons/ic_prompt_template_filled.svg",
};

export const ROOT_ROUTES = ["all", "my-templates"];

export const FOLDERS = {
  id: "root",
  name: "Root",
  type: "FOLDER",
  children: [
    {
      id: "my-templates",
      name: "My templates",
      type: "FOLDER",
      children: [],
    },
    {
      id: "all",
      name: "All Prompts",
      type: "FOLDER",
      children: [],
    },
  ],
};

export const EMPTY_MESSAGE = {
  template: {
    title: "No templates yet",
    description:
      "Save a prompt as a template to start new prompts from it, with its messages, variables and model settings.",
    icon: "/assets/icons/ic_scratch.svg",
  },
  prompt: {
    title: "No prompts yet",
    description:
      "Write a prompt, test it against models in the playground, and version it. Your code fetches the version by label.",
    icon: "/assets/icons/ic_prompt_message.svg",
  },
};

export const handleMenuItemEvent = (event, type, extraProperties = {}) => {
  trackEvent(event, {
    [PropertyName.click]: true,
    [PropertyName.type]: type,
    ...extraProperties,
  });
};
