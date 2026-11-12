package toolview

import (
	"context"
	"strings"
	"testing"
)

func TestNativeStripRequiredChoiceRetainsFunction(t *testing.T) {
	ctx := context.Background()
	for _, tc := range []struct {
		name   string
		make   func([]byte) *NativeRequest
		fields string
		tools  string
		drop   string
	}{
		{
			name:   "responses-required",
			make:   Responses,
			fields: `"model":"model","input":"hello","tool_choice":"required"`,
			tools:  `[{"type":"function","name":"lookup","parameters":{"type":"object"}},{"type":"function","name":"blocked","parameters":{"type":"object"}}]`,
			drop:   `,{"type":"function","name":"blocked","parameters":{"type":"object"}}`,
		},
		{
			name:   "messages-any",
			make:   Anthropic,
			fields: `"model":"model","max_tokens":64,"messages":[{"role":"user","content":"hello"}],"tool_choice":{"type":"any"}`,
			tools:  `[{"name":"lookup","input_schema":{"type":"object"}},{"name":"blocked","input_schema":{"type":"object"}}]`,
			drop:   `,{"name":"blocked","input_schema":{"type":"object"}}`,
		},
		{
			name:   "messages-any-serial",
			make:   Anthropic,
			fields: `"model":"model","max_tokens":64,"messages":[{"role":"user","content":"hello"}],"tool_choice":{"type":"any","disable_parallel_tool_use":true}`,
			tools:  `[{"name":"lookup","input_schema":{"type":"object"}},{"name":"blocked","input_schema":{"type":"object"}}]`,
			drop:   `,{"name":"blocked","input_schema":{"type":"object"}}`,
		},
		{
			name:   "messages-any-parallel",
			make:   Anthropic,
			fields: `"model":"model","max_tokens":64,"messages":[{"role":"user","content":"hello"}],"tool_choice":{"type":"any","disable_parallel_tool_use":false}`,
			tools:  `[{"name":"lookup","input_schema":{"type":"object"}},{"name":"blocked","input_schema":{"type":"object"}}]`,
			drop:   `,{"name":"blocked","input_schema":{"type":"object"}}`,
		},
	} {
		for _, mode := range []struct {
			name  string
			count int
		}{
			{"retain-one", 1},
			{"remove-all", 0},
			{"retain-all", 2},
		} {
			t.Run(tc.name+"/"+mode.name, func(t *testing.T) {
				body := " \n{" + tc.fields + `,"tools":` + tc.tools + `,"opaque":{"n":1e+03,"name":"blocked"}}` + "\n "
				r := tc.make([]byte(body))
				s, err := r.ToolSnapshot(ctx)
				if err != nil || s.Len() != 2 {
					t.Fatalf("snapshot: count=%d, error=%v", s.Len(), err)
				}
				var keep []ToolID
				for i := 0; i < mode.count; i++ {
					d, _ := s.Tool(i)
					if d.Kind() != Function {
						t.Fatal("fixture is not a supported function declaration")
					}
					keep = append(keep, d.ID())
				}
				generation := r.Generation()
				err = r.ApplyToolFilter(ctx, s, keep)
				if mode.count == 0 {
					if err == nil || string(r.Body()) != body || r.Generation() != generation {
						t.Fatal("unsatisfiable required choice was not refused transactionally")
					}
					return
				}
				if err != nil {
					t.Fatalf("required choice with a retained function was refused: %v", err)
				}
				want := body
				if mode.count == 1 {
					want = strings.Replace(body, tc.drop, "", 1)
					generation++
				}
				if string(r.Body()) != want || r.Generation() != generation {
					t.Fatal("filter changed the selector or unrelated bytes, or committed incorrectly")
				}
				after, err := r.ToolSnapshot(ctx)
				if err != nil || after.Len() != mode.count {
					t.Fatalf("filtered snapshot: count=%d, error=%v", after.Len(), err)
				}
			})
		}
	}
}

func TestNativeStripRequiredChoiceStillRefusesUnresolvedReferences(t *testing.T) {
	for _, tc := range []struct {
		name     string
		messages bool
		fields   string
	}{
		{"responses-previous-response", false, `"tool_choice":"required","input":"hello","previous_response_id":"stored"`},
		{"responses-conversation", false, `"tool_choice":"required","input":"hello","conversation":"stored"`},
		{"responses-removed-call", false, `"tool_choice":"required","input":[{"type":"function_call","name":"drop","call_id":"c1","arguments":"{}"}]`},
		{"responses-id-only-output", false, `"tool_choice":"required","input":[{"type":"function_call_output","call_id":"c1","output":"hello"}]`},
		{"messages-removed-call", true, `"tool_choice":{"type":"any"},"messages":[{"role":"assistant","content":[{"type":"tool_use","name":"drop","id":"c1","input":{}}]}]`},
		{"messages-id-only-result", true, `"tool_choice":{"type":"any"},"messages":[{"role":"user","content":[{"type":"tool_result","tool_use_id":"c1","content":"hello"}]}]`},
		{"messages-any-with-name", true, `"tool_choice":{"type":"any","name":"keep"}`},
		{"messages-any-unknown-field", true, `"tool_choice":{"type":"any","future_option":true}`},
		{"messages-any-invalid-parallel", true, `"tool_choice":{"type":"any","disable_parallel_tool_use":"true"}`},
	} {
		t.Run(tc.name, func(t *testing.T) {
			assertNonToolStrip(t, tc.messages, tc.fields, true)
		})
	}
}
