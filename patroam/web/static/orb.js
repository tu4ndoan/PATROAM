/* PATROAM neural-network orb — pure Canvas2D, no WebGL/libraries.
   Usage: const orb = Orb.init(canvas); orb.setState('listening'); */
(function(){
  const lerp=(a,b,t)=>a+(b-a)*t;
  const hex=h=>{h=h.replace('#','');return [parseInt(h.slice(0,2),16),parseInt(h.slice(2,4),16),parseInt(h.slice(4,6),16)];};
  const mix=(a,b,t)=>[Math.round(lerp(a[0],b[0],t)),Math.round(lerp(a[1],b[1],t)),Math.round(lerp(a[2],b[2],t))];
  const rgba=(c,a)=>`rgba(${c[0]},${c[1]},${c[2]},${a})`;

  const STATES={
    idle:      {c1:hex('#3357ff'),c2:hex('#c23bff'),spin:0.05,flow:0.8,energy:0.55,auto:0},
    sleeping:  {c1:hex('#2f6bff'),c2:hex('#b13bff'),spin:0.06,flow:0.7,energy:0.6, auto:0},
    listening: {c1:hex('#2f9bff'),c2:hex('#ff3bd0'),spin:0.16,flow:1.6,energy:1.0, auto:3.0},
    thinking:  {c1:hex('#6a4bff'),c2:hex('#ff3bd0'),spin:0.30,flow:2.2,energy:1.0, auto:1.5},
    speaking:  {c1:hex('#22c3ff'),c2:hex('#ff4bd6'),spin:0.20,flow:2.0,energy:1.2, auto:1.1},
  };

  function init(cv){
    const ctx=cv.getContext('2d');
    let DPR=Math.min(window.devicePixelRatio||1,1.5), W=0,H=0;
    function resize(){ W=innerWidth;H=innerHeight;cv.width=Math.round(W*DPR);cv.height=Math.round(H*DPR);ctx.setTransform(DPR,0,0,DPR,0,0);makeStars(); }
    addEventListener('resize',resize);

    let target=STATES.idle;
    const cur={c1:target.c1.slice(),c2:target.c2.slice(),spin:target.spin,flow:target.flow,energy:target.energy,auto:0};

    // Node graph.
    const GA=Math.PI*(3-Math.sqrt(5)), nodes=[];
    const SHELLS=[{r:0,n:1,sz:3.2},{r:0.5,n:26,sz:2.3},{r:0.85,n:64,sz:1.8},{r:1.18,n:120,sz:1.3}];
    for(const sh of SHELLS) for(let k=0;k<sh.n;k++){
      let x,y,z;
      if(sh.n===1){x=y=z=0;} else {const yy=1-(k+0.5)/sh.n*2,rad=Math.sqrt(Math.max(0,1-yy*yy)),th=k*GA;
        x=Math.cos(th)*rad*sh.r;y=yy*sh.r;z=Math.sin(th)*rad*sh.r;}
      nodes.push({x,y,z,sz:sh.sz,rad:Math.hypot(x,y,z),phase:Math.random()*6.28,col:0,sx:0,sy:0,persp:1});
    }
    for(const nd of nodes) nd.col=Math.max(0,Math.min(1,(nd.x+1.2)/2.4));

    const edges=[], seen=new Set();
    for(let i=0;i<nodes.length;i++){ const a=nodes[i],d=[];
      for(let j=0;j<nodes.length;j++){ if(i===j)continue; const b=nodes[j]; d.push([(a.x-b.x)**2+(a.y-b.y)**2+(a.z-b.z)**2,j]); }
      d.sort((p,q)=>p[0]-q[0]);
      for(let m=0;m<2;m++){ const j=d[m][1],key=i<j?i+'_'+j:j+'_'+i; if(!seen.has(key)){seen.add(key);edges.push({a:i,b:j});} } }
    const NB=8;
    for(const ed of edges){ const c=(nodes[ed.a].col+nodes[ed.b].col)*0.5; ed.bucket=Math.min(NB-1,Math.max(0,Math.round(c*(NB-1)))); }

    let stars=[];
    function makeStars(){ stars=[]; const n=Math.round(W*H/14000);
      for(let i=0;i<n;i++) stars.push({x:Math.random()*W,y:Math.random()*H,b:0.2+Math.random()*0.5,ph:Math.random()*6.28}); }
    resize();

    // Interaction.
    let rotX=-0.2,rotY=0.5,zoom=1,dragging=false,moved=false,autoSpin=true,lastX,lastY;
    const pulses=[];
    function addPulse(){ pulses.push(performance.now()/1000); if(pulses.length>5)pulses.shift(); }
    cv.addEventListener('pointerdown',e=>{dragging=true;moved=false;autoSpin=false;lastX=e.clientX;lastY=e.clientY;});
    addEventListener('pointerup',()=>{ if(dragging&&!moved)addPulse(); dragging=false; setTimeout(()=>autoSpin=true,2500); });
    addEventListener('pointermove',e=>{ if(!dragging)return; const dx=e.clientX-lastX,dy=e.clientY-lastY;
      if(Math.abs(dx)+Math.abs(dy)>3)moved=true; rotY+=dx*0.006; rotX=Math.max(-1.4,Math.min(1.4,rotX+dy*0.006)); lastX=e.clientX;lastY=e.clientY; });
    cv.addEventListener('wheel',e=>{e.preventDefault();zoom=Math.max(0.55,Math.min(2.4,zoom*(1-e.deltaY*0.0012)));},{passive:false});

    const D=3.4; let t=0,last=performance.now(),autoT=0;
    function project(p,scale,cx,cy,cosX,sinX,cosY,sinY){
      const x1=p.x*cosY+p.z*sinY,z1=-p.x*sinY+p.z*cosY,y2=p.y*cosX-z1*sinX,z2=p.y*sinX+z1*cosX,persp=D/(D-z2);
      return {sx:cx+x1*persp*scale,sy:cy-y2*persp*scale,persp}; }
    function pulseBoost(rad,now){ let s=0; for(const p of pulses){const w=(now-p)*1.2; if(w>1.8)continue; const d=rad-w; s+=Math.exp(-(d*d)/0.02);} return s; }

    function frame(now){
      requestAnimationFrame(frame);
      const dt=Math.min(0.05,(now-last)/1000); last=now; t+=dt;
      const k=Math.min(1,dt*3);
      cur.c1=mix(cur.c1,target.c1,k);cur.c2=mix(cur.c2,target.c2,k);
      cur.spin=lerp(cur.spin,target.spin,k);cur.flow=lerp(cur.flow,target.flow,k);
      cur.energy=lerp(cur.energy,target.energy,k);cur.auto=lerp(cur.auto,target.auto,k);
      if(autoSpin) rotY+=cur.spin*dt;
      if(cur.auto>0.05){ autoT+=dt; if(autoT>cur.auto){autoT=0;addPulse();} }

      const cx=W/2,cy=H/2,scale=Math.min(W,H)*0.32*zoom,nowS=now/1000;
      const cosX=Math.cos(rotX),sinX=Math.sin(rotX),cosY=Math.cos(rotY),sinY=Math.sin(rotY);

      ctx.globalCompositeOperation='source-over';
      const bg=ctx.createRadialGradient(cx,cy,0,cx,cy,Math.max(W,H)*0.7);
      bg.addColorStop(0,'#100726');bg.addColorStop(1,'#04030a');ctx.fillStyle=bg;ctx.fillRect(0,0,W,H);
      ctx.globalCompositeOperation='lighter';
      const gx=cx+scale*0.22,gy=cy+scale*0.42,halo=ctx.createRadialGradient(gx,gy,0,gx,gy,scale*1.0);
      halo.addColorStop(0,rgba(cur.c2,0.22));halo.addColorStop(1,rgba(cur.c2,0));ctx.fillStyle=halo;ctx.fillRect(0,0,W,H);
      for(const s of stars){const a=s.b*(0.4+0.6*Math.sin(t*2+s.ph));ctx.fillStyle='rgba(150,160,220,'+a.toFixed(3)+')';ctx.fillRect(s.x,s.y,1.3,1.3);}

      for(const p of nodes){const x1=p.x*cosY+p.z*sinY,z1=-p.x*sinY+p.z*cosY,y2=p.y*cosX-z1*sinX,z2=p.y*sinX+z1*cosX,persp=D/(D-z2);
        p.sx=cx+x1*persp*scale;p.sy=cy-y2*persp*scale;p.persp=persp;}

      const paths=[]; for(let i=0;i<NB;i++)paths.push(new Path2D());
      for(const ed of edges){const a=nodes[ed.a],b=nodes[ed.b];const pth=paths[ed.bucket];pth.moveTo(a.sx,a.sy);pth.lineTo(b.sx,b.sy);}
      ctx.lineWidth=0.8;
      for(let i=0;i<NB;i++){const c=mix(cur.c1,cur.c2,i/(NB-1)),flow=0.5+0.5*Math.sin(t*cur.flow+i*0.9),al=Math.min(0.7,(0.10+0.13*flow)*cur.energy);
        ctx.strokeStyle=rgba(c,al);ctx.stroke(paths[i]);}

      for(const p of nodes){const breath=0.55+0.45*Math.sin(t*1.6+p.phase),boost=pulseBoost(p.rad,nowS),
        a=Math.min(1,(0.5+0.5*breath)*cur.energy+boost),r=(1.0+p.sz*0.55)*p.persp*(1+boost*0.5),c=mix(cur.c1,cur.c2,p.col);
        ctx.fillStyle=rgba(c,a);ctx.beginPath();ctx.arc(p.sx,p.sy,r,0,6.2832);ctx.fill();}
    }
    requestAnimationFrame(frame);

    return { setState(name){ if(STATES[name]){target=STATES[name];addPulse();} }, pulse:addPulse };
  }
  window.Orb={init};
})();
