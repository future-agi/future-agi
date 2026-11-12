# Gateway request recipes

Use **Gateway > Models > a model > API** to copy the request for that model.
Copying or selecting a recipe sends no inference request and changes no provider
configuration. Requests you run yourself can incur provider or credit charges.

## Start with the right URL and model

- Use a **gateway API key**, held in `AGENTCC_API_KEY`, not a provider key or an
  admin token. Keep it server-side; do not put it in URLs or browser storage.
- Copy the public gateway base URL from the console. For a deployment under a
  path prefix, keep that prefix: `https://YOUR_GATEWAY_HOST/team/gateway/v1`.
- The OpenAI SDK base ends in `/v1`. The Messages SDK base does **not**: use
  `https://YOUR_GATEWAY_HOST/team/gateway`, because that SDK appends `/v1/messages`.
  Raw HTTP uses `/v1/messages` or `/v1/responses` exactly once.
- Use the model ID the API tab shows. A qualified own-provider ID takes the form
  `YOUR_PROVIDER_ID/YOUR_MODEL`; replace both parts with your configured route.
  An organization provider ID is not necessarily an upstream vendor slug.
- If the hosted URL or own-provider access cannot be verified, the console does
  not offer a request. Review Providers or retry the provider read; a retained
  model listing is not proof of current access.

Basic chat, embeddings, image and rerank examples remain available for their
request kinds. Basic chat can export the submitted request, including sampling
and streaming settings. Streaming output can be partial: inspect errors and the
request ID in Logs before deciding whether to retry; do not automatically replay
an uncertain request.

## Caller model fallback and provider preferences

Choose **Routing and fallback** on a chat model. Replace every `YOUR_...` value
before running this instructional example:

```sh
curl https://YOUR_GATEWAY_HOST/team/gateway/v1/chat/completions \
  -H "Authorization: Bearer $AGENTCC_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "YOUR_PROVIDER_ID/YOUR_PRIMARY_MODEL",
    "messages": [{"role": "user", "content": "Hello"}],
    "models": ["YOUR_PROVIDER_ID/YOUR_FALLBACK_MODEL"],
    "provider": {
      "order": ["YOUR_UPSTREAM_PROVIDER_SLUG"],
      "only": ["YOUR_UPSTREAM_PROVIDER_SLUG"],
      "allow_fallbacks": false
    }
  }'
```

`models` and `provider` are **top-level wire fields**, not headers or organization
settings. For Python, the OpenAI SDK merges `extra_body` into the wire body:

```python
import os
from openai import OpenAI

client = OpenAI(
    base_url="https://YOUR_GATEWAY_HOST/team/gateway/v1",
    api_key=os.environ["AGENTCC_API_KEY"],
    max_retries=0,
)
response = client.chat.completions.create(
    model="YOUR_PROVIDER_ID/YOUR_PRIMARY_MODEL",
    messages=[{"role": "user", "content": "Hello"}],
    extra_body={
        "models": ["YOUR_PROVIDER_ID/YOUR_FALLBACK_MODEL"],
        "provider": {
            "order": ["YOUR_UPSTREAM_PROVIDER_SLUG"],
            "only": ["YOUR_UPSTREAM_PROVIDER_SLUG"],
            "allow_fallbacks": False,
        },
    },
)
print(response.choices[0].message.content)
```

In TypeScript, spread these custom fields into the request body, not the second
argument of `create` and not an `extra_body` property:

```typescript
import OpenAI from "openai";

const client = new OpenAI({
  baseURL: "https://YOUR_GATEWAY_HOST/team/gateway/v1",
  apiKey: process.env.AGENTCC_API_KEY,
  maxRetries: 0,
});
const routing = {
  models: ["YOUR_PROVIDER_ID/YOUR_FALLBACK_MODEL"],
  provider: {
    order: ["YOUR_UPSTREAM_PROVIDER_SLUG"],
    only: ["YOUR_UPSTREAM_PROVIDER_SLUG"],
    allow_fallbacks: false,
  },
};
const response = await client.chat.completions.create({
  model: "YOUR_PROVIDER_ID/YOUR_PRIMARY_MODEL",
  messages: [{ role: "user", content: "Hello" }],
  ...routing,
});
console.log(response.choices[0].message.content);
```

Important limits:

- The caller's `models[]` replaces the configured model fallback chain for this
  request. List at most **five** model IDs. Eligible fallback hops are attempted
  on **429 or upstream 5xx** errors, not guaranteed to succeed.
- An explicit organization `fallback_enabled: false` disables caller chains too.
  An unset switch preserves the gateway default; the recipe does not change it.
- On aggregator routes, `provider.order` and `provider.only` name **upstream-provider
  slugs**, not the organization provider ID you added in the console. Direct
  single-provider routes match their gateway provider ID instead. Replace the
  sample slug for the actual route; do not assume these identifiers are equal.
- `only` restricts eligible providers; `order` expresses preference order;
  `allow_fallbacks: false` prevents selecting providers outside that order. This
  is distinct from the caller's model fallback list.
- Support is route-dependent. Missing endpoint data can produce
  `provider_preference_unavailable`; filters that leave no eligible route can
  produce `no_provider_matches`. BYOK aggregators apply forwarded preferences
  upstream. Endpoint registration does not establish preference support.
- Key, model and billing restrictions still apply to every hop. Mixed-price-unit
  fallback parity is **not supported**. These examples do not promise automatic
  success, mixed billing or unrestricted cross-model routing.

## Messages and Responses: simple text, own provider only

On an own-provider chat route, explicitly choose **Messages (instructional)** or
**Responses (instructional)**. Use the recipe only if the provider supports that
API. The model catalog does not verify native API compatibility, so these are
instructional examples, not a compatibility certification. They do not export a
previous submitted chat request and are not offered for Future AGI credits.

Messages requires `max_tokens` and `messages`. This Python example uses the
unversioned SDK root, keeping the deployment prefix:

```python
import os
from anthropic import Anthropic

client = Anthropic(
    base_url="https://YOUR_GATEWAY_HOST/team/gateway",
    api_key=os.environ["AGENTCC_API_KEY"],
    max_retries=0,
)
response = client.messages.create(
    model="YOUR_PROVIDER_ID/YOUR_MESSAGES_MODEL",
    max_tokens=256,
    messages=[{"role": "user", "content": "Hello"}],
)
for block in response.content:
    if block.type == "text":
        print(block.text)
```

Responses uses the OpenAI `/v1` base and `input`, rather than chat `messages`:

```typescript
import OpenAI from "openai";

const client = new OpenAI({
  baseURL: "https://YOUR_GATEWAY_HOST/team/gateway/v1",
  apiKey: process.env.AGENTCC_API_KEY,
  maxRetries: 0,
});
const response = await client.responses.create({
  model: "YOUR_PROVIDER_ID/YOUR_RESPONSES_MODEL",
  input: "Hello",
});
console.log(response.output_text);
```

The API tab also offers cURL and the other SDK language for both recipes. A
registered endpoint does not promise every model, native beta field, tool shape
or managed route supports that dialect. Messages routing translation can refuse
native-only fields with `messages_routing_translation_unsupported`; use the
provider's documentation for anything beyond simple text. These snippets are
not a report of an executed SDK call.

## Speech: save the binary response

Select a Speech model, or explicitly select **Speech** when the model's type is
not reported. Replace `YOUR_VOICE` with a voice supported by your provider and
confirm it supports MP3 output:

```sh
curl https://YOUR_GATEWAY_HOST/team/gateway/v1/audio/speech \
  -H "Authorization: Bearer $AGENTCC_API_KEY" \
  -H "Content-Type: application/json" \
  --output speech.mp3 \
  -d '{
    "model": "YOUR_PROVIDER_ID/YOUR_SPEECH_MODEL",
    "input": "Hello",
    "voice": "YOUR_VOICE",
    "response_format": "mp3"
  }'
```

The response is binary, not chat text. An HTTP error can instead be JSON; inspect
the response before treating the saved file as playable audio.

## Transcription: upload a file, read text

Select a Transcription model, or explicitly select **Transcription** for an
unreported type. Replace the file path with your audio file (up to 25 MB):

```sh
curl https://YOUR_GATEWAY_HOST/team/gateway/v1/audio/transcriptions \
  -H "Authorization: Bearer $AGENTCC_API_KEY" \
  -F 'model=YOUR_PROVIDER_ID/YOUR_TRANSCRIPTION_MODEL' \
  -F 'file=@./YOUR_AUDIO_FILE.wav' \
  -F 'response_format=text'
```

This is multipart, not a JSON file placeholder. Let cURL choose the multipart
boundary; do **not** force a JSON `Content-Type`. `response_format=text` requests
plain text on stdout. If the provider returns JSON instead, read its `text`
field. Available formats depend on the upstream provider.

Audio recipes have no chat stream toggle. For unreported model types, selecting
a shape does not test compatibility. Managed non-chat, unresolved own-provider
access, a missing hosted URL and unsupported video remain without a runnable
example; this guide does not enable those routes.
