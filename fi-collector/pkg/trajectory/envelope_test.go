package trajectory

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"strings"
	"testing"
	"unicode/utf8"
	"unsafe"
)

const (
	envTestProject = "11111111-1111-4111-8111-111111111111"
	envTestSession = "22222222-2222-4222-8222-222222222222"
	envTestTrace   = "33333333-3333-4333-8333-333333333333"
)

func mustParseEnvelope(t testing.TB, ext ExtractionResult) Envelope {
	t.Helper()
	env, err := ParseEnvelope(ext.EnvelopeJSON)
	if err != nil {
		t.Fatalf("ParseEnvelope(%s): %v", ext.EnvelopeJSON, err)
	}
	return env
}

// requireRoundTrip rebuilds every extracted side from the stored envelope and
// items, as a reader does, and requires the exact original strings.
func requireRoundTrip(t testing.TB, ext ExtractionResult, input, output string) {
	t.Helper()
	env := mustParseEnvelope(t, ext)
	resolve := ItemsResolver(ext.ItemScopeID, ext.Items)
	if (env.Input != nil) != ext.InputApplied || (env.Output != nil) != ext.OutputApplied {
		t.Fatalf("applied flags %v/%v disagree with envelope %s", ext.InputApplied, ext.OutputApplied, ext.EnvelopeJSON)
	}
	if env.Input != nil {
		got, err := ReconstructSide(env.Input, ext.InputHashes, ext.ToolDefHashes, resolve)
		if err != nil || got != input {
			t.Fatalf("input round trip failed (err=%v)\n got=%q\nwant=%q", err, got, input)
		}
	} else if ext.InputPreview != input {
		t.Fatalf("unextracted input side was changed: %q", ext.InputPreview)
	}
	if env.Output != nil {
		got, err := ReconstructSide(env.Output, ext.OutputHashes, nil, resolve)
		if err != nil || got != output {
			t.Fatalf("output round trip failed (err=%v)\n got=%q\nwant=%q", err, got, output)
		}
	} else if ext.OutputPreview != output {
		t.Fatalf("unextracted output side was changed: %q", ext.OutputPreview)
	}
}

func fiftyTurnPayloads() (inputs, outputs []string) {
	systemPrompt := "You are an autonomous software engineering agent. " + strings.Repeat("Rule: always write tests. ", 40)
	tools := make([]map[string]any, 15)
	for i := range tools {
		tools[i] = map[string]any{"type": "function", "function": map[string]any{
			"name":        fmt.Sprintf("tool_%02d", i),
			"description": strings.Repeat(fmt.Sprintf("Tool %d documentation. ", i), 15),
		}}
	}
	msgs := []map[string]any{{"role": "system", "content": systemPrompt}}
	for turn := 1; turn <= 50; turn++ {
		msgs = append(msgs, map[string]any{"role": "user", "content": fmt.Sprintf("Turn %02d %s", turn, strings.Repeat("context ", 30))})
		in, _ := json.MarshalIndent(map[string]any{"model": "claude-sonnet-4-6", "tools": tools, "messages": msgs}, "", "  ")
		reply := map[string]any{"role": "assistant", "content": fmt.Sprintf("Turn %02d reply %s", turn, strings.Repeat("reasoning ", 30))}
		out, _ := json.Marshal(map[string]any{"choices": []any{map[string]any{"index": 0, "message": reply}}})
		inputs = append(inputs, string(in))
		outputs = append(outputs, string(out))
		msgs = append(msgs, reply)
	}
	return inputs, outputs
}

// Item.Content must be a substring of the span's own input/output, so the
// pending buffer holds one copy of the payload, as dev does.
func TestExtractSpanItemsShareInputBacking(t *testing.T) {
	inputs, outputs := fiftyTurnPayloads()
	input, output := inputs[len(inputs)-1], outputs[len(outputs)-1]
	ext := ExtractSpan(envTestProject, envTestSession, envTestTrace, input, output, DefaultPreviewMaxBytes)
	if !ext.InputApplied || !ext.OutputApplied {
		t.Fatalf("expected both sides extracted")
	}
	within := func(content, backing string) bool {
		if len(content) == 0 {
			return true
		}
		lo := uintptr(unsafe.Pointer(unsafe.StringData(backing)))
		p := uintptr(unsafe.Pointer(unsafe.StringData(content)))
		return p >= lo && p+uintptr(len(content)) <= lo+uintptr(len(backing))
	}
	if len(ext.Items) < 100 {
		t.Fatalf("expected the full history as items, got %d", len(ext.Items))
	}
	for i, it := range ext.Items {
		if !within(it.Content, input) && !within(it.Content, output) {
			t.Fatalf("item %d (%s/%s) content was copied out of the payload", i, it.ItemType, it.Role)
		}
	}
	requireRoundTrip(t, ext, input, output)
}

func BenchmarkExtractSpan50Turn(b *testing.B) {
	inputs, outputs := fiftyTurnPayloads()
	input, output := inputs[len(inputs)-1], outputs[len(outputs)-1]
	b.ReportAllocs()
	b.SetBytes(int64(len(input) + len(output)))
	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		if ext := ExtractSpan(envTestProject, envTestSession, envTestTrace, input, output, DefaultPreviewMaxBytes); !ext.Applied {
			b.Fatal("not extracted")
		}
	}
}

// The v1 wrapper map skipped any key equal to an unset tools/system key, so
// a "" key was silently dropped. The v2 gaps keep every byte.
func TestExtractSpanEmptyKeyRoundTrip(t *testing.T) {
	input := `{"":1,"messages":[{"role":"user","content":"hi"}]}`
	ext := ExtractSpan(envTestProject, envTestSession, envTestTrace, input, "", 128)
	if !ext.InputApplied {
		t.Fatalf("expected the input side extracted")
	}
	requireRoundTrip(t, ext, input, "")
	env := mustParseEnvelope(t, ext)
	if env.Input.Gaps[0] != `{"":1,"messages":[` {
		t.Fatalf("first gap = %q", env.Input.Gaps[0])
	}
}

func TestExtractSpanRefusesWhenReconstructMismatch(t *testing.T) {
	input := `{"model":"m","messages":[{"role":"user","content":"hi"}]}`
	output := `[{"role":"assistant","content":"hello"}]`
	t.Cleanup(func() { extractFaultHook = nil })

	extractFaultHook = func(side string, tmpl *SideTemplate) {
		if side == "in" {
			tmpl.Gaps[0] = strings.Replace(tmpl.Gaps[0], `"m"`, `"x"`, 1)
		}
	}
	ext := ExtractSpan(envTestProject, envTestSession, envTestTrace, input, output, 128)
	if ext.InputApplied || !ext.OutputApplied {
		t.Fatalf("a corrupted input template must leave only that side inline: in=%v out=%v", ext.InputApplied, ext.OutputApplied)
	}
	if ext.InputPreview != input || len(ext.InputHashes) != 0 || len(ext.ToolDefHashes) != 0 {
		t.Fatalf("inline side carries pointers: %+v", ext)
	}
	for _, it := range ext.Items {
		if it.Role == "user" {
			t.Fatalf("items of the refused side were kept: %+v", it)
		}
	}
	requireRoundTrip(t, ext, input, output)

	extractFaultHook = func(side string, tmpl *SideTemplate) { tmpl.Checksum = strings.Repeat("0", 32) }
	if ext := ExtractSpan(envTestProject, envTestSession, envTestTrace, input, output, 128); ext.Applied {
		t.Fatalf("a wrong checksum must refuse both sides, got %s", ext.EnvelopeJSON)
	}
}

func TestEnvelopeCarriesOriginalUTF8Lengths(t *testing.T) {
	input := "{\n  \"messages\": [\n    {\"role\": \"user\", \"content\": \"café \U0001F680   done\"}\n  ]\n}"
	output := `[{"role":"assistant","content":"日本語の返事"}]`
	ext := ExtractSpan(envTestProject, envTestSession, envTestTrace, input, output, 16)
	env := mustParseEnvelope(t, ext)
	if env.Input == nil || env.Output == nil {
		t.Fatalf("expected both sides extracted: %s", ext.EnvelopeJSON)
	}
	if env.Input.Length != utf8.RuneCountInString(input) || env.Input.Length == len(input) {
		t.Fatalf("in.il = %d, want %d code points (byte length %d)", env.Input.Length, utf8.RuneCountInString(input), len(input))
	}
	if env.Output.Length != utf8.RuneCountInString(output) {
		t.Fatalf("out.il = %d, want %d", env.Output.Length, utf8.RuneCountInString(output))
	}
	// The key is "il" inside each side, what the SQL helpers read.
	var raw struct {
		In  map[string]any `json:"in"`
		Out map[string]any `json:"out"`
	}
	if err := json.Unmarshal([]byte(ext.EnvelopeJSON), &raw); err != nil {
		t.Fatal(err)
	}
	if raw.In["il"] != float64(env.Input.Length) || raw.Out["il"] != float64(env.Output.Length) {
		t.Fatalf("il keys missing from %s", ext.EnvelopeJSON)
	}
	requireRoundTrip(t, ext, input, output)
}

func TestEnvelopeCarriesOriginalSha256(t *testing.T) {
	input := `{"messages":[{"role":"user","content":"hi"}],"model":"m"}`
	ext := ExtractSpan(envTestProject, envTestSession, envTestTrace, input, "", 128)
	env := mustParseEnvelope(t, ext)
	sum := sha256.Sum256([]byte(input))
	if want := hex.EncodeToString(sum[:16]); env.Input.Checksum != want {
		t.Fatalf("in.x = %s, want %s", env.Input.Checksum, want)
	}
	resolve := ItemsResolver(ext.ItemScopeID, ext.Items)
	tampered := func(ref string) (string, bool) {
		c, ok := resolve(ref)
		return strings.Replace(c, "hi", "ho", 1), ok
	}
	if _, err := ReconstructSide(env.Input, ext.InputHashes, ext.ToolDefHashes, tampered); err == nil {
		t.Fatalf("a changed item must fail the checksum")
	}
}

func TestExtractSpanByteExactVariants(t *testing.T) {
	cases := map[string]string{
		"pretty_crlf":       "{\r\n  \"model\": \"gpt-4o\",\r\n  \"messages\": [\r\n    { \"role\": \"user\", \"content\": \"a\" },\r\n    { \"role\": \"assistant\", \"content\": \"b\" }\r\n  ]\r\n}",
		"messages_first":    `{"messages":[{"content":"x","role":"user"}],"temperature":0.2,"model":"m"}`,
		"escapes":           `{"messages":[{"role":"user","content":"café vs café, a\/b, \"q\",  "}]}`,
		"numbers":           `{"messages":[{"role":"user","content":"n","n":[1e5,1.0,-0,1E+2,18446744073709551616]}],"top_p":1.0}`,
		"line_separator":    "{\"messages\":[{\"role\":\"user\",\"content\":\"a b c\"}]}",
		"duplicate_message": `[{"role":"user","content":"same"},{"role":"user","content":"same"}]`,
		"surrounding_space": "  \n\t[{\"role\":\"user\",\"content\":\"x\"}]\n  ",
		"tools_between":     `{"tools":[{"type":"function","function":{"name":"f"}}],"model":"m","messages":[{"role":"system","content":"s"},{"role":"user","content":"u"}],"system":[{"type":"text","text":"sys"}]}`,
	}
	for name, input := range cases {
		t.Run(name, func(t *testing.T) {
			ext := ExtractSpan(envTestProject, envTestSession, envTestTrace, input, "", 128)
			if !ext.InputApplied {
				t.Fatalf("expected extraction of %q", input)
			}
			requireRoundTrip(t, ext, input, "")
		})
	}
	ext := ExtractSpan(envTestProject, envTestSession, envTestTrace, cases["duplicate_message"], "", 128)
	if len(ext.InputHashes) != 2 || ext.InputHashes[0] != ext.InputHashes[1] {
		t.Fatalf("identical messages must share one hash: %v", ext.InputHashes)
	}
}

func TestExtractSpanSidesStayInlineWhenUnsafe(t *testing.T) {
	for name, input := range map[string]string{
		"duplicate_top_level_key": `{"messages":[{"role":"user","content":"a"}],"messages":[{"role":"user","content":"b"}]}`,
		"invalid_utf8":            "{\"messages\":[{\"role\":\"user\",\"content\":\"\xff\xfe\"}]}",
		"trailing_garbage":        `{"messages":[{"role":"user","content":"a"}]} x`,
		"two_values":              `[{"role":"user","content":"a"}][{"role":"user","content":"b"}]`,
		"plain_text":              "just text",
	} {
		t.Run(name, func(t *testing.T) {
			ext := ExtractSpan(envTestProject, envTestSession, envTestTrace, input, "", 128)
			if ext.Applied {
				t.Fatalf("expected inline, got envelope %s", ext.EnvelopeJSON)
			}
		})
	}
}

func TestExtractSpanSidesAreIndependent(t *testing.T) {
	input := "plain question"
	output := `[{"role":"assistant","content":"answer"}]`
	ext := ExtractSpan(envTestProject, envTestSession, envTestTrace, input, output, 128)
	if ext.InputApplied || !ext.OutputApplied || ext.InputPreview != input {
		t.Fatalf("want only output extracted: %+v", ext)
	}
	env := mustParseEnvelope(t, ext)
	if env.Input != nil || env.Output == nil || env.Output.Refs != "m" {
		t.Fatalf("unexpected envelope %s", ext.EnvelopeJSON)
	}
	requireRoundTrip(t, ext, input, output)
}

func TestReconstructSideRejectsMalformedTemplates(t *testing.T) {
	resolve := func(ref string) (string, bool) { return `{"role":"user","content":"x"}`, true }
	good := &SideTemplate{Gaps: []string{"[", "]"}, Refs: "m"}
	good.Length = utf8.RuneCountInString(`[{"role":"user","content":"x"}]`)
	good.Checksum = PayloadChecksum(`[{"role":"user","content":"x"}]`)
	if got, err := ReconstructSide(good, []string{"h"}, nil, resolve); err != nil || got != `[{"role":"user","content":"x"}]` {
		t.Fatalf("good template: %q %v", got, err)
	}
	for name, tc := range map[string]struct {
		tmpl  SideTemplate
		msgs  []string
		tools []string
	}{
		"extra_hash":        {tmpl: *good, msgs: []string{"h", "h"}},
		"missing_tool_hash": {tmpl: SideTemplate{Gaps: []string{"[", "]"}, Refs: "t", Length: good.Length, Checksum: good.Checksum}},
		"system_without_sh": {tmpl: SideTemplate{Gaps: []string{"[", "]"}, Refs: "s", Length: good.Length, Checksum: good.Checksum}},
		"gap_count":         {tmpl: SideTemplate{Gaps: []string{"["}, Refs: "m", Length: good.Length, Checksum: good.Checksum}, msgs: []string{"h"}},
		"unknown_slot":      {tmpl: SideTemplate{Gaps: []string{"[", "]"}, Refs: "q", Length: good.Length, Checksum: good.Checksum}},
		"wrong_length":      {tmpl: SideTemplate{Gaps: good.Gaps, Refs: "m", Length: good.Length + 1, Checksum: good.Checksum}, msgs: []string{"h"}},
	} {
		t.Run(name, func(t *testing.T) {
			tmpl := tc.tmpl
			if _, err := ReconstructSide(&tmpl, tc.msgs, tc.tools, resolve); err == nil {
				t.Fatalf("expected an error")
			}
		})
	}
	if _, err := ParseEnvelope(`{"v":1,"in":{"f":"msg_array"}}`); err == nil {
		t.Fatalf("a v1 envelope must be rejected")
	}
}
