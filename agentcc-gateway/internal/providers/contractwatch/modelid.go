package contractwatch

import (
	"regexp"
	"sort"
	"strconv"
	"strings"
)

// ModelID is a model ID taken apart for template selection. Nothing here is
// authoritative: it is a heuristic over the naming conventions providers use
// (gemini-3.1-flash-lite, claude-opus-4-5-20251101, gpt-4.1-2025-04-14,
// command-a-03-2025, us.amazon.nova-2-lite-v1:0, openai/gpt-oss-120b).
type ModelID struct {
	Raw string
	// API is Bedrock's: "converse" for an inference profile, "invoke" for a
	// base model ID (the adapter's isInferenceProfile split); "" elsewhere.
	API string
	// Geo is a Bedrock inference profile's geography ("us", "global").
	Geo string
	// Vendor is a Bedrock vendor ("anthropic", "amazon") or the path prefix
	// of a vendor/model ID ("openai" in openai/gpt-oss-120b).
	Vendor string
	// Family is the model line without tier words: gemini, gpt, claude,
	// command-a, nova, llama, gpt-oss.
	Family string
	// Version is the release number: [4 1] for 4.1, [4 5] for claude's 4-5.
	Version []int
	// Date is a snapshot date or stamp: YYYYMMDD, YYYYMM, or a raw token
	// (0905, 001) when its format is ambiguous.
	Date string
	// Tiers are size/tier words, sorted: flash, lite, pro, argon, sol,
	// opus, haiku, mini, scout...
	Tiers []string
	// Thinking is the ID's own hint: "yes" for reasoning/thinking tokens,
	// "no" for non-reasoning, "" when the ID says nothing.
	Thinking string
	// Sizes are parameter-count tokens: 120b, 17b, 16e.
	Sizes []string
}

// bedrockProfilePrefixes mirrors isInferenceProfile in
// internal/providers/bedrock/converse.go: IDs with these prefixes go to
// Converse, every other ID to InvokeModel. Fixtures recorded through one API
// are no template for the other. modelid_test.go reads converse.go and fails
// if the lists disagree.
var bedrockProfilePrefixes = []string{"arn:", "us.", "eu.", "ap.", "global."}

// bedrockGeos are the inference-profile geographies Bedrock uses as an ID's
// first dotted segment, whichever API the adapter routes them to.
var bedrockGeos = map[string]bool{
	"us": true, "eu": true, "ap": true, "apac": true, "global": true, "jp": true,
	"au": true, "ca": true, "us-gov": true, "uk": true, "in": true, "sa": true, "me": true,
}

// tierWords are words that name a size or tier inside a family. A word not
// here and before the version is part of the family name (command-a,
// gpt-oss), so the list only needs the words that also appear before the
// version, as in claude-opus-5-5.
var tierWords = map[string]bool{
	"haiku": true, "sonnet": true, "opus": true, "fable": true, "mythos": true,
	"flash": true, "pro": true, "lite": true, "ultra": true, "nano": true, "mini": true, "micro": true,
	"max": true, "plus": true, "premier": true, "large": true, "medium": true, "small": true,
	"argon": true, "sol": true, "terra": true, "luna": true, "astra": true,
	"fast": true, "light": true, "turbo": true, "air": true, "scout": true, "maverick": true,
}

// variantWords name a modality or purpose variant (command-a-vision,
// gemini-2.5-flash-image); like tier words they end the family name.
var variantWords = map[string]bool{
	"vision": true, "translate": true, "code": true, "coder": true, "codex": true, "audio": true,
	"search": true, "image": true, "realtime": true, "live": true, "tts": true, "transcribe": true,
	"embed": true, "embedding": true, "multimodal": true, "omni": true, "guard": true, "safeguard": true,
}

// stageWords mark release stage or packaging, not a different model shape.
var stageWords = map[string]bool{
	"preview": true, "exp": true, "experimental": true, "latest": true, "beta": true, "alpha": true,
	"instruct": true, "it": true, "chat": true, "versatile": true, "instant": true, "v": true,
}

// thinkingWords mark a reasoning variant in the ID.
var thinkingWords = map[string]bool{"reasoning": true, "thinking": true, "think": true, "reasoner": true}

var (
	bedrockVersionSuffix = regexp.MustCompile(`(-v\d+(:\d+)?|-\d+:\d+|:\d+)$`)
	numberRe             = regexp.MustCompile(`^\d+(\.\d+)*$`)
	alphaNumRe           = regexp.MustCompile(`^([a-z]+)(\d+(?:\.\d+)*)$`)
	numAlphaRe           = regexp.MustCompile(`^(\d+(?:\.\d+)?)([a-z]+)$`)
	sizeRe               = regexp.MustCompile(`^(\d+(\.\d+)?[bmke]|a\d+b|\d+x\d+b)$`)
	alphaRe              = regexp.MustCompile(`^[a-z]+$`)
)

// ParseModelID takes id apart. section selects provider conventions (only
// "bedrock" differs).
func ParseModelID(section, id string) ModelID {
	m := ModelID{Raw: id}
	s := strings.ToLower(id)

	if section == "bedrock" {
		m.API = "invoke"
		for _, p := range bedrockProfilePrefixes {
			if strings.HasPrefix(s, p) {
				m.API = "converse"
			}
		}
		if i := strings.LastIndex(s, "/"); strings.HasPrefix(s, "arn:") && i >= 0 {
			s = s[i+1:]
		}
		segs := strings.Split(s, ".")
		if len(segs) >= 3 && bedrockGeos[segs[0]] {
			m.Geo, segs = segs[0], segs[1:]
		}
		if len(segs) >= 2 {
			m.Vendor, segs = segs[0], segs[1:]
		}
		s = bedrockVersionSuffix.ReplaceAllString(strings.Join(segs, "."), "")
	} else {
		s = strings.TrimPrefix(s, "models/")
		if i := strings.LastIndex(s, "/"); i >= 0 {
			m.Vendor, s = s[:i], s[i+1:]
		}
	}

	toks := strings.FieldsFunc(s, func(r rune) bool { return r == '-' || r == '_' || r == ':' || r == '@' || r == '/' })
	familyOpen := true
	var family []string
	negate := false
	addTier := func(t string) {
		if !contains(m.Tiers, t) {
			m.Tiers = append(m.Tiers, t)
		}
	}
	setVersion := func(parts ...string) {
		if m.Version != nil {
			return
		}
		for _, p := range parts {
			for _, n := range strings.Split(p, ".") {
				v, _ := strconv.Atoi(n)
				m.Version = append(m.Version, v)
			}
		}
		if len(m.Version) > 3 {
			m.Version = m.Version[:3]
		}
	}

	for i := 0; i < len(toks); i++ {
		t := toks[i]
		next := func(k int) string {
			if i+k < len(toks) {
				return toks[i+k]
			}
			return ""
		}
		switch {
		// Dates: 20250414, 2025-04-14, 03-2025 (Cohere's MM-YYYY).
		case len(t) == 8 && numberRe.MatchString(t) && (strings.HasPrefix(t, "19") || strings.HasPrefix(t, "20")):
			m.Date = t
			familyOpen = false
		case len(t) == 4 && strings.HasPrefix(t, "20") && isDigits(t) && len(next(1)) == 2 && isDigits(next(1)) && len(next(2)) == 2 && isDigits(next(2)):
			m.Date = t + next(1) + next(2)
			i += 2
			familyOpen = false
		case len(t) == 2 && isDigits(t) && t >= "01" && t <= "12" && len(next(1)) == 4 && strings.HasPrefix(next(1), "20") && isDigits(next(1)):
			m.Date = next(1) + t
			i++
			familyOpen = false
		// Four-digit stamps (MMDD, YYMM) and zero-padded snapshot numbers.
		case len(t) == 4 && isDigits(t), len(t) == 3 && isDigits(t) && t[0] == '0':
			if m.Date == "" {
				m.Date = t
			}
			familyOpen = false
		case numberRe.MatchString(t):
			// A run of numbers is one version: claude's 4-5 is 4.5.
			parts := []string{t}
			for numberRe.MatchString(next(1)) && len(next(1)) <= 2 && !strings.Contains(t, ".") {
				parts = append(parts, next(1))
				i++
			}
			setVersion(parts...)
			familyOpen = false
		case sizeRe.MatchString(t):
			m.Sizes = append(m.Sizes, t)
			familyOpen = false
		case numAlphaRe.MatchString(t): // gpt-4o: version 4, tier o
			sm := numAlphaRe.FindStringSubmatch(t)
			setVersion(sm[1])
			addTier(sm[2])
			familyOpen = false
		case alphaNumRe.MatchString(t):
			// llama3, qwen3.6, o3, k2: a name glued to its version, which
			// may go on in the next token (llama3-1 is 3.1).
			sm := alphaNumRe.FindStringSubmatch(t)
			word := sm[1]
			switch {
			case familyOpen && !tierWords[word] && !variantWords[word]:
				family = append(family, word)
			case tierWords[word] || variantWords[word]:
				addTier(word)
			}
			parts := []string{sm[2]}
			for !strings.Contains(sm[2], ".") && len(parts) < 3 && numberRe.MatchString(next(1)) && len(next(1)) <= 2 {
				parts = append(parts, next(1))
				i++
			}
			setVersion(parts...)
			familyOpen = false
		case alphaRe.MatchString(t):
			switch {
			case t == "non" || t == "no":
				negate = true
				continue
			case thinkingWords[t]:
				if negate {
					m.Thinking = "no"
				} else {
					m.Thinking = "yes"
				}
				familyOpen = false
			case stageWords[t]:
				familyOpen = false
			case (tierWords[t] || variantWords[t]) && len(family) > 0:
				addTier(t)
				familyOpen = false
			case familyOpen:
				family = append(family, t)
			default:
				addTier(t)
			}
		default:
			// Mixed tokens (r7b, x5): part of the name while it is open.
			if familyOpen {
				family = append(family, t)
			} else {
				addTier(t)
			}
		}
		negate = false
	}
	m.Family = strings.Join(family, "-")
	if (m.Family == "" || m.Family == "v") && m.Vendor != "" {
		m.Family = m.Vendor
	}
	sort.Strings(m.Tiers)
	sort.Strings(m.Sizes)
	return m
}

func isDigits(s string) bool {
	if s == "" {
		return false
	}
	for i := 0; i < len(s); i++ {
		if s[i] < '0' || s[i] > '9' {
			return false
		}
	}
	return true
}

// versionValue orders versions: 3.9 < 3.10 < 4 < 4.1. Minor parts above 99
// are clamped. ok is false when the ID has no version.
func (m ModelID) versionValue() (int, bool) {
	if len(m.Version) == 0 {
		return 0, false
	}
	v := m.Version[0] * 10000
	if len(m.Version) > 1 {
		minor := m.Version[1]
		if minor > 99 {
			minor = 99
		}
		v += minor * 100
	}
	if len(m.Version) > 2 {
		patch := m.Version[2]
		if patch > 99 {
			patch = 99
		}
		v += patch
	}
	return v, true
}

// VersionString is the version as dotted text ("4.1"), or "".
func (m ModelID) VersionString() string {
	parts := make([]string, len(m.Version))
	for i, v := range m.Version {
		parts[i] = strconv.Itoa(v)
	}
	return strings.Join(parts, ".")
}

// dateValue is Date as a comparable YYYYMMDD number (YYYYMM00 for a month),
// or 0 when Date is not a full or month date.
func (m ModelID) dateValue() int {
	switch len(m.Date) {
	case 8:
		v, _ := strconv.Atoi(m.Date)
		return v
	case 6:
		v, _ := strconv.Atoi(m.Date)
		return v * 100
	}
	return 0
}
