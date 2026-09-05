from __future__ import annotations
from pathlib import Path
import json, hashlib, numpy as np, torch
from torch.utils.data import DataLoader,TensorDataset
from .song_context_model import MultiTrackContextNet,SongContextConfig

def train_song_context(dataset_path:str|Path,out_dir:str|Path,epochs:int=2,batch_size:int=512,seed:int=800):
    torch.manual_seed(seed); np.random.seed(seed); d=np.load(dataset_path)
    tensors={k:torch.from_numpy(d[k]) for k in ('contexts','chords','roles','meters','positions','targets','split')}
    cfg=SongContextConfig(); model=MultiTrackContextNet(cfg); opt=torch.optim.AdamW(model.parameters(),lr=2e-3,weight_decay=1e-4)
    def loader(which,shuffle):
        m=tensors['split']==which
        ds=TensorDataset(tensors['contexts'][m].float(),tensors['chords'][m].long(),tensors['roles'][m].long(),tensors['meters'][m].long(),tensors['positions'][m].float(),tensors['targets'][m].float())
        return DataLoader(ds,batch_size=batch_size,shuffle=shuffle)
    best=None; best_val=1e9; hist=[]
    for ep in range(1,epochs+1):
        model.train(); ls=[]
        for x,ch,r,me,p,y in loader(0,True):
            pred=model(x,ch,r,me,p); loss=((pred-y)**2).mean(); opt.zero_grad(); loss.backward(); torch.nn.utils.clip_grad_norm_(model.parameters(),1); opt.step(); ls.append(loss.item())
        model.eval(); vl=[]
        with torch.no_grad():
            for x,ch,r,me,p,y in loader(1,False): vl.append(((model(x,ch,r,me,p)-y)**2).mean().item())
        tr=float(np.mean(ls)); va=float(np.mean(vl)); hist.append({'epoch':ep,'trainMSE':tr,'validationMSE':va})
        if va<best_val: best_val=va; best={k:v.detach().cpu().clone() for k,v in model.state_dict().items()}
    model.load_state_dict(best); hl=[]; mae=[]
    with torch.no_grad():
        for x,ch,r,me,p,y in loader(2,False):
            pr=model(x,ch,r,me,p); hl.append(((pr-y)**2).mean().item()); mae.append((pr-y).abs().mean().item())
    out=Path(out_dir); out.mkdir(parents=True,exist_ok=True)
    ck=out/'song_context_model_v1.pt'; torch.save({'config':cfg.to_dict(),'state_dict':best},ck)
    report={'schema':'dna-song-context-training','version':'1.0','epochs':epochs,'bestValidationMSE':best_val,'holdoutMSE':float(np.mean(hl)),'holdoutMAE':float(np.mean(mae)),'velocityInput':False,'velocityTarget':False,'velocityOutputHead':False,'history':hist}
    (out/'song_context_training_report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    (out/'song_context_model_v1.sha256').write_text(hashlib.sha256(ck.read_bytes()).hexdigest()+'  '+ck.name+'\n')
    return report
