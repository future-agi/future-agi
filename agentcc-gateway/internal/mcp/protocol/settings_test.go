package protocol

import (
	"reflect"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
)

func TestSettingsDefaults(t *testing.T) {
	if !reflect.DeepEqual(AllVersions(), config.MCPProtocolVersions()) {
		t.Fatalf("AllVersions %v != config.MCPProtocolVersions %v", AllVersions(), config.MCPProtocolVersions())
	}
	s := Settings{}.WithDefaults()
	if s.MaxBodyBytes != config.DefaultConfig().MCP.MaxBodyBytes || s.PageSize != DefaultPageSize || s.ListTTL != DefaultListTTL ||
		s.StreamAfter != DefaultStreamAfter || s.KeepAlive != DefaultKeepAlive || len(s.Versions) != 5 {
		t.Fatalf("defaults = %+v", s)
	}
	kept := Settings{Versions: []string{Version2026}, MaxBodyBytes: 7}.WithDefaults()
	if len(kept.Versions) != 1 || kept.MaxBodyBytes != 7 {
		t.Fatalf("set fields replaced: %+v", kept)
	}
}
