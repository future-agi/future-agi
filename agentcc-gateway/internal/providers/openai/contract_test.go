package openai

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"mime"
	"mime/multipart"
	"net/url"
	"reflect"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/providers/common"
	"github.com/futureagi/agentcc-gateway/internal/providers/contracttest"
)

// Contract tests replay each fixture under testdata/contracts/<model>/<case>
// through the real provider against a replay server and check what comes
// out. See internal/providers/contracttest/README.md.
//
//	go test ./internal/providers/openai/ -run Contract           # check
//	go test ./internal/providers/openai/ -run Contract -update   # rewrite goldens
//
// This adapter serves every OpenAI-format upstream, so a fixture's model key
// (meta.json's model, Case.Model) comes in two forms:
//
//   - <model-id>: the official OpenAI API, e.g. gpt-5.6-sol. The provider is
//     built as if pointed at api.openai.com, whose max_tokens handling
//     differs (normalizeMaxTokens).
//   - <preset>@<model-id>: an OpenAI-compatible upstream named by its preset
//     in providers/presets.go, e.g. groq@openai/gpt-oss-120b, since the same
//     model ID can be served by several upstreams with different shapes.
//
// The directory is the key as contracttest.ModelDir encodes it
// (groq%40openai%2Fgpt-oss-120b). The test checks the outbound model against
// the model ID after the "@".
//
// A case drives the provider method its case.json names (contracttest
// Endpoints): chat and chat_via_responses decode request.json as a canonical
// chat request; the other endpoints decode it into their own request type
// (contractRequest). Each (upstream, endpoint) pair has its own strict
// declaration (contractStrictFor), because the value sets differ: an
// embeddings body's object is "list", a chat body's "chat.completion", and
// Together documents a finish reason ("eos") OpenAI does not.

// compatPresets are the presets (providers/presets.go KnownProviders) that
// have fixtures here. The providers package imports this one, so the test
// cannot read the table itself; a typo in a model key fails instead.
var compatPresets = map[string]bool{
	"groq": true, "perplexity": true, "together": true, "openrouter": true,
	"mistral": true, "fireworks": true, "cerebras": true,
}

// officialDialect is the dialect of the official OpenAI API, the upstream a
// fixture without a preset names.
const officialDialect = "openai"

// fixtureUpstream splits a fixture's model key into the preset ("" for the
// official API) and the model ID the gateway sends upstream.
func fixtureUpstream(t testing.TB, key string) (preset, model string) {
	t.Helper()
	preset, model, ok := strings.Cut(key, "@")
	if !ok {
		return "", key
	}
	if !compatPresets[preset] {
		t.Fatalf("fixture model key %q names preset %q, which is not in compatPresets", key, preset)
	}
	return preset, model
}

// contractDialect is the upstream whose documented value sets apply to c:
// the preset, or "openai" for the official API.
func contractDialect(t testing.TB, c contracttest.Case) string {
	t.Helper()
	if preset, _ := fixtureUpstream(t, c.Model); preset != "" {
		return preset
	}
	return officialDialect
}

func TestContract(t *testing.T) {
	for _, c := range contracttest.Discover(t, adapterName) {
		t.Run(c.ID(), func(t *testing.T) {
			t.Parallel()
			strict := contractStrictFor(t, c)
			out := runContractCase(t, c)
			assertContractGoldens(t, c, out)
			checkContractCase(t, c, out)
			strict.AssertMutationsDetected(t, c, func(t testing.TB, m contracttest.Case) {
				checkContractCase(t, m, runContractCase(t, m))
			})
		})
	}
}

// contractOutput is what the adapter did with one case.
type contractOutput struct {
	upstream *contracttest.Replay
	shapes   []common.UnknownShape
	err      error

	chat   *models.ChatCompletionResponse // chat, chat_via_responses
	stream contracttest.StreamResult      // their streams
	// result is any other endpoint's golden form (contractResultGolden).
	result any
	// body is what a passthrough endpoint (speech, responses, transcription)
	// handed back, byte for byte.
	body        []byte
	contentType string
}

// newContractProvider builds the provider against the replay server, from a
// testing.TB so the mutation self-test can run it. For the official API it
// sets what New derives from an api.openai.com base URL, so the outbound
// request is the one OpenAI would receive.
func newContractProvider(t testing.TB, c contracttest.Case, url string) *Provider {
	t.Helper()
	p, err := New("test-openai", config.ProviderConfig{BaseURL: url, APIKey: "test-key"})
	if err != nil {
		t.Fatalf("New: %v", err)
	}
	t.Cleanup(func() { _ = p.Close() })
	if preset, _ := fixtureUpstream(t, c.Model); preset == "" {
		p.openAIAPI = true
	}
	return p
}

// contractRequest decodes a non-chat case's request.json into T.
func contractRequest[T any](t testing.TB, c contracttest.Case) *T {
	t.Helper()
	var req T
	if err := json.Unmarshal(c.RawRequest, &req); err != nil {
		t.Fatalf("decoding request.json as %T: %v", req, err)
	}
	return &req
}

// audioRequest is request.json of a transcription or translation case: the
// canonical request's JSON fields, plus the name of the uploaded file
// (contracttest.InputFileKey), which sits beside request.json.
type audioRequest struct {
	models.TranscriptionRequest
	File string `json:"file"`
}

// contractUpload is c's upload exactly as cmd/contract-recorder sends it
// (Case.InputFile): the file its request.json names, read from beside the
// request.json in use, or contracttest.DefaultUpload when it names none.
// The replay server never listens to it; its name and size are pinned by
// the upstream golden.
func contractUpload(t testing.TB, c contracttest.Case) *contracttest.InputFile {
	t.Helper()
	up, err := c.InputFile()
	if err != nil {
		t.Fatalf("%s: %v", c.ID(), err)
	}
	return up
}

// passthroughRequest is request.json of an assistants or vector_stores case:
// the method, path and body the gateway forwards.
type passthroughRequest struct {
	Method string          `json:"method"`
	Path   string          `json:"path"`
	Query  url.Values      `json:"query,omitempty"`
	Body   json.RawMessage `json:"body,omitempty"`
}

// runContractCase sends c's request through the real provider against a
// replay of c's body.
func runContractCase(t testing.TB, c contracttest.Case) contractOutput {
	t.Helper()
	upstream := contracttest.ReplayServer(t, c)
	p := newContractProvider(t, c, upstream.URL)
	ctx, shapes := common.WithShapeRecorder(context.Background())
	out := contractOutput{upstream: upstream}

	switch c.Endpoint {
	case contracttest.EndpointChat:
		if c.IsStream {
			chunks, errs := p.StreamChatCompletion(ctx, c.Request)
			out.stream = contracttest.DrainStream(t, chunks, errs)
			out.err = out.stream.Err
		} else {
			out.chat, out.err = p.ChatCompletion(ctx, c.Request)
		}
	case contracttest.EndpointChatViaResponses:
		if c.IsStream {
			chunks, errs := p.StreamChatCompletionViaResponses(ctx, c.Request)
			out.stream = contracttest.DrainStream(t, chunks, errs)
			out.err = out.stream.Err
		} else {
			out.chat, out.err = p.ChatCompletionViaResponses(ctx, c.Request)
		}
	case contracttest.EndpointCompletions:
		resp, err := p.TextCompletion(ctx, contractRequest[models.CompletionRequest](t, c))
		out.result, out.err = resp, err
	case contracttest.EndpointEmbeddings:
		resp, err := p.CreateEmbedding(ctx, contractRequest[models.EmbeddingRequest](t, c))
		out.result, out.err = resp, err
	case contracttest.EndpointImages:
		resp, err := p.CreateImage(ctx, contractRequest[models.ImageRequest](t, c))
		out.result, out.err = resp, err
	case contracttest.EndpointSpeech:
		body, contentType, err := p.CreateSpeech(ctx, contractRequest[models.SpeechRequest](t, c))
		out.err = err
		if err == nil {
			out.body, out.err = io.ReadAll(body)
			_ = body.Close()
			out.contentType = contentType
			out.result = passthroughGolden(out.body, contentType)
		}
	case contracttest.EndpointTranscription:
		req := contractRequest[audioRequest](t, c)
		up := contractUpload(t, c)
		req.FileName, req.FileData = up.Name, up.Data
		resp, err := p.CreateTranscription(ctx, &req.TranscriptionRequest)
		out.err = err
		if err == nil {
			out.body, out.contentType = resp.Body, resp.ContentType
			out.result = transcriptionGolden{Text: resp.Text, Passthrough: passthroughGolden(resp.Body, resp.ContentType)}
		}
	case contracttest.EndpointTranslation:
		req := contractRequest[audioRequest](t, c)
		up := contractUpload(t, c)
		tr := &models.TranslationRequest{Model: req.Model, FileName: up.Name, FileData: up.Data,
			Prompt: req.Prompt, ResponseFormat: req.ResponseFormat, Temperature: req.Temperature}
		resp, err := p.CreateTranslation(ctx, tr)
		out.err = err
		if err == nil {
			out.body, out.contentType = resp.Body, resp.ContentType
			out.result = transcriptionGolden{Text: resp.Text, Passthrough: passthroughGolden(resp.Body, resp.ContentType)}
		}
	case contracttest.EndpointResponses:
		if c.IsStream {
			body, status, err := p.StreamResponse(ctx, c.RawRequest)
			out.err = err
			if err == nil {
				out.body, out.err = io.ReadAll(body)
				_ = body.Close()
				out.result = responsesGolden{Status: status, Passthrough: passthroughGolden(out.body, "")}
			}
		} else {
			body, status, err := p.CreateResponse(ctx, c.RawRequest)
			out.err = err
			if err == nil {
				out.body = body
				out.result = responsesGolden{Status: status, Passthrough: passthroughGolden(body, "")}
			}
		}
	case contracttest.EndpointVectorStores:
		req := contractRequest[passthroughRequest](t, c)
		body, status, headers, err := p.ProxyVectorStoresRequest(ctx, req.Method, req.Path, req.Body, req.Query)
		out.err = err
		if err == nil {
			out.body = body
			out.result = map[string]any{"status": status, "headers": headers, "passthrough": passthroughGolden(body, "")}
		}
	case contracttest.EndpointListModels:
		list, err := p.ListModels(ctx)
		out.result, out.err = list, err
	case contracttest.EndpointOCR:
		resp, err := p.OCR(ctx, contractRequest[models.OCRRequest](t, c))
		out.result, out.err = resp, err
	default:
		t.Fatalf("endpoint %q has no driver in this suite", c.Endpoint)
	}
	out.shapes = shapes.Shapes()
	return out
}

// passthrough is the golden form of a body the adapter hands back unchanged:
// its size and content type, not its bytes (checkContractCase compares those
// with the fixture).
type passthrough struct {
	ContentType string `json:"content_type,omitempty"`
	Bytes       int    `json:"bytes"`
}

func passthroughGolden(body []byte, contentType string) passthrough {
	return passthrough{ContentType: contentType, Bytes: len(body)}
}

// transcriptionGolden is the golden form of a transcription or translation:
// the text the gateway lifts out for logs and guardrails, and the body it
// passes through.
type transcriptionGolden struct {
	Text        string      `json:"text"`
	Passthrough passthrough `json:"passthrough"`
}

// responsesGolden is the golden form of the Responses passthrough.
type responsesGolden struct {
	Status      int         `json:"status"`
	Passthrough passthrough `json:"passthrough"`
}

// streamErrorGolden is a stream's golden when it ended with an error: the
// chunks, and the canonical error the client and the gateway's error path
// see.
type streamErrorGolden struct {
	Stream contracttest.StreamResult `json:"stream"`
	Error  contracttest.ErrorResult  `json:"canonical_error"`
}

// assertContractGoldens pins the canonical output (or error) and the
// outbound request.
func assertContractGoldens(t *testing.T, c contracttest.Case, out contractOutput) {
	t.Helper()
	switch {
	case c.IsError():
		contracttest.AssertGolden(t, c.GoldenPath(), contracttest.ErrorGolden(out.err))
	case c.IsStream && (c.Endpoint == contracttest.EndpointChat || c.Endpoint == contracttest.EndpointChatViaResponses):
		if out.err != nil {
			contracttest.AssertGolden(t, c.GoldenPath(), streamErrorGolden{Stream: out.stream, Error: contracttest.ErrorGolden(out.err)})
		} else {
			contracttest.AssertGolden(t, c.GoldenPath(), out.stream)
		}
	case out.chat != nil:
		contracttest.AssertGolden(t, c.GoldenPath(), out.chat)
	default:
		contracttest.AssertGolden(t, c.GoldenPath(), out.result)
	}
	contracttest.AssertGolden(t, c.UpstreamGoldenPath(), contractUpstreamGolden(t, out.upstream.Request(t)))
}

// contractUpstreamGolden is the outbound request in golden form. A multipart
// body (audio uploads) is decoded into its fields, since its boundary is
// random.
func contractUpstreamGolden(t testing.TB, req contracttest.RecordedRequest) contracttest.RecordedRequest {
	t.Helper()
	mediaType, params, err := mime.ParseMediaType(req.Headers["content-type"])
	if err != nil || mediaType != "multipart/form-data" {
		return req
	}
	raw, _ := req.Body.(string)
	fields, err := multipartFields(raw, params["boundary"])
	if err != nil {
		t.Fatalf("decoding the outbound multipart body: %v", err)
	}
	req.Headers = cloneHeaders(req.Headers)
	req.Headers["content-type"] = "multipart/form-data; boundary=<random>"
	req.Body = fields
	return req
}

func cloneHeaders(h map[string]string) map[string]string {
	out := make(map[string]string, len(h))
	for k, v := range h {
		out[k] = v
	}
	return out
}

// multipartFields decodes a multipart form into field -> value; a file part
// becomes {"filename", "bytes"}, and a repeated field a list.
func multipartFields(body, boundary string) (map[string]any, error) {
	fields := map[string]any{}
	r := multipart.NewReader(strings.NewReader(body), boundary)
	for {
		part, err := r.NextPart()
		if errors.Is(err, io.EOF) {
			return fields, nil
		}
		if err != nil {
			return nil, err
		}
		data, err := io.ReadAll(part)
		if err != nil {
			return nil, err
		}
		var v any = string(data)
		if part.FileName() != "" {
			v = map[string]any{"filename": part.FileName(), "bytes": len(data)}
		}
		switch prev := fields[part.FormName()].(type) {
		case nil:
			fields[part.FormName()] = v
		case []any:
			fields[part.FormName()] = append(prev, v)
		default:
			fields[part.FormName()] = []any{prev, v}
		}
	}
}

// contractPaths is the upstream path each endpoint calls (vector_stores
// calls request.json's).
var contractPaths = map[string]string{
	contracttest.EndpointChat:             "/v1/chat/completions",
	contracttest.EndpointChatViaResponses: "/v1/responses",
	contracttest.EndpointResponses:        "/v1/responses",
	contracttest.EndpointCompletions:      "/v1/completions",
	contracttest.EndpointEmbeddings:       "/v1/embeddings",
	contracttest.EndpointImages:           "/v1/images/generations",
	contracttest.EndpointSpeech:           "/v1/audio/speech",
	contracttest.EndpointTranscription:    "/v1/audio/transcriptions",
	contracttest.EndpointTranslation:      "/v1/audio/translations",
	contracttest.EndpointListModels:       "/v1/models",
	contracttest.EndpointOCR:              "/v1/ocr",
}

// checkContractCase is every check except the goldens: what a live
// recording and the mutation self-test go through.
func checkContractCase(t testing.TB, c contracttest.Case, out contractOutput) {
	t.Helper()
	checkOutboundRequest(t, c, out.upstream)

	midStream := streamErrorEvents(t, c) > 0
	switch {
	case c.IsError():
		if len(out.stream.Chunks) > 0 {
			t.Errorf("an upstream %d answer produced %d stream chunks", c.Status, len(out.stream.Chunks))
		}
		// One ExpectGaps over every error check, so case.json's sanity_gaps
		// cover them all: AssertSaneError on a copy without the gaps feeds
		// its failures to this one.
		contracttest.ExpectGaps(t, c, func(t testing.TB) {
			bare := c
			bare.SanityGaps = nil
			contracttest.AssertSaneError(t, bare, out.err)
			assertErrorParamKept(t, c, out.err)
			assertRequestForwarded(t, c, out.upstream)
		})
	case midStream:
		assertMidStreamError(t, c, out)
	case out.err != nil:
		t.Fatalf("adapter error: %v", out.err)
	}
	if !c.IsError() && c.IsStream {
		checkStreamFraming(t, c)
	}

	contractStrictFor(t, c).Check(t, c)
	contracttest.AssertNoUnknownShapes(t, c, out.shapes)

	if c.IsError() || midStream {
		return
	}
	contracttest.ExpectGaps(t, c, func(t testing.TB) {
		assertContractSane(t, c, out)
		assertRequestForwarded(t, c, out.upstream)
	})
}

// assertErrorParamKept checks the parameter an upstream error names reaches
// the canonical error.
func assertErrorParamKept(t testing.TB, c contracttest.Case, err error) {
	t.Helper()
	var body struct {
		Error struct {
			Param *string `json:"param"`
		} `json:"error"`
	}
	if json.Unmarshal(c.Body, &body) != nil || body.Error.Param == nil || *body.Error.Param == "" {
		return
	}
	var apiErr *models.APIError
	if errors.As(err, &apiErr) && (apiErr.Param == nil || *apiErr.Param != *body.Error.Param) {
		t.Errorf("the upstream error param %q is not on the canonical error", *body.Error.Param)
	}
}

// multipartNames are request.json keys an audio upload sends under another
// form field name.
var multipartNames = map[string]string{"timestamp_granularities": "timestamp_granularities[]"}

// assertRequestForwarded checks every top-level key of a non-chat
// request.json reaches the upstream request. Those adapters marshal the
// canonical request type, which has no Extra, so a documented parameter the
// type lacks is dropped silently. (Chat requests keep unknown keys in Extra
// and are rewritten on purpose: max_tokens, the Responses translation.)
func assertRequestForwarded(t testing.TB, c contracttest.Case, upstream *contracttest.Replay) {
	t.Helper()
	switch c.Endpoint {
	case contracttest.EndpointCompletions, contracttest.EndpointEmbeddings, contracttest.EndpointImages, contracttest.EndpointSpeech,
		contracttest.EndpointTranscription, contracttest.EndpointTranslation, contracttest.EndpointOCR:
	default:
		return
	}
	var sent map[string]json.RawMessage
	if err := json.Unmarshal(c.RawRequest, &sent); err != nil {
		t.Fatalf("decoding request.json: %v", err)
	}
	body, _ := contractUpstreamGolden(t, upstream.Request(t)).Body.(map[string]any)
	for key := range sent {
		name := key
		if alt, ok := multipartNames[key]; ok && body[name] == nil {
			name = alt
		}
		if _, ok := body[name]; !ok {
			t.Errorf("request.json key %q is not in the upstream request: the adapter dropped it", key)
		}
	}
}

// checkOutboundRequest checks the request went to the endpoint's path, and,
// for a request that names a model, that the fixture is filed under the
// model the gateway actually asked for.
func checkOutboundRequest(t testing.TB, c contracttest.Case, upstream *contracttest.Replay) {
	t.Helper()
	req := upstream.Request(t)
	want := contractPaths[c.Endpoint]
	if c.Endpoint == contracttest.EndpointVectorStores {
		want = contractRequest[passthroughRequest](t, c).Path
	}
	if req.Path != want {
		t.Errorf("upstream request path = %q, want %q", req.Path, want)
	}
	var model any
	switch body := contractUpstreamGolden(t, req).Body.(type) {
	case map[string]any:
		model = body["model"]
	}
	switch c.Endpoint {
	case contracttest.EndpointListModels, contracttest.EndpointVectorStores:
		if model != nil {
			t.Errorf("upstream %s request names a model (%v)", c.Endpoint, model)
		}
		return
	}
	if _, m := fixtureUpstream(t, c.Model); model != m {
		t.Errorf("upstream request model = %v, want %q (from the fixture's model key %q)", model, m, c.Model)
	}
}

// streamErrorEvents counts the events of a 2xx stream case that are an
// upstream error rather than output: a Chat Completions chunk holding an
// "error" object (what OpenAI-compatible servers send when they fail
// mid-stream), or a Responses error / response.failed event.
func streamErrorEvents(t testing.TB, c contracttest.Case) int {
	t.Helper()
	if !c.IsStream || c.IsError() {
		return 0
	}
	n := 0
	for _, ev := range contracttest.ParseSSE(c.Body) {
		switch c.Endpoint {
		case contracttest.EndpointChat:
			if isErrorChunk(ev.Data) {
				n++
			}
		case contracttest.EndpointChatViaResponses, contracttest.EndpointResponses:
			if typ := ev.Type(); typ == "error" || typ == "response.failed" {
				n++
			}
		}
	}
	return n
}

// isErrorChunk reports whether a chat stream event's data is an error
// object, as streamErrorChunk decides for a decoded chunk.
func isErrorChunk(data []byte) bool {
	var probe struct {
		Error json.RawMessage `json:"error"`
	}
	return json.Unmarshal(data, &probe) == nil && len(probe.Error) > 0 && string(probe.Error) != "null"
}

// assertMidStreamError checks a stream that fails part-way: the output
// before the failure reaches the client, then the stream ends with a
// canonical error that names the failure. The Responses passthrough relays
// the stream as is; the server records the error event (server/stream_end.go).
func assertMidStreamError(t testing.TB, c contracttest.Case, out contractOutput) {
	t.Helper()
	if c.Endpoint == contracttest.EndpointResponses {
		if out.err != nil {
			t.Errorf("the Responses passthrough failed on a stream it should relay: %v", out.err)
		}
		return
	}
	var apiErr *models.APIError
	switch {
	case out.err == nil:
		t.Errorf("the upstream failed mid-stream but the stream ended without an error")
	case !errors.As(out.err, &apiErr):
		t.Errorf("the mid-stream failure became a %T, not a *models.APIError: %v", out.err, out.err)
	case strings.TrimSpace(apiErr.Message) == "":
		t.Errorf("the mid-stream failure's canonical error has no message")
	}
	if len(out.stream.Chunks) == 0 {
		t.Errorf("no chunk reached the client before the mid-stream failure")
	}
}

// checkStreamFraming checks a 2xx stream's framing, independently of what
// the adapter does with it: a Chat Completions stream is data-only (no event
// names, no "type") with the [DONE] sentinel last when present; a Responses
// stream names every event with its documented type, and the data carries
// the same type.
func checkStreamFraming(t testing.TB, c contracttest.Case) {
	t.Helper()
	events := contracttest.ParseSSE(c.Body)
	if len(events) == 0 {
		t.Fatalf("stream fixture has no events")
	}
	switch c.Endpoint {
	case contracttest.EndpointChat:
		contracttest.AssertKnownEventTypes(t, events, "")
		for i, ev := range events {
			if ev.Name != "" {
				t.Errorf("event %d is named %q; Chat Completions streams are data-only", i, ev.Name)
			}
			if string(ev.Data) == "[DONE]" {
				if i != len(events)-1 {
					t.Errorf("[DONE] is event %d of %d; it ends the stream", i, len(events))
				}
				continue
			}
			if !bytes.HasPrefix(bytes.TrimSpace(ev.Data), []byte("{")) || !json.Valid(ev.Data) {
				t.Errorf("event %d: data is not a JSON object: %.80s", i, ev.Data)
			}
		}
	case contracttest.EndpointChatViaResponses, contracttest.EndpointResponses:
		contracttest.AssertKnownEventTypes(t, events, responsesStreamEvents...)
		for i, ev := range events {
			var e struct {
				Type string `json:"type"`
			}
			if err := json.Unmarshal(ev.Data, &e); err != nil {
				t.Errorf("event %d (%q): data is not a JSON object: %v", i, ev.Name, err)
				continue
			}
			if e.Type != ev.Name {
				t.Errorf("event %d %q carries data.type %q; the API sends them equal", i, ev.Name, e.Type)
			}
		}
	}
}

// assertContractSane checks the invariants of a successful case's canonical
// output, by endpoint. Every check reads the upstream body independently of
// the adapter, so a field the adapter stops reading shows up here even when
// the golden was rewritten.
func assertContractSane(t testing.TB, c contracttest.Case, out contractOutput) {
	t.Helper()
	switch c.Endpoint {
	case contracttest.EndpointChat:
		if c.IsStream {
			contracttest.AssertSane(t, contracttest.FromStream(out.stream.Chunks), chatStreamHasOutput(c.Body))
		} else {
			contracttest.AssertSane(t, contracttest.FromResponse(out.chat), chatResponseHasOutput(t, c.Body))
		}
	case contracttest.EndpointChatViaResponses:
		if c.IsStream {
			contracttest.AssertSane(t, contracttest.FromStream(out.stream.Chunks), responsesStreamHasOutput(c.Body))
		} else {
			contracttest.AssertSane(t, contracttest.FromResponse(out.chat), responsesBodyHasOutput(c.Body))
		}
	case contracttest.EndpointCompletions:
		saneCompletion(t, c, out.result.(*models.CompletionResponse))
	case contracttest.EndpointEmbeddings:
		saneEmbedding(t, c, out.result.(*models.EmbeddingResponse))
	case contracttest.EndpointImages:
		saneImage(t, c, out.result.(*models.ImageResponse))
	case contracttest.EndpointSpeech, contracttest.EndpointResponses, contracttest.EndpointVectorStores:
		samePassthrough(t, c, out.body)
	case contracttest.EndpointTranscription, contracttest.EndpointTranslation:
		samePassthrough(t, c, out.body)
		saneTranscription(t, c, out.result.(transcriptionGolden))
	case contracttest.EndpointListModels:
		saneModelList(t, c, out.result.([]models.ModelObject))
	case contracttest.EndpointOCR:
		saneOCR(t, c, out.result.(*models.OCRResponse))
	}
}

// samePassthrough checks a passthrough endpoint handed the upstream body
// back byte for byte.
func samePassthrough(t testing.TB, c contracttest.Case, body []byte) {
	t.Helper()
	if !bytes.Equal(body, c.Body) {
		t.Errorf("the adapter changed a passthrough body: got %d bytes, the upstream sent %d", len(body), len(c.Body))
	}
}

// chatResponseHasOutput reports whether the upstream chat response carried
// text or a tool call, which the canonical response must then carry too. A
// refusal is not counted: the field audit is what flags a dropped refusal.
func chatResponseHasOutput(t testing.TB, body []byte) bool {
	t.Helper()
	var r struct {
		Choices []struct {
			Message struct {
				Content   json.RawMessage   `json:"content"`
				ToolCalls []json.RawMessage `json:"tool_calls"`
			} `json:"message"`
		} `json:"choices"`
	}
	if err := json.Unmarshal(body, &r); err != nil {
		t.Fatalf("decoding fixture: %v", err)
	}
	for _, ch := range r.Choices {
		if hasContent(ch.Message.Content) || len(ch.Message.ToolCalls) > 0 {
			return true
		}
	}
	return false
}

// hasContent reports whether message content is a non-empty string or a
// non-empty array of parts.
func hasContent(raw json.RawMessage) bool {
	var s string
	if json.Unmarshal(raw, &s) == nil {
		return s != ""
	}
	var parts []json.RawMessage
	return json.Unmarshal(raw, &parts) == nil && len(parts) > 0
}

// chatStreamHasOutput is chatResponseHasOutput for a stream.
func chatStreamHasOutput(body []byte) bool {
	for _, ev := range contracttest.ParseSSE(body) {
		var chunk struct {
			Choices []struct {
				Delta struct {
					Content   *string           `json:"content"`
					ToolCalls []json.RawMessage `json:"tool_calls"`
				} `json:"delta"`
			} `json:"choices"`
		}
		if json.Unmarshal(ev.Data, &chunk) != nil {
			continue
		}
		for _, c := range chunk.Choices {
			if (c.Delta.Content != nil && *c.Delta.Content != "") || len(c.Delta.ToolCalls) > 0 {
				return true
			}
		}
	}
	return false
}

// responsesBodyHasOutput reports whether a Responses API response carried
// output text, a refusal, or a function call.
func responsesBodyHasOutput(body []byte) bool {
	var r struct {
		Output []struct {
			Type    string `json:"type"`
			Content []struct {
				Text    string `json:"text"`
				Refusal string `json:"refusal"`
			} `json:"content"`
		} `json:"output"`
	}
	if json.Unmarshal(body, &r) != nil {
		return false
	}
	for _, item := range r.Output {
		if item.Type == "function_call" {
			return true
		}
		for _, part := range item.Content {
			if item.Type == "message" && (part.Text != "" || part.Refusal != "") {
				return true
			}
		}
	}
	return false
}

// responsesStreamHasOutput is responsesBodyHasOutput for a stream: a text
// or refusal delta, or a function call item.
func responsesStreamHasOutput(body []byte) bool {
	for _, ev := range contracttest.ParseSSE(body) {
		var e struct {
			Type  string `json:"type"`
			Delta string `json:"delta"`
			Item  struct {
				Type string `json:"type"`
			} `json:"item"`
		}
		if json.Unmarshal(ev.Data, &e) != nil {
			continue
		}
		switch {
		case (e.Type == "response.output_text.delta" || e.Type == "response.refusal.delta") && e.Delta != "":
			return true
		case e.Type == "response.output_item.added" && e.Item.Type == "function_call":
			return true
		}
	}
	return false
}

// saneUsage checks token counts a cost is computed from.
func saneUsage(t testing.TB, prompt, completion, total int) {
	t.Helper()
	if prompt <= 0 {
		t.Errorf("usage.prompt_tokens = %d, want > 0", prompt)
	}
	if total != prompt+completion {
		t.Errorf("usage.total_tokens = %d, want prompt %d + completion %d", total, prompt, completion)
	}
}

func saneCompletion(t testing.TB, c contracttest.Case, resp *models.CompletionResponse) {
	t.Helper()
	var up struct {
		Choices []struct {
			Text string `json:"text"`
		} `json:"choices"`
	}
	_ = json.Unmarshal(c.Body, &up)
	if len(resp.Choices) != len(up.Choices) {
		t.Errorf("%d choices, the upstream sent %d", len(resp.Choices), len(up.Choices))
	}
	for i, ch := range resp.Choices {
		contracttest.AssertCanonicalFinishReason(t, ch.FinishReason)
		if i < len(up.Choices) && up.Choices[i].Text != "" && ch.Text == "" {
			t.Errorf("choice %d: the upstream sent text but the canonical choice has none", i)
		}
	}
	if resp.Usage == nil {
		t.Errorf("usage is missing")
		return
	}
	saneUsage(t, resp.Usage.PromptTokens, resp.Usage.CompletionTokens, resp.Usage.TotalTokens)
}

func saneEmbedding(t testing.TB, c contracttest.Case, resp *models.EmbeddingResponse) {
	t.Helper()
	var up struct {
		Data []json.RawMessage `json:"data"`
	}
	_ = json.Unmarshal(c.Body, &up)
	if len(resp.Data) != len(up.Data) || len(resp.Data) == 0 {
		t.Errorf("%d embeddings, the upstream sent %d", len(resp.Data), len(up.Data))
	}
	for i, d := range resp.Data {
		var floats []float64
		var b64 string
		if (json.Unmarshal(d.Embedding, &floats) != nil || len(floats) == 0) && (json.Unmarshal(d.Embedding, &b64) != nil || b64 == "") {
			t.Errorf("embedding %d is neither a non-empty float array nor a base64 string", i)
		}
	}
	if resp.Usage == nil {
		t.Errorf("usage is missing")
		return
	}
	saneUsage(t, resp.Usage.PromptTokens, 0, resp.Usage.TotalTokens)
}

func saneImage(t testing.TB, c contracttest.Case, resp *models.ImageResponse) {
	t.Helper()
	var up struct {
		Data  []json.RawMessage `json:"data"`
		Usage json.RawMessage   `json:"usage"`
	}
	_ = json.Unmarshal(c.Body, &up)
	if len(resp.Data) != len(up.Data) || len(resp.Data) == 0 {
		t.Errorf("%d images, the upstream sent %d", len(resp.Data), len(up.Data))
	}
	for i, d := range resp.Data {
		if d.URL == "" && d.B64JSON == "" {
			t.Errorf("image %d has neither a url nor b64_json", i)
		}
	}
	if len(up.Usage) > 0 && string(up.Usage) != "null" {
		// gpt-image models are billed by these tokens (plugins/cost imageCost).
		if resp.Usage == nil {
			t.Errorf("the upstream reported usage but the canonical response has none, so the image is priced per image")
			return
		}
		saneUsage(t, resp.Usage.InputTokens, resp.Usage.OutputTokens, resp.Usage.TotalTokens)
	}
}

func saneTranscription(t testing.TB, c contracttest.Case, got transcriptionGolden) {
	t.Helper()
	want := string(c.Body)
	if mediaType, _, _ := mime.ParseMediaType(c.ContentType); mediaType == "application/json" {
		var up struct {
			Text string `json:"text"`
		}
		_ = json.Unmarshal(c.Body, &up)
		want = up.Text
	}
	if got.Text != want {
		t.Errorf("lifted text = %q, want %q", got.Text, want)
	}
	if strings.TrimSpace(got.Text) == "" {
		t.Errorf("the transcription has no text")
	}
}

func saneModelList(t testing.TB, c contracttest.Case, list []models.ModelObject) {
	t.Helper()
	var up struct {
		Data []json.RawMessage `json:"data"`
	}
	_ = json.Unmarshal(c.Body, &up)
	if len(list) != len(up.Data) || len(list) == 0 {
		t.Errorf("%d models, the upstream listed %d", len(list), len(up.Data))
	}
	for i, m := range list {
		if m.ID == "" {
			t.Errorf("model %d has no id", i)
		}
	}
}

func saneOCR(t testing.TB, c contracttest.Case, resp *models.OCRResponse) {
	t.Helper()
	var up struct {
		Pages []json.RawMessage `json:"pages"`
	}
	_ = json.Unmarshal(c.Body, &up)
	if len(resp.Pages) != len(up.Pages) || len(resp.Pages) == 0 {
		t.Errorf("%d pages, the upstream sent %d", len(resp.Pages), len(up.Pages))
	}
	for i, pg := range resp.Pages {
		if strings.TrimSpace(pg.Markdown) == "" {
			t.Errorf("page %d has no markdown", i)
		}
	}
}

// contractStrictFor is the strict declaration for c's upstream and endpoint.
func contractStrictFor(t testing.TB, c contracttest.Case) *contracttest.Strict {
	t.Helper()
	key := contractDialect(t, c) + "/" + c.Endpoint
	st := contractStricts[key]
	if st == nil {
		t.Fatalf("no strict declaration for %s: add the dialect's endpoint to ContractEnums", key)
	}
	return st
}

// contractDocs splits a case's body into what the adapter decodes. An error
// body, and the error chunk of a failing chat stream, decode as
// models.ErrorResponse (parseProviderError) at ErrorRoot.
func contractDocs(c contracttest.Case) []contracttest.Doc {
	errorType := reflect.TypeFor[models.ErrorResponse]()
	if c.IsError() {
		if !json.Valid(c.Body) {
			errorType = nil // an HTML page from a proxy: parseProviderError keeps it as text
		}
		return []contracttest.Doc{{Root: contracttest.ErrorRoot, Data: c.Body, Type: errorType}}
	}
	var events []contracttest.Event
	if c.IsStream {
		events = contracttest.ParseSSE(c.Body)
	}
	docs := make([]contracttest.Doc, 0, len(events))
	switch c.Endpoint {
	case contracttest.EndpointChat:
		if !c.IsStream {
			return []contracttest.Doc{{Root: "$", Data: c.Body, Type: reflect.TypeFor[models.ChatCompletionResponse]()}}
		}
		for _, ev := range events {
			switch {
			case string(ev.Data) == "[DONE]":
				docs = append(docs, contracttest.Doc{Root: "chunk:$", Data: ev.Data})
			case isErrorChunk(ev.Data):
				docs = append(docs, contracttest.Doc{Root: contracttest.ErrorRoot, Data: ev.Data, Type: errorType})
			default:
				docs = append(docs, contracttest.Doc{Root: "chunk:$", Data: ev.Data, Type: reflect.TypeFor[models.StreamChunk]()})
			}
		}
	case contracttest.EndpointChatViaResponses:
		if !c.IsStream {
			return []contracttest.Doc{{Root: "$", Data: c.Body, Type: reflect.TypeFor[responsesResponse]()}}
		}
		for _, ev := range events {
			var typ reflect.Type
			if responsesConvertedEvents[ev.Name] {
				typ = reflect.TypeFor[responsesStreamEvent]()
			}
			docs = append(docs, contracttest.Doc{Root: ev.Name + ":$", Data: ev.Data, Type: typ})
		}
	case contracttest.EndpointResponses:
		// A passthrough: the adapter decodes nothing. The server reads the
		// model and usage (internal/server/contract_openai_responses_test.go).
		if !c.IsStream {
			return []contracttest.Doc{{Root: "$", Data: c.Body}}
		}
		for _, ev := range events {
			docs = append(docs, contracttest.Doc{Root: ev.Name + ":$", Data: ev.Data})
		}
	case contracttest.EndpointCompletions:
		return []contracttest.Doc{{Root: "$", Data: c.Body, Type: reflect.TypeFor[models.CompletionResponse]()}}
	case contracttest.EndpointEmbeddings:
		return []contracttest.Doc{{Root: "$", Data: c.Body, Type: reflect.TypeFor[models.EmbeddingResponse]()}}
	case contracttest.EndpointImages:
		return []contracttest.Doc{{Root: "$", Data: c.Body, Type: reflect.TypeFor[models.ImageResponse]()}}
	case contracttest.EndpointTranscription, contracttest.EndpointTranslation:
		// NewTranscriptionResponse decodes a JSON body for its text; text,
		// srt and vtt bodies are not JSON and are passed through whole.
		if mediaType, _, _ := mime.ParseMediaType(c.ContentType); mediaType == "application/json" {
			return []contracttest.Doc{{Root: "$", Data: c.Body, Type: reflect.TypeFor[models.TranscriptionResponse]()}}
		}
		return nil
	case contracttest.EndpointListModels:
		return []contracttest.Doc{{Root: "$", Data: c.Body, Type: reflect.TypeFor[models.ModelListResponse]()}}
	case contracttest.EndpointOCR:
		return []contracttest.Doc{{Root: "$", Data: c.Body, Type: reflect.TypeFor[models.OCRResponse]()}}
	case contracttest.EndpointVectorStores:
		return []contracttest.Doc{{Root: "$", Data: c.Body}}
	case contracttest.EndpointSpeech:
		return nil // audio bytes
	default:
		panic(fmt.Sprintf("contractDocs: endpoint %q has no documents", c.Endpoint))
	}
	return docs
}
