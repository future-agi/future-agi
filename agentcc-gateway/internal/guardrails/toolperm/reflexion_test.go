package toolperm

import "testing"

func TestToolPermissionProviderAliases(t *testing.T) {
	for _, provider := range []string{"tool_permission", "tool_permissions"} {
		if !IsToolPermConfig(map[string]interface{}{"provider": provider}) {
			t.Errorf("unrecognized control-plane provider %q", provider)
		}
	}
	if IsToolPermConfig(map[string]interface{}{"provider": "other"}) {
		t.Fatal("unrelated provider matched")
	}
}
