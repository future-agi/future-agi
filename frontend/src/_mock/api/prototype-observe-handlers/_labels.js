export const ALL_LABELS = [
  {
    id: "f0ac59b2-5969-412f-b351-49c8287bdfbc",
    name: "Cat",
    type: "categorical",
    organization: "2f099199-4a16-4547-acfe-f21a27ae468c",
    settings: {
      options: [
        {
          label: "Yes",
        },
        {
          label: "No",
        },
        {
          label: "Maybe",
        },
      ],
      strategy: null,
      rulePrompt: "",
      multiChoice: false,
      autoAnnotate: false,
    },
    project: "47e7ea0c-d45c-488a-81a7-99d394365106",
    description: "categorical",
  },
  {
    id: "93188525-26a1-4f53-8297-3e1a20ddd49b",
    name: "NUmeric",
    type: "numeric",
    organization: "2f099199-4a16-4547-acfe-f21a27ae468c",
    settings: {
      max: 10,
      min: 0,
      stepSize: 1,
      displayType: "slider",
    },
    project: "47e7ea0c-d45c-488a-81a7-99d394365106",
    description: "numeric",
  },
  {
    id: "7333f224-f740-414b-a96a-e993518bf34d",
    name: "Thumbs up Down",
    type: "thumbs_up_down",
    organization: "2f099199-4a16-4547-acfe-f21a27ae468c",
    settings: {},
    project: "47e7ea0c-d45c-488a-81a7-99d394365106",
    description: "dsadada",
  },
  {
    id: "27d10a37-551e-40ca-9695-1eff644c97be",
    name: "Star",
    type: "star",
    organization: "2f099199-4a16-4547-acfe-f21a27ae468c",
    settings: {
      noOfStars: 5,
    },
    project: "47e7ea0c-d45c-488a-81a7-99d394365106",
    description: "cxzcz",
  },
  {
    id: "3b954a9c-437c-437c-bae2-94ebeeb7988d",
    name: "dsada",
    type: "text",
    organization: "2f099199-4a16-4547-acfe-f21a27ae468c",
    settings: {
      maxLength: 100,
      minLength: 10,
      placeholder: "dsada",
    },
    project: "47e7ea0c-d45c-488a-81a7-99d394365106",
    description: "dsada",
  },
];

export const ALL_LABELS_VALUES = {
  "f0ac59b2-5969-412f-b351-49c8287bdfbc": {
    value: "['Yes']",
  },
  "93188525-26a1-4f53-8297-3e1a20ddd49b": {
    value: "4",
  },
  "7333f224-f740-414b-a96a-e993518bf34d": {
    value: "up",
  },
  "27d10a37-551e-40ca-9695-1eff644c97be": {
    value: "4",
  },
  "3b954a9c-437c-437c-bae2-94ebeeb7988d": {
    value: "dsada",
  },
};
// This will be account specific change it as u want
export const ALL_ANNOTATORS = [
  {
    id: "1",
    name: "example-user-10@example.com",
  },
  {
    id: "2",
    name: "Atharva Bhange new",
  },
  {
    id: "3",
    name: "athr",
  },
];

export const ALL_ANNOTATORS_VALUES = ALL_LABELS.map((label, index) => {
  return {
    ...label,
    value: ALL_LABELS_VALUES[label.id].value,
    updatedAt: new Date(),
    updatedBy: ALL_ANNOTATORS[index % ALL_ANNOTATORS.length].name,
  };
});
