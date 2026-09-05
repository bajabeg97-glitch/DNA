from __future__ import annotations
from pathlib import Path
import json,time,random
import numpy as np
import torch
from torch import nn
from .relationship_sequence_model import RelationshipSequenceTransformer,SequenceModelConfig


def train_sequence_model(dataset_path,out_dir,epochs=18,batch_size=8,lr=4e-4,seed=823,patience=5):
    random.seed(seed);np.random.seed(seed);torch.manual_seed(seed); out=Path(out_dir);out.mkdir(parents=True,exist_ok=True); z=np.load(dataset_path)
    arr={k:z[k] for k in ('features','pitches','kind','action','interval','delay_qn','duration_ratio','mask','split')}; cfg=SequenceModelConfig(feature_dim=arr['features'].shape[-1],max_seq=arr['features'].shape[1]); model=RelationshipSequenceTransformer(cfg); opt=torch.optim.AdamW(model.parameters(),lr=lr,weight_decay=.01)
    # inverse sqrt class weighting so rare HOLD is learned without dominating.
    act=arr['action'][arr['mask']]; cnt=np.bincount(act,minlength=3).astype(float); w=np.sqrt(cnt.max()/np.maximum(1,cnt)); w=np.clip(w,1,8); action_ce=nn.CrossEntropyLoss(weight=torch.tensor(w,dtype=torch.float32),ignore_index=-100)
    def batches(which,shuffle=False):
        ids=np.flatnonzero(arr['split']==which); ids=ids.copy();
        if shuffle:np.random.shuffle(ids)
        for i in range(0,len(ids),batch_size):yield ids[i:i+batch_size]
    def loss_for(ids,training):
        f=torch.from_numpy(arr['features'][ids]).float();p=torch.from_numpy(arr['pitches'][ids]).long();k=torch.from_numpy(arr['kind'][ids]).long();m=torch.from_numpy(arr['mask'][ids]).bool();a=torch.from_numpy(arr['action'][ids]).long();it=torch.from_numpy(arr['interval'][ids]).long();d=torch.from_numpy(arr['delay_qn'][ids]).float();r=torch.from_numpy(arr['duration_ratio'][ids]).float();o=model(f,p,k,m)
        la=action_ce(o['action_logits'].reshape(-1,3),a.reshape(-1)); play=(a==0)&m
        li=nn.functional.cross_entropy(o['interval_logits'][play],it[play]) if play.any() else la*0
        ld=nn.functional.smooth_l1_loss(o['delay_qn'][play],d[play]) if play.any() else la*0
        lr_=nn.functional.smooth_l1_loss(o['duration_ratio'][play],r[play]) if play.any() else la*0
        return la+.35*li+.2*ld+.2*lr_
    best=None;best_loss=1e9;hist=[];bad=0;start=time.time()
    for ep in range(1,epochs+1):
        model.train(); vals=[]
        for ids in batches(0,True):opt.zero_grad();loss=loss_for(ids,True);loss.backward();torch.nn.utils.clip_grad_norm_(model.parameters(),1.0);opt.step();vals.append(float(loss.detach()))
        model.eval(); vv=[]
        with torch.no_grad():
            for ids in batches(1):vv.append(float(loss_for(ids,False)))
        vl=float(np.mean(vv)); hist.append({'epoch':ep,'trainLoss':float(np.mean(vals)),'validationLoss':vl})
        if vl<best_loss-1e-5:best_loss=vl;best={k:v.detach().cpu().numpy() for k,v in model.state_dict().items()};best_ep=ep;bad=0
        else:bad+=1
        if bad>=patience:break
    model.load_state_dict({k:torch.from_numpy(v) for k,v in best.items()}); model.eval()
    hold=[]; correct=0; total=0; play_i=0; play_n=0
    with torch.no_grad():
        for ids in batches(2):
            f=torch.from_numpy(arr['features'][ids]).float();p=torch.from_numpy(arr['pitches'][ids]).long();k=torch.from_numpy(arr['kind'][ids]).long();m=torch.from_numpy(arr['mask'][ids]).bool();a=torch.from_numpy(arr['action'][ids]).long();it=torch.from_numpy(arr['interval'][ids]).long();o=model(f,p,k,m);pred=o['action_logits'].argmax(-1); valid=m&(a>=0); correct+=int((pred[valid]==a[valid]).sum()); total+=int(valid.sum()); play=valid&(a==0); ip=o['interval_logits'].argmax(-1); play_i+=int((ip[play]==it[play]).sum());play_n+=int(play.sum())
    np.savez_compressed(out/'relationship_sequence_transformer_v2.npz',**best); (out/'relationship_sequence_model_config.json').write_text(json.dumps(cfg.to_dict(),indent=2),encoding='utf-8')
    report={'schema':'dna-relationship-sequence-training','version':'2.0','objective':'NOTE_LEVEL_PLAY_SKIP_HOLD_PLUS_INTERVAL_DELAY_GATE','bestEpoch':best_ep,'bestValidationLoss':best_loss,'holdoutActionAccuracy':correct/max(1,total),'holdoutIntervalAccuracyOnPlay':play_i/max(1,play_n),'holdoutNotes':total,'history':hist,'actionClassWeights':w.tolist(),'velocityUsed':False,'elapsedSeconds':round(time.time()-start,3)}
    (out/'relationship_sequence_training_report.json').write_text(json.dumps(report,indent=2),encoding='utf-8');return report
