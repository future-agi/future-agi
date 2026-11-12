export const DefaultMessages = [
  {
    role: "system",
    content: [
      {
        type: "text",
        text: "",
      },
    ],
  },
  {
    role: "user",
    content: [
      {
        type: "text",
        text: "",
      },
    ],
  },
];

export const createDraftPayload = {
  name: "",
  prompt_config: [
    {
      messages: DefaultMessages,
    },
  ],
};

export const dummyEvaluationData = {
  status: true,
  result: {
    column_config: [
      {
        id: "{{variable1}}",
        name: "{{variable1}}",
      },
      {
        id: "{{variable2}}",
        name: "{{variable2}}",
      },
      {
        id: "Output-v10",
        name: "Output",
        template_version: "v10",
        messages: [
          {
            role: "system",
            content: [
              {
                text: "",
                type: "text",
              },
            ],
          },
          {
            role: "user",
            content: [
              {
                text: "Hello from India\\n",
                type: "text",
              },
            ],
          },
        ],
      },
    ],
    table: [
      {
        row_id: "row_1",
        "{{variable1}}": "A1",
        "{{variable2}}": "B1",
        "Output-v10": "Output with A1 and B1",
      },
      {
        row_id: "row_2",
        "{{variable1}}": "A2",
        "{{variable2}}": "B2",
        "Output-v10": "Output with A2 and B2",
      },
      {
        row_id: "row_3",
        "{{variable1}}": "A3",
        "{{variable2}}": "B3",
        "Output-v10": "Output with A3 and B3",
      },
      {
        row_id: "row_3",
        "{{variable1}}": "A3",
        "{{variable2}}": "B3",
        "Output-v10": "Output with A3 and B3",
      },
      {
        row_id: "row_4",
        "{{variable1}}": "A4",
        "{{variable2}}": "B4",
        "Output-v10": "Output with A4 and B4",
      },
      {
        row_id: "row_5",
        "{{variable1}}": "A5",
        "{{variable2}}": "B5",
        "Output-v10": "Output with A5 and B5",
      },
    ],
  },
};

export const dummyEvaluationDataV2 = {
  status: true,
  result: {
    variables: {
      topic: ["cars", "planes"],
      num_words: ["20", null],
    },
    v1: {
      output: [
        "Sure! Cars have been a significant part of modern society since the early 20th century, revolutionizing transportation and shaping urban development. Initially powered by steam or electricity, the widespread adoption of gasoline engines transformed cars into reliable modes of transport.\n\n### Evolution of Cars\nThe first mass-produced car was the Ford Model T, introduced in 1908, which made automobiles accessible to the general public. Over the decades, cars evolved from simple machines into complex technological marvels featuring advanced safety systems, fuel efficiency improvements, and infotainment options.\n\n### Types of Cars\nCars come in various types to suit different needs:\n- **Sedans**: Known for comfort and practicality.\n- **SUVs (Sport Utility Vehicles)**: Popular for their spaciousness and off-road capabilities.\n- **Coupes**: Sporty two-door vehicles often favored for their style.\n- **Hatchbacks**: Versatile with a rear door that swings upward; they combine compact size with utility.\n- **Electric Vehicles (EVs)**: Gaining popularity due to environmental concerns; models like Tesla have set new standards for performance and range.\n\n### Future Trends\nThe automotive industry is currently experiencing significant changes driven by technology:\n1. **Electric Mobility**: With increasing awareness about climate change, many manufacturers are transitioning towards electric vehicles to reduce carbon emissions.\n2. **Autonomous Driving**: Companies are investing heavily in self-driving technologies that promise safer roads and increased convenience.\n3. **Connected Cars**: The integration of IoT (Internet of Things) allows vehicles to communicate with each other and infrastructure for enhanced safety features.\n\n### Cultural Impact\nCars have also had a profound cultural impact. They symbolize freedom and independence in many cultures while influencing music, art, cinema, and lifestyle choices.\n\nIn conclusion, cars continue to play an essential role in our daily lives while evolving rapidly due to technological advancements. Whether as a means of personal expression or practical transportation solutions, they remain an integral part of modern life.",
      ],
      eval_names: [],
      eval_output: [],
      messages: [
        {
          role: "system",
          content: [
            {
              text: "",
              type: "text",
            },
          ],
        },
        {
          role: "user",
          content: [
            {
              text: "Hello can you write something about {{topic}}\n",
              type: "text",
            },
          ],
        },
      ],
      model_detail: {
        logo_url: "/icons/settings/openai.png",
        providers: "openai",
        model_name: "gpt-4o-mini",
        is_available: true,
      },
    },
    v6: {
      output: [
        "Cars revolutionized transportation, offering speed and convenience. They symbolize freedom, innovation, and style while impacting the economy and environment significantly.",
        'Sure! Here’s a brief overview of planes without using the word "plane":\n\nThese vehicles are designed for air travel, featuring wings that generate lift and engines providing thrust. They can vary in size from small private crafts to large commercial jets, capable of transporting passengers or cargo across vast distances. The design includes components like fuselage, tail section, and landing gear. Pilots operate them using complex instruments and navigational systems to ensure safety during flight. Aviation has significantly changed global connectivity and commerce since its inception.\n\nLet me know if you need more information!',
      ],
      eval_names: [],
      eval_output: [],
      messages: [
        {
          role: "system",
          content: [
            {
              text: "",
              type: "text",
            },
          ],
        },
        {
          role: "user",
          content: [
            {
              text: "Hello can you write something about {{topic}} in {{num_words}} words\n",
              type: "text",
            },
          ],
        },
      ],
      model_detail: {
        logo_url: "/icons/settings/openai.png",
        providers: "openai",
        model_name: "gpt-4o-mini",
        is_available: true,
      },
    },
  },
};
