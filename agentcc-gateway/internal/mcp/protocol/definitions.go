package protocol

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"strings"
)

// GatewayMetaPrefix starts the _meta keys the gateway owns
// (io.futureagi/*); every other _meta key is the upstream's.
const GatewayMetaPrefix = "io.futureagi/"

// EditDefinition returns a tool, resource or prompt definition as the client
// receives it (raw pass-through, MCA C26, C30): the upstream's object raw,
// with only the gateway-owned fields edited. name, when non-empty, replaces
// the definition's name (the gateway's exposed name). The io.futureagi/*
// namespace of _meta is the gateway's: every key of it the upstream sent is
// dropped, always, and only the gateway's own entries (the io.futureagi/*
// keys of gateway, which the gateway set, never the upstream-decoded _meta)
// are merged in. title, icons, annotations, the upstream's other _meta keys
// and unknown extension fields pass unchanged. A raw value that is not an
// object is returned as it is.
func EditDefinition(raw json.RawMessage, name string, gateway map[string]json.RawMessage) json.RawMessage {
	var obj map[string]json.RawMessage
	if json.Unmarshal(raw, &obj) != nil || obj == nil {
		return raw
	}
	if name != "" {
		obj["name"], _ = json.Marshal(name)
	}
	editMeta(obj, gateway)
	out, err := json.Marshal(obj)
	if err != nil {
		return raw
	}
	return out
}

// StripGatewayMeta returns an upstream result with every io.futureagi/* key
// removed from its _meta (the result's own, the object a client reads the
// gateway's keys from: io.futureagi/error, io.futureagi/tool_call,
// io.futureagi/billing), so an upstream cannot pass off a denial, a billing
// record or any other gateway marker as the gateway's. The gateway's own
// entries are merged afterwards (the call's response meta). The result is
// always decoded and re-encoded, so a duplicated _meta member cannot hide a
// key from one JSON parser and show it to another. Nested _meta objects
// (content blocks, structured content) are the upstream's data and pass
// unchanged. A result that is not an object is returned as it is.
func StripGatewayMeta(result json.RawMessage) json.RawMessage {
	var obj map[string]json.RawMessage
	if json.Unmarshal(result, &obj) != nil || obj == nil {
		return result
	}
	editMeta(obj, nil)
	out, err := json.Marshal(obj)
	if err != nil {
		return result
	}
	return out
}

// editMeta drops every io.futureagi/* key of obj's _meta object and merges
// in gateway's io.futureagi/* entries. A _meta left empty by the drop is
// removed; one that is not an object is left as it is.
func editMeta(obj map[string]json.RawMessage, gateway map[string]json.RawMessage) {
	meta := map[string]json.RawMessage{}
	if raw, ok := obj["_meta"]; ok {
		if json.Unmarshal(raw, &meta) != nil || meta == nil {
			return
		}
	}
	changed := false
	for k := range meta {
		if strings.HasPrefix(k, GatewayMetaPrefix) {
			delete(meta, k)
			changed = true
		}
	}
	for k, v := range gateway {
		if strings.HasPrefix(k, GatewayMetaPrefix) {
			meta[k] = v
			changed = true
		}
	}
	switch {
	case !changed:
	case len(meta) == 0:
		delete(obj, "_meta")
	default:
		obj["_meta"], _ = json.Marshal(meta)
	}
}

// AppendTextContent returns a tools/call result with one more text content
// block (the gateway's deprecation note), every other member unchanged. A
// result that is not an object with a content array is returned as it is.
func AppendTextContent(result json.RawMessage, text string) json.RawMessage {
	var obj map[string]json.RawMessage
	if json.Unmarshal(result, &obj) != nil || obj == nil {
		return result
	}
	var content []json.RawMessage
	if json.Unmarshal(obj["content"], &content) != nil {
		return result
	}
	block, _ := json.Marshal(struct {
		Type string `json:"type"`
		Text string `json:"text"`
	}{"text", text})
	obj["content"], _ = json.Marshal(append(content, block))
	out, err := json.Marshal(obj)
	if err != nil {
		return result
	}
	return out
}

// Binding hashes the parts that identify a caller and the list it reads, for
// a cursor's binding: the hash only, so no identifier travels in a cursor.
func Binding(parts ...string) string {
	sum := sha256.Sum256([]byte(strings.Join(parts, "\x00")))
	return hex.EncodeToString(sum[:16])
}
