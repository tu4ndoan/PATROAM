/* PATROAM web client: WebSocket to the backend + browser mic & voice. */
(function(){
  const $=id=>document.getElementById(id);
  const orb=Orb.init($('orb'));
  const statusEl=$('status');
  function status(m){ statusEl.textContent=m||''; }

  // ── State / orb ─────────────────────────────────────────────────────────────
  let sessionUntil=0, alwaysOn=true, ttsOn=true, responding=false, speaking=false, currentSpeech='';
  const SESSION_MS=30000;
  // Keep roughly in sync with WAKE_PHRASES in patroam/config.py.
  const WAKE_PHRASES=['patroam','hey patroam','patrom','patroum','patron','petroam','patram',
    'hey bro','hey dude','hey agent p','agent p','hey agent pea','agent pea','hey p','hey pea','hey pee','hey peep'];
  const STOP=['stop listening','go to sleep','go back to sleep','that is all',"that's all",'never mind','stop patroam'];
  function sessionActive(){ return Date.now()<sessionUntil; }
  function rest(){ orb.setState(sessionActive()?'listening':(alwaysOn?'sleeping':'idle')); }

  // ── WebSocket ────────────────────────────────────────────────────────────────
  let ws, wsReady=false;
  function connect(){
    ws=new WebSocket((location.protocol==='https:'?'wss://':'ws://')+location.host+'/ws');
    ws.onopen=()=>{ wsReady=true; status('connected'); };
    ws.onclose=()=>{ wsReady=false; status('reconnecting…'); setTimeout(connect,1500); };
    ws.onmessage=e=>{ const m=JSON.parse(e.data); handle(m); };
  }
  function send(obj){ if(wsReady) ws.send(JSON.stringify(obj)); }

  // ── Chat panel ────────────────────────────────────────────────────────────────
  const logEl=$('log');
  let curBot=null, streamed=false;
  function addMsg(who,text,cls){ const d=document.createElement('div'); d.className='msg '+cls;
    const w=document.createElement('span'); w.className='who'; w.textContent=who; d.appendChild(w);
    const t=document.createTextNode(text||''); d.appendChild(t); d._t=t;
    logEl.appendChild(d); logEl.scrollTop=logEl.scrollHeight; return d; }
  function growMsg(d,text){ d._t.textContent+=text; logEl.scrollTop=logEl.scrollHeight; }
  function setMsg(d,text){ d._t.textContent=text; logEl.scrollTop=logEl.scrollHeight; }
  function toggleChat(show){ const c=$('chat'); const open=show===undefined?c.classList.contains('hidden'):show;
    c.classList.toggle('hidden',!open); document.body.classList.toggle('chat-open',open); }
  $('chatToggle').onclick=()=>toggleChat(); $('chatClose').onclick=()=>toggleChat(false);

  function handle(m){
    if(m.type==='models'){ const sel=$('model'); sel.innerHTML='';
      (m.models&&m.models.length?m.models:['(no models)']).forEach(x=>{const o=document.createElement('option');o.value=o.textContent=x;sel.appendChild(o);});
      if(m.current) sel.value=m.current; }
    else if(m.type==='token'){ streamed=true;
      if(!curBot) curBot=addMsg('PATROAM','','bot');
      growMsg(curBot, m.text);
      ttsBuf+=m.text; flushSentences(); }            // speak each sentence as it lands
    else if(m.type==='reply'){ responding=false;
      if(streamed){ if(curBot) setMsg(curBot, m.text); flushRest(); }
      else { addMsg('PATROAM', m.text, 'bot'); speakWhole(m.text); }
      streamed=false; curBot=null; }
    else if(m.type==='error'){ responding=false; status(m.text); rest(); }
    else if(m.type==='status'){ status(m.text); }
    else if(m.type==='stop'){ responding=false; stopSpeaking(); status('stopped'); rest(); }
    else if(m.type==='state'){ orb.setState(m.state); }   // mirror local-machine voice
  }

  function sendCommand(text){ if(!text)return; responding=true; status('“'+text+'”');
    orb.setState('thinking'); addMsg('You', text, 'you');
    ttsBuf=''; streamed=false; curBot=null; send({type:'text',text}); }

  // ── Browser voice out (speechSynthesis, British male if available) ────────────
  let voice=null;
  function pickVoice(){ const vs=speechSynthesis.getVoices();
    voice = vs.find(v=>/en-GB/i.test(v.lang)&&/male|ryan|george|daniel|thomas|arthur/i.test(v.name))
         || vs.find(v=>/en-GB/i.test(v.lang)) || vs.find(v=>/en[-_]/i.test(v.lang)) || vs[0]||null; }
  if('speechSynthesis' in window){ speechSynthesis.onvoiceschanged=pickVoice; pickVoice(); }

  // Speak chunks as they arrive so it starts talking right away.
  let ttsBuf='', pending=0;
  function speakChunk(text){ text=(text||'').trim();
    if(!text || !ttsOn || !('speechSynthesis' in window)) return;
    if(pending===0) orb.setState('speaking');
    speaking=true; pending++;
    sessionUntil=Date.now()+SESSION_MS;                   // don't sleep while speaking
    currentSpeech=(currentSpeech+' '+text).slice(-400);   // for echo guard
    const u=new SpeechSynthesisUtterance(text);
    if(voice) u.voice=voice; u.rate=1.0; u.pitch=1.0;
    u.onend=()=>{ pending--; if(pending<=0){ pending=0; speaking=false;
      sessionUntil=Date.now()+SESSION_MS; rest(); } };   // restart timer after speech
    speechSynthesis.speak(u);
  }
  // Break at sentence ends always, or at a clause (, ; :) once long enough.
  function nextChunk(buf){ for(let i=0;i<buf.length;i++){ const c=buf[i];
    if('.!?\n'.includes(c) || (',;:'.includes(c)&&i>=14)) return [buf.slice(0,i+1), buf.slice(i+1)]; }
    return [null, buf]; }
  function flushSentences(){ let chunk; for(;;){ [chunk,ttsBuf]=nextChunk(ttsBuf); if(chunk===null)break; speakChunk(chunk); } }
  function flushRest(){ const s=ttsBuf.trim(); ttsBuf=''; if(s) speakChunk(s); }
  function speakWhole(text){ status(text); ttsBuf=''; currentSpeech=''; speakChunk(text); }
  function stopSpeaking(){ speaking=false; pending=0; ttsBuf=''; try{speechSynthesis.cancel();}catch(_){} }

  // ── Browser voice in (Web Speech API) ─────────────────────────────────────────
  const SR=window.SpeechRecognition||window.webkitSpeechRecognition;
  let rec=null, wantListen=false, forceNext=false;
  function norm(s){ return s.toLowerCase().replace(/[^a-z0-9 ]/g,' ').replace(/\s+/g,' ').trim(); }
  function lev(a,b){ const m=a.length,n=b.length; if(!m)return n; if(!n)return m;
    let prev=Array.from({length:n+1},(_,i)=>i),cur=new Array(n+1);
    for(let i=1;i<=m;i++){ cur[0]=i; for(let j=1;j<=n;j++){ cur[j]=Math.min(prev[j]+1,cur[j-1]+1,prev[j-1]+(a[i-1]===b[j-1]?0:1)); } [prev,cur]=[cur,prev]; }
    return prev[n]; }
  function ratio(a,b){ const M=Math.max(a.length,b.length); return M?1-lev(a,b)/M:1; }
  const PHRASES=WAKE_PHRASES.map(p=>norm(p).split(' ').filter(Boolean)).sort((a,b)=>b.length-a.length);
  function findCommand(text){
    const toks=norm(text).split(' ').filter(Boolean), n=toks.length;
    for(let i=0;i<n;i++) for(const pt of PHRASES){ const L=pt.length; if(!L||i+L>n)continue;
      const a=toks.slice(i,i+L).join(''), b=pt.join('');
      if(a===b||ratio(a,b)>=0.78) return toks.slice(i+L).join(' '); }
    return null;
  }
  function isStop(text){ const n=norm(text); return STOP.some(p=>n.includes(norm(p))); }
  function isEcho(text){ if(!currentSpeech)return false; const cs=norm(currentSpeech),
    ws=norm(text).split(' ').filter(Boolean); if(!ws.length)return true;
    let hit=0; for(const w of ws) if(cs.includes(w))hit++; return hit/ws.length>=0.5; }

  function onResult(ev){
    const r=ev.results[ev.results.length-1]; if(!r.isFinal) return;
    const text=r[0].transcript.trim(); if(!text) return;
    // Barge-in: if PATROAM is talking and you say something new, stop it and act.
    if(speaking){
      if(isEcho(text)) return;                 // it just heard itself
      stopSpeaking();
      sessionUntil=Date.now()+SESSION_MS;
      const c=findCommand(text); const cmd=(c===null?text:c).trim();
      if(cmd) sendCommand(cmd);
      return;
    }
    if(forceNext){ forceNext=false; sessionUntil=Date.now()+SESSION_MS; sendCommand(text); return; }
    if(sessionActive()){
      if(isStop(text)){ sessionUntil=0; status('asleep — say “hey patroam”'); rest(); return; }
      const c=findCommand(text); const cmd=(c===null?text:c).trim();
      sessionUntil=Date.now()+SESSION_MS; if(cmd) sendCommand(cmd); return;
    }
    const c=findCommand(text); if(c===null) return;          // no wake word -> ignore
    sessionUntil=Date.now()+SESSION_MS; orb.setState('listening');
    if(c.trim()) sendCommand(c.trim()); else send({type:'wake'});   // bare wake -> greeting
  }

  function startRec(){
    if(!SR){ status('This browser has no speech recognition (try Chrome/Edge).'); return; }
    wantListen=true;
    rec=new SR(); rec.continuous=true; rec.interimResults=false; rec.lang='en-US';
    rec.onresult=onResult;
    rec.onerror=e=>{ if(e.error==='not-allowed') status('microphone blocked'); };
    rec.onend=()=>{ if(wantListen){ try{rec.start();}catch(_){} } };  // keep mic alive
    try{ rec.start(); status('always-on: say “hey patroam”'); rest(); }catch(_){}
  }
  function stopRec(){ wantListen=false; if(rec){ try{rec.stop();}catch(_){} } }

  // ── Controls ──────────────────────────────────────────────────────────────────
  $('send').onclick=()=>{ const v=$('text').value.trim(); if(v){ sendCommand(v); $('text').value=''; } };
  $('text').addEventListener('keydown',e=>{ if(e.key==='Enter'){e.preventDefault();$('send').onclick();} });
  $('model').onchange=()=>send({type:'model',name:$('model').value});
  $('refresh').onclick=async()=>{ try{const r=await fetch('/api/models');const j=await r.json();handle({type:'models',models:j.models});}catch(_){}};

  $('tts').onclick=()=>{ ttsOn=!ttsOn; $('tts').classList.toggle('on',ttsOn); if(!ttsOn)speechSynthesis.cancel(); };

  const mic=$('mic');
  mic.onclick=()=>{ if(!SR){status('no speech recognition');return;} forceNext=true; status('listening…'); orb.setState('listening');
    if(!wantListen) startRec(); };

  function applyWake(on){ alwaysOn=on; $('wake').classList.toggle('on',on); $('wake').textContent=on?'👂 Always-on':'😴 Always-on';
    if(on) startRec(); else { stopRec(); sessionUntil=0; rest(); } }
  $('wake').onclick=()=>applyWake(!alwaysOn);

  // ── Boot ──────────────────────────────────────────────────────────────────────
  connect();
  // Time-of-day greeting (browser can't speak until a user gesture).
  function timeGreeting(){ const h=new Date().getHours();
    return (h<12?'Good morning':h<18?'Good afternoon':'Good evening')+', Sir.'; }
  // Browsers need a user gesture before mic/audio. Try now; if blocked, start on first tap.
  let greeted=false;
  function enable(){ if('speechSynthesis' in window) pickVoice(); applyWake(true);
    if(!greeted){ greeted=true; const g=timeGreeting(); addMsg('PATROAM', g, 'bot'); speakWhole(g); }
    window.removeEventListener('pointerdown',enable); }
  window.addEventListener('pointerdown',enable, {once:true});
  status('click anywhere to enable voice, or just type below');
})();
