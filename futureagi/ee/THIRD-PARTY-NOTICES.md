# Voice audio metrics notices

## DNSMOS reference code and operator-supplied weights

Upstream: https://github.com/microsoft/DNS-Challenge

Pinned commit: `591184a9fcb2cbdec02520fed81a32bbbf9d73ff`.
The regular OVRL polynomial coefficients and window constants reproduced from
`DNSMOS/dnsmos_local.py` are Copyright (c) Microsoft Corporation, MIT licensed
under the repository's `LICENSE-CODE` (notice reproduced below).

The regular model is `DNSMOS/DNSMOS/sig_bak_ovr.onnx`:

- Size: **1,157,965 bytes**.
- SHA-256: `269fbebdb513aa23cddfbb593542ecc540284a91849ac50516870e1ac78f6edd`.
- Git blob: `814b8dc6dafa729c0b821a64bef2e7156dc0c01a`.

The upstream repository legal notice licenses documentation and other content
under CC BY 4.0 (https://creativecommons.org/licenses/by/4.0/legalcode) and code
under MIT. It does not name the weights per-file. Future AGI therefore does
**not redistribute these weights** in the image; operators supply them after
reviewing the upstream terms. Microsoft names, logos and trademarks are not
licensed. Do not substitute personalized DNSMOS, P.808 or NISQA weights.

Requested citations:

- Reddy, Gopal, Cutler. “DNSMOS: A non-intrusive perceptual objective speech
  quality metric to evaluate noise suppressors.” ICASSP 2021.
- Reddy, Gopal, Cutler. “DNSMOS P.835: A non-intrusive perceptual objective speech
  quality metric to evaluate noise suppressors.” ICASSP 2022.

## Silero VAD

`silero-vad==6.2.3` is distributed as a Python dependency in the `ml` extra,
including its bundled model files. Upstream:
https://github.com/snakers4/silero-vad. MIT License, Copyright (c) 2020-present
Silero Team. The wheel and source hashes are pinned in `futureagi/uv.lock`;
installation must retain the package's license notice. Production loads the
local bundled model, not a mutable `torch.hub` download.

## MIT notice

The following MIT terms apply to the reproduced Microsoft code and Silero VAD,
with their respective copyright notices above:

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

## VQI calibration data

TODO: filled by the calibration study commit
