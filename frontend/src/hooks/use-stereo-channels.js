import { useState, useEffect, useRef } from "react";

/**
 * Downsamples a Float32Array of PCM samples into N peak values (0–1).
 * Each peak is the max absolute amplitude in its bucket — this is what
 * WaveSurfer uses to draw the waveform bars.
 */
function extractPeaks(channelData, numPeaks = 800) {
  const effectivePeaks = Math.min(numPeaks, channelData?.length);
  const blockSize = Math.max(
    1,
    Math.floor(channelData?.length / effectivePeaks),
  );
  const peaks = new Float32Array(effectivePeaks);
  for (let i = 0; i < effectivePeaks; i++) {
    let max = 0;
    const start = i * blockSize;
    // Extend the final bucket to the end of the array so remainder samples
    // (channelData.length % effectivePeaks) are included rather than dropped.
    const end =
      i === effectivePeaks - 1 ? channelData?.length : start + blockSize;
    for (let j = start; j < end; j++) {
      const abs = Math.abs(channelData[j]);
      if (abs > max) max = abs;
    }
    peaks[i] = max;
  }
  return peaks;
}

/**
 * Splits a stereo audio URL into two mono blob URLs (left = assistant, right = customer).
 *
 * Both output URLs share the same duration because they originate from one file,
 * which fixes the waveform alignment issue caused by using separate mono files
 * with different durations.
 *
 * Also returns real waveform peaks extracted from the decoded audio so WaveSurfer
 * can skip its own decode step and render the waveform immediately.
 *
 * @param {string} stereoUrl - URL of the stereo recording
 * @returns {{ assistantUrl: string, customerUrl: string, assistantPeaks: number[]|null, customerPeaks: number[]|null, loading: boolean, error: string|null }}
 */
export default function useStereoChannels(stereoUrl, isInbound = false) {
  const [state, setState] = useState({
    assistantUrl: "",
    customerUrl: "",
    assistantPeaks: null,
    customerPeaks: null,
    loading: !!stereoUrl,
    error: null,
  });
  const prevUrl = useRef("");
  const blobUrls = useRef([]);

  useEffect(() => {
    if (!stereoUrl || stereoUrl === prevUrl.current) return;
    prevUrl.current = stereoUrl;

    // Revoke old blob URLs
    blobUrls.current.forEach((u) => URL.revokeObjectURL(u));
    blobUrls.current = [];

    let cancelled = false;

    const split = async () => {
      setState({
        assistantUrl: "",
        customerUrl: "",
        assistantPeaks: null,
        customerPeaks: null,
        loading: true,
        error: null,
      });

      try {
        const response = await fetch(stereoUrl);
        if (!response.ok)
          throw new Error(`Failed to fetch stereo audio: ${response.status}`);
        const arrayBuffer = await response.arrayBuffer();

        const audioCtx = new AudioContext();
        const decoded = await audioCtx.decodeAudioData(arrayBuffer);
        await audioCtx.close();

        if (cancelled) return;

        const numChannels = decoded.numberOfChannels;
        const sampleRate = decoded.sampleRate;

        // Extract channels — if mono, duplicate to both tracks
        const leftData = decoded.getChannelData(0);
        const rightData =
          numChannels >= 2 ? decoded.getChannelData(1) : leftData;
        // Base: left=sim, right=agent; inbound flips.
        const [aData, cData] = isInbound
          ? [leftData, rightData]
          : [rightData, leftData];

        // Extract real peaks now while we have the decoded PCM data.
        // Passing these to WaveSurfer means it renders the waveform instantly
        // without needing to decode the blob URL a second time.
        const assistantPeaks = extractPeaks(aData);
        const customerPeaks =
          numChannels >= 2 ? extractPeaks(cData) : assistantPeaks;

        const assistantBlob = encodeWav(aData, sampleRate);
        const customerBlob =
          numChannels >= 2 ? encodeWav(cData, sampleRate) : assistantBlob;

        const assistantBlobUrl = URL.createObjectURL(assistantBlob);
        const customerBlobUrl = URL.createObjectURL(customerBlob);
        blobUrls.current = [assistantBlobUrl, customerBlobUrl];

        if (!cancelled) {
          setState({
            assistantUrl: assistantBlobUrl,
            customerUrl: customerBlobUrl,
            assistantPeaks,
            customerPeaks,
            loading: false,
            error: null,
          });
        }
      } catch (err) {
        if (!cancelled) {
          setState((prev) => ({
            ...prev,
            loading: false,
            error: err.message,
          }));
        }
      }
    };

    split();

    return () => {
      cancelled = true;
    };
  }, [stereoUrl]);

  // Cleanup blob URLs on unmount
  useEffect(() => {
    return () => {
      blobUrls.current.forEach((u) => URL.revokeObjectURL(u));
    };
  }, []);

  return state;
}

/**
 * Encode a Float32Array of PCM samples into a WAV Blob.
 */
function encodeWav(samples, sampleRate) {
  const numSamples = samples.length;
  const buffer = new ArrayBuffer(44 + numSamples * 2);
  const view = new DataView(buffer);

  // RIFF header
  writeString(view, 0, "RIFF");
  view.setUint32(4, 36 + numSamples * 2, true);
  writeString(view, 8, "WAVE");

  // fmt chunk
  writeString(view, 12, "fmt ");
  view.setUint32(16, 16, true); // chunk size
  view.setUint16(20, 1, true); // PCM
  view.setUint16(22, 1, true); // mono
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true); // byte rate
  view.setUint16(32, 2, true); // block align
  view.setUint16(34, 16, true); // bits per sample

  // data chunk
  writeString(view, 36, "data");
  view.setUint32(40, numSamples * 2, true);

  // PCM samples — clamp to 16-bit range using Int16Array for ~10x faster encoding
  const int16 = new Int16Array(numSamples);
  for (let i = 0; i < numSamples; i++) {
    const s = Math.max(-1, Math.min(1, samples[i]));
    int16[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
  }
  new Uint8Array(buffer, 44).set(new Uint8Array(int16.buffer));

  return new Blob([buffer], { type: "audio/wav" });
}

function writeString(view, offset, str) {
  for (let i = 0; i < str.length; i++) {
    view.setUint8(offset + i, str.charCodeAt(i));
  }
}
