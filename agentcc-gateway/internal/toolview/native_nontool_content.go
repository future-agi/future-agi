package toolview

import "github.com/futureagi/agentcc-gateway/internal/models"

// These are the explicitly recognized non-tool envelopes in requestview's
// Responses and Messages readers, not its unknown-type text fallback. Inspect
// their structural fields only; payload strings and extension objects are opaque.
// This classifies reference safety during strip, not provider schema acceptance.
func nativeNonToolPart(part *nativeNode, protocol models.NativeProtocol) bool {
	if part == nil || part.kind != '{' || nativeReferenceFields(part) {
		return false
	}
	typ := part.field("type").str()
	if protocol == models.NativeResponses {
		switch typ {
		case "input_text", "output_text", "text", "summary_text":
			return nativeStrings(part, "text")
		case "refusal":
			return nativeStrings(part, "refusal")
		case "input_image":
			// The reader treats a missing/null URL as absent; a string locator is still required.
			url := part.field("image_url")
			return (url == nil || url.kind == 'n' || url.kind == '"') &&
				nativeOptionalStrings(part, "file_id", "detail") && nativeHasString(part, "image_url", "file_id")
		case "input_file":
			return nativeOptionalStrings(part, "file_id", "file_data", "file_url", "filename") && nativeHasString(part, "file_id", "file_data", "file_url")
		case "input_audio":
			audio := part.field("input_audio")
			return audio != nil && audio.kind == '{' && !nativeReferenceFields(audio) && nativeStrings(audio, "data", "format")
		default:
			return false
		}
	}
	if protocol != models.NativeMessages {
		return false
	}
	switch typ {
	case "text":
		return nativeStrings(part, "text")
	case "image":
		return nativeNonToolSource(part.field("source"), false)
	case "document":
		return nativeDocumentMetadata(part) && nativeNonToolSource(part.field("source"), true)
	case "search_result":
		return nativeStrings(part, "source", "title") && nativeNonToolBlocks(part.field("content"))
	case "thinking":
		return nativeStrings(part, "thinking") && nativeOptionalStrings(part, "signature")
	case "redacted_thinking":
		return nativeStrings(part, "data")
	case "container_upload":
		return nativeStrings(part, "file_id")
	default:
		return false
	}
}

// Reasoning is self-contained model content, unlike item_reference or an
// ID-only function result. Summary/content lists cannot hide tool envelopes.
func nativeReasoningItem(item *nativeNode) bool {
	if !nativeOptionalStrings(item, "id", "status", "encrypted_content") || !nativeTextParts(item.field("summary"), "summary_text") {
		return false
	}
	if content := item.field("content"); content != nil && !nativeTextParts(content, "reasoning_text") {
		return false
	}
	return true
}

func nativeTextParts(n *nativeNode, typ string) bool {
	if n == nil || n.kind != '[' {
		return false
	}
	for _, part := range n.children {
		if part.kind != '{' || nativeReferenceFields(part) || part.field("type").str() != typ || !nativeStrings(part, "text") {
			return false
		}
	}
	return true
}

// Follow only supported document/search-result content locations. Tool calls,
// unresolved results and unknown future block types are not non-tool content.
func nativeNonToolBlocks(n *nativeNode) bool {
	if n == nil || n.kind != '[' {
		return false
	}
	for _, block := range n.children {
		if !nativeNonToolPart(block, models.NativeMessages) {
			return false
		}
	}
	return true
}

func nativeNonToolSource(source *nativeNode, document bool) bool {
	if source == nil || source.kind != '{' || nativeReferenceFields(source) {
		return false
	}
	switch source.field("type").str() {
	case "base64":
		return nativeStrings(source, "media_type", "data")
	case "url":
		return nativeStrings(source, "url")
	case "file":
		return nativeStrings(source, "file_id")
	case "text":
		return document && nativeStrings(source, "data") && nativeOptionalStrings(source, "media_type")
	case "content":
		if !document {
			return false
		}
		content := source.field("content")
		return (content != nil && content.kind == '"') || nativeNonToolBlocks(content)
	default:
		return false
	}
}

func nativeStrings(n *nativeNode, keys ...string) bool {
	for _, key := range keys {
		if value := n.field(key); value == nil || value.kind != '"' {
			return false
		}
	}
	return true
}

// The Messages reader treats missing/null document title and context as absent.
// Keep this separate from source validation and other optional field contracts.
func nativeDocumentMetadata(n *nativeNode) bool {
	for _, key := range []string{"title", "context"} {
		if value := n.field(key); value != nil && value.kind != 'n' && value.kind != '"' {
			return false
		}
	}
	return true
}

func nativeOptionalStrings(n *nativeNode, keys ...string) bool {
	for _, key := range keys {
		if value := n.field(key); value != nil && value.kind != '"' {
			return false
		}
	}
	return true
}

func nativeHasString(n *nativeNode, keys ...string) bool {
	for _, key := range keys {
		if value := n.field(key); value != nil && value.kind == '"' {
			return true
		}
	}
	return false
}
