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
  boxShadow: "2px -2px 12px 0px rgba(0, 0, 0, 0.08)",
  fileIcon: "/assets/icons/ic_prompt.svg",
  folderIcon: "/assets/icons/ic_folder.svg",
  fileColor: "red.500",
  folderColor: "blue.500",
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
    title: "Create new template",
    description:
      "Craft a prompt, customize it to your needs, and save it as a reusable template.",
    icon: "/assets/icons/ic_scratch.svg",
  },
  prompt: {
    title: "Create your first prompt",
    description:
      "Write or generate a prompt, run it with a focused input, and save versions as you improve the output.",
    icon: "/assets/icons/ic_prompt_message.svg",
  },
};

export const ONBOARDING_EMPTY_MESSAGE = {
  prompt: {
    title: "Start your first prompt loop",
    description:
      "Create one prompt, run it against an example, save the baseline, then compare the next version.",
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
