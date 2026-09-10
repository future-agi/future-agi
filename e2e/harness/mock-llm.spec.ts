import { test, expect, request } from '@playwright/test';
import { E2E } from '../lib/env';

test('gateway routes chat completions to the deterministic mock', async () => {
  const req = await request.newContext();
  const res = await req.post(`${E2E.gatewayUrl}/v1/chat/completions`, {
    headers: { Authorization: 'Bearer local-dev-only-shared-secret-replace-me' },
    data: { model: 'gpt-4o-mini', messages: [{ role: 'user', content: 'ping' }] },
  });
  expect(res.status()).toBe(200);
  const body = await res.json();
  expect(body.choices[0].message.content).toBe('echo: ping');
  await req.dispose();
});

test('mock returns the Generate Prompt pipeline marker for the generation stage', async () => {
  const req = await request.newContext();
  const res = await req.post(`${E2E.gatewayUrl}/v1/chat/completions`, {
    headers: { Authorization: 'Bearer local-dev-only-shared-secret-replace-me' },
    data: {
      model: 'gpt-4o-mini',
      messages: [
        {
          role: 'user',
          content:
            'Based on the analysis, create an optimized prompt that achieves the intended goals:\n\n' +
            'Task Description:\ndraft a support email\n\nAnalysis Results:\nsome analysis\n\nRequirements:\n...',
        },
      ],
    },
  });
  expect(res.status()).toBe(200);
  const body = await res.json();
  expect(body.choices[0].message.content).toBe('e2e-generated-prompt: draft a support email');
  await req.dispose();
});

test('mock serves image generation as decodable b64 PNG with an echo revised_prompt', async () => {
  const req = await request.newContext();
  const res = await req.post(`${E2E.mockLlmUrl}/v1/images/generations`, {
    data: { model: 'gpt-image-1', prompt: 'a small cat' },
  });
  expect(res.status()).toBe(200);
  const body = await res.json();
  expect(body.data[0].revised_prompt).toBe('echo-image: a small cat');
  const png = Buffer.from(body.data[0].b64_json, 'base64');
  // PNG magic bytes.
  expect(png.subarray(0, 4).toString('binary')).toBe('\x89PNG');
  expect(body.data[0].url).toBeUndefined();
  await req.dispose();
});

test('mock serves TTS as a 1s WAV', async () => {
  const req = await request.newContext();
  const res = await req.post(`${E2E.mockLlmUrl}/v1/audio/speech`, {
    data: { model: 'gpt-4o-mini-tts', input: 'hello there', voice: 'alloy' },
  });
  expect(res.status()).toBe(200);
  expect(res.headers()['content-type']).toContain('audio/wav');
  const wav = await res.body();
  expect(wav.subarray(0, 4).toString('binary')).toBe('RIFF');
  expect(wav.length).toBe(16044);
  await req.dispose();
});

test('mock transcribes a multipart upload echoing the filename', async () => {
  const req = await request.newContext();
  const res = await req.post(`${E2E.mockLlmUrl}/v1/audio/transcriptions`, {
    multipart: {
      file: { name: 'clip.wav', mimeType: 'audio/wav', buffer: Buffer.from('abcde') },
      model: 'whisper-1',
    },
  });
  expect(res.status()).toBe(200);
  const body = await res.json();
  expect(body.text).toBe('echo-transcript: clip.wav 5b');
  await req.dispose();
});

test('mock records requests for the /__e2e/requests introspection endpoint', async () => {
  const req = await request.newContext();
  const res = await req.get(`${E2E.mockLlmUrl}/__e2e/requests?path=/v1/images/generations`);
  expect(res.status()).toBe(200);
  const log = await res.json();
  expect(Array.isArray(log)).toBe(true);
  await req.dispose();
});
