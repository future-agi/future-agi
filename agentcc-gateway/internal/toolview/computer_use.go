package toolview

import (
	"bytes"
	"context"
	"encoding/json"
	"io"
	"strings"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// ComputerUseSchema is installed only by an accepted internal schema owner.
// No production factory or public caller-controlled registration exists here.
type ComputerUseSchema interface {
	ValidateRequest(context.Context, []byte, models.ComputerUseRequirement) error
	ValidateResponse(context.Context, []byte, models.ComputerUseRequirement) error
	NewStream(context.Context, models.ComputerUseRequirement) (ComputerUseStream, error)
}
type ComputerUseStream interface {
	Feed(chunk []byte) (validatedOriginal []byte, err error)
	Finish() (validatedOriginal []byte, err error)
}

func (r *NativeRequest) ComputerUseRequirement(ctx context.Context) (models.ComputerUseRequirement, error) {
	if r == nil || ctx.Err() != nil {
		return models.ComputerUseRequirement{}, errNative
	}
	// An empty internal requirement is not evidence that the native body is
	// ordinary. Classify before the handler may choose unrestricted compatibility,
	// independently of cap hooks and ordinary tool-policy enablement.
	declared, err := r.declaresComputerUse(ctx)
	required := r.required
	if declared {
		required.Required = true
	}
	if err != nil {
		return required, errNative
	}
	// Classification adds a restriction only. It cannot invent an accepted
	// protocol/version tuple, install a schema, or attest a provider/model route.
	if err := r.ValidateComputerUse(ctx, required); err != nil {
		return required, err
	}
	return required, nil
}

// declaresComputerUse inspects declaration discriminators only. Function names,
// parameter schemas, messages and opaque metadata are not CUA declarations.
// Unknown versions in the computer family are restricted too; they must never
// become ordinary merely because no accepted schema knows them yet.
func (r *NativeRequest) declaresComputerUse(ctx context.Context) (bool, error) {
	root, err := computerUseFields(ctx, r.Body())
	if err != nil {
		return false, errNative
	}
	tools := bytes.TrimSpace(root["tools"])
	if len(tools) == 0 || bytes.Equal(tools, []byte("null")) {
		return false, nil
	}
	var items []json.RawMessage
	if tools[0] == '{' && r.protocol == models.NativeGenAI {
		items = []json.RawMessage{tools}
	} else if tools[0] != '[' || json.Unmarshal(tools, &items) != nil {
		return false, errNative
	}
	declared := false
	for _, raw := range items {
		tool, err := computerUseFields(ctx, raw)
		if err != nil {
			return false, errNative
		}
		switch r.protocol {
		case models.NativeGenAI:
			for key := range tool {
				if strings.HasPrefix(strings.ToLower(key), "computer") {
					declared = true
				}
			}
		case models.NativeMessages, models.NativeResponses:
			typ := tool["type"]
			if typ == nil && r.protocol == models.NativeMessages {
				continue // Messages custom function declaration.
			}
			var name string
			if json.Unmarshal(typ, &name) != nil || name == "" {
				return false, errNative
			}
			if strings.HasPrefix(strings.ToLower(name), "computer") {
				declared = true
			}
		default:
			return false, errNative
		}
	}
	return declared, ctx.Err()
}

// computerUseFields reads only an object's immediate members, retaining opaque
// values as raw JSON. Classification must not apply the guardrail reader's
// depth limit to messages, arguments, metadata or function parameter schemas.
// Duplicate keys at the root/declaration boundary remain ambiguous and refused;
// decoding the raw values still validates JSON without building a recursive tree.
func computerUseFields(ctx context.Context, body []byte) (map[string]json.RawMessage, error) {
	d := json.NewDecoder(bytes.NewReader(body))
	if ctx.Err() != nil {
		return nil, errNative
	}
	if tok, err := d.Token(); err != nil || tok != json.Delim('{') {
		return nil, errNative
	}
	fields := make(map[string]json.RawMessage)
	for d.More() {
		if ctx.Err() != nil {
			return nil, errNative
		}
		tok, err := d.Token()
		key, ok := tok.(string)
		if err != nil || !ok || fields[key] != nil {
			return nil, errNative
		}
		var raw json.RawMessage
		if d.Decode(&raw) != nil {
			return nil, errNative
		}
		fields[key] = raw
	}
	if tok, err := d.Token(); err != nil || tok != json.Delim('}') {
		return nil, errNative
	}
	if _, err := d.Token(); err != io.EOF || ctx.Err() != nil {
		return nil, errNative
	}
	return fields, nil
}

func (r *NativeRequest) ValidateComputerUse(ctx context.Context, required models.ComputerUseRequirement) error {
	if r == nil || ctx.Err() != nil {
		return errNative
	}
	if !required.Required {
		return nil
	}
	if r.schema == nil || required.Protocol != r.protocol || required.Action == "" || required.ToolProtocol == "" || required.VendorVersion == "" {
		return errNative
	}
	if r.protocol == models.NativeGenAI && required.Stream != r.stream {
		return errNative
	}
	body := r.Body()
	if err := r.schema.ValidateRequest(ctx, body, required); err != nil || !bytes.Equal(body, r.Body()) {
		return errNative
	}
	return nil
}

// ValidateResponse is a closed, byte-preserving facade over the private schema.
func (r *NativeRequest) ValidateResponse(ctx context.Context, body []byte, required models.ComputerUseRequirement) error {
	if !required.Required {
		return nil
	}
	if r == nil || r.schema == nil || ctx.Err() != nil {
		return errNative
	}
	copy := bytes.Clone(body)
	if r.schema.ValidateResponse(ctx, copy, required) != nil || !bytes.Equal(copy, body) {
		return errNative
	}
	return nil
}
func (r *NativeRequest) NewComputerUseStream(ctx context.Context, required models.ComputerUseRequirement) (ComputerUseStream, error) {
	if r == nil || r.schema == nil || !required.Required || !required.Stream || ctx.Err() != nil {
		return nil, errNative
	}
	stream, err := r.schema.NewStream(ctx, required)
	if err != nil || stream == nil {
		return nil, errNative
	}
	return stream, nil
}
