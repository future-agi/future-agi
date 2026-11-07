package anthropic

import (
	"bytes"
	"context"
	"encoding/json"
	"io"
	"reflect"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/anthropicfmt"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/providers/contracttest"
	"github.com/futureagi/agentcc-gateway/internal/streaming"
)

// The non-chat surfaces of the adapter, driven through the code the gateway
// runs for them (case.json "endpoint"):
//
//   - anthropic_messages: the native /v1/messages passthrough
//     (CreateAnthropicMessage / StreamAnthropicMessage). The body reaches the
//     client unchanged; what the gateway parses from it is the billing, with
//     anthropicfmt (ExtractUsage and ExtractModel, or StreamUsageScanner fed
//     event by event), as handlers_anthropic.go's applyAnthropicUsage and
//     applyAnthropicStreamUsage do.
//   - anthropic_count_tokens: CountAnthropicTokens, a byte passthrough.
//   - list_models: ListModels.
//
// Their bodies get roots of their own ("native:$", "native/<event>:$",
// "native/error:$", "count_tokens:$", "models:$") so their paths, allowlist
// entries and opaque regions never mix with the chat ones in a model's
// meta.json and in opaque.golden.json.

// nativeHeaders are the Anthropic headers the handler forwards from the
// client (anthropic-version, anthropic-beta, ...): what every SDK sends.
var nativeHeaders = map[string]string{"anthropic-version": defaultAnthropicVersion}

// nativeUsage is what the gateway's billing reads from a usage object
// (anthropicfmt.ExtractUsage, and StreamUsageScanner's usageEvent): every
// other usage field reaches the client but is never priced.
type nativeUsage struct {
	InputTokens  int `json:"input_tokens"`
	OutputTokens int `json:"output_tokens"`
}

// nativeEventTypePaths is the type path of every documented event under the
// native stream roots. The roots are spelled out because a "*:" path would
// also match the non-stream root "native:$", whose type is "message".
func nativeEventTypePaths() []string {
	paths := make([]string, len(knownStreamEvents))
	for i, e := range knownStreamEvents {
		paths[i] = "native/" + e + ":$.type"
	}
	return paths
}

// nativeEnums are the documented value sets on the native paths: event
// types (StreamUsageScanner.Finished looks for message_delta), the tier and
// speed billing does not read (a new one is a new price), and error types.
var nativeEnums = []contracttest.EnumSpec{
	{Spec: "MessageStreamEvent.type + the streaming docs' ping and error", Paths: nativeEventTypePaths(), Values: knownStreamEvents},
	{Spec: "Message.type", Paths: []string{"native:$.type", "native/message_start:$.message.type"}, Values: []string{"message"}},
	{Spec: "Usage.service_tier", Paths: []string{"native:$.usage.service_tier", "native/message_start:$.message.usage.service_tier"},
		Values: []string{"standard", "priority", "batch"}},
	{Spec: "Speed", Paths: []string{"native:$.usage.speed", "native/message_start:$.message.usage.speed"}, Values: []string{"standard", "fast"}},
	{Spec: "ErrorResponse.type", Paths: []string{"native/error:$.type"}, Values: []string{"error"}},
	{Spec: errorTypeSpec, Paths: []string{"native/error:$.error.type"}, Values: errorTypes},
	{Spec: "ErrorResponse.error.details.error_code", Paths: []string{"native/error:$.error.details.error_code"}, Values: errorCodes},
}

// nativeStrict: the passthrough decodes nothing but what billing reads, so
// every document is opaque except the usage objects, the model and the
// event type.
var nativeStrict = &contracttest.Strict{
	Docs:  nativeDocs,
	Enums: nativeEnums,
	Decoded: []contracttest.Decoded{
		// ExtractUsage reads $.usage; StreamUsageScanner reads usage at the
		// top of an event (message_delta) and under message (message_start).
		{Path: "*:$.usage", Type: reflect.TypeFor[nativeUsage]()},
		{Path: "*:$.message.usage", Type: reflect.TypeFor[nativeUsage]()},
		// ExtractModel and StreamUsageScanner.Model.
		{Path: "*:$.model", Type: reflect.TypeFor[string]()},
		{Path: "*:$.message.model", Type: reflect.TypeFor[string]()},
		// StreamUsageScanner.Finished.
		{Path: "*:$.type", Type: reflect.TypeFor[string]()},
	},
}

// countTokensStrict: CountAnthropicTokens is a byte passthrough, so its body
// is opaque by design.
var countTokensStrict = &contracttest.Strict{Docs: rootedDocs("count_tokens:$")}

// listModelsStrict: ListModels reads nothing (gap X8), so the list is opaque.
var listModelsStrict = &contracttest.Strict{Docs: rootedDocs("models:$")}

func nativeDocs(c contracttest.Case) []contracttest.Doc {
	switch {
	case c.IsError() && !isJSON(c.ContentType):
		return nil
	case c.IsError():
		return []contracttest.Doc{{Root: "native/error:$", Data: c.Body}}
	case c.IsStream:
		events := contracttest.ParseSSE(c.Body)
		docs := make([]contracttest.Doc, len(events))
		for i, ev := range events {
			docs[i] = contracttest.Doc{Root: "native/" + ev.Name + ":$", Data: ev.Data}
		}
		return docs
	default:
		return []contracttest.Doc{{Root: "native:$", Data: c.Body}}
	}
}

// rootedDocs is a Docs for an endpoint whose body (or error body) the
// gateway passes on without decoding.
func rootedDocs(root string) func(c contracttest.Case) []contracttest.Doc {
	return func(c contracttest.Case) []contracttest.Doc {
		if c.IsError() && !isJSON(c.ContentType) {
			return nil
		}
		return []contracttest.Doc{{Root: root, Data: c.Body}}
	}
}

// nativeResult is the golden form of a passthrough call: what the client
// gets, and what the gateway bills.
type nativeResult struct {
	Status int `json:"status"`
	// Body is "unchanged" when the client gets the upstream's bytes, else
	// what it gets.
	Body    string                    `json:"body"`
	Error   *contracttest.ErrorResult `json:"error,omitempty"`
	Billing *nativeBilling            `json:"billing,omitempty"`

	raw []byte
}

// nativeBilling is what the handler puts on the request context for the
// cost plugin: rc.ResolvedModel and rc.Response.Usage's prompt and
// completion tokens.
type nativeBilling struct {
	Model        string `json:"model"`
	InputTokens  int    `json:"input_tokens"`
	OutputTokens int    `json:"output_tokens"`
	// UsageFound and Finished are StreamUsageScanner's Usage ok and
	// Finished: a stream that never reached message_delta is billed from an
	// estimate instead.
	UsageFound *bool `json:"usage_found,omitempty"`
	Finished   *bool `json:"finished,omitempty"`
}

// runNonChat drives a non-chat case.
func runNonChat(t testing.TB, c contracttest.Case, p *Provider, ctx context.Context, out *caseOutput) {
	t.Helper()
	switch c.Endpoint {
	case contracttest.EndpointAnthropicMessages:
		res := &nativeResult{}
		if c.IsStream {
			stream, status, err := p.StreamAnthropicMessage(ctx, c.RawRequest, nativeHeaders)
			res.Status, out.err = status, err
			if stream != nil {
				raw, rerr := io.ReadAll(stream)
				_ = stream.Close()
				if rerr != nil {
					t.Fatalf("reading the stream: %v", rerr)
				}
				res.raw = raw
			}
		} else {
			res.raw, res.Status, out.err = p.CreateAnthropicMessage(ctx, c.RawRequest, nativeHeaders)
		}
		if out.err == nil && res.Status < 400 {
			res.Billing = billNative(c.IsStream, res.raw)
		}
		out.native = res
	case contracttest.EndpointAnthropicCountTokens:
		res := &nativeResult{}
		res.raw, res.Status, out.err = p.CountAnthropicTokens(ctx, c.RawRequest, nativeHeaders)
		out.native = res
	case contracttest.EndpointListModels:
		out.models, out.err = p.ListModels(ctx)
	}
}

// billNative is the billing the handler takes from a passthrough body, as
// handleAnthropicNonStream (applyAnthropicUsage) and handleAnthropicStream
// (each relayed event into a StreamUsageScanner, then
// applyAnthropicStreamUsage) do.
func billNative(stream bool, body []byte) *nativeBilling {
	if !stream {
		in, out := anthropicfmt.ExtractUsage(body)
		return &nativeBilling{Model: anthropicfmt.ExtractModel(body), InputTokens: in, OutputTokens: out}
	}
	var scanner anthropicfmt.StreamUsageScanner
	events := streaming.NewEventReader(bytes.NewReader(body))
	for {
		ev, err := events.Next()
		scanner.Write(ev.Raw)
		if err != nil {
			break
		}
	}
	in, out, ok := scanner.Usage()
	finished := scanner.Finished()
	return &nativeBilling{Model: scanner.Model(), InputTokens: in, OutputTokens: out, UsageFound: &ok, Finished: &finished}
}

// golden is res as AssertGolden pins it.
func (res *nativeResult) golden(c contracttest.Case, err error) *nativeResult {
	g := *res
	g.Body = string(res.raw)
	if bytes.Equal(res.raw, c.Body) {
		g.Body = "unchanged"
	}
	if err != nil {
		e := contracttest.ErrorGolden(err)
		g.Error = &e
	}
	return &g
}

// listModelsResult is the golden form of a ListModels call.
type listModelsResult struct {
	Models []models.ModelObject `json:"models"`
	Error  string               `json:"error,omitempty"`
}

func assertNonChatGoldens(t *testing.T, c contracttest.Case, out caseOutput) {
	t.Helper()
	switch c.Endpoint {
	case contracttest.EndpointListModels:
		res := listModelsResult{Models: out.models}
		if out.err != nil {
			res.Error = out.err.Error()
		}
		contracttest.AssertGolden(t, c.GoldenPath(), res)
		// ListModels sends no request today (gap X8); pin whatever it sends.
		reqs := out.upstream.Requests()
		if reqs == nil {
			reqs = []contracttest.RecordedRequest{}
		}
		contracttest.AssertGolden(t, c.UpstreamGoldenPath(), reqs)
		return
	default:
		contracttest.AssertGolden(t, c.GoldenPath(), out.native.golden(c, out.err))
	}
	contracttest.AssertGolden(t, c.UpstreamGoldenPath(), out.upstream.Request(t))
}

// checkNonChat is checkCase for the non-chat endpoints.
func checkNonChat(t testing.TB, c contracttest.Case, out caseOutput) {
	t.Helper()
	strict := strictFor(t, c)
	switch c.Endpoint {
	case contracttest.EndpointAnthropicMessages:
		checkPassthroughRequest(t, c, out, "/v1/messages")
		checkNative(t, c, out)
	case contracttest.EndpointAnthropicCountTokens:
		checkPassthroughRequest(t, c, out, "/v1/messages/count_tokens")
		if out.err != nil {
			t.Fatalf("CountAnthropicTokens: %v", out.err)
		}
		checkPassedThrough(t, c, out.native)
	case contracttest.EndpointListModels:
		if out.err != nil {
			t.Fatalf("ListModels: %v", out.err)
		}
		contracttest.ExpectGaps(t, c, func(t testing.TB) { checkListedModels(t, c, out) })
	}
	strict.Check(t, c)
	contracttest.AssertNoUnknownShapes(t, c, out.shapes)
}

// checkPassthroughRequest checks a passthrough sent the client's body,
// unchanged, to path.
func checkPassthroughRequest(t testing.TB, c contracttest.Case, out caseOutput, path string) {
	t.Helper()
	req := out.upstream.Request(t)
	if req.Method != "POST" || req.Path != path {
		t.Errorf("upstream request = %s %s, want POST %s", req.Method, req.Path, path)
	}
	var want any
	dec := json.NewDecoder(bytes.NewReader(c.RawRequest))
	dec.UseNumber()
	if err := dec.Decode(&want); err != nil {
		t.Fatalf("request.json: %v", err)
	}
	if !reflect.DeepEqual(req.Body, want) {
		t.Errorf("the passthrough changed the request body:\n got %v\nwant %v", req.Body, want)
	}
	if body, _ := req.Body.(map[string]any); body["model"] != c.Model {
		t.Errorf("upstream request model = %v, want the fixture's model directory %q", body["model"], c.Model)
	}
}

// checkPassedThrough checks the client gets the upstream's status and bytes.
func checkPassedThrough(t testing.TB, c contracttest.Case, res *nativeResult) {
	t.Helper()
	if res.Status != c.Status {
		t.Errorf("status %d reaches the client; the upstream answered %d", res.Status, c.Status)
	}
	if !bytes.Equal(res.raw, c.Body) {
		t.Errorf("the client gets %d bytes that are not the upstream's %d", len(res.raw), len(c.Body))
	}
}

// checkNative checks a /v1/messages passthrough call.
func checkNative(t testing.TB, c contracttest.Case, out caseOutput) {
	t.Helper()
	res := out.native
	switch {
	case c.IsError() && c.IsStream:
		// StreamAnthropicMessage returns the upstream's error as an error,
		// which the handler answers with.
		if res.raw != nil {
			t.Errorf("an upstream %d answer produced a stream", c.Status)
		}
		contracttest.AssertSaneError(t, c, out.err)
		return
	case out.err != nil:
		t.Fatalf("native passthrough: %v", out.err)
	}
	checkPassedThrough(t, c, res)
	if c.IsError() {
		return // forwarded as is, and not billed
	}
	if c.IsStream {
		events := contracttest.ParseSSE(c.Body)
		contracttest.AssertKnownEventTypes(t, events, knownStreamEvents...)
		checkEventEnvelopes(t, events)
	}
	contracttest.ExpectGaps(t, c, func(t testing.TB) { checkNativeBilling(t, c, res.Billing) })
}

// upstreamUsage is what the upstream says a response used.
type upstreamUsage struct {
	model                                                   string
	input, output, cacheRead, cacheWrite                    int
	sawInput, sawOutput, sawCacheRead, sawCacheWrite, ended bool
}

// add takes the counts present in a usage object; for a stream, later
// events (message_delta's cumulative usage) override earlier ones.
func (u *upstreamUsage) add(raw json.RawMessage) {
	var v struct {
		InputTokens              *int `json:"input_tokens"`
		OutputTokens             *int `json:"output_tokens"`
		CacheReadInputTokens     *int `json:"cache_read_input_tokens"`
		CacheCreationInputTokens *int `json:"cache_creation_input_tokens"`
	}
	if json.Unmarshal(raw, &v) != nil {
		return
	}
	set := func(dst *int, saw *bool, src *int) {
		if src != nil {
			*dst, *saw = *src, true
		}
	}
	set(&u.input, &u.sawInput, v.InputTokens)
	set(&u.output, &u.sawOutput, v.OutputTokens)
	set(&u.cacheRead, &u.sawCacheRead, v.CacheReadInputTokens)
	set(&u.cacheWrite, &u.sawCacheWrite, v.CacheCreationInputTokens)
}

func readUpstreamUsage(c contracttest.Case) upstreamUsage {
	var u upstreamUsage
	if !c.IsStream {
		var m struct {
			Model string          `json:"model"`
			Usage json.RawMessage `json:"usage"`
		}
		_ = json.Unmarshal(c.Body, &m)
		u.model = m.Model
		u.add(m.Usage)
		u.ended = true
		return u
	}
	for _, ev := range contracttest.ParseSSE(c.Body) {
		var e struct {
			Usage   json.RawMessage `json:"usage"`
			Message struct {
				Model string          `json:"model"`
				Usage json.RawMessage `json:"usage"`
			} `json:"message"`
		}
		if json.Unmarshal(ev.Data, &e) != nil {
			continue
		}
		switch ev.Name {
		case "message_start":
			u.model = e.Message.Model
			u.add(e.Message.Usage)
		case "message_delta":
			u.add(e.Usage)
			u.ended = true
		}
	}
	return u
}

// checkNativeBilling checks the billing against the usage the upstream
// reported. Anthropic's input_tokens excludes cache reads and writes
// (build-with-claude/prompt-caching: total input = cache_read_input_tokens +
// cache_creation_input_tokens + input_tokens), so billing input_tokens alone
// under-bills a cached request.
func checkNativeBilling(t testing.TB, c contracttest.Case, b *nativeBilling) {
	t.Helper()
	if b == nil {
		t.Errorf("a 2xx passthrough was not billed")
		return
	}
	u := readUpstreamUsage(c)
	if u.input+u.cacheRead+u.cacheWrite == 0 || u.output == 0 {
		t.Errorf("the upstream reports no usage to bill (input_tokens %d, output_tokens %d): a renamed usage block bills nothing", u.input, u.output)
	}
	if b.Model != u.model {
		t.Errorf("billed model %q; the upstream answered with %q", b.Model, u.model)
	}
	if b.OutputTokens != u.output {
		t.Errorf("billed %d output tokens; the upstream reported %d", b.OutputTokens, u.output)
	}
	switch total := u.input + u.cacheRead + u.cacheWrite; b.InputTokens {
	case total:
	case u.input:
		t.Errorf("billed %d input tokens: input_tokens only, leaving out cache_read_input_tokens %d and cache_creation_input_tokens %d",
			b.InputTokens, u.cacheRead, u.cacheWrite)
	default:
		t.Errorf("billed %d input tokens; the upstream's input is %d (input_tokens %d, cache reads %d, cache writes %d)",
			b.InputTokens, total, u.input, u.cacheRead, u.cacheWrite)
	}
	if c.IsStream {
		if b.UsageFound == nil || !*b.UsageFound {
			t.Errorf("the stream usage scanner found no usage")
		}
		if u.ended && (b.Finished == nil || !*b.Finished) {
			t.Errorf("the stream reached message_delta, but the scanner says it did not finish, so its output is estimated")
		}
	}
}

// checkListedModels checks ListModels returns what the upstream lists.
func checkListedModels(t testing.TB, c contracttest.Case, out caseOutput) {
	t.Helper()
	var list struct {
		Data []struct {
			ID string `json:"id"`
		} `json:"data"`
	}
	if err := json.Unmarshal(c.Body, &list); err != nil {
		t.Errorf("the upstream's list is not a model list: %v", err)
		return
	}
	if len(out.models) != len(list.Data) {
		t.Errorf("ListModels returned %d models (after %d upstream requests); the upstream lists %d",
			len(out.models), len(out.upstream.Requests()), len(list.Data))
		return
	}
	for i, m := range out.models {
		if m.ID != list.Data[i].ID {
			t.Errorf("ListModels model %d is %q; the upstream lists %q", i, m.ID, list.Data[i].ID)
		}
	}
}
