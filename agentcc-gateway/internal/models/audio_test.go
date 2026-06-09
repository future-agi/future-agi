package models

import (
	"encoding/json"
	"testing"
)

func TestSpeechRequestMarshalRoundTrip(t *testing.T) {
	speed := 1.5
	req := SpeechRequest{
		Model:          "tts-1-hd",
		Input:          "Hello, how are you?",
		Voice:          "alloy",
		ResponseFormat: "opus",
		Speed:          &speed,
	}

	data, err := json.Marshal(&req)
	if err != nil {
		t.Fatalf("Marshal error: %v", err)
	}

	var raw map[string]json.RawMessage
	if err := json.Unmarshal(data, &raw); err != nil {
		t.Fatalf("Unmarshal raw error: %v", err)
	}

	for _, key := range []string{"model", "input", "voice", "response_format", "speed"} {
		if _, ok := raw[key]; !ok {
			t.Errorf("expected key %q in marshaled JSON", key)
		}
	}
}

func TestSpeechRequestUnmarshal(t *testing.T) {
	input := `{
		"model": "tts-1",
		"input": "Testing speech synthesis.",
		"voice": "nova",
		"response_format": "aac",
		"speed": 0.75
	}`

	var req SpeechRequest
	if err := json.Unmarshal([]byte(input), &req); err != nil {
		t.Fatalf("Unmarshal error: %v", err)
	}

	if req.Model != "tts-1" {
		t.Errorf("Model = %q, want %q", req.Model, "tts-1")
	}
	if req.Input != "Testing speech synthesis." {
		t.Errorf("Input = %q, want %q", req.Input, "Testing speech synthesis.")
	}
	if req.Voice != "nova" {
		t.Errorf("Voice = %q, want %q", req.Voice, "nova")
	}
	if req.ResponseFormat != "aac" {
		t.Errorf("ResponseFormat = %q, want %q", req.ResponseFormat, "aac")
	}
	if req.Speed == nil || *req.Speed != 0.75 {
		t.Errorf("Speed = %v, want 0.75", req.Speed)
	}
}

func TestSpeechRequestOmitEmpty(t *testing.T) {
	req := SpeechRequest{
		Model: "tts-1",
		Input: "Hello",
		Voice: "echo",
	}

	data, err := json.Marshal(&req)
	if err != nil {
		t.Fatalf("Marshal error: %v", err)
	}

	var raw map[string]json.RawMessage
	if err := json.Unmarshal(data, &raw); err != nil {
		t.Fatalf("Unmarshal raw error: %v", err)
	}

	for _, key := range []string{"response_format", "speed"} {
		if _, ok := raw[key]; ok {
			t.Errorf("key %q should be omitted when zero/nil", key)
		}
	}

	// Required fields must be present.
	for _, key := range []string{"model", "input", "voice"} {
		if _, ok := raw[key]; !ok {
			t.Errorf("required key %q should be present", key)
		}
	}
}

func TestTranscriptionRequestMarshalRoundTrip(t *testing.T) {
	temp := 0.2
	req := TranscriptionRequest{
		Model:          "whisper-1",
		FileData:       []byte("fake-audio-data"),
		FileName:       "recording.mp3",
		Language:       "en",
		ResponseFormat: "verbose_json",
		Temperature:    &temp,
	}

	data, err := json.Marshal(&req)
	if err != nil {
		t.Fatalf("Marshal error: %v", err)
	}

	var raw map[string]json.RawMessage
	if err := json.Unmarshal(data, &raw); err != nil {
		t.Fatalf("Unmarshal raw error: %v", err)
	}

	// FileData and FileName are json:"-", so they must NOT appear.
	for _, key := range []string{"FileData", "file_data", "FileName", "file_name"} {
		if _, ok := raw[key]; ok {
			t.Errorf("key %q should not be in marshaled JSON (tagged with json:\"-\")", key)
		}
	}

	// JSON-serializable fields must be present.
	for _, key := range []string{"model", "language", "response_format", "temperature"} {
		if _, ok := raw[key]; !ok {
			t.Errorf("expected key %q in marshaled JSON", key)
		}
	}
}

func TestTranscriptionRequestUnmarshal(t *testing.T) {
	input := `{
		"model": "whisper-1",
		"language": "fr",
		"response_format": "srt",
		"temperature": 0.3
	}`

	var req TranscriptionRequest
	if err := json.Unmarshal([]byte(input), &req); err != nil {
		t.Fatalf("Unmarshal error: %v", err)
	}

	if req.Model != "whisper-1" {
		t.Errorf("Model = %q, want %q", req.Model, "whisper-1")
	}
	if req.Language != "fr" {
		t.Errorf("Language = %q, want %q", req.Language, "fr")
	}
	if req.ResponseFormat != "srt" {
		t.Errorf("ResponseFormat = %q, want %q", req.ResponseFormat, "srt")
	}
	if req.Temperature == nil || *req.Temperature != 0.3 {
		t.Errorf("Temperature = %v, want 0.3", req.Temperature)
	}

	// FileData and FileName should remain zero since they are not JSON-populated.
	if len(req.FileData) != 0 {
		t.Errorf("FileData should be empty, got %v", req.FileData)
	}
	if req.FileName != "" {
		t.Errorf("FileName should be empty, got %q", req.FileName)
	}
}

func TestTranscriptionRequestOmitEmpty(t *testing.T) {
	req := TranscriptionRequest{
		Model: "whisper-1",
	}

	data, err := json.Marshal(&req)
	if err != nil {
		t.Fatalf("Marshal error: %v", err)
	}

	var raw map[string]json.RawMessage
	if err := json.Unmarshal(data, &raw); err != nil {
		t.Fatalf("Unmarshal raw error: %v", err)
	}

	for _, key := range []string{"language", "response_format", "temperature"} {
		if _, ok := raw[key]; ok {
			t.Errorf("key %q should be omitted when zero/nil", key)
		}
	}

	if _, ok := raw["model"]; !ok {
		t.Error("required key \"model\" should be present")
	}
}

func TestTranscriptionResponseMarshalRoundTrip(t *testing.T) {
	resp := TranscriptionResponse{
		Text: "Hello, this is a test transcription.",
	}

	data, err := json.Marshal(&resp)
	if err != nil {
		t.Fatalf("Marshal error: %v", err)
	}

	var got TranscriptionResponse
	if err := json.Unmarshal(data, &got); err != nil {
		t.Fatalf("Unmarshal error: %v", err)
	}

	if got.Text != "Hello, this is a test transcription." {
		t.Errorf("Text = %q, want %q", got.Text, "Hello, this is a test transcription.")
	}
}

func TestTranscriptionResponseUnmarshal(t *testing.T) {
	input := `{"text": "The quick brown fox jumps over the lazy dog."}`

	var resp TranscriptionResponse
	if err := json.Unmarshal([]byte(input), &resp); err != nil {
		t.Fatalf("Unmarshal error: %v", err)
	}

	if resp.Text != "The quick brown fox jumps over the lazy dog." {
		t.Errorf("Text = %q, want %q", resp.Text, "The quick brown fox jumps over the lazy dog.")
	}
}
