package vserver

import (
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// TestMetaToolNamesReserved: each meta-tool name is reserved in every case,
// and no upstream tool of any member, under either naming, override or
// normalization, is ever exposed under one of them.
func TestMetaToolNamesReserved(t *testing.T) {
	for _, name := range []string{MetaSearchTools, MetaDescribeTool, MetaCallTool} {
		for _, variant := range []string{name, strings.ToUpper(name), strings.ToUpper(name[:1]) + name[1:]} {
			if !IsReserved(variant) {
				t.Errorf("IsReserved(%q) = false", variant)
			}
		}
		if IsReserved(EncodePrefixed("github", name)) {
			t.Errorf("the prefixed name %q is reserved: only the bare meta-tool names are", EncodePrefixed("github", name))
		}
		spaced := strings.ReplaceAll(name, "_", " ") // normalizes to the meta-tool name
		for _, spec := range []Spec{
			{Naming: NamingFlat},
			{Naming: NamingPrefixed},
			{Naming: NamingFlat, Overrides: overridesNaming("m/renamed", name)},
		} {
			tools := []Tool{tool("m", name), tool("m", strings.ToUpper(name)), tool("m", spaced), tool("m", "renamed"), tool("m", "ok")}
			v, _ := Compose(spec, tools, nil)
			for _, e := range v.Entries {
				if IsReserved(e.Exposed) {
					t.Fatalf("spec %+v exposes %q under a meta-tool name", spec, e.Exposed)
				}
			}
		}
	}
}

// overridesNaming renames the tool ref to name.
func overridesNaming(ref, name string) map[string]*tenant.MCPToolOverride {
	return map[string]*tenant.MCPToolOverride{ref: {Name: name}}
}
