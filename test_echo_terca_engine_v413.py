from collections import Counter
import special_track_engine as ste


def note(pitch,start,end,vel=100,key='solo-prof'):
    on={'tick':start,'order':start*2+1,'kind':'channel','command':9,'channel':0,'data':[pitch,vel],'remove':False}
    off={'tick':end,'order':end*2+2,'kind':'channel','command':8,'channel':0,'data':[pitch,0],'remove':False}
    return {'pitch':pitch,'channel':0,'on':on,'off':off,'instrumentKey':key}

def track(index,name):
    return {'index':index,'events':[{'tick':0,'order':0,'kind':'meta','metaType':3,'payload':name.encode()}],'endTick':4000}

PROFILE={'samples':100,'register':{'low':48,'high':96},'curve':{'floor':45,'soft':58,'lowMid':70,'optimal':82,'highMid':94,'strong':106,'ceiling':118}}

def test_echo_repair_aligns_to_main_and_quiets_factory_layer():
    main_t=track(0,'Main Solo'); echo_t=track(1,'Echo')
    main=[note(60,0,180,105),note(62,480,660,100),note(64,960,1140,98),note(65,1440,1620,96)]
    echo=[note(60,245,430,90),note(62,725,910,88),note(64,1205,1390,85),note(65,1685,1870,84)]
    groups=[{'role':'solo','confidence':.95,'track':main_t,'channel':0,'notes':main,'trackName':'Main Solo'},
            {'role':'echo','confidence':.99,'track':echo_t,'channel':0,'notes':echo,'trackName':'Echo'}]
    stats=Counter()
    r=ste.optimize_existing_echo_terca(groups,{},1920,480,{'solo-prof':PROFILE},{},stats)
    assert r[0]['kind']=='echo' and r[0]['mode'] in ('PRESERVE','REPAIR')
    assert echo[0]['on']['tick']==240
    assert echo[0]['on']['data'][1] < main[0]['on']['data'][1]
    assert echo[0]['off']['tick']-echo[0]['on']['tick'] < main[0]['off']['tick']-main[0]['on']['tick']


def test_terca_repair_uses_diatonic_third_and_main_timing():
    main_t=track(0,'Main Solo'); third_t=track(2,'Terca')
    main=[note(60,0,240,100),note(62,480,720,100),note(64,960,1200,100)]
    third=[note(64,5,250,90),note(65,485,730,90),note(68,965,1210,90)] # last is wrong, should G=67 in C major
    groups=[{'role':'solo','confidence':.95,'track':main_t,'channel':0,'notes':main,'trackName':'Main Solo'},
            {'role':'third','confidence':.99,'track':third_t,'channel':0,'notes':third,'trackName':'Terca'}]
    harmony={0:{'root':0,'quality':'major','pitchClasses':{0,4,7},'confidence':.9,'score':1}}
    stats=Counter()
    r=ste.optimize_existing_echo_terca(groups,harmony,1920,480,{'solo-prof':PROFILE},{},stats)
    assert r[0]['kind']=='third'
    assert [n['on']['tick'] for n in third[:2]]==[0,480]
    assert third[0]['pitch']==64
    assert third[1]['pitch']==65
    # third source E -> G
    assert third[2]['pitch']==67


def test_low_similarity_explicit_echo_rebuilds_from_main():
    main_t=track(0,'Main Solo'); echo_t=track(1,'Echo')
    main=[note(60,0,220,100),note(62,480,700,100),note(64,960,1180,100),note(65,1440,1660,100)]
    bad=[note(70,100,200,100),note(71,700,800,100),note(72,1300,1400,100)]
    groups=[{'role':'solo','confidence':.95,'track':main_t,'channel':0,'notes':main,'trackName':'Main Solo'},
            {'role':'echo','confidence':.99,'track':echo_t,'channel':0,'notes':bad,'trackName':'Echo'}]
    stats=Counter()
    r=ste.optimize_existing_echo_terca(groups,{},1920,480,{'solo-prof':PROFILE},{},stats)
    assert r[0]['mode']=='REBUILD'
    assert all(n['on']['remove'] for n in bad)
    generated=[e for e in echo_t['events'] if e.get('kind')=='channel' and e.get('command')==9]
    assert generated

def test_unnamed_legacy_aux_track_is_inferred_from_relationship():
    main_t=track(0,'Track 1'); aux_t=track(1,'Track 2')
    main=[note(60,0,180,100),note(62,480,660,100),note(64,960,1140,100),note(65,1440,1620,100)]
    aux=[note(60,240,360,75),note(62,720,840,75),note(64,1200,1320,75),note(65,1680,1800,75)]
    groups=[{'role':'solo','confidence':.88,'track':main_t,'channel':0,'notes':main,'trackName':'Track 1'},
            {'role':'solo','confidence':.78,'track':aux_t,'channel':0,'notes':aux,'trackName':'Track 2'}]
    stats=Counter()
    r=ste.optimize_existing_echo_terca(groups,{},1920,480,{'solo-prof':PROFILE},{},stats)
    assert r and r[0]['kind']=='echo' and r[0]['inferred'] is True
