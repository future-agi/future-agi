import { yellow } from "@mui/material/colors";
import { orange, red } from "src/theme/palette";
import {
  blobUrlManager,
  audioContextManager,
} from "src/utils/memory-management";
import { canonicalEntries } from "src/utils/utils";

export const formatTime = (seconds) => {
  if (!seconds || isNaN(seconds)) return "0:00";
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m}:${s < 10 ? "0" : ""}${s}`;
};

export const getColorByWeight = (weight) => {
  if (weight >= 0.6) {
    return { activeColor: red[500], inactiveColor: red[200] };
  } else if (weight >= 0.3) {
    return { activeColor: orange[500], inactiveColor: orange[200] };
  } else {
    return { activeColor: yellow[500], inactiveColor: yellow[200] };
  }
};

export const transformAudioData = (valueInfos) => {
  const metadata = valueInfos;
  const errorAnalysis = metadata?.errorAnalysis || metadata?.error_analysis;
  if (!errorAnalysis) return [];

  return canonicalEntries(errorAnalysis)
    .map(([, value]) => value)
    .flat()
    .map(({ orgSegment, rankReason, weight }, index) => {
      if (!orgSegment) return null;

      const {
        startTime: _startTime,
        endTime: _endTime,
        url: segmentUrl,
      } = orgSegment;
      const { activeColor, inactiveColor } = getColorByWeight(weight);

      return {
        id: `audio-segment-${index}`,
        audioData: { url: segmentUrl || orgSegment.url }, // Use segment-specific URL
        startTime: 0, // Segment URL is pre-trimmed, so start from 0
        endTime: orgSegment.duration, // Use full duration of trimmed segment
        activeColor,
        inactiveColor,
        description: rankReason,
      };
    })
    .filter(Boolean);
};

export const trimAudio = (audioUrl, startTime, endTime) => {
  return new Promise((resolve, reject) => {
    fetch(audioUrl)
      .then((response) => response.arrayBuffer())
      .then((arrayBuffer) => {
        const audioContext = audioContextManager.getContext();
        return audioContext.decodeAudioData(arrayBuffer);
      })
      .then((audioBuffer) => {
        const sampleRate = audioBuffer.sampleRate;
        const startSample = Math.floor(startTime * sampleRate);
        const endSample = endTime
          ? Math.floor(endTime * sampleRate)
          : audioBuffer.length;

        // Create a new buffer for the clipped audio using the managed context
        const audioContext = audioContextManager.getContext();
        const clippedBuffer = audioContext.createBuffer(
          audioBuffer.numberOfChannels,
          endSample - startSample,
          sampleRate,
        );

        // Copy data from original buffer to new buffer
        for (
          let channel = 0;
          channel < audioBuffer.numberOfChannels;
          channel++
        ) {
          const channelData = audioBuffer.getChannelData(channel);
          clippedBuffer
            .getChannelData(channel)
            .set(channelData.slice(startSample, endSample));
        }

        // Convert buffer to Blob URL
        return bufferToWave(clippedBuffer);
      })
      .then((wavBlob) => {
        // Use managed blob URL creation
        const blobUrl = blobUrlManager.createBlobUrl(wavBlob);
        // Return both the URL and a cleanup function
        resolve({
          url: blobUrl,
          cleanup: () => blobUrlManager.revokeBlobUrl(blobUrl),
        });
      })
      .catch((error) => reject(error));
  });
};

const bufferToWave = (audioBuffer) => {
  return new Promise((resolve) => {
    const offlineContext = new OfflineAudioContext(
      audioBuffer.numberOfChannels,
      audioBuffer.length,
      audioBuffer.sampleRate,
    );

    const source = offlineContext.createBufferSource();
    source.buffer = audioBuffer;
    source.connect(offlineContext.destination);
    source.start();

    offlineContext.startRendering().then((renderedBuffer) => {
      const wavBlob = audioBufferToWav(renderedBuffer);
      resolve(wavBlob);
    });
  });
};

const audioBufferToWav = (audioBuffer) => {
  const numOfChannels = audioBuffer.numberOfChannels;
  const sampleRate = audioBuffer.sampleRate;
  const length = audioBuffer.length * numOfChannels * 2 + 44;
  const buffer = new ArrayBuffer(length);
  const view = new DataView(buffer);

  let offset = 0;
  const writeString = (s) => {
    for (let i = 0; i < s.length; i++) {
      view.setUint8(offset + i, s.charCodeAt(i));
    }
    offset += s.length;
  };

  const writeUint32 = (d) => {
    view.setUint32(offset, d, true);
    offset += 4;
  };

  const writeUint16 = (d) => {
    view.setUint16(offset, d, true);
    offset += 2;
  };

  writeString("RIFF");
  writeUint32(length - 8);
  writeString("WAVE");
  writeString("fmt ");
  writeUint32(16);
  writeUint16(1);
  writeUint16(numOfChannels);
  writeUint32(sampleRate);
  writeUint32(sampleRate * numOfChannels * 2);
  writeUint16(numOfChannels * 2);
  writeUint16(16);
  writeString("data");
  writeUint32(length - offset - 4);

  for (let i = 0; i < audioBuffer.length; i++) {
    for (let channel = 0; channel < numOfChannels; channel++) {
      const sample = Math.max(
        -1,
        Math.min(1, audioBuffer.getChannelData(channel)[i]),
      );
      view.setInt16(
        offset,
        sample < 0 ? sample * 0x8000 : sample * 0x7fff,
        true,
      );
      offset += 2;
    }
  }

  return new Blob([buffer], { type: "audio/wav" });
};

export function getAudioErrorMessage(err) {
  const msg = err?.message?.toLowerCase() || "";
  const name = err?.name?.toLowerCase() || "";

  const isAbort = name === "aborterror" || msg.includes("abort");

  if (isAbort) {
    return { message: null, isAbort: true };
  }

  if (msg.includes("getduration") || msg.includes("reading 'getduration'")) {
    return {
      message:
        "We couldn't read the audio duration. Try using a different link or file.",
      isAbort: false,
    };
  }

  if (msg.includes("failed to fetch")) {
    return {
      message:
        "We couldn’t fetch the audio. Please check the link or your internet connection.",
      isAbort: false,
    };
  }

  if (
    msg.includes("ffmpegdemuxer") ||
    msg.includes("demuxer_error_could_not_open")
  ) {
    return {
      message:
        "This audio format is not supported or the file is corrupted. Try another one.",
      isAbort: false,
    };
  }

  if (msg.includes("media_element_error") || msg.includes("format error")) {
    return {
      message:
        "This audio format is not supported by your browser. Try converting the audio or using a different file.",
      isAbort: false,
    };
  }

  return {
    message: "An unexpected error occurred while loading the audio.",
    isAbort: false,
  };
}
