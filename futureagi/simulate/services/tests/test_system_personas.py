from simulate.services.system_personas import SYSTEM_PERSONAS

REQUIRED_KEYS = {
    "persona_id",
    "name",
    "description",
    "gender",
    "age_group",
    "occupation",
    "location",
    "personality",
    "communication_style",
    "languages",
    "accent",
    "conversation_speed",
    "background_sound",
    "finished_speaking_sensitivity",
    "interrupt_sensitivity",
    "custom_properties",
    "additional_instruction",
}

ADVERSARIAL_NAMES = {
    "The Prompt Injector",
    "The Jailbreaker",
    "The System Prompt Probe",
    "The Data Exfiltrator",
    "The Provocateur",
}


class TestSystemPersonas:
    def test_persona_ids_unique(self):
        ids = [p["persona_id"] for p in SYSTEM_PERSONAS]
        assert len(ids) == len(set(ids))

    def test_every_persona_has_required_keys(self):
        for p in SYSTEM_PERSONAS:
            assert REQUIRED_KEYS.issubset(p.keys()), p.get("name")

    def test_adversarial_personas_present(self):
        names = {p["name"] for p in SYSTEM_PERSONAS}
        assert ADVERSARIAL_NAMES.issubset(names)

    def test_adversarial_personas_have_instructions(self):
        for p in SYSTEM_PERSONAS:
            if p["name"] in ADVERSARIAL_NAMES:
                assert p["additional_instruction"].strip()
