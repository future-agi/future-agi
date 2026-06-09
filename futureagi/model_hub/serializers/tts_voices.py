from rest_framework import serializers

from model_hub.models.tts_voices import TTSVoice


class TTSVoiceSerializer(serializers.ModelSerializer):
    """A custom text-to-speech voice registered for the workspace, used to give
    simulated/voice agents a specific synthesized voice. It points at a voice in an
    external TTS provider (e.g. ElevenLabs) via provider + voice_id. Created voices
    are always voice_type='custom' (system voices are managed separately). Listed/read
    via list_tts_voices / get_tts_voice; remove via delete_tts_voice (soft delete)."""

    class Meta:
        model = TTSVoice
        fields = [
            "id",
            "name",
            "description",
            "voice_id",
            "provider",
            "model",
            "voice_type",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at", "voice_type"]
        extra_kwargs = {
            "name": {"help_text": "Human-readable display name for this custom voice."},
            "description": {
                "help_text": "Optional description of the voice (tone, accent, use case)."
            },
            "voice_id": {
                "help_text": "The voice identifier in the TTS provider's catalog "
                "(e.g. an ElevenLabs voice id)."
            },
            "provider": {
                "help_text": "TTS provider that hosts this voice (e.g. 'elevenlabs')."
            },
            "model": {
                "help_text": "Provider TTS model to synthesize with (e.g. the "
                "ElevenLabs model name)."
            },
            "voice_type": {
                "help_text": "Read-only: 'custom' for voices added here, 'system' "
                "for built-in voices."
            },
        }

    def create(self, validated_data):
        validated_data["organization"] = self.context["request"].user.organization
        # Ensure voice_type is custom
        validated_data["voice_type"] = "custom"
        return super().create(validated_data)
