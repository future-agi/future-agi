import { Box } from "@mui/material";
import PropTypes from "prop-types";
import React from "react";
import { generateNMarks } from "src/sections/develop-detail/Common/common";
import SliderRow from "../custom-model-options/SliderRow/SliderRow";
import { FormSearchSelectFieldControl } from "../FromSearchSelectField";
import HeadingAndSubheading from "../HeadingAndSubheading/HeadingAndSubheading";
import { useWatch } from "react-hook-form";

const voiceProviders = [
  {
    id: "elevenlabs",
    name: "ElevenLabs",
    description: "High-quality AI voices with emotional range",
    disabled: false,
  },
  {
    id: "azure",
    name: "Azure Cognitive Services",
    description:
      "Microsoft's neural text-to-speech (available with enterprise)",
    disabled: true,
  },
  {
    id: "openai",
    name: "OpenAI TTS",
    description: "OpenAI's text-to-speech models (available with enterprise)",
    disabled: true,
  },
  {
    id: "google",
    name: "Google Cloud TTS",
    description: "Google's WaveNet voices (available with enterprise)",
    disabled: true,
  },
  {
    id: "aws",
    name: "Amazon Polly",
    description:
      "AWS neural text-to-speech service (available with enterprise)",
    disabled: true,
  },
];

const voiceNames = {
  elevenlabs: [
    {
      id: "burt",
      name: "Burt",
      description: "Deep, resonant male voice with gravitas",
      gender: "male",
    },
    {
      id: "marissa",
      name: "Marissa",
      description: "Energetic, professional female voice",
      gender: "female",
    },
    {
      id: "andrea",
      name: "Andrea",
      description: "Warm, articulate female voice",
      gender: "female",
    },
    {
      id: "sarah",
      name: "Sarah",
      description: "Clear, friendly female voice",
      gender: "female",
    },
    {
      id: "phillip",
      name: "Phillip",
      description: "Authoritative, refined male voice",
      gender: "male",
    },
    {
      id: "steve",
      name: "Steve",
      description: "Casual, approachable male voice",
      gender: "male",
    },
    {
      id: "joseph",
      name: "Joseph",
      description: "Confident, professional male voice",
      gender: "male",
    },
    {
      id: "myra",
      name: "Myra",
      description: "Gentle, soothing female voice",
      gender: "female",
    },
    {
      id: "paula",
      name: "Paula",
      description: "Dynamic, engaging female voice",
      gender: "female",
    },
    {
      id: "ryan",
      name: "Ryan",
      description: "Young, energetic male voice",
      gender: "male",
    },
    {
      id: "drew",
      name: "Drew",
      description: "Friendly, conversational male voice",
      gender: "male",
    },
    {
      id: "paul",
      name: "Paul",
      description: "Mature, trustworthy male voice",
      gender: "male",
    },
    {
      id: "mrb",
      name: "MrB",
      description: "Distinctive, characterful male voice",
      gender: "male",
    },
    {
      id: "matilda",
      name: "Matilda",
      description: "Sweet, cheerful female voice",
      gender: "female",
    },
    {
      id: "mark",
      name: "Mark",
      description: "Clear, professional male voice",
      gender: "male",
    },
  ],
  azure: [
    {
      id: "jenny",
      name: "Jenny Neural",
      description: "Professional US English female",
      gender: "female",
      accent: "US",
    },
    {
      id: "guy",
      name: "Guy Neural",
      description: "Confident US English male",
      gender: "male",
      accent: "US",
    },
    {
      id: "aria",
      name: "Aria Neural",
      description: "Clear US English female",
      gender: "female",
      accent: "US",
    },
    {
      id: "davis",
      name: "Davis Neural",
      description: "Professional US English male",
      gender: "male",
      accent: "US",
    },
    {
      id: "jane",
      name: "Jane Neural",
      description: "British English female",
      gender: "female",
      accent: "UK",
    },
  ],
  openai: [
    {
      id: "alloy",
      name: "Alloy",
      description: "Balanced, versatile voice",
      gender: "neutral",
    },
    {
      id: "echo",
      name: "Echo",
      description: "Clear, professional voice",
      gender: "neutral",
    },
    {
      id: "fable",
      name: "Fable",
      description: "Warm, storytelling voice",
      gender: "neutral",
    },
    {
      id: "onyx",
      name: "Onyx",
      description: "Deep, authoritative voice",
      gender: "male",
    },
    {
      id: "nova",
      name: "Nova",
      description: "Energetic, modern voice",
      gender: "female",
    },
    {
      id: "shimmer",
      name: "Shimmer",
      description: "Bright, cheerful voice",
      gender: "female",
    },
  ],
  google: [
    {
      id: "en-us-wavenet-a",
      name: "WaveNet Female A",
      description: "Standard US female voice",
      gender: "female",
      accent: "US",
    },
    {
      id: "en-us-wavenet-b",
      name: "WaveNet Male A",
      description: "Standard US male voice",
      gender: "male",
      accent: "US",
    },
    {
      id: "en-us-wavenet-c",
      name: "WaveNet Female B",
      description: "Natural US female voice",
      gender: "female",
      accent: "US",
    },
    {
      id: "en-us-wavenet-d",
      name: "WaveNet Male B",
      description: "Natural US male voice",
      gender: "male",
      accent: "US",
    },
  ],
  aws: [
    {
      id: "joanna",
      name: "Joanna",
      description: "Professional US female voice",
      gender: "female",
      accent: "US",
    },
    {
      id: "matthew",
      name: "Matthew",
      description: "Professional US male voice",
      gender: "male",
      accent: "US",
    },
    {
      id: "ivy",
      name: "Ivy",
      description: "Young US female voice",
      gender: "female",
      accent: "US",
    },
    {
      id: "justin",
      name: "Justin",
      description: "Young US male voice",
      gender: "male",
      accent: "US",
    },
    {
      id: "amy",
      name: "Amy",
      description: "British female voice",
      gender: "female",
      accent: "UK",
    },
  ],
};

const VoiceAndSpeechForm = ({ control, errors }) => {
  const voiceProvider = useWatch({
    control: control,
    name: "voiceProvider",
  });

  return (
    <Box py={3} display={"flex"} flexDirection={"column"} gap={3}>
      <FormSearchSelectFieldControl
        control={control}
        options={voiceProviders.map((provider) => {
          return {
            label: provider.name,
            value: provider.id,
            disabled: provider.disabled,
            component: (
              <Box>
                <HeadingAndSubheading
                  heading={provider.name}
                  subHeading={provider.description}
                />
              </Box>
            ),
          };
        })}
        error={errors?.voiceProvider && errors.voiceProvider.message}
        fieldName={"voiceProvider"}
        label={"Voice Provider"}
        size={"small"}
        fullWidth
      />
      <FormSearchSelectFieldControl
        control={control}
        options={(voiceNames[voiceProvider] ?? []).map((voice) => {
          return {
            label: voice.name,
            value: voice.id,
            disabled: voice.disabled ?? false,
            component: (
              <Box>
                <HeadingAndSubheading
                  heading={voice.name}
                  subHeading={voice.description}
                />
              </Box>
            ),
          };
        })}
        error={errors?.voiceProvider && errors.voiceProvider.message}
        fieldName={"voiceName"}
        label={"Voice Name"}
        size={"small"}
        fullWidth
        emptyMessage={
          !voiceProvider || voiceProvider === ""
            ? "Please select voice provider"
            : "No suitable option"
        }
      />
      <Box px={1}>
        <SliderRow
          label="Interrupt Sensitivity"
          control={control}
          fieldName={"interruptSensitivity"}
          min={0}
          max={10}
          step={0.5}
          sliderContainerStyles={{}}
          marks={generateNMarks(0, 10, 0.5)}
          inputSectionStyles={undefined}
        />
      </Box>
      <Box px={1}>
        <SliderRow
          label="Conversation Speed"
          control={control}
          fieldName={"conversationSpeed"}
          min={0.7}
          max={1.2}
          step={0.1}
          sliderContainerStyles={{}}
          marks={generateNMarks(0.7, 1.2)}
          inputSectionStyles={undefined}
        />
      </Box>
      <Box px={1}>
        <SliderRow
          label="Finished Speaking Sensitivity"
          control={control}
          fieldName={"finishedSpeakingSensitivity"}
          min={0}
          max={10}
          step={1}
          sliderContainerStyles={{}}
          marks={generateNMarks(0, 10, 1)}
          inputSectionStyles={undefined}
        />
      </Box>
    </Box>
  );
};

export default VoiceAndSpeechForm;

VoiceAndSpeechForm.propTypes = {
  control: PropTypes.object,
  errors: PropTypes.object,
};
