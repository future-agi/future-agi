package toolview

import (
	"bytes"
	"context"
	"encoding/json"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"strings"
	"testing"
)

func TestNativeSnapshotTransactionalFidelity(t *testing.T) {
	ctx := context.Background()
	fixtures := []struct {
		make func([]byte) *NativeRequest
		body string
	}{
		{Anthropic, `{ "messages": [], "tools": [ {"name":"keep","input_schema":{}}, {"name":"drop","input_schema":{}} ], "opaque": {"id":"untouched"} }`},
		{Responses, `{ "input":"hello", "tools":[{"type":"function","name":"keep"},{"type":"function","name":"drop"}], "opaque": {"id":"untouched"} }`},
		{func(b []byte) *NativeRequest { return Gemini(b, "model", false) }, `{ "contents":[], "tools":[{"functionDeclarations":[{"name":"keep"},{"name":"drop"}],"googleSearch":{}}], "opaque": {"id":"untouched"} }`},
	}
	for _, f := range fixtures {
		input := []byte(f.body)
		r := f.make(input)
		input[0] = '!'
		before := r.Body()
		copied := r.Body()
		copied[0] = '!'
		if !bytes.Equal(r.Body(), before) {
			t.Fatal("body aliases owner")
		}
		s, err := r.ToolSnapshot(ctx)
		if err != nil {
			t.Fatal(err)
		}
		var keep []ToolID
		for i := 0; i < s.Len(); i++ {
			d, _ := s.Tool(i)
			if d.PolicyName() != "drop" {
				keep = append(keep, d.ID())
			}
		}
		if err := r.ApplyToolFilter(ctx, s, keep); err != nil {
			t.Fatal(err)
		}
		if !json.Valid(r.Body()) || bytes.Contains(r.Body(), []byte(`"name":"drop"`)) || !bytes.Contains(r.Body(), []byte(`"opaque": {"id":"untouched"}`)) {
			t.Fatal("filter fidelity")
		}
		if err := r.ApplyToolFilter(ctx, s, keep); err == nil {
			t.Fatal("stale snapshot accepted")
		}
		other := f.make(before)
		if err := other.ApplyToolFilter(ctx, s, keep); err == nil {
			t.Fatal("foreign snapshot accepted")
		}
	}
}

func TestNativeCheckedRewriteSurvivesFilter(t *testing.T) {
	ctx := context.Background()
	r := Anthropic([]byte(`{"messages":[{"role":"user","content":"before"}],"tools":[{"name":"drop","input_schema":{}}]}`))
	old, err := r.ToolSnapshot(ctx)
	if err != nil {
		t.Fatal(err)
	}
	chat, why := r.ChatRequest(ctx)
	if why != "" || chat == nil {
		t.Fatal("view unreadable")
	}
	chat.Messages[0].Content = json.RawMessage(`"after"`)
	if err := r.ApplyChecks(); err != nil {
		t.Fatal(err)
	}
	if err := r.ApplyToolFilter(ctx, old, nil); err == nil {
		t.Fatal("rewrite did not invalidate")
	}
	s, err := r.ToolSnapshot(ctx)
	if err != nil {
		t.Fatal(err)
	}
	if err := r.ApplyToolFilter(ctx, s, nil); err != nil {
		t.Fatal(err)
	}
	if !bytes.Contains(r.Body(), []byte(`"after"`)) || bytes.Contains(r.Body(), []byte(`"before"`)) {
		t.Fatal("rewrite lost")
	}
}

func TestNativeModelRewritePreservesUntouchedBytes(t *testing.T) {
	body := []byte(" \n{ \"model\" : \"before\", \"opaque\" : { \"image\" : \"synthetic\" } }\n ")
	got, err := RewriteModel(body, "after")
	want := bytes.Replace(body, []byte(`"before"`), []byte(`"after"`), 1)
	if err != nil || !bytes.Equal(got, want) {
		t.Fatal("model rewrite round-tripped native payload")
	}
}

func TestNativeClosedInvalidDeclarations(t *testing.T) {
	for _, body := range []string{
		`{"tools":[{"type":"unrecognized_private_canary","name":"private_canary"}]}`,
		`{"tools":[],"tools":[]}`,
		`{"tools":[{"type":"function","name":"x","name":"y"}]}`,
		`{"tools":false}`,
		`{"tools":[{"type":"function","name":""}]}`,
	} {
		_, err := Responses([]byte(body)).ToolSnapshot(context.Background())
		if err == nil || strings.Contains(err.Error(), "private_canary") {
			t.Fatal("not a closed refusal")
		}
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if _, err := Responses([]byte(`{"tools":[]}`)).ToolSnapshot(ctx); err == nil {
		t.Fatal("cancellation ignored")
	}
}

func TestNativeFilterReferencesAndRequiredSchema(t *testing.T) {
	ctx := context.Background()
	r := Responses([]byte(`{"tools":[{"type":"function","name":"drop"}],"tool_choice":{"type":"function","name":"drop"}}`))
	s, err := r.ToolSnapshot(ctx)
	if err != nil {
		t.Fatal(err)
	}
	before := r.Body()
	if err := r.ApplyToolFilter(ctx, s, nil); err == nil || !bytes.Equal(before, r.Body()) {
		t.Fatal("reference removal committed")
	}
	required := models.ComputerUseRequirement{Required: true, Protocol: models.NativeResponses, Action: "generate", ToolProtocol: "synthetic", VendorVersion: "test"}
	if r.ValidateComputerUse(ctx, required) == nil {
		t.Fatal("missing schema accepted")
	}
}
