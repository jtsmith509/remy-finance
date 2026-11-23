import math, sys, json, time
import numpy as np
from scipy import stats
def sim(rng,R,W,mu,Hmax,ar=False,t3=False,q=0.10,delta=0.0):
    L=W+Hmax+2
    g = rng.standard_t(3,(R,L))/math.sqrt(3) if t3 else rng.standard_normal((R,L))
    P=np.zeros((R,L+1)); P[:,1:]=np.cumsum(g,1)
    if ar:
        c=np.empty((R,W)); c[:,0]=rng.standard_normal(R); e=rng.standard_normal((R,W))*0.8
        for t in range(1,W): c[:,t]=0.6*c[:,t-1]+e[:,t]
    else: c=rng.standard_normal((R,W))
    n=rng.poisson(10,(R,W))*(rng.random((R,W))>=q)
    cell=np.repeat(np.arange(R*W),n.ravel()); r=cell//W; t=cell%W; N=cell.size
    H=rng.integers(1,Hmax+1,N)
    G=P[r,t+H]-P[r,t]
    d=(mu+0.7*c.ravel()[cell]+0.3*rng.standard_normal(N))*G/np.sqrt(H)+0.5*rng.standard_normal(N)+delta
    S=np.bincount(cell,d,R*W).reshape(R,W); return S,n.astype(float)
def tstat(S,n,K):
    R,W=S.shape; N=n.sum(1); T=S.sum(1)/N; u=S-T[:,None]*n
    tt=np.arange(W)+0.5; j=np.arange(1,K+1)[:,None]
    M=math.sqrt(2/W)*np.cos(np.pi*j*tt/W)
    # orthonormality check
    assert np.allclose(M@M.T,np.eye(K))
    Om=((u@M.T)**2).mean(1); return T/(np.sqrt(W*Om)/N)
Rtot=int(sys.argv[1]); chunk=20000
cells=[]
for W in (43,95):
    for mu in (0,0.5,1.0,2.0):
        for H in (4,8,12):
            cells.append(dict(W=W,mu=mu,Hmax=H))
    cells+= [dict(W=W,mu=0.5,Hmax=8,ar=True),dict(W=W,mu=1.0,Hmax=8,ar=True),dict(W=W,mu=0.5,Hmax=8,t3=True),dict(W=W,mu=1.0,Hmax=8,t3=True),
             dict(W=W,mu=0.5,Hmax=8,q=0.35),dict(W=W,mu=1.0,Hmax=8,q=0.35),dict(W=W,mu=1.0,Hmax=8,delta=0.1367),dict(W=W,mu=0.0,Hmax=8,delta=0.1367)]
out=[]
for ci,c in enumerate(cells):
    W=c['W']; Ks={'k10':W//10,'k8':W//8,'k12':W//12}
    rej={f"{k}_{a}":0 for k in Ks for a in ('05','025')}
    rng=np.random.default_rng([555,ci]); done=0
    kw={k:v for k,v in c.items() if k not in('W','mu','Hmax')}
    while done<Rtot:
        m=min(chunk,Rtot-done); S,n=sim(rng,m,W,c['mu'],c['Hmax'],**kw)
        for k,K in Ks.items():
            a=np.abs(tstat(S,n,K))
            rej[k+'_05']+=int((a>stats.t.ppf(.975,K)).sum()); rej[k+'_025']+=int((a>stats.t.ppf(.9875,K)).sum())
        done+=m
    row=dict(c,**{k:v/Rtot for k,v in rej.items()}); out.append(row); print(json.dumps(row),flush=True)
json.dump(out,open(sys.argv[2],'w'),indent=1)
