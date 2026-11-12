package capmock

import (
	"slices"
	"strings"
)

// The mock never needs a real key. It refuses to start while any variable
// named like a vendor key is set, so a real key cannot sit beside it by
// mistake (and a gateway pointed at it cannot pick one up from its shell).
var (
	vendorTokens = []string{"EXA", "PARALLEL", "PERPLEXITY", "PPLX", "TAVILY", "BRAVE", "SEARXNG", "FIRECRAWL",
		"JINA", "MISTRAL", "AZURE", "TEXTRACT", "AWS", "DEEPGRAM", "ASSEMBLYAI", "VOYAGE", "COHERE", "CO",
		"KERNEL", "BROWSERBASE", "E2B", "DAYTONA", "SANDBOX", "FAL", "PIPEDREAM", "CDP", "COINBASE", "X402"}
	secretTokens = []string{"KEY", "TOKEN", "SECRET", "PASSWORD", "CREDENTIAL", "CREDENTIALS"}
)

// VendorKeyVars returns the names (never the values) of the variables in
// environ (KEY=value pairs) that are named like a vendor credential: a
// vendor word and a secret word among the name's _-separated words, such as
// EXA_API_KEY, FAL_KEY or AWS_SECRET_ACCESS_KEY.
func VendorKeyVars(environ []string) []string {
	var out []string
	for _, kv := range environ {
		name, _, _ := strings.Cut(kv, "=")
		words := strings.FieldsFunc(strings.ToUpper(name), func(r rune) bool {
			return !(r >= 'A' && r <= 'Z' || r >= '0' && r <= '9')
		})
		vendor := slices.ContainsFunc(words, func(w string) bool { return slices.Contains(vendorTokens, w) })
		secret := slices.ContainsFunc(words, func(w string) bool { return slices.Contains(secretTokens, w) })
		if vendor && secret {
			out = append(out, name)
		}
	}
	slices.Sort(out)
	return out
}
