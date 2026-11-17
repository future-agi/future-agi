package mcp

import "encoding/json"

func cloneContentRaw(raw json.RawMessage) json.RawMessage {
	return append(json.RawMessage(nil), raw...)
}

func cloneContentMeta(meta map[string]json.RawMessage) map[string]json.RawMessage {
	if meta == nil {
		return nil
	}
	out := make(map[string]json.RawMessage, len(meta))
	for k, v := range meta {
		out[k] = cloneContentRaw(v)
	}
	return out
}

func cloneContentBool(value *bool) *bool {
	if value == nil {
		return nil
	}
	v := *value
	return &v
}

// CloneToolList transfers ownership across client, callback and tenant cache
// delivery boundaries. A validated cache entry must not share mutable Raw,
// schemas, annotations or gateway metadata with a consumer.
func CloneToolList(items []Tool) []Tool {
	if items == nil {
		return nil
	}
	out := append([]Tool{}, items...)
	for i := range out {
		t := &out[i]
		t.Raw = cloneContentRaw(t.Raw)
		t.InputSchema = cloneContentRaw(t.InputSchema)
		t.OutputSchema = cloneContentRaw(t.OutputSchema)
		t.Icons = cloneContentRaw(t.Icons)
		t.Meta = cloneContentRaw(t.Meta)
		t.GatewayMeta = cloneContentMeta(t.GatewayMeta)
		if t.Annotations != nil {
			a := *t.Annotations
			a.ReadOnlyHint = cloneContentBool(a.ReadOnlyHint)
			a.DestructiveHint = cloneContentBool(a.DestructiveHint)
			a.IdempotentHint = cloneContentBool(a.IdempotentHint)
			a.OpenWorldHint = cloneContentBool(a.OpenWorldHint)
			t.Annotations = &a
		}
	}
	return out
}

func cloneResourceList(items []Resource) []Resource {
	if items == nil {
		return nil
	}
	out := append([]Resource{}, items...)
	for i := range out {
		r := &out[i]
		r.Raw = cloneContentRaw(r.Raw)
		r.Size = cloneContentRaw(r.Size)
		r.Annotations = cloneContentRaw(r.Annotations)
		r.Icons = cloneContentRaw(r.Icons)
		r.Meta = cloneContentRaw(r.Meta)
		r.GatewayMeta = cloneContentMeta(r.GatewayMeta)
	}
	return out
}

func clonePromptList(items []Prompt) []Prompt {
	if items == nil {
		return nil
	}
	out := append([]Prompt{}, items...)
	for i := range out {
		p := &out[i]
		p.Raw = cloneContentRaw(p.Raw)
		p.Arguments = append([]PromptArgument(nil), p.Arguments...)
		p.Icons = cloneContentRaw(p.Icons)
		p.Meta = cloneContentRaw(p.Meta)
		p.GatewayMeta = cloneContentMeta(p.GatewayMeta)
	}
	return out
}
