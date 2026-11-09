// Source-bound credential regressions. All credentials are synthetic.
package speech

import (
	"context"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"net/url"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

// Encode whole values, including surrounding bytes, independently of the scrubber.
func r5Spellings(key string) map[string]string {
	quoted, _ := json.Marshal(key)
	out := map[string]string{"exact": key, "json": string(quoted[1 : len(quoted)-1]), "percent": url.QueryEscape(key), "percent_lower": strings.ReplaceAll(url.QueryEscape(key), "%2F", "%2f"), "hex": hex.EncodeToString([]byte(key)), "HEX": strings.ToUpper(hex.EncodeToString([]byte(key)))}
	for offset := 0; offset < 3; offset++ {
		for name, enc := range map[string]*base64.Encoding{"std": base64.StdEncoding, "url": base64.URLEncoding, "rawstd": base64.RawStdEncoding, "rawurl": base64.RawURLEncoding} {
			out[fmt.Sprintf("%s_%d", name, offset)] = enc.EncodeToString([]byte(strings.Repeat("!", offset) + key + "suffix"))
		}
	}
	return out
}
func r5Text(err error) string {
	if err == nil {
		return ""
	}
	out := err.Error()
	var api *models.APIError
	if errors.As(err, &api) {
		out += "|" + api.Message + "|" + api.Code + "|" + api.Type
	}
	return out
}
func TestR5SpeechCredentialBoundary(t *testing.T) {
	key := `opaque.R5/"\+Credential-abcdefghijklmnopqrstuvwxyz`
	for name, echo := range r5Spellings(key) {
		t.Run(name, func(t *testing.T) {
			p, err := New("diagnostic-before "+echo+" diagnostic-after", config.ProviderConfig{APIFormat: "deepgram", APIKey: key})
			if err != nil {
				t.Fatal(err)
			}
			_, err = p.ChatCompletion(context.Background(), nil)
			if strings.Contains(r5Text(err), echo) {
				t.Fatal("speech adapter error retained held credential")
			}
			chunks, errs := p.StreamChatCompletion(context.Background(), nil)
			for range chunks {
			}
			err = <-errs
			if strings.Contains(r5Text(err), echo) {
				t.Fatal("speech stream adapter error retained held credential")
			}
			if !strings.Contains(r5Text(err), "diagnostic-before") {
				t.Fatal("lost diagnostic control")
			}
		})
	}
}
