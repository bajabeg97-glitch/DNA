from dna_midi_studio.midi import MidiFile, MidiTrack, MidiEvent
from dna_midi_studio.song_reconstruction_planner import RegionRequest, plan_song_reconstruction


def _midi():
    ev=[]; o=0
    # bass ch0 track0, deliberately choppy/high-leap
    for s,e,p in [(0,20,36),(480,500,60),(960,980,36),(1440,1460,60)]:
        ev += [MidiEvent(s,o,'channel',0x90,bytes((p,80))), MidiEvent(e,o+1,'channel',0x80,bytes((p,0)))]; o+=2
    ev.append(MidiEvent(1920,o,'meta',None,b'',0x2F))
    return MidiFile(1,480,[MidiTrack(ev)])


def test_budget_limits_automatic_regions():
    m=_midi()
    req=[RegionRequest(f'r{i}','bass',0,0,0,1920,.95,True) for i in range(4)]
    out=plan_song_reconstruction(m,req,max_auto_regions=2,max_replace_regions=1)
    assert out['budget']['selected'] == 1
    assert out['budget']['selectedReplace'] == 1
    assert sum(1 for r in out['regions'] if r['selectedForAutomaticAction']) == 1
    assert sum(1 for r in out['regions'] if r['routing']=='DEFERRED_BY_REPLACE_BUDGET') == 3


def test_keep_regions_are_never_selected():
    m=_midi()
    req=[RegionRequest('good','bass',0,0,0,1920,.0,False)]
    out=plan_song_reconstruction(m,req,max_auto_regions=3)
    if out['regions'][0]['decision']['decision']=='KEEP':
        assert not out['regions'][0]['selectedForAutomaticAction']


def test_factory_velocity_and_hard_authority_stay_locked():
    out=plan_song_reconstruction(_midi(),[RegionRequest('x','bass',0,0,0,1920,.9,True)])
    assert out['policy']['velocityAuthority']=='FACTORY_ONLY'
    assert out['policy']['hardAuthority']=='CORE_INVARIANTS_AND_DEVICE_EVIDENCE'
    assert out['policy']['interactionEvidence']=='SOFT_ONLY'


def test_deterministic_ordering():
    m=_midi(); req=[RegionRequest('b','bass',0,0,0,1920,.9,True),RegionRequest('a','bass',0,0,0,1920,.9,True)]
    a=plan_song_reconstruction(m,req,max_auto_regions=1)
    b=plan_song_reconstruction(m,req,max_auto_regions=1)
    assert a==b
