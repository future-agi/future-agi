package modeldb

import (
	"os"
	"regexp"
	"strings"
	"testing"
)

// The refresh script writes one litellm.json to every place that reads it: the
// gateway's own, the collector's and the backend's. A target it forgot would
// bill another price than the gateway does.
func TestRefreshScriptWritesEveryCopy(t *testing.T) {
	data, err := os.ReadFile("../../scripts/update-litellm-pricing.sh")
	if err != nil {
		t.Fatalf("read the refresh script: %v", err)
	}
	script := string(data)

	if !strings.Contains(script, `DEST="internal/modeldb/litellm.json"`) {
		t.Errorf(`the script's DEST is not "internal/modeldb/litellm.json"`)
	}
	for variable, path := range map[string]string{
		"COLLECTOR":   "../fi-collector/pkg/pricing/model_prices.json",
		"PYTHON_COPY": "../futureagi/agentic_eval/core_evals/run_prompt/gateway_prices.json",
	} {
		if !strings.Contains(script, variable+`="`+path+`"`) {
			t.Errorf("the script does not set %s to %q", variable, path)
		}
		copyLine := regexp.MustCompile(`(?m)^cp "\$DEST" "\$` + variable + `"$`)
		if !copyLine.MatchString(script) {
			t.Errorf(`the script does not run cp "$DEST" "$%s"`, variable)
		}
	}
}
