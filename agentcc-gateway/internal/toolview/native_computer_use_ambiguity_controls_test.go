package toolview

import (
	"context"
	"testing"
)

// Each malformed declaration is independently selectable in the off-host
// guard-removal run; an earlier failure cannot mask a later negative control.
func TestCUAC26R2RequirementAmbiguityControlsIndependent(t *testing.T) {
	for _, tc := range []struct{ name, body string }{
		{"nonlist-tools", `{"tools":false}`},
		{"null-tool", `{"tools":[null]}`},
		{"null-discriminator", `{"tools":[{"type":null}]}`},
		{"duplicate-discriminator", `{"tools":[{"type":"computer_use_preview","type":"function","name":"lookup"}]}`},
		{"duplicate-tools-cua-first", `{"tools":[{"type":"computer_use_preview"}],"tools":[]}`},
		{"duplicate-tools-cua-last", `{"tools":[],"tools":[{"type":"computer_use_preview"}]}`},
	} {
		t.Run(tc.name, func(t *testing.T) {
			if _, err := Responses([]byte(tc.body)).ComputerUseRequirement(context.Background()); err == nil {
				t.Fatal("ambiguous declaration entered unrestricted compatibility")
			}
		})
	}
}
