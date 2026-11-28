// Package trajectorycontract builds the shared writer/reader contract fixtures
// for the trajectory store: a corpus of span payloads run through the real
// span converter with deduplication on, plus the dedup-off conversion of the
// same spans as the byte oracle.
//
// The output lives in futureagi/tracer/tests/fixtures/trajectory_contract/:
//
//   - payloads.jsonl: per case, the exact input/output the dedup-off path
//     stores in spans.input/output, and which sides the dedup path extracts;
//   - spans.jsonl: the dedup-on span rows, encoded as the ClickHouse writer
//     sends them (JSONEachRow, HTML escaping off);
//   - items.jsonl: the trajectory_items rows those spans reference.
//
// The Go drift test regenerates them; the Python tests hydrate spans.jsonl
// from items.jsonl and require payloads.jsonl byte for byte.
package trajectorycontract

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"sort"
	"strings"
	"time"

	chexp "github.com/future-agi/future-agi/fi-collector/exporter/clickhouse25exporter"
	"github.com/future-agi/future-agi/fi-collector/pkg/trajectory"
	"go.opentelemetry.io/collector/pdata/pcommon"
	"go.opentelemetry.io/collector/pdata/ptrace"
)

// Fixture file names inside FixtureDir.
const (
	PayloadsFile = "payloads.jsonl"
	SpansFile    = "spans.jsonl"
	ItemsFile    = "items.jsonl"
)

// FixtureDir is the fixture directory relative to the repository root.
const FixtureDir = "futureagi/tracer/tests/fixtures/trajectory_contract"

const (
	// ProjectID and OrgID own every fixture span.
	ProjectID = "6f1c2a52-8d0e-4b6a-9a3f-5d2e7c1b4a90"
	OrgID     = "0b7e4d21-3c5a-4f8e-b6d9-2a1c8e7f5b30"
	// fixedVersion replaces the wall-clock _version so the rows are stable.
	fixedVersion = uint64(1_790_000_000_000_000_000)
)

var (
	baseTime    = time.Date(2026, 11, 20, 12, 0, 0, 0, time.UTC)
	persistedAt = time.Date(2026, 11, 20, 12, 30, 0, 0, time.UTC)
)

// Case is one corpus span.
type Case struct {
	Name    string
	Session string // session.id attribute; empty scopes items by trace id
	// Input and Output are the input.value / output.value string attributes.
	Input  string
	Output string
	// InputMap, when set, is sent as a map-valued input.value instead.
	InputMap map[string]any
	// Attrs are extra string attributes (flattened GenAI keys and the like).
	Attrs map[string]string
	// WantInput and WantOutput say which sides the dedup path must extract.
	WantInput, WantOutput bool
}

func pretty(v any) string {
	b, err := json.MarshalIndent(v, "", "  ")
	if err != nil {
		panic(err)
	}
	return string(b)
}

// Corpus is the fixed case list. Changing it changes the fixtures.
func Corpus() []Case {
	longHistory := []any{map[string]any{"role": "system", "content": "You are a careful agent. " + strings.Repeat("Follow the policy. ", 20)}}
	for i := 1; i <= 6; i++ {
		longHistory = append(longHistory,
			map[string]any{"role": "user", "content": fmt.Sprintf("Question %d: %s", i, strings.Repeat("details ", 12))},
			map[string]any{"role": "assistant", "content": fmt.Sprintf("Answer %d: %s", i, strings.Repeat("reasoning ", 12))},
		)
	}
	longHistory = append(longHistory, map[string]any{"role": "user", "content": "Latest question that the preview should surface"})

	return []Case{
		{
			Name:      "pretty_printed_two_space",
			Session:   "contract-session-a",
			Input:     pretty(map[string]any{"model": "gpt-4o", "messages": []any{map[string]any{"role": "system", "content": "Be brief."}, map[string]any{"role": "user", "content": "Hello"}}}),
			Output:    pretty(map[string]any{"id": "chatcmpl-1", "choices": []any{map[string]any{"index": 0, "message": map[string]any{"role": "assistant", "content": "Hi"}}}}),
			WantInput: true, WantOutput: true,
		},
		{
			Name:      "crlf_line_endings",
			Session:   "contract-session-a",
			Input:     "{\r\n  \"messages\": [\r\n    {\"role\": \"user\", \"content\": \"line one\"},\r\n    {\"role\": \"assistant\", \"content\": \"line two\"}\r\n  ],\r\n  \"model\": \"gpt-4o\"\r\n}\r\n",
			Output:    "{\"choices\":[{\"index\":0,\"message\":{\"role\":\"assistant\",\"content\":\"ok\"}}]}",
			WantInput: true, WantOutput: true,
		},
		{
			Name:      "messages_before_model_unusual_key_order",
			Input:     `{"messages":[{"content":"order matters","role":"user"}],"temperature":0.7,"model":"gpt-4o-mini","stream":false}`,
			Output:    `[{"content":"kept","role":"assistant"}]`,
			WantInput: true, WantOutput: true,
		},
		{
			Name:      "escapes_and_accents",
			Input:     `{"messages":[{"role":"user","content":"café and café, a\/b, \"quoted\", tab\t, Aß🚀"}]}`,
			Output:    `[{"role":"assistant","content":"résumé \/ résumé"}]`,
			WantInput: true, WantOutput: true,
		},
		{
			Name:      "number_spellings",
			Input:     `{"messages":[{"role":"user","content":"numbers","values":[1e5,1.0,-0,1E+2,12345678901234567890123,0.1000]}],"temperature":1.0,"seed":18446744073709551617}`,
			Output:    `[{"role":"assistant","content":"done","score":2.50}]`,
			WantInput: true, WantOutput: true,
		},
		{
			Name:      "line_and_paragraph_separators",
			Input:     "{\"messages\":[{\"role\":\"user\",\"content\":\"a b c\"}]}",
			Output:    "[{\"role\":\"assistant\",\"content\":\"x y\"}]",
			WantInput: true, WantOutput: true,
		},
		{
			Name:      "duplicate_messages",
			Session:   "contract-session-b",
			Input:     `[{"role":"user","content":"same"},{"role":"user","content":"same"},{"role":"assistant","content":"same"}]`,
			Output:    `[{"role":"assistant","content":"same"}]`,
			WantInput: true, WantOutput: true,
		},
		{
			Name:      "empty_string_key",
			Input:     `{"":"empty key value","messages":[{"role":"user","content":"hi"}]}`,
			Output:    "plain text reply",
			WantInput: true, WantOutput: false,
		},
		{
			Name:      "anthropic_system_array_and_tools",
			Session:   "contract-session-c",
			Input:     `{"model":"claude-sonnet-4-6","system":[{"type":"text","text":"You are a coding agent."},{"type":"text","text":"Use tools.","cache_control":{"type":"ephemeral"}}],"tools":[{"name":"read_file","description":"Read a file","input_schema":{"type":"object","properties":{"path":{"type":"string"}}}},{"name":"write_file","description":"Write a file","input_schema":{"type":"object"}}],"messages":[{"role":"user","content":[{"type":"text","text":"Fix main.go"}]}],"max_tokens":1024}`,
			Output:    `{"id":"msg_1","type":"message","role":"assistant","content":[{"type":"text","text":"Reading it."}]}`,
			WantInput: true, WantOutput: false,
		},
		{
			Name:      "responses_instructions_and_input",
			Input:     `{"model":"gpt-5","instructions":"Answer in one line.","input":[{"role":"user","content":"Ping"},{"type":"function_call_output","call_id":"c1","output":"{\"ok\":true}"}]}`,
			Output:    `[{"type":"message","role":"assistant","content":[{"type":"output_text","text":"Pong"}]}]`,
			WantInput: true, WantOutput: true,
		},
		{
			Name:      "choices_output_with_tool_calls",
			Session:   "contract-session-c",
			Input:     `{"model":"gpt-4o","tools":[{"type":"function","function":{"name":"get_weather","parameters":{"type":"object"}}}],"messages":[{"role":"user","content":"Weather?"},{"role":"assistant","content":null,"tool_calls":[{"id":"call_1","type":"function","function":{"name":"get_weather","arguments":"{\"city\":\"Paris\"}"}}]},{"role":"tool","tool_call_id":"call_1","content":"18C"}]}`,
			Output:    `{"id":"chatcmpl-2","choices":[{"index":0,"message":{"role":"assistant","content":"It is 18C."},"finish_reason":"stop"}],"usage":{"prompt_tokens":10,"completion_tokens":5}}`,
			WantInput: true, WantOutput: true,
		},
		{
			Name:      "leading_and_trailing_whitespace",
			Input:     "\n\t  [{\"role\":\"user\",\"content\":\"padded\"}]  \n",
			Output:    "  {\"choices\":[{\"text\":\"completion\"}]}\t",
			WantInput: true, WantOutput: true,
		},
		{
			Name:   "non_utf8_stays_inline",
			Input:  "{\"messages\":[{\"role\":\"user\",\"content\":\"bad \xff\xfe bytes\"}]}",
			Output: "[{\"role\":\"assistant\",\"content\":\"also \xc3\x28 bad\"}]",
		},
		{
			Name:      "long_history_over_preview_limit",
			Session:   "contract-session-d",
			Input:     pretty(map[string]any{"model": "gpt-4o", "messages": longHistory}),
			Output:    `[{"role":"assistant","content":"` + strings.Repeat("long answer ", 60) + `"}]`,
			WantInput: true, WantOutput: true,
		},
		{
			Name:   "plain_text_both_sides",
			Input:  "What is the capital of France?",
			Output: "Paris.",
		},
		{
			Name:   "non_conversation_json",
			Input:  `{"sql":"SELECT 1","params":[1,2]}`,
			Output: `{"rows":[[1]]}`,
		},
		{
			Name:      "map_valued_input_attribute",
			InputMap:  map[string]any{"model": "gpt-4o", "messages": []any{map[string]any{"role": "user", "content": "from a map attribute"}}},
			Output:    `[{"role":"assistant","content":"map reply"}]`,
			WantInput: true, WantOutput: true,
		},
		{
			Name:    "flattened_genai_attributes",
			Session: "contract-session-e",
			Attrs: map[string]string{
				"gen_ai.input.messages":   `[{"role":"user","parts":[{"type":"text","content":"Summarise this"}]}]`,
				"gen_ai.output.messages":  `[{"role":"assistant","parts":[{"type":"text","content":"Summary"}],"finish_reason":"stop"}]`,
				"gen_ai.prompt.0.role":    "user",
				"gen_ai.prompt.0.content": "Summarise this",
				"gen_ai.request.model":    "gpt-4o",
			},
			WantInput: true, WantOutput: true,
		},
		{
			Name:      "media_data_uri",
			Input:     `{"messages":[{"role":"user","content":[{"type":"text","text":"What is in this image?"},{"type":"image_url","image_url":{"url":"data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="}}]}]}`,
			Output:    `[{"role":"assistant","content":"A single pixel."}]`,
			WantInput: true, WantOutput: true,
		},
		{
			// Raw string literals: the payload carries the six-character
			// escapes, not the decoded characters. Appended last so the ids of
			// every earlier case stay the same.
			Name:      "unicode_escape_sequences",
			Session:   "contract-session-f",
			Input:     `{"messages":[{"role":"user","content":"caf\u00e9 vs caf\u00C9, rocket \ud83d\ude80, a\u2028b\u2029c, \u0041\u002f"}],"model":"gpt-4o"}`,
			Output:    `[{"role":"assistant","content":"r\u00e9sum\u00e9 \ud83d\ude00 line\u2028break"}]`,
			WantInput: true, WantOutput: true,
		},
	}
}

func buildTraces(index int, c Case) ptrace.Traces {
	td := ptrace.NewTraces()
	rs := td.ResourceSpans().AppendEmpty()
	rs.Resource().Attributes().PutStr("service.name", "trajectory-contract")
	rs.Resource().Attributes().PutStr("fi.project_id", ProjectID)
	rs.Resource().Attributes().PutStr("fi.org_id", OrgID)
	rs.Resource().Attributes().PutStr("project_type", "observe")
	sp := rs.ScopeSpans().AppendEmpty().Spans().AppendEmpty()
	var tid [16]byte
	tid[0], tid[1], tid[15] = 0xc0, byte(index+1), 0x01
	var sid [8]byte
	sid[0], sid[1], sid[7] = 0x5a, byte(index+1), 0x02
	sp.SetTraceID(pcommon.TraceID(tid))
	sp.SetSpanID(pcommon.SpanID(sid))
	sp.SetName("llm.chat")
	start := baseTime.Add(time.Duration(index) * time.Second)
	sp.SetStartTimestamp(pcommon.NewTimestampFromTime(start))
	sp.SetEndTimestamp(pcommon.NewTimestampFromTime(start.Add(250 * time.Millisecond)))
	attrs := sp.Attributes()
	attrs.PutStr("fi.span.kind", "LLM")
	if c.Session != "" {
		attrs.PutStr("session.id", c.Session)
	}
	if c.InputMap != nil {
		if err := attrs.PutEmptyMap("input.value").FromRaw(c.InputMap); err != nil {
			panic(err)
		}
	} else if c.Input != "" {
		attrs.PutStr("input.value", c.Input)
	}
	if c.Output != "" {
		attrs.PutStr("output.value", c.Output)
	}
	keys := make([]string, 0, len(c.Attrs))
	for k := range c.Attrs {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	for _, k := range keys {
		attrs.PutStr(k, c.Attrs[k])
	}
	return td
}

// Payload is one payloads.jsonl line.
type Payload struct {
	Case            string `json:"case"`
	ProjectID       string `json:"project_id"`
	TraceID         string `json:"trace_id"`
	SpanID          string `json:"span_id"`
	Input           string `json:"input"`
	Output          string `json:"output"`
	InputExtracted  bool   `json:"input_extracted"`
	OutputExtracted bool   `json:"output_extracted"`
}

// Files is the generated fixture content by file name.
type Files map[string][]byte

// Generate converts the corpus and returns the three fixture files. It fails
// when a case is extracted differently from its expectation, or when an
// extracted side does not reconstruct to the dedup-off bytes.
func Generate() (Files, error) {
	ctx := context.Background()
	on := chexp.ConvertOptions{Trajectory: trajectory.Config{Mode: trajectory.ModeOn}.Normalize()}
	var payloads, spans, items bytes.Buffer
	seenItems := make(map[trajectory.ItemKey]struct{})
	for i, c := range Corpus() {
		dev, err := chexp.ConvertWithOptions(ctx, buildTraces(i, c), nil, chexp.ConvertOptions{})
		if err != nil || len(dev.Rows) != 1 {
			return nil, fmt.Errorf("%s: dedup-off conversion: %v (%d rows)", c.Name, err, len(dev.Rows))
		}
		res, err := chexp.ConvertWithOptions(ctx, buildTraces(i, c), nil, on)
		if err != nil || len(res.Rows) != 1 {
			return nil, fmt.Errorf("%s: dedup-on conversion: %v (%d rows)", c.Name, err, len(res.Rows))
		}
		devRow, row := dev.Rows[0], res.Rows[0]
		devRow["_version"], row["_version"] = fixedVersion, fixedVersion
		wantIn, _ := devRow["input"].(string)
		wantOut, _ := devRow["output"].(string)

		var caseItems []trajectory.Item
		if res.Trajectory != nil {
			caseItems = res.Trajectory.Items()
		}
		gotIn, gotOut, err := hydrateRow(row, caseItems)
		if err != nil {
			return nil, fmt.Errorf("%s: %w", c.Name, err)
		}
		inExtracted, outExtracted := gotIn != nil, gotOut != nil
		if inExtracted != c.WantInput || outExtracted != c.WantOutput {
			return nil, fmt.Errorf("%s: extracted in=%v out=%v, want in=%v out=%v", c.Name, inExtracted, outExtracted, c.WantInput, c.WantOutput)
		}
		if inExtracted && *gotIn != wantIn {
			return nil, fmt.Errorf("%s: input reconstructs to %q, dev stores %q", c.Name, *gotIn, wantIn)
		}
		if outExtracted && *gotOut != wantOut {
			return nil, fmt.Errorf("%s: output reconstructs to %q, dev stores %q", c.Name, *gotOut, wantOut)
		}
		if !inExtracted && row["input"] != wantIn || !outExtracted && row["output"] != wantOut {
			return nil, fmt.Errorf("%s: an inline side differs from dev", c.Name)
		}

		traceID, _ := row["trace_id"].(string)
		spanID, _ := row["id"].(string)
		if err := encodeLine(&payloads, Payload{
			Case: c.Name, ProjectID: ProjectID, TraceID: traceID, SpanID: spanID,
			Input: wantIn, Output: wantOut, InputExtracted: inExtracted, OutputExtracted: outExtracted,
		}); err != nil {
			return nil, err
		}
		if err := encodeLine(&spans, row); err != nil {
			return nil, err
		}
		for _, it := range caseItems {
			if _, dup := seenItems[it.Key()]; dup {
				continue
			}
			seenItems[it.Key()] = struct{}{}
			if err := encodeLine(&items, it.ToRow(persistedAt)); err != nil {
				return nil, err
			}
		}
	}
	return Files{PayloadsFile: payloads.Bytes(), SpansFile: spans.Bytes(), ItemsFile: items.Bytes()}, nil
}

// hydrateRow rebuilds the extracted sides of one span row from its pointer
// columns and items, as a reader does. A nil side was not extracted.
func hydrateRow(row map[string]any, items []trajectory.Item) (*string, *string, error) {
	if row["payload_encoding"] != trajectory.EncodingItemsV1 {
		return nil, nil, nil
	}
	envJSON, _ := row["payload_envelope"].(string)
	env, err := trajectory.ParseEnvelope(envJSON)
	if err != nil {
		return nil, nil, err
	}
	scope, _ := row["item_scope_id"].(string)
	resolve := trajectory.ItemsResolver(scope, items)
	inRefs, _ := row["input_item_hashes"].([]string)
	outRefs, _ := row["output_item_hashes"].([]string)
	toolRefs, _ := row["tool_def_hashes"].([]string)
	var in, out *string
	if env.Input != nil {
		s, err := trajectory.ReconstructSide(env.Input, inRefs, toolRefs, resolve)
		if err != nil {
			return nil, nil, fmt.Errorf("input: %w", err)
		}
		in = &s
	}
	if env.Output != nil {
		s, err := trajectory.ReconstructSide(env.Output, outRefs, nil, resolve)
		if err != nil {
			return nil, nil, fmt.Errorf("output: %w", err)
		}
		out = &s
	}
	return in, out, nil
}

// encodeLine appends v as one JSON line, encoded like the ClickHouse writer
// encodes JSONEachRow rows.
func encodeLine(buf *bytes.Buffer, v any) error {
	enc := json.NewEncoder(buf)
	enc.SetEscapeHTML(false)
	return enc.Encode(v)
}
