from dna_midi_studio.instrument_behavior import PROFILES
from dna_midi_studio.instrument_performance_grammar import GRAMMARS, grammar_for
from dna_midi_studio.instrument_profile_matrix import POLICIES, complete_profile, profile_document, catalog

REQUIRED = {"register","gate","density","controllers","articulationNoise","interaction","repair","generation"}


def test_every_behavior_role_has_explicit_policy_and_grammar():
    roles=set(PROFILES)
    assert roles == set(POLICIES)
    assert roles <= set(GRAMMARS)
    for role in roles:
        assert grammar_for(role).role == role


def test_every_complete_profile_has_all_blocks_and_authorities():
    for role in sorted(POLICIES):
        d=profile_document(role)
        assert set(d["policies"]) == REQUIRED
        assert all(d["policies"][k] for k in REQUIRED)
        assert d["velocityAuthority"] == "FACTORY_ONLY"
        assert d["deviceArticulationAuthority"] == "EXACT_SOUND_PROFILE_REQUIRED"
        assert d["unknownDeviceFieldsPolicy"] == "KEEP_UNKNOWN_NEVER_GUESS"
        assert len(d["performanceGrammar"]["phrasePlan"]) == 4
        assert d["hardRules"]


def test_solo_profile_is_deep_and_protected():
    p=profile_document("solo")
    joined=" ".join(p["policies"]["repair"] + p["hardRules"])
    assert "continuity" in joined.lower()
    assert "PRESERVE_MAIN_MELODY" in p["hardRules"]
    assert "NONEMPTY_SOLO_NO_GENERIC_REPLACE" in p["hardRules"]


def test_drum_profile_is_element_identity_aware():
    p=profile_document("drums")
    assert any("kit-element" in x for x in p["policies"]["register"])
    assert "NO_DRUM_OCTAVE_REPAIR" in p["hardRules"]


def test_guitar_and_bass_device_triggers_are_not_inferred():
    for role in ("bass","rhythm-guitar","power-riff"):
        p=profile_document(role)
        assert p["deviceArticulationAuthority"] == "EXACT_SOUND_PROFILE_REQUIRED"
        assert any("UNVERIFIED" in x or "EXACT_SOUND" in x for x in p["hardRules"])


def test_catalog_reports_full_coverage():
    c=catalog()
    assert c["coverage"]["allHaveExplicitPolicy"] is True
    assert c["coverage"]["allHaveExplicitGrammar"] is True
    assert c["coverage"]["roleCount"] == len(PROFILES)
