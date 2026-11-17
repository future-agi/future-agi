package tenancy

import (
	"context"
	"encoding/json"
	"errors"
	"sync/atomic"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/mcp"
)

type refreshListClient struct {
	resources atomic.Value // []json.RawMessage
	prompts   atomic.Value // []json.RawMessage
}

func newRefreshListClient(resources, prompts []json.RawMessage) *refreshListClient {
	c := &refreshListClient{}
	c.resources.Store(resources)
	c.prompts.Store(prompts)
	return c
}

func (c *refreshListClient) ListTools(context.Context) ([]json.RawMessage, error) {
	return []json.RawMessage{json.RawMessage(`{"name":"tool","inputSchema":{"type":"object"}}`)}, nil
}
func (c *refreshListClient) CallTool(context.Context, string, json.RawMessage, mcp.CallOptions) (json.RawMessage, error) {
	return nil, errors.New("unused")
}
func (c *refreshListClient) ListResources(context.Context) ([]json.RawMessage, error) {
	return append([]json.RawMessage(nil), c.resources.Load().([]json.RawMessage)...), nil
}
func (c *refreshListClient) ReadResource(context.Context, string, mcp.CallOptions) (json.RawMessage, error) {
	return nil, errors.New("unused")
}
func (c *refreshListClient) ListPrompts(context.Context) ([]json.RawMessage, error) {
	return append([]json.RawMessage(nil), c.prompts.Load().([]json.RawMessage)...), nil
}
func (c *refreshListClient) GetPrompt(context.Context, string, json.RawMessage, mcp.CallOptions) (json.RawMessage, error) {
	return nil, errors.New("unused")
}
func (*refreshListClient) Healthy() bool { return true }
func (*refreshListClient) Close() error  { return nil }

func TestListRefreshInvalidatesWhenResourceOrPromptDefinitionChanges(t *testing.T) {
	m := New(Options{})
	t.Cleanup(func() { _ = m.Close(context.Background()) })
	p := newPool(m, "org-a")
	e := newEntry(p, spec{id: "github", source: SourceOrg})

	c := newRefreshListClient(
		[]json.RawMessage{json.RawMessage(`{"uri":"mcp://one","name":"one"}`)},
		[]json.RawMessage{json.RawMessage(`{"name":"prompt","description":"first"}`)},
	)
	if err := e.list(c); err != nil {
		t.Fatal(err)
	}
	first := p.gen.Load()

	c.resources.Store([]json.RawMessage{json.RawMessage(`{"uri":"mcp://two","name":"two"}`)})
	c.prompts.Store([]json.RawMessage{json.RawMessage(`{"name":"prompt","description":"second"}`)})
	if err := e.list(c); err != nil {
		t.Fatal(err)
	}
	if got := p.gen.Load(); got <= first {
		t.Fatalf("list generation did not advance after same-size resource/prompt updates: %d -> %d", first, got)
	}
	if got := e.status().Resources[0].Name; got != "two" {
		t.Fatalf("resource refresh kept stale definition %q", got)
	}
	if got := e.status().Prompts[0].Description; got != "second" {
		t.Fatalf("prompt refresh kept stale definition %q", got)
	}
}
