package gemini

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"mime"
	"reflect"
	"slices"
	"strconv"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/genaifmt"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/providers/contracttest"
)

// The non-chat endpoints the adapter serves, each against its own document
// root (fixtureKey.root):
//
//	endpoint            adapter method                         root(s)
//	genai_generate      GenerateContent / StreamGenerateContent genai:$ (genai_error:$ for an error)
//	genai_count_tokens  GenAICountTokens                       count_tokens:$
//	genai_embed         EmbedContent                           genai_embed:$
//	embeddings          CreateEmbedding                        embed:$ (one input), batch_embed:$ (several)
//	images              CreateImage (Imagen :predict)          predict:$
//	speech              CreateSpeech (generateContent, AUDIO)  tts:$
//	list_models         ListModels                             list_models:$
//
// Vertex AI documents get a vertex_ prefix (vertex_embed:$, vertex_genai:$,
// vertex_tts:$); an error body keeps its root on both APIs (genai_error:$,
// error:$). The native passthroughs relay the upstream body to a google-genai
// client byte for byte; what the gateway reads out of it is billing, so that
// is what their documents model.

// genaiRelayed is what the gateway reads out of a native generateContent body
// it relays (server/handlers_genai.go): usageMetadata for billing
// (genaifmt.ExtractUsageMetadata) and modelVersion (ExtractModelVersion).
// Everything else goes to the client unread, so it is declared raw and the
// strict walk lists it as opaque. (Guardrails also read candidate text; that
// is not a translation or billing path.) checkGenAIMirror keeps this type
// honest against genaifmt. The top-level fields are both APIs':
// modelStatus is the Gemini API's only, createTime Vertex AI's only.
type genaiRelayed struct {
	Candidates     json.RawMessage   `json:"candidates"`
	PromptFeedback json.RawMessage   `json:"promptFeedback"`
	UsageMetadata  *genaiBilledUsage `json:"usageMetadata"`
	ModelVersion   string            `json:"modelVersion"`
	ResponseID     json.RawMessage   `json:"responseId"`
	ModelStatus    json.RawMessage   `json:"modelStatus"`
	CreateTime     json.RawMessage   `json:"createTime"`
}

// genaiBilledUsage is the usageMetadata genaifmt.ExtractUsageMetadata decodes.
type genaiBilledUsage struct {
	PromptTokenCount     int `json:"promptTokenCount"`
	CandidatesTokenCount int `json:"candidatesTokenCount"`
	ThoughtsTokenCount   int `json:"thoughtsTokenCount"`
	TotalTokenCount      int `json:"totalTokenCount"`
}

// documentedUsage is every UsageMetadata count the Gemini API and Vertex AI
// document, for judging what billing should have read.
type documentedUsage struct {
	PromptTokenCount        int    `json:"promptTokenCount"`
	CandidatesTokenCount    int    `json:"candidatesTokenCount"`
	ThoughtsTokenCount      int    `json:"thoughtsTokenCount"`
	ToolUsePromptTokenCount int    `json:"toolUsePromptTokenCount"`
	CachedContentTokenCount int    `json:"cachedContentTokenCount"`
	TotalTokenCount         int    `json:"totalTokenCount"`
	ServiceTier             string `json:"serviceTier"`
	// TrafficType is Vertex AI's: how the request was served and priced.
	TrafficType string `json:"trafficType"`
}

// nativeResult is what a native passthrough call gave back.
type nativeResult struct {
	Status int
	Body   []byte
}

// speechResult is what CreateSpeech gave back.
type speechResult struct {
	ContentType string
	Audio       []byte
}

// genaiStream reports whether a genai_generate case streams: its response is
// a stream, or (an error case, whose body is the error) its name starts with
// "stream", the rule the recorder's streamOf uses. A native body has no
// stream key: streaming is another URL.
func genaiStream(c contracttest.Case) bool {
	return c.IsStream || strings.HasPrefix(c.Name, "stream")
}

// embeddingInputs is the canonical embedding request's input, as the adapter
// reads it: one input goes to embedContent, more to batchEmbedContents.
func embeddingInputs(c contracttest.Case) []string {
	var req models.EmbeddingRequest
	if json.Unmarshal(c.RawRequest, &req) != nil {
		return nil
	}
	in, _ := parseEmbeddingInput(req.Input)
	return in
}

// surfaceDocs is contractDocs for the non-chat endpoints.
func surfaceDocs(c contracttest.Case, k fixtureKey) []contracttest.Doc {
	errDoc := errorDocs(c.Body)
	switch c.Endpoint {
	case contracttest.EndpointGenAIGenerate:
		relayedErr := contracttest.Doc{Root: "genai_error:$", Data: c.Body}
		switch {
		case c.IsError():
			return []contracttest.Doc{relayedErr}
		case c.IsStream:
			events := contracttest.ParseSSE(c.Body)
			docs := make([]contracttest.Doc, len(events))
			for i, ev := range events {
				docs[i] = contracttest.Doc{Root: k.root("genai"), Data: ev.Data, Type: reflect.TypeFor[genaiRelayed]()}
				if isErrorChunk(ev.Data) {
					docs[i] = contracttest.Doc{Root: "genai_error:$", Data: ev.Data}
				}
			}
			return docs
		default:
			return []contracttest.Doc{{Root: k.root("genai"), Data: c.Body, Type: reflect.TypeFor[genaiRelayed]()}}
		}
	case contracttest.EndpointGenAICountTokens, contracttest.EndpointGenAIEmbed:
		// Relayed, nothing read: the whole body is opaque.
		if c.IsError() {
			return []contracttest.Doc{{Root: "genai_error:$", Data: c.Body}}
		}
		name := "count_tokens"
		if c.Endpoint == contracttest.EndpointGenAIEmbed {
			name = "genai_embed"
		}
		return []contracttest.Doc{{Root: k.root(name), Data: c.Body}}
	case contracttest.EndpointEmbeddings:
		switch {
		case c.IsError():
			return errDoc
		case len(embeddingInputs(c)) > 1:
			return []contracttest.Doc{{Root: k.root("batch_embed"), Data: c.Body, Type: reflect.TypeFor[geminiBatchEmbedResponse]()}}
		default:
			return []contracttest.Doc{{Root: k.root("embed"), Data: c.Body, Type: reflect.TypeFor[geminiEmbedResponse]()}}
		}
	case contracttest.EndpointImages:
		if c.IsError() {
			return errDoc
		}
		return []contracttest.Doc{{Root: k.root("predict"), Data: c.Body, Type: reflect.TypeFor[geminiPredictResponse]()}}
	case contracttest.EndpointSpeech:
		if c.IsError() {
			return errDoc
		}
		return []contracttest.Doc{{Root: k.root("tts"), Data: c.Body, Type: reflect.TypeFor[geminiResponse]()}}
	case contracttest.EndpointListModels:
		// ListModels reads nothing (gap G26): opaque until it does.
		return []contracttest.Doc{{Root: k.root("list_models"), Data: c.Body}}
	}
	return nil
}

// runSurface calls the adapter method c's endpoint names.
func runSurface(t testing.TB, c contracttest.Case, k fixtureKey, p *Provider, ctx context.Context, out *caseOutput) {
	t.Helper()
	// The native calls forward no client headers: a fixture's request is
	// the body alone.
	var headers map[string]string
	switch c.Endpoint {
	case contracttest.EndpointGenAIGenerate:
		if genaiStream(c) {
			stream, status, err := p.StreamGenerateContent(ctx, k.model, c.RawRequest, headers)
			out.native, out.err = &nativeResult{Status: status}, err
			if err == nil {
				if out.native.Body, err = readAll(stream); err != nil {
					t.Fatalf("reading the relayed stream: %v", err)
				}
			}
			return
		}
		body, status, err := p.GenerateContent(ctx, k.model, c.RawRequest, headers)
		out.native, out.err = &nativeResult{Status: status, Body: body}, err
	case contracttest.EndpointGenAICountTokens:
		body, status, err := p.GenAICountTokens(ctx, k.model, c.RawRequest, headers)
		out.native, out.err = &nativeResult{Status: status, Body: body}, err
	case contracttest.EndpointGenAIEmbed:
		body, status, err := p.EmbedContent(ctx, k.model, c.RawRequest, headers)
		out.native, out.err = &nativeResult{Status: status, Body: body}, err
	case contracttest.EndpointEmbeddings:
		req := decodeRequest[models.EmbeddingRequest](t, c)
		out.embed, out.err = p.CreateEmbedding(ctx, req)
	case contracttest.EndpointImages:
		req := decodeRequest[models.ImageRequest](t, c)
		out.image, out.err = p.CreateImage(ctx, req)
	case contracttest.EndpointSpeech:
		req := decodeRequest[models.SpeechRequest](t, c)
		audio, contentType, err := p.CreateSpeech(ctx, req)
		out.err = err
		if err == nil {
			out.speech = &speechResult{ContentType: contentType}
			if out.speech.Audio, err = readAll(audio); err != nil {
				t.Fatalf("reading the speech audio: %v", err)
			}
		}
	case contracttest.EndpointListModels:
		out.models, out.err = p.ListModels(ctx)
		out.listRan = true
	default:
		t.Fatalf("endpoint %q: the gemini adapter does not implement it", c.Endpoint)
	}
}

// decodeRequest decodes c's request.json as T.
func decodeRequest[T any](t testing.TB, c contracttest.Case) *T {
	t.Helper()
	var req T
	if err := json.Unmarshal(c.RawRequest, &req); err != nil {
		t.Fatalf("decoding %s request.json: %v", c.Endpoint, err)
	}
	return &req
}

// nativeGolden is the golden form of a native passthrough call: what the
// client got (the status, and whether the body came back byte for byte) and
// what the gateway bills from it.
type nativeGolden struct {
	Status  int  `json:"status"`
	Relayed bool `json:"relayed"`
	// Billed is what genaifmt reads from a generateContent body: what
	// applyGenAIUsage / applyGenAIStreamUsage bill
	// (server/contract_gemini_genai_test.go holds the server to it).
	Billed *genaiBilling `json:"billed,omitempty"`
	// BilledInputCharacters is what the native embedContent route bills
	// (genaifmt.EmbedInputCharacters of the request).
	BilledInputCharacters *int                      `json:"billed_input_characters,omitempty"`
	Error                 *contracttest.ErrorResult `json:"error,omitempty"`
}

// genaiBilling is what the native generateContent route bills.
type genaiBilling struct {
	InputTokens  int    `json:"input_tokens"`
	OutputTokens int    `json:"output_tokens"`
	Model        string `json:"model"`
}

// billGenAI is what the server bills from a relayed generateContent body,
// computed the way it does: genaifmt.ExtractUsageMetadata and
// ExtractModelVersion for a body, a StreamUsageScanner fed the stream for a
// stream.
func billGenAI(body []byte, stream bool) genaiBilling {
	if stream {
		var s genaifmt.StreamUsageScanner
		s.Write(body)
		p, c, _ := s.Usage()
		return genaiBilling{InputTokens: p, OutputTokens: c, Model: s.Model()}
	}
	p, c, _ := genaifmt.ExtractUsageMetadata(body)
	return genaiBilling{InputTokens: p, OutputTokens: c, Model: genaifmt.ExtractModelVersion(body)}
}

// speechGolden is the golden form of CreateSpeech's answer.
type speechGolden struct {
	ContentType string `json:"content_type"`
	Bytes       int    `json:"bytes"`
	SHA256      string `json:"sha256"`
}

// listGolden is the golden form of ListModels' answer.
type listGolden struct {
	Models []models.ModelObject `json:"models"`
	Error  *string              `json:"error"`
}

// assertSurfaceGoldens is assertGoldens for the non-chat endpoints.
func assertSurfaceGoldens(t *testing.T, c contracttest.Case, out caseOutput) {
	t.Helper()
	var golden any
	switch {
	case out.native != nil || isNative(c.Endpoint):
		g := nativeGolden{}
		if out.native != nil {
			g.Status, g.Relayed = out.native.Status, out.err == nil && bytes.Equal(out.native.Body, c.Body)
		}
		if out.err != nil {
			e := contracttest.ErrorGolden(out.err)
			g.Error = &e
		}
		switch {
		case c.IsError() || out.err != nil:
		case c.Endpoint == contracttest.EndpointGenAIGenerate:
			b := billGenAI(out.native.Body, genaiStream(c))
			g.Billed = &b
		case c.Endpoint == contracttest.EndpointGenAIEmbed:
			n := genaifmt.EmbedInputCharacters(c.RawRequest)
			g.BilledInputCharacters = &n
		}
		golden = g
	case c.Endpoint == contracttest.EndpointListModels:
		g := listGolden{Models: out.models}
		if out.err != nil {
			s := out.err.Error()
			g.Error = &s
		}
		golden = g
	case out.err != nil:
		golden = contracttest.ErrorGolden(out.err)
	case out.embed != nil:
		golden = out.embed
	case out.image != nil:
		golden = out.image
	case out.speech != nil:
		sum := sha256.Sum256(out.speech.Audio)
		golden = speechGolden{ContentType: out.speech.ContentType, Bytes: len(out.speech.Audio), SHA256: hex.EncodeToString(sum[:])}
	}
	contracttest.AssertGolden(t, c.GoldenPath(), golden)
	if c.Endpoint == contracttest.EndpointListModels {
		contracttest.AssertGolden(t, c.UpstreamGoldenPath(), out.upstream.Requests())
		return
	}
	contracttest.AssertGolden(t, c.UpstreamGoldenPath(), out.upstream.Request(t))
}

func isNative(endpoint string) bool {
	switch endpoint {
	case contracttest.EndpointGenAIGenerate, contracttest.EndpointGenAICountTokens, contracttest.EndpointGenAIEmbed:
		return true
	}
	return false
}

// checkSurface holds the non-chat endpoints' hard rules: the request went to
// the fixture's model and method, a passthrough relayed the body untouched,
// and a translated answer carries what the upstream sent.
func checkSurface(t testing.TB, c contracttest.Case, k fixtureKey, out caseOutput) {
	t.Helper()
	switch c.Endpoint {
	case contracttest.EndpointGenAIGenerate:
		action, query := "generateContent", ""
		if genaiStream(c) {
			action, query = "streamGenerateContent", "alt=sse"
		}
		assertUpstreamCall(t, c, k, out.upstream, "POST", action, query)
		switch {
		case c.IsError() && genaiStream(c):
			// The adapter's error is judged by saneSurface.
			if out.err == nil {
				t.Errorf("the upstream answered %d to a stream, but StreamGenerateContent returned a stream", c.Status)
			}
		default:
			assertRelayed(t, c, out)
		}
		if c.IsStream && !c.IsError() {
			assertDataOnlyChunks(t, contracttest.ParseSSE(c.Body))
		}
		if !c.IsError() {
			checkGenAIMirror(t, c)
		}
	case contracttest.EndpointGenAICountTokens:
		assertUpstreamCall(t, c, k, out.upstream, "POST", "countTokens", "")
		assertRelayed(t, c, out)
	case contracttest.EndpointGenAIEmbed:
		assertUpstreamCall(t, c, k, out.upstream, "POST", "embedContent", "")
		assertRelayed(t, c, out)
	case contracttest.EndpointEmbeddings:
		inputs := embeddingInputs(c)
		action := "embedContent"
		if len(inputs) > 1 {
			action = "batchEmbedContents"
		}
		assertUpstreamCall(t, c, k, out.upstream, "POST", action, "")
		if !c.IsError() {
			if out.err != nil {
				t.Fatalf("CreateEmbedding: %v", out.err)
			}
			checkEmbeddings(t, c, inputs, out.embed)
		}
	case contracttest.EndpointImages:
		assertUpstreamCall(t, c, k, out.upstream, "POST", "predict", "")
		if !c.IsError() {
			if out.err != nil {
				t.Fatalf("CreateImage: %v", out.err)
			}
			checkImages(t, c, out.image)
		}
	case contracttest.EndpointSpeech:
		assertUpstreamCall(t, c, k, out.upstream, "POST", "generateContent", "")
		if !c.IsError() {
			if out.err != nil {
				t.Fatalf("CreateSpeech: %v", out.err)
			}
			checkSpeech(t, c, out.speech)
		}
	case contracttest.EndpointListModels:
		if out.err != nil {
			t.Errorf("ListModels: %v", out.err)
		}
	}
}

// assertRelayed checks a passthrough call returned the upstream's status and
// body untouched, and no error.
func assertRelayed(t testing.TB, c contracttest.Case, out caseOutput) {
	t.Helper()
	switch {
	case out.err != nil:
		t.Errorf("the passthrough returned an error instead of relaying the upstream's %d: %v", c.Status, out.err)
	case out.native.Status != c.Status:
		t.Errorf("the passthrough relayed status %d; the upstream answered %d", out.native.Status, c.Status)
	case !bytes.Equal(out.native.Body, c.Body):
		t.Errorf("the passthrough did not relay the upstream body byte for byte (%d bytes in, %d out)", len(c.Body), len(out.native.Body))
	}
}

// checkGenAIMirror checks genaiRelayed still decodes usage the way
// genaifmt does, so the strict checks on it watch what billing reads.
func checkGenAIMirror(t testing.TB, c contracttest.Case) {
	t.Helper()
	got := billGenAI(c.Body, c.IsStream)
	bodies := [][]byte{c.Body}
	if c.IsStream {
		bodies = nil
		for _, ev := range contracttest.ParseSSE(c.Body) {
			bodies = append(bodies, ev.Data)
		}
	}
	var want genaiBilling
	for i := len(bodies) - 1; i >= 0; i-- {
		var r genaiRelayed
		if json.Unmarshal(bodies[i], &r) != nil || r.UsageMetadata == nil {
			continue
		}
		u := r.UsageMetadata
		if u.PromptTokenCount > 0 || u.CandidatesTokenCount+u.ThoughtsTokenCount > 0 {
			want = genaiBilling{InputTokens: u.PromptTokenCount, OutputTokens: u.CandidatesTokenCount + u.ThoughtsTokenCount}
			break
		}
	}
	if got.InputTokens != want.InputTokens || got.OutputTokens != want.OutputTokens {
		t.Errorf("genaifmt bills input %d / output %d, but genaiRelayed (this suite's model of what it reads) gives %d / %d: "+
			"update genaiRelayed and genaiBilledUsage to match genaifmt", got.InputTokens, got.OutputTokens, want.InputTokens, want.OutputTokens)
	}
}

// checkEmbeddings checks every vector the upstream sent reached the client,
// in order.
func checkEmbeddings(t testing.TB, c contracttest.Case, inputs []string, resp *models.EmbeddingResponse) {
	t.Helper()
	var vectors [][]float64
	if len(inputs) > 1 {
		var b geminiBatchEmbedResponse
		_ = json.Unmarshal(c.Body, &b)
		for _, e := range b.Embeddings {
			vectors = append(vectors, e.Values)
		}
	} else {
		var s geminiEmbedResponse
		_ = json.Unmarshal(c.Body, &s)
		if s.Embedding != nil {
			vectors = append(vectors, s.Embedding.Values)
		}
	}
	if len(vectors) != len(inputs) {
		t.Errorf("the upstream sent %d embeddings for %d inputs", len(vectors), len(inputs))
	}
	if len(resp.Data) != len(vectors) {
		t.Fatalf("the canonical response has %d embeddings; the upstream sent %d", len(resp.Data), len(vectors))
	}
	for i, d := range resp.Data {
		var got []float64
		if d.Index != i || json.Unmarshal(d.Embedding, &got) != nil || !slices.Equal(got, vectors[i]) {
			t.Errorf("embedding %d (index %d) is not the upstream's vector %d", i, d.Index, i)
		}
	}
}

// checkImages checks every image the upstream sent reached the client.
func checkImages(t testing.TB, c contracttest.Case, resp *models.ImageResponse) {
	t.Helper()
	var up geminiPredictResponse
	_ = json.Unmarshal(c.Body, &up)
	if len(resp.Data) != len(up.Predictions) {
		t.Fatalf("the canonical response has %d images; the upstream sent %d predictions", len(resp.Data), len(up.Predictions))
	}
	for i, d := range resp.Data {
		if d.B64JSON != up.Predictions[i].BytesBase64Encoded {
			t.Errorf("image %d is not the upstream's prediction %d", i, i)
		}
	}
}

// checkSpeech checks the audio is the upstream's PCM, raw or in a WAV header,
// with a content type naming its rate.
func checkSpeech(t testing.TB, c contracttest.Case, got *speechResult) {
	t.Helper()
	var resp geminiResponse
	if err := json.Unmarshal(c.Body, &resp); err != nil {
		t.Fatalf("decoding fixture: %v", err)
	}
	var pcm []byte
	rate := geminiSpeechSampleRate
	for _, cand := range resp.Candidates {
		for _, p := range cand.Content.Parts {
			if p.InlineData == nil || !isAudioMimeType(p.InlineData.MimeType) {
				continue
			}
			b, err := base64.StdEncoding.DecodeString(p.InlineData.Data)
			if err != nil {
				t.Fatalf("fixture audio is not base64: %v", err)
			}
			pcm = append(pcm, b...)
			if _, params, err := mime.ParseMediaType(p.InlineData.MimeType); err == nil {
				if r, err := strconv.Atoi(params["rate"]); err == nil {
					rate = r
				}
			}
		}
	}
	req := decodeRequest[models.SpeechRequest](t, c)
	want, wantType := pcm, "audio/L16;rate="+strconv.Itoa(rate)
	if req.ResponseFormat == "wav" {
		want, wantType = wrapPCMInWAV(pcm, rate), "audio/wav"
	}
	if got.ContentType != wantType || !bytes.Equal(got.Audio, want) {
		t.Errorf("speech answered %d bytes of %s; the upstream's audio is %d bytes of %s", len(got.Audio), got.ContentType, len(want), wantType)
	}
}

// saneSurface is the non-chat endpoints' sanity: the upstream method exists,
// errors map sanely, and what the gateway bills is what the upstream
// reported.
func saneSurface(t testing.TB, c contracttest.Case, k fixtureKey, out caseOutput) {
	t.Helper()
	if c.Endpoint != contracttest.EndpointListModels {
		saneMethod(t, k, out.upstream)
	}
	switch c.Endpoint {
	case contracttest.EndpointGenAIGenerate:
		switch {
		case c.IsError() && genaiStream(c):
			saneError(t, c, out.err)
		case !c.IsError():
			saneGenAIBilling(t, c)
		}
	case contracttest.EndpointGenAIEmbed:
		if !c.IsError() {
			if n := embedUsage(c.Body); n > 0 {
				t.Errorf("embedding usage: the native route bills %d request characters and ignores the %d prompt tokens the "+
					"response reports", genaifmt.EmbedInputCharacters(c.RawRequest), n)
			}
		}
	case contracttest.EndpointEmbeddings:
		if c.IsError() {
			saneError(t, c, out.err)
			return
		}
		if n := embedUsage(c.Body); n > 0 && (out.embed.Usage == nil || out.embed.Usage.PromptTokens != n) {
			got := 0
			if out.embed.Usage != nil {
				got = out.embed.Usage.PromptTokens
			}
			t.Errorf("embedding usage: the upstream reported %d prompt tokens; the canonical usage.prompt_tokens is %d", n, got)
		}
	case contracttest.EndpointImages:
		if c.IsError() {
			saneError(t, c, out.err)
		}
	case contracttest.EndpointSpeech:
		if c.IsError() {
			saneError(t, c, out.err)
			return
		}
		var resp struct {
			UsageMetadata *struct {
				CandidatesTokenCount    int `json:"candidatesTokenCount"`
				CandidatesTokensDetails []struct {
					Modality   string `json:"modality"`
					TokenCount int    `json:"tokenCount"`
				} `json:"candidatesTokensDetails"`
			} `json:"usageMetadata"`
		}
		if json.Unmarshal(c.Body, &resp) == nil && resp.UsageMetadata != nil && resp.UsageMetadata.CandidatesTokenCount > 0 {
			t.Errorf("speech usage: the upstream reported %d output tokens %v, but the speech route returns audio alone and "+
				"bills input characters", resp.UsageMetadata.CandidatesTokenCount, resp.UsageMetadata.CandidatesTokensDetails)
		}
	case contracttest.EndpointListModels:
		saneListModels(t, c, out)
	}
}

// saneGenAIBilling checks what the native generateContent route bills
// against what the upstream reported: Google bills the prompt and the
// tool-use prompt as input, the candidates and thoughts as output, cached
// prompt tokens at a cached rate, flex or priority requests at their tier's
// rate, and on Vertex AI provisioned-throughput, priority and flex traffic
// (usageMetadata.trafficType) other than on demand.
func saneGenAIBilling(t testing.TB, c contracttest.Case) {
	t.Helper()
	billed := billGenAI(c.Body, c.IsStream)
	u := lastUsage(c.Body, c.IsStream)
	if u == nil {
		t.Errorf("the response reports no usageMetadata to bill")
		return
	}
	if want := u.PromptTokenCount + u.ToolUsePromptTokenCount; billed.InputTokens != want {
		if u.ToolUsePromptTokenCount > 0 {
			t.Errorf("billed input_tokens %d leave out toolUsePromptTokenCount %d (input is prompt %d + tool-use prompt %d)",
				billed.InputTokens, u.ToolUsePromptTokenCount, u.PromptTokenCount, u.ToolUsePromptTokenCount)
		} else {
			t.Errorf("billed input_tokens %d; usageMetadata.promptTokenCount is %d", billed.InputTokens, u.PromptTokenCount)
		}
	}
	if want := u.CandidatesTokenCount + u.ThoughtsTokenCount; billed.OutputTokens != want {
		t.Errorf("billed output_tokens %d; candidates %d + thoughts %d", billed.OutputTokens, u.CandidatesTokenCount, u.ThoughtsTokenCount)
	}
	if u.CachedContentTokenCount > 0 {
		t.Errorf("billing has no count for cachedContentTokenCount %d: those of the %d input tokens are billed at the full input rate",
			u.CachedContentTokenCount, billed.InputTokens)
	}
	if u.ServiceTier != "" && u.ServiceTier != "standard" && u.ServiceTier != "unspecified" {
		t.Errorf("serviceTier %q is not billed: the request is priced at the standard rate", u.ServiceTier)
	}
	if u.TrafficType != "" && u.TrafficType != "ON_DEMAND" && u.TrafficType != "TRAFFIC_TYPE_UNSPECIFIED" {
		t.Errorf("trafficType %q is not billed: the request is priced as pay-as-you-go on-demand", u.TrafficType)
	}
}

// lastUsage is the final usageMetadata of a body or stream, or nil.
func lastUsage(body []byte, stream bool) *documentedUsage {
	bodies := [][]byte{body}
	if stream {
		bodies = nil
		for _, ev := range contracttest.ParseSSE(body) {
			bodies = append(bodies, ev.Data)
		}
	}
	for i := len(bodies) - 1; i >= 0; i-- {
		var r struct {
			UsageMetadata *documentedUsage `json:"usageMetadata"`
		}
		if json.Unmarshal(bodies[i], &r) == nil && r.UsageMetadata != nil {
			return r.UsageMetadata
		}
	}
	return nil
}

// embedUsage is the prompt token count an embedContent or
// batchEmbedContents body reports, 0 when it reports none.
func embedUsage(body []byte) int {
	var r struct {
		UsageMetadata struct {
			PromptTokenCount int `json:"promptTokenCount"`
		} `json:"usageMetadata"`
	}
	_ = json.Unmarshal(body, &r)
	return r.UsageMetadata.PromptTokenCount
}

// saneListModels checks ListModels lists the models GET /v1beta/models
// does, by their IDs.
func saneListModels(t testing.TB, c contracttest.Case, out caseOutput) {
	t.Helper()
	var list struct {
		Models []struct {
			Name string `json:"name"`
		} `json:"models"`
	}
	_ = json.Unmarshal(c.Body, &list)
	if len(out.models) == 0 {
		t.Errorf("ListModels returned 0 models; GET /v1beta/models lists %d", len(list.Models))
		return
	}
	var got []string
	for _, m := range out.models {
		got = append(got, m.ID)
	}
	for _, m := range list.Models {
		if id := strings.TrimPrefix(m.Name, "models/"); !slices.Contains(got, id) {
			t.Errorf("ListModels does not list %s", id)
		}
	}
	reqs := out.upstream.Requests()
	if len(reqs) != 1 || reqs[0].Method != "GET" || reqs[0].Path != "/v1beta/models" {
		t.Errorf("ListModels should make one GET /v1beta/models; it made %+v", reqs)
	}
}
