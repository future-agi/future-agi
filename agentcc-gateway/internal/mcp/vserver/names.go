// Package vserver composes an org's virtual MCP servers: the members' tools under collision-free exposed names, overrides,
// include and exclude globs and a stable order, plus the access rules that
// narrow what a principal may list and call. It imports nothing of package
// mcp, so the request hooks and any admin view share one composition.
package vserver

import (
	"crypto/sha256"
	"encoding/hex"
	"strings"
)

// Naming modes of a virtual server.
const (
	NamingPrefixed = "prefixed"
	NamingFlat     = "flat"
)

// Separator joins a member's key and a tool name under prefixed naming.
const Separator = "__"

// MaxNameLen caps an exposed name. A longer name keeps its first hashedKeep
// characters, then "_" and the first 8 hex digits of the sha256 of the whole
// name, which is exactly MaxNameLen characters.
const (
	MaxNameLen = 64
	hashedKeep = 55
)

// The gateway's own meta-tools: a virtual server in search mode lists
// exactly these three, and no upstream tool is ever exposed under one of
// their names, in any case (Compose rejects it as ReasonReserved).
const (
	MetaSearchTools  = "search_tools"
	MetaDescribeTool = "describe_tool"
	MetaCallTool     = "call_tool"
)

// reserved are the names the gateway's own meta-tools use.
var reserved = map[string]bool{MetaSearchTools: true, MetaDescribeTool: true, MetaCallTool: true}

// IsReserved reports whether name is a meta-tool name, case-folded.
func IsReserved(name string) bool { return reserved[strings.ToLower(name)] }

// Normalize maps every character outside [A-Za-z0-9_.-] to "_" and caps the
// result at MaxNameLen.
func Normalize(name string) string {
	return capName(sanitize(name))
}

// EncodePrefixed is the prefixed exposed name of an upstream tool:
// <member key>__<tool>, normalized.
func EncodePrefixed(memberKey, tool string) string {
	return Normalize(memberKey + Separator + tool)
}

// FoldKey is the key two exposed names collide on: case-folded.
func FoldKey(name string) string { return strings.ToLower(name) }

func sanitize(s string) string {
	var b strings.Builder
	b.Grow(len(s))
	for _, c := range s {
		switch {
		case c >= 'a' && c <= 'z', c >= 'A' && c <= 'Z', c >= '0' && c <= '9', c == '_', c == '.', c == '-':
			b.WriteRune(c)
		default:
			b.WriteByte('_')
		}
	}
	return b.String()
}

func capName(name string) string {
	if len(name) <= MaxNameLen {
		return name
	}
	sum := sha256.Sum256([]byte(name))
	return name[:hashedKeep] + "_" + hex.EncodeToString(sum[:])[:8]
}
