package azure

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"io"
	"mime"
	"mime/multipart"
	"reflect"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/providers/common"
	"github.com/futureagi/agentcc-gateway/internal/providers/contracttest"
)

// Contract tests replay each fixture under testdata/contracts/<deployment>/<case>
// through the real provider against a replay server and check what comes
// out. See internal/providers/contracttest/README.md.
//
//	go test ./internal/providers/azure/ -run Contract           # check
//	go test ./internal/providers/azure/ -run Contract -update   # rewrite goldens
//
// Azure routes by deployment, not by the body's model, so a fixture directory
// is the deployment name, which the test checks against the URL path. Name a
// deployment after the model it serves.
//
// Every endpoint the adapter implements has cases (case.json "endpoint"):
// chat (ChatCompletion / StreamChatCompletion), embeddings, images, speech
// (TTS), transcription, and list_models. Each is called at the api-version
// in apiVersions, the gateway's default otherwise; the outbound request
// golden pins it.
//
// This adapter is a copy of the OpenAI one with Azure's URL and auth: it
// decodes chat bodies straight into the canonical types, whose top level and
// usage keep unknown keys in Extra (passed through), but whose choices,
// messages and deltas drop them. Strict mode therefore treats the chat root
// as opaque and declares the choices Decoded.

// apiVersions is the api-version each deployment (or case, by ID) is called
// at, when not the gateway's default (defaultAPIVersion, 2024-10-21):
// audio/speech and gpt-image-1 exist only in the preview API, and one chat
// case shows the preview chat shape.
var apiVersions = map[string]string{
	"gpt-image-1":       "2025-04-01-preview",
	"gpt-4o-mini-tts":   "2025-04-01-preview",
	"gpt-4o-transcribe": "2025-04-01-preview",
	"gpt-4.1-2025-04-14/text_api_2025_04_01_preview": "2025-04-01-preview",
}

// apiVersion is the api-version c is called at.
func apiVersion(c contracttest.Case) string {
	if v, ok := apiVersions[c.ID()]; ok {
		return v
	}
	if v, ok := apiVersions[c.Model]; ok {
		return v
	}
	return defaultAPIVersion
}

// endpointPaths is the deployment path each endpoint calls.
var endpointPaths = map[string]string{
	contracttest.EndpointChat:          "chat/completions",
	contracttest.EndpointEmbeddings:    "embeddings",
	contracttest.EndpointImages:        "images/generations",
	contracttest.EndpointSpeech:        "audio/speech",
	contracttest.EndpointTranscription: "audio/transcriptions",
}

// severityPaths are the content-filter severity fields at root, one per
// harm category the spec lists (contentFilterResultsBase).
func severityPaths(root string) []string {
	var out []string
	for _, cat := range []string{"hate", "self_harm", "sexual", "violence"} {
		out = append(out, root+"."+cat+".severity")
	}
	return out
}

// The value sets the Azure OpenAI data-plane specs document, by endpoint
// (github.com/Azure/azure-rest-api-specs at
// 7b9f1ff0714627e3680552703174ffe11538d5c5,
// specification/cognitiveservices/data-plane/OpenAIInference: stable/2024-10-21
// and preview/2025-04-01-preview inference.json; OpenAIAuthoring
// stable/2024-10-21 azureopenai.json for the models list; read 2026-10-28).
// Spec names the schema; the spec-diff check compares the two. Severities and
// error codes are checked although the adapter drops them (gaps A1, A3, A4):
// a new value is drift either way.
var (
	severityValues = []string{"safe", "low", "medium", "high"}

	chatEnums = []contracttest.EnumSpec{
		{Spec: "createChatCompletionResponse.object", Paths: []string{"$.object"}, Values: []string{"chat.completion"}},
		{Spec: "createChatCompletionStreamResponse.object", Paths: []string{"chunk:$.object"}, Values: []string{"chat.completion.chunk"}},
		{Spec: "createChatCompletionResponse.choices[].finish_reason (and the stream's)",
			Paths:  []string{"$.choices[].finish_reason", "chunk:$.choices[].finish_reason"},
			Values: []string{"stop", "length", "tool_calls", "content_filter", "function_call"}},
		{Spec: "chatCompletionResponseMessage.role", Paths: []string{"$.choices[].message.role"}, Values: []string{"assistant"}},
		{Spec: "chatCompletionStreamResponseDelta.role", Paths: []string{"chunk:$.choices[].delta.role"},
			Values: []string{"system", "user", "assistant", "tool"}},
		{Spec: "chatCompletionMessageToolCall.type", Paths: []string{"$.choices[].message.tool_calls[].type", "chunk:$.choices[].delta.tool_calls[].type"},
			Values: []string{"function"}},
		{Spec: "contentFilterSeverityResult.severity",
			Paths: append(append(severityPaths("$.choices[].content_filter_results"),
				severityPaths("$.prompt_filter_results[].content_filter_results")...),
				severityPaths("error:$.error.innererror.content_filter_result")...),
			Values: severityValues},
		{Spec: "innerErrorCode", Paths: []string{"error:$.error.innererror.code"}, Values: []string{"ResponsibleAIPolicyViolation"}},
	}
	embeddingEnums = []contracttest.EnumSpec{
		// The spec types these as plain strings; OpenAI's, which Azure
		// serves, fixes them.
		{Spec: "embeddings 200 response object (OpenAI: list)", Paths: []string{"$.object"}, Values: []string{"list"}},
		{Spec: "embeddings 200 response data[].object (OpenAI: embedding)", Paths: []string{"$.data[].object"}, Values: []string{"embedding"}},
	}
	imageEnums = []contracttest.EnumSpec{
		{Spec: "generateImagesResponse data[] contentFilterSeverityResult.severity",
			Paths:  append(severityPaths("$.data[].content_filter_results"), severityPaths("$.data[].prompt_filter_results")...),
			Values: severityValues},
	}
	transcriptionEnums = []contracttest.EnumSpec{
		{Spec: "audioVerboseResponse.task", Paths: []string{"$.task"}, Values: []string{"transcribe", "translate"}},
	}
	listModelEnums = []contracttest.EnumSpec{
		{Spec: "authoring TypeDiscriminator", Paths: []string{"$.object", "$.data[].object"},
			Values: []string{"list", "fine_tuning.job", "file", "fine_tuning.job.event", "fine_tuning.job.checkpoint", "model", "batch", "upload", "upload.part"}},
		{Spec: "authoring LifeCycleStatus", Paths: []string{"$.data[].lifecycle_status"}, Values: []string{"preview", "generally-available"}},
		{Spec: "authoring FineTuningState", Paths: []string{"$.data[].status"},
			Values: []string{"created", "pending", "running", "succeeded", "cancelled", "failed"}},
	}
)

// ContractEnums is every endpoint's value sets, for the spec-diff check.
var ContractEnums = concatEnums(chatEnums, embeddingEnums, imageEnums, transcriptionEnums, listModelEnums)

func concatEnums(sets ...[]contracttest.EnumSpec) []contracttest.EnumSpec {
	var out []contracttest.EnumSpec
	for _, s := range sets {
		out = append(out, s...)
	}
	return out
}

// The strict declarations, one per endpoint, since each decodes its body
// into its own type and shares paths like $.object with different values.
var (
	chatStrict = &contracttest.Strict{
		Docs:  chatDocs,
		Enums: chatEnums,
		Decoded: []contracttest.Decoded{
			// ChatCompletionResponse and StreamChunk keep unknown top-level
			// keys in Extra (passed through), so the root is opaque; the
			// choices are not, and drop what their structs lack.
			{Path: "$.choices", Type: reflect.TypeFor[[]models.Choice]()},
			{Path: "chunk:$.choices", Type: reflect.TypeFor[[]models.StreamChoice]()},
		},
	}
	embeddingStrict = &contracttest.Strict{
		Docs:  jsonDocs(reflect.TypeFor[models.EmbeddingResponse]()),
		Enums: embeddingEnums,
		// The vector passes through untouched: a float array, or a base64
		// string for encoding_format base64.
		Free: []string{"$.data[].embedding"},
	}
	imageStrict = &contracttest.Strict{
		Docs:  jsonDocs(reflect.TypeFor[models.ImageResponse]()),
		Enums: imageEnums,
	}
	speechStrict = &contracttest.Strict{
		// Audio bytes: nothing is decoded.
		Docs: func(c contracttest.Case) []contracttest.Doc { return errorDocs(c) },
	}
	transcriptionStrict = &contracttest.Strict{
		// models.NewTranscriptionResponse decodes a JSON body as
		// TranscriptionResponse (text) and passes the body through; a text,
		// srt or vtt body is not decoded.
		Docs: func(c contracttest.Case) []contracttest.Doc {
			if c.IsError() || !isJSONContentType(c.ContentType) {
				return errorDocs(c)
			}
			return []contracttest.Doc{{Root: "$", Data: c.Body, Type: reflect.TypeFor[models.TranscriptionResponse]()}}
		},
		Enums: transcriptionEnums,
	}
	listModelsStrict = &contracttest.Strict{
		Docs:  jsonDocs(reflect.TypeFor[models.ModelListResponse]()),
		Enums: listModelEnums,
	}
)

// strictFor is c's endpoint's declaration.
func strictFor(t testing.TB, c contracttest.Case) *contracttest.Strict {
	switch c.Endpoint {
	case contracttest.EndpointChat:
		return chatStrict
	case contracttest.EndpointEmbeddings:
		return embeddingStrict
	case contracttest.EndpointImages:
		return imageStrict
	case contracttest.EndpointSpeech:
		return speechStrict
	case contracttest.EndpointTranscription:
		return transcriptionStrict
	case contracttest.EndpointListModels:
		return listModelsStrict
	}
	t.Fatalf("the azure adapter has no %q endpoint", c.Endpoint)
	return nil
}

// errorDocs is an error body as parseProviderError decodes it,
// models.ErrorResponse, or nothing for a body that is not JSON (an error) or
// any body that is not an error.
func errorDocs(c contracttest.Case) []contracttest.Doc {
	if !c.IsError() || !json.Valid(c.Body) {
		return nil
	}
	return []contracttest.Doc{{Root: contracttest.ErrorRoot, Data: c.Body, Type: reflect.TypeFor[models.ErrorResponse]()}}
}

// jsonDocs is the Docs of an endpoint whose body the adapter decodes into
// typ.
func jsonDocs(typ reflect.Type) func(c contracttest.Case) []contracttest.Doc {
	return func(c contracttest.Case) []contracttest.Doc {
		if c.IsError() {
			return errorDocs(c)
		}
		return []contracttest.Doc{{Root: "$", Data: c.Body, Type: typ}}
	}
}

// chatDocs: a response as models.ChatCompletionResponse; a stream as one
// models.StreamChunk per data event, rooted "chunk:$" (the stream is
// data-only), the [DONE] sentinel undecoded; an error as errorDocs.
func chatDocs(c contracttest.Case) []contracttest.Doc {
	switch {
	case c.IsError():
		return errorDocs(c)
	case c.IsStream:
		events := contracttest.ParseSSE(c.Body)
		docs := make([]contracttest.Doc, len(events))
		for i, ev := range events {
			docs[i] = contracttest.Doc{Root: "chunk:$", Data: ev.Data, Type: reflect.TypeFor[models.StreamChunk]()}
			if string(ev.Data) == "[DONE]" {
				docs[i].Type = nil
			}
		}
		return docs
	default:
		return []contracttest.Doc{{Root: "$", Data: c.Body, Type: reflect.TypeFor[models.ChatCompletionResponse]()}}
	}
}

func isJSONContentType(ct string) bool {
	mt, _, err := mime.ParseMediaType(ct)
	return err == nil && mt == "application/json"
}

func TestContract(t *testing.T) {
	for _, c := range contracttest.Discover(t, adapterName) {
		t.Run(c.ID(), func(t *testing.T) {
			t.Parallel()
			out := runCase(t, c)
			assertGoldens(t, c, out)
			checkCase(t, c, out)
			strictFor(t, c).AssertMutationsDetected(t, c, func(t testing.TB, m contracttest.Case) {
				checkCase(t, m, runCase(t, m))
			})
		})
	}
}

// caseOutput is what the adapter did with one case.
type caseOutput struct {
	resp          *models.ChatCompletionResponse
	stream        contracttest.StreamResult
	embedding     *models.EmbeddingResponse
	image         *models.ImageResponse
	audio         []byte
	audioType     string
	transcription *models.TranscriptionResponse
	models        []models.ModelObject
	err           error
	shapes        []common.UnknownShape
	upstream      *contracttest.Replay
}

// transcriptionUpload is c's upload exactly as cmd/contract-recorder sends
// it (Case.InputFile): the file its request.json names, read from beside the
// request.json in use, or contracttest.DefaultUpload (a short silent WAV)
// when it names none. The replay server never listens to it; its name and
// size are pinned by the upstream golden.
func transcriptionUpload(t testing.TB, c contracttest.Case) *contracttest.InputFile {
	t.Helper()
	up, err := c.InputFile()
	if err != nil {
		t.Fatalf("%s: %v", c.ID(), err)
	}
	return up
}

// decodeRequest decodes c's request.json into v, failing t on error.
func decodeRequest(t testing.TB, c contracttest.Case, v any) {
	t.Helper()
	if err := json.Unmarshal(c.RawRequest, v); err != nil {
		t.Fatalf("decoding %s: %v", contracttest.RequestFile, err)
	}
}

// runCase sends c's request through the real provider against a replay of
// c's body.
func runCase(t testing.TB, c contracttest.Case) caseOutput {
	t.Helper()
	upstream := contracttest.ReplayServer(t, c)
	p, err := New("test-provider", config.ProviderConfig{
		BaseURL: upstream.URL, APIKey: "test-api-key", MaxConcurrent: 10, ConnPoolSize: 5,
		Headers: map[string]string{"api-version": apiVersion(c)},
	})
	if err != nil {
		t.Fatalf("New: %v", err)
	}
	t.Cleanup(func() { _ = p.Close() })
	ctx, shapes := common.WithShapeRecorder(context.Background())

	out := caseOutput{upstream: upstream}
	switch c.Endpoint {
	case contracttest.EndpointChat:
		if c.IsStream {
			chunks, errs := p.StreamChatCompletion(ctx, c.Request)
			out.stream = contracttest.DrainStream(t, chunks, errs)
			out.err = out.stream.Err
		} else {
			out.resp, out.err = p.ChatCompletion(ctx, c.Request)
		}
	case contracttest.EndpointEmbeddings:
		var req models.EmbeddingRequest
		decodeRequest(t, c, &req)
		out.embedding, out.err = p.CreateEmbedding(ctx, &req)
	case contracttest.EndpointImages:
		var req models.ImageRequest
		decodeRequest(t, c, &req)
		out.image, out.err = p.CreateImage(ctx, &req)
	case contracttest.EndpointSpeech:
		var req models.SpeechRequest
		decodeRequest(t, c, &req)
		var body io.ReadCloser
		body, out.audioType, out.err = p.CreateSpeech(ctx, &req)
		if out.err == nil {
			out.audio, out.err = io.ReadAll(body)
			_ = body.Close()
		}
	case contracttest.EndpointTranscription:
		var req models.TranscriptionRequest
		decodeRequest(t, c, &req)
		up := transcriptionUpload(t, c)
		req.FileData, req.FileName = up.Data, up.Name
		out.transcription, out.err = p.CreateTranscription(ctx, &req)
	case contracttest.EndpointListModels:
		out.models, out.err = p.ListModels(ctx)
	default:
		t.Fatalf("the azure adapter has no %q endpoint", c.Endpoint)
	}
	out.shapes = shapes.Shapes()
	return out
}

// audioGolden is the golden form of a speech answer: the audio is passed
// through, so its content type, length and digest are what to pin.
type audioGolden struct {
	ContentType string `json:"content_type"`
	Bytes       int    `json:"bytes"`
	SHA256      string `json:"sha256"`
}

// transcriptionGolden is the golden form of a transcription: the text the
// gateway lifts out for logs and traces, and the body it passes through.
type transcriptionGolden struct {
	Text        string `json:"text"`
	ContentType string `json:"content_type"`
	// BodyIsUpstream is whether the body handed to the client is the
	// upstream's, byte for byte.
	BodyIsUpstream bool `json:"body_is_upstream"`
}

// assertGoldens pins the canonical output (or error) and the outbound
// request.
func assertGoldens(t *testing.T, c contracttest.Case, out caseOutput) {
	t.Helper()
	var got any
	switch {
	case c.IsError():
		got = contracttest.ErrorGolden(out.err)
	case c.Endpoint == contracttest.EndpointChat && c.IsStream:
		got = out.stream
	case c.Endpoint == contracttest.EndpointChat:
		got = out.resp
	case c.Endpoint == contracttest.EndpointEmbeddings:
		got = out.embedding
	case c.Endpoint == contracttest.EndpointImages:
		got = out.image
	case c.Endpoint == contracttest.EndpointSpeech:
		sum := sha256.Sum256(out.audio)
		got = audioGolden{ContentType: out.audioType, Bytes: len(out.audio), SHA256: hex.EncodeToString(sum[:])}
	case c.Endpoint == contracttest.EndpointTranscription:
		got = transcriptionGolden{Text: out.transcription.Text, ContentType: out.transcription.ContentType,
			BodyIsUpstream: bytes.Equal(out.transcription.Body, c.Body)}
	case c.Endpoint == contracttest.EndpointListModels:
		got = out.models
	}
	contracttest.AssertGolden(t, c.GoldenPath(), got)
	contracttest.AssertGolden(t, c.UpstreamGoldenPath(), upstreamRequest(t, out.upstream))
}

// upstreamRequest is the recorded outbound request, with a multipart body
// (transcription) parsed into its fields, since its boundary is random.
func upstreamRequest(t testing.TB, upstream *contracttest.Replay) contracttest.RecordedRequest {
	t.Helper()
	req := upstream.Request(t)
	mt, params, err := mime.ParseMediaType(req.Headers["content-type"])
	if err != nil || mt != "multipart/form-data" {
		return req
	}
	body, _ := req.Body.(string)
	form := map[string]any{}
	r := multipart.NewReader(strings.NewReader(body), params["boundary"])
	for {
		part, err := r.NextPart()
		if err == io.EOF {
			break
		}
		if err != nil {
			t.Fatalf("parsing the outbound multipart body: %v", err)
		}
		data, _ := io.ReadAll(part)
		if part.FileName() != "" {
			sum := sha256.Sum256(data)
			form[part.FormName()] = map[string]any{"filename": part.FileName(), "content_type": part.Header.Get("Content-Type"),
				"bytes": len(data), "sha256": hex.EncodeToString(sum[:])}
			continue
		}
		key := part.FormName()
		if prev, ok := form[key]; ok { // a repeated field, e.g. timestamp_granularities[]
			if list, isList := prev.([]string); isList {
				form[key] = append(list, string(data))
			} else {
				form[key] = []string{prev.(string), string(data)}
			}
			continue
		}
		form[key] = string(data)
	}
	req.Headers["content-type"] = "multipart/form-data; boundary=<random>"
	req.Body = form
	return req
}

// checkCase is every check except the goldens: what a live recording and the
// mutation self-test go through.
func checkCase(t testing.TB, c contracttest.Case, out caseOutput) {
	t.Helper()
	assertUpstreamRoute(t, c, out.upstream.Request(t))

	var events []contracttest.Event
	if c.Endpoint == contracttest.EndpointChat && c.IsStream && !c.IsError() {
		events = contracttest.ParseSSE(c.Body)
		checkDataOnlyStream(t, events)
	}

	switch {
	case c.IsError():
		if c.IsStream && len(out.stream.Chunks) > 0 {
			t.Errorf("an upstream %d answer produced %d stream chunks", c.Status, len(out.stream.Chunks))
		}
		contracttest.AssertSaneError(t, c, out.err)
	case out.err != nil:
		t.Fatalf("adapter error: %v", out.err)
	}

	strictFor(t, c).Check(t, c)
	contracttest.AssertNoUnknownShapes(t, c, out.shapes)

	if c.IsError() {
		return
	}
	switch c.Endpoint {
	case contracttest.EndpointChat:
		if c.IsStream {
			contracttest.AssertSane(t, contracttest.FromStream(out.stream.Chunks), streamHasOutput(events))
		} else {
			contracttest.AssertSane(t, contracttest.FromResponse(out.resp), responseHasOutput(t, c.Body))
		}
	case contracttest.EndpointEmbeddings:
		contracttest.ExpectGaps(t, c, func(t testing.TB) { saneEmbedding(t, out.embedding) })
	case contracttest.EndpointImages:
		contracttest.ExpectGaps(t, c, func(t testing.TB) { saneImage(t, out.image) })
	case contracttest.EndpointSpeech:
		if !bytes.Equal(out.audio, c.Body) {
			t.Errorf("the audio handed back (%d bytes) is not the upstream's (%d bytes)", len(out.audio), len(c.Body))
		}
		if out.audioType == "" {
			t.Errorf("the audio has no content type")
		}
	case contracttest.EndpointTranscription:
		contracttest.ExpectGaps(t, c, func(t testing.TB) { saneTranscription(t, c, out.transcription) })
	case contracttest.EndpointListModels:
		contracttest.ExpectGaps(t, c, func(t testing.TB) { saneModelList(t, out.models) })
	}
}

// assertUpstreamRoute checks the request went to the fixture's deployment,
// endpoint and api-version.
func assertUpstreamRoute(t testing.TB, c contracttest.Case, req contracttest.RecordedRequest) {
	t.Helper()
	want := "/openai/deployments/" + c.Model + "/" + endpointPaths[c.Endpoint]
	if c.Endpoint == contracttest.EndpointListModels {
		want = "/openai/models"
	}
	if req.Path != want {
		t.Errorf("upstream request path = %q, want %q (the fixture's deployment directory and endpoint)", req.Path, want)
	}
	if want := "api-version=" + apiVersion(c); req.Query != want {
		t.Errorf("upstream request query = %q, want %q", req.Query, want)
	}
}

// checkDataOnlyStream checks the framing of a Chat Completions stream: no
// event names, JSON objects with no "type" (a typed event, as the Responses
// API sends, is not a chat chunk), [DONE] last when present.
func checkDataOnlyStream(t testing.TB, events []contracttest.Event) {
	t.Helper()
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
}

// saneEmbedding: one vector or more, each non-empty, and the usage the cost
// plugin prices embeddings by.
func saneEmbedding(t testing.TB, resp *models.EmbeddingResponse) {
	t.Helper()
	if len(resp.Data) == 0 {
		t.Errorf("no embeddings")
	}
	for i, d := range resp.Data {
		if len(bytes.TrimSpace(d.Embedding)) == 0 || string(d.Embedding) == "null" || string(d.Embedding) == "[]" {
			t.Errorf("embedding %d is empty", i)
		}
	}
	if resp.Usage == nil || resp.Usage.PromptTokens <= 0 {
		t.Errorf("usage is missing or has no prompt_tokens, which embeddings are billed by: %+v", resp.Usage)
	}
}

// saneImage: one image or more, each a URL or base64 data.
func saneImage(t testing.TB, resp *models.ImageResponse) {
	t.Helper()
	if len(resp.Data) == 0 {
		t.Errorf("no images")
	}
	for i, d := range resp.Data {
		if d.URL == "" && d.B64JSON == "" {
			t.Errorf("image %d has neither url nor b64_json", i)
		}
	}
}

// saneTranscription: the body reaches the client unchanged, and the gateway
// found the text in it.
func saneTranscription(t testing.TB, c contracttest.Case, resp *models.TranscriptionResponse) {
	t.Helper()
	if !bytes.Equal(resp.Body, c.Body) {
		t.Errorf("the transcription body handed back is not the upstream's")
	}
	if strings.TrimSpace(resp.Text) == "" {
		t.Errorf("the gateway found no text in the transcription")
	}
}

// saneModelList: models, each with an ID.
func saneModelList(t testing.TB, list []models.ModelObject) {
	t.Helper()
	if len(list) == 0 {
		t.Errorf("no models")
	}
	for i, m := range list {
		if m.ID == "" {
			t.Errorf("model %d has no id", i)
		}
		if m.Created == 0 {
			t.Errorf("model %s has no created time", m.ID)
		}
	}
}

// responseHasOutput reports whether the upstream response carried text or a
// tool call, which the canonical response must then carry too.
func responseHasOutput(t testing.TB, body []byte) bool {
	t.Helper()
	var r struct {
		Choices []struct {
			Message struct {
				Content   *string           `json:"content"`
				ToolCalls []json.RawMessage `json:"tool_calls"`
			} `json:"message"`
		} `json:"choices"`
	}
	if err := json.Unmarshal(body, &r); err != nil {
		t.Fatalf("decoding fixture: %v", err)
	}
	for _, ch := range r.Choices {
		if (ch.Message.Content != nil && *ch.Message.Content != "") || len(ch.Message.ToolCalls) > 0 {
			return true
		}
	}
	return false
}

// streamHasOutput is responseHasOutput for a stream.
func streamHasOutput(events []contracttest.Event) bool {
	for _, ev := range events {
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
