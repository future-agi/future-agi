package toolpolicy

import (
	"bytes"
	"context"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/toolview"
	"testing"
)

func TestPreparedNativeCannotChangeCarrier(t *testing.T) {
	for _, change := range []func(*models.RequestContext){
		func(rc *models.RequestContext) { rc.PassThrough = nil },
		func(rc *models.RequestContext) { rc.Request = &models.ChatCompletionRequest{} },
		func(rc *models.RequestContext) { rc.PassThrough = toolview.Responses([]byte(`{"input":"synthetic"}`)) },
	} {
		p := New(config.ToolPolicyConfig{}, nil)
		rc := models.AcquireRequestContext()
		rc.Request = nil
		rc.PassThrough = toolview.Responses([]byte(`{"input":"synthetic"}`))
		if err := p.PrepareNative(context.Background(), rc); err != nil {
			t.Fatal(err)
		}
		change(rc)
		if err := p.PrepareNative(context.Background(), rc); err == nil {
			t.Fatal("prepared request carrier replaced")
		}
		if result := p.ProcessRequest(context.Background(), rc); result.Error == nil {
			t.Fatal("priority 60 bypassed preparation")
		}
		rc.Release()
	}
}

func TestPrepareNativeIdempotentAndSealed(t *testing.T) {
	p := New(config.ToolPolicyConfig{Enabled: true, Deny: []string{"drop"}}, nil)
	rc := models.AcquireRequestContext()
	defer rc.Release()
	rc.Request = nil
	view := toolview.Responses([]byte(`{"input":"x","tools":[{"type":"function","name":"keep"},{"type":"function","name":"drop"}]}`))
	rc.PassThrough = view
	if err := p.PrepareNative(context.Background(), rc); err != nil {
		t.Fatal(err)
	}
	prepared := view.Body()
	if bytes.Contains(prepared, []byte(`"drop"`)) || rc.Request != nil {
		t.Fatal("native body not filtered or chat fabricated")
	}
	if err := p.PrepareNative(context.Background(), rc); err != nil || !bytes.Equal(prepared, view.Body()) {
		t.Fatal("not idempotent")
	}
	rc.Metadata["auth_denied_tools"] = "keep"
	if err := p.PrepareNative(context.Background(), rc); err == nil {
		t.Fatal("policy reload after quote accepted")
	}
	rc.RunFinalizers()
	if _, ok := p.native.Load(rc); ok {
		t.Fatal("prepared body retained")
	}
}

func TestNativePolicyMatchesChatGates(t *testing.T) {
	for _, enabled := range []bool{false, true} {
		p := New(config.ToolPolicyConfig{Enabled: enabled, DefaultAction: "reject"}, nil)
		rc := models.AcquireRequestContext()
		rc.Request = nil
		rc.Metadata["auth_denied_tools"] = "private_canary"
		rc.PassThrough = toolview.Responses([]byte(`{"tools":[{"type":"function","name":"private_canary"}]}`))
		err := p.PrepareNative(context.Background(), rc)
		if (err != nil) != enabled {
			t.Fatal("key-only gate differs from chat")
		}
		if err != nil && bytes.Contains([]byte(err.Message), []byte("private_canary")) {
			t.Fatal("private tool name escaped")
		}
		rc.Release()
	}
}

func TestNativeSemanticCountUnknownAndCustom(t *testing.T) {
	p := New(config.ToolPolicyConfig{Enabled: true, MaxToolsPerRequest: 1, Deny: []string{"code_execution"}}, nil)
	rc := models.AcquireRequestContext()
	defer rc.Release()
	rc.Request = nil
	rc.PassThrough = toolview.Gemini([]byte(`{"tools":[{"functionDeclarations":[{"name":"a"},{"name":"b"}]}]}`), "model", false)
	if err := p.PrepareNative(context.Background(), rc); err == nil {
		t.Fatal("grouped count ignored")
	}
	rc.PassThrough = toolview.Responses([]byte(`{"tools":[{"type":"unrecognized"}]}`))
	if err := p.PrepareNative(context.Background(), rc); err == nil {
		t.Fatal("unknown native classification ignored")
	}
	rc.PassThrough = toolview.Responses([]byte(`{"tools":[{"type":"function","name":"codeExecution"}]}`))
	if err := p.PrepareNative(context.Background(), rc); err != nil {
		t.Fatal(err)
	}
	if !bytes.Contains(rc.PassThrough.(models.NativeBody).Body(), []byte("codeExecution")) {
		t.Fatal("custom spelling treated as builtin")
	}
}
