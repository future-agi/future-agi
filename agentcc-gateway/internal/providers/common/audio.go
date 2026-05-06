package common

import "strings"

// MapAudioFormatToMime converts OpenAI audio format strings to MIME types.
// Used by multiple providers (anthropic, bedrock, gemini) during multimodal
// content translation.
func MapAudioFormatToMime(format string) string {
	switch strings.ToLower(format) {
	case "wav":
		return "audio/wav"
	case "mp3":
		return "audio/mpeg"
	case "flac":
		return "audio/flac"
	case "opus":
		return "audio/opus"
	case "pcm16":
		return "audio/L16"
	case "ogg":
		return "audio/ogg"
	default:
		return "audio/wav"
	}
}
