// Package toolview owns native declaration edits without canonicalizing payloads.
package toolview

import (
	"bytes"
	"context"
	"errors"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/requestview"
)

type ToolID uint32
type Kind uint8

const (
	Unknown Kind = iota
	Function
	Builtin
	ComputerUse
)

type Descriptor struct {
	id         ToolID
	policyName string
	kind       Kind
}
type Snapshot struct {
	owner        *NativeRequest
	generation   uint64
	tools        []Descriptor
	declarations []*nativeNode
	root         *nativeNode
}
type NativeToolRequest interface {
	models.NativeBody
	ToolSnapshot(context.Context) (Snapshot, error)
	ApplyToolFilter(context.Context, Snapshot, []ToolID) error
}
type NativeRequest struct {
	view       *requestview.View
	protocol   models.NativeProtocol
	model      string
	stream     bool
	generation uint64
	schema     ComputerUseSchema
	required   models.ComputerUseRequirement
}

var errNative = errors.New("native tool request cannot be validated")

func Anthropic(body []byte) *NativeRequest {
	r := &NativeRequest{protocol: models.NativeMessages}
	r.rebuild(body)
	return r
}
func Responses(body []byte) *NativeRequest {
	r := &NativeRequest{protocol: models.NativeResponses}
	r.rebuild(body)
	return r
}
func Gemini(body []byte, model string, stream bool) *NativeRequest {
	r := &NativeRequest{protocol: models.NativeGenAI, model: model, stream: stream}
	r.rebuild(body)
	return r
}
func (r *NativeRequest) rebuild(body []byte) {
	body = bytes.Clone(body)
	switch r.protocol {
	case models.NativeMessages:
		r.view = requestview.Anthropic(body)
	case models.NativeResponses:
		r.view = requestview.Responses(body)
	case models.NativeGenAI:
		r.view = requestview.Gemini(body, r.model, r.stream)
	}
	r.generation++
}
func (d Descriptor) ID() ToolID         { return d.id }
func (d Descriptor) PolicyName() string { return d.policyName }
func (d Descriptor) Kind() Kind         { return d.kind }
func (s Snapshot) Len() int             { return len(s.tools) }
func (s Snapshot) Tool(i int) (Descriptor, bool) {
	if i < 0 || i >= len(s.tools) {
		return Descriptor{}, false
	}
	return s.tools[i], true
}

// Generation invalidates private policy snapshots even after a byte-equivalent edit.
func (r *NativeRequest) Generation() uint64 {
	if r == nil {
		return 0
	}
	return r.generation
}
func (r *NativeRequest) Body() []byte {
	if r == nil || r.view == nil {
		return nil
	}
	return bytes.Clone(r.view.Body())
}
func (r *NativeRequest) ChatRequest(ctx context.Context) (*models.ChatCompletionRequest, string) {
	if r == nil || r.view == nil {
		return nil, errNative.Error()
	}
	chat, why := r.view.ChatRequest(ctx)
	if why != "" {
		return chat, errNative.Error()
	}
	return chat, ""
}
func (r *NativeRequest) ApplyChecks() error {
	if r == nil || r.view == nil {
		return errNative
	}
	before := r.Body()
	err := r.view.ApplyChecks()
	if !bytes.Equal(before, r.view.Body()) {
		r.generation++
	}
	if err != nil {
		return errNative
	}
	return nil
}
func (r *NativeRequest) ToolSnapshot(ctx context.Context) (Snapshot, error) {
	if r == nil || r.view == nil || ctx.Err() != nil {
		return Snapshot{}, errNative
	}
	root, err := parseNative(ctx, r.view.Body())
	if err != nil {
		return Snapshot{}, errNative
	}
	s := Snapshot{owner: r, generation: r.generation, root: root}
	if err := r.describe(&s); err != nil {
		return Snapshot{}, errNative
	}
	return s, nil
}
func (r *NativeRequest) ApplyToolFilter(ctx context.Context, s Snapshot, keep []ToolID) error {
	if r == nil || s.owner != r || s.generation != r.generation || s.root == nil || ctx.Err() != nil {
		return errNative
	}
	wanted := make(map[ToolID]bool, len(keep))
	for _, id := range keep {
		if id == 0 || int(id) > len(s.tools) || wanted[id] {
			return errNative
		}
		wanted[id] = true
	}
	if len(wanted) == len(s.tools) {
		return nil
	}
	// Without the accepted schema's reference semantics a required declaration
	// cannot be removed, even when the remaining body would still be valid JSON.
	if r.required.Required {
		return errNative
	}
	removed := map[*nativeNode]bool{}
	functions := map[string]bool{}
	for i, d := range s.tools {
		if !wanted[d.id] {
			removed[s.declarations[i]] = true
		} else if d.kind == Function {
			// Builtin policy aliases are not Function reference identities.
			functions[d.policyName] = true
		}
	}
	if nativeReferences(r.protocol, s.root, functions) {
		return errNative
	}
	body := r.view.Body()
	candidate, ok := filterNative(body, s.root, removed)
	if !ok || ctx.Err() != nil {
		return errNative
	}
	probe := &NativeRequest{protocol: r.protocol, model: r.model, stream: r.stream}
	probe.rebuild(candidate)
	if _, err := probe.ToolSnapshot(ctx); err != nil {
		return errNative
	}
	r.rebuild(candidate)
	return nil
}

var _ NativeToolRequest = (*NativeRequest)(nil)
