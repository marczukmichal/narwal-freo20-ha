// Audio stays in memory; all network traffic uses the authenticated HA socket.
export class NarwalCameraAudio {
 constructor(card){
  this.card=card;this.sources=new Set();this.generation=0;this.micGeneration=0;this.volume=.7;
  this.release=()=>this.stopTalk();this.shutdown=()=>this.stop();
  window.addEventListener('blur',this.release);window.addEventListener('pagehide',this.shutdown);
 }
 command(action,extra={},session=this.card.cameraSession){return this.card._hass.callWS({type:'narwal_cloud/audio',action,session_id:session,...extra});}
 mount(){
  const root=this.card.shadowRoot;this.listen=root.querySelector('[data-camera-listen]');this.talk=root.querySelector('[data-camera-talk]');this.note=root.querySelector('.audio-status');
  if(!this.listen||this.listen.dataset.bound)return;
  this.listen.dataset.bound='1';this.listen.addEventListener('click',()=>this.listening?this.stopListen():this.startListen());
  this.talk.addEventListener('contextmenu',e=>e.preventDefault());
  this.talk.addEventListener('pointerdown',e=>{if(e.button!==0)return;e.preventDefault();this.talk.setPointerCapture(e.pointerId);this.startTalk();});
  for(const event of ['pointerup','pointercancel','lostpointercapture'])this.talk.addEventListener(event,this.release);
  this.talk.addEventListener('keydown',e=>{if([' ','Enter'].includes(e.key)&&!e.repeat){e.preventDefault();this.startTalk();}});
  this.talk.addEventListener('keyup',e=>{if([' ','Enter'].includes(e.key)){e.preventDefault();this.stopTalk();}});
  this.talk.addEventListener('blur',this.release);
  root.querySelector('[data-camera-volume]').addEventListener('input',e=>{this.volume=Number(e.target.value)/100;if(this.gain)this.gain.gain.value=this.volume;});
  this.text=root.querySelector('[data-camera-text]');this.speak=root.querySelector('[data-camera-speak]');this.speakStop=root.querySelector('[data-camera-speak-stop]');
  this.speak?.addEventListener('click',()=>this.speakText());this.speakStop?.addEventListener('click',()=>{this.stopTalk();this.message('Talking ended.');});
  this.refresh();
 }
 refresh(){
  if(!this.listen)return;
  const connected=this.card.cameraSession&&this.card.cameraStatus==='connected';
  this.listen.disabled=!connected||this.listenStarting||this.speaking;this.talk.disabled=!connected||this.speaking;
  if(this.speak)this.speak.disabled=!connected||this.held||this.speaking;
  if(this.speakStop)this.speakStop.disabled=!this.speaking;
  if(this.text)this.text.disabled=!!this.speaking;
  this.listen.textContent=this.listening?'Mute listening':'Listen';this.listen.setAttribute('aria-pressed',String(!!this.listening));
  this.talk.textContent=this.talkId?'Talking, release to mute':this.held?'Starting the microphone…':'Hold to talk';this.talk.setAttribute('aria-pressed',String(!!this.held));
 }
 message(text){if(this.note)this.note.textContent=text;}
 async context(){const ctx=new AudioContext();await ctx.resume();return ctx;}
 async startListen(){
  if(this.listening||this.listenStarting)return;const generation=++this.generation,session=this.card.cameraSession;this.listenStarting=true;this.refresh();this.message('Connecting audio…');
  try{
   const ctx=await this.context();if(generation!==this.generation){await ctx.close();return;}this.playContext=ctx;this.gain=ctx.createGain();this.gain.gain.value=this.volume;this.gain.connect(ctx.destination);
   await this.command('listen_start',{},session);
   if(generation!==this.generation||session!==this.card.cameraSession){await this.command('listen_stop',{},session).catch(()=>{});return;}
   this.listening=true;this.playAt=0;this.listenSince=performance.now();this.lastAudio=0;this.message('Listening on.');this.poll(generation,session);
  }catch(e){if(generation===this.generation){this.stopListen();this.message(e.message||'Could not start listening');}}
  finally{if(generation===this.generation)this.listenStarting=false;this.refresh();}
 }
 async poll(generation,session){
  if(!this.listening||generation!==this.generation)return;
  try{
   const result=await this.command('poll',{},session);if(generation!==this.generation)return;
   if(result.pcm){
    const bytes=Uint8Array.from(atob(result.pcm),c=>c.charCodeAt(0));const view=new DataView(bytes.buffer),ctx=this.playContext,buffer=ctx.createBuffer(1,bytes.length/2,16000),channel=buffer.getChannelData(0);
    for(let i=0;i<channel.length;i++)channel[i]=view.getInt16(i*2,true)/32768;
    if(this.playAt>ctx.currentTime+.5){for(const s of this.sources)s.stop();this.sources.clear();this.playAt=0;}
    const source=ctx.createBufferSource();source.buffer=buffer;source.connect(this.gain);source.onended=()=>this.sources.delete(source);this.sources.add(source);
    this.playAt=Math.max(ctx.currentTime+.03,this.playAt);source.start(this.playAt);this.playAt+=buffer.duration;this.lastAudio=performance.now();this.message('Listening on.');
   }else if(performance.now()-(this.lastAudio||this.listenSince)>5000)this.message('No audio received from the robot. Listening is not confirmed.');
   this.pollTimer=setTimeout(()=>this.poll(generation,session),100);
  }catch(e){if(generation===this.generation){this.stopListen();this.message(e.message||'Audio connection lost');}}
 }
 stopListen(){
  const active=this.listening||this.listenStarting,session=this.card.cameraSession;this.generation++;this.listening=false;this.listenStarting=false;clearTimeout(this.pollTimer);
  for(const source of this.sources){try{source.stop();}catch{}}this.sources.clear();this.playContext?.close().catch(()=>{});this.playContext=null;
  if(active&&session)this.command('listen_stop',{},session).catch(()=>{});this.refresh();
 }
 async startTalk(){
  if(this.held||!this.card.cameraSession||this.card.cameraStatus!=='connected')return;
  if(!window.isSecureContext||!navigator.mediaDevices?.getUserMedia){this.message('The microphone needs HTTPS. Open Home Assistant over https.');return;}
  this.stopListen();this.held=true;const generation=++this.micGeneration,session=this.card.cameraSession;this.refresh();
  try{
   const stream=await navigator.mediaDevices.getUserMedia({audio:{channelCount:1,echoCancellation:true,noiseSuppression:true},video:false});
   if(!this.held||generation!==this.micGeneration){stream.getTracks().forEach(t=>t.stop());return;}this.micStream=stream;
   const ctx=await this.context();if(generation!==this.micGeneration){await ctx.close();return;}this.micContext=ctx;
   await ctx.audioWorklet.addModule('/local/narwal/audio-worklet.js?v=1');
   if(generation!==this.micGeneration)return;
   const result=await this.command('talk_start',{},session);
   if(generation!==this.micGeneration||!this.held){await this.command('talk_stop',{talk_id:result.talk_id},session).catch(()=>{});return;}
   this.talkId=result.talk_id;this.talkSession=session;this.seq=0;this.sending=false;this.pcmQueue=[];
   this.micNode=new AudioWorkletNode(ctx,'narwal-mic');this.micSource=ctx.createMediaStreamSource(stream);this.micSource.connect(this.micNode);
   const mute=ctx.createGain();mute.gain.value=0;this.micNode.connect(mute);mute.connect(ctx.destination);
   this.micNode.port.onmessage=e=>this.sendPCM(e.data,generation);this.message('The microphone works locally. Playback on the robot is not confirmed. Release to end.');this.refresh();
  }catch(e){if(generation===this.micGeneration){this.stopTalk();this.message(e.name==='NotAllowedError'?'The browser did not allow the microphone.':e.message||'Microphone unavailable');}}
 }
 async speakText(){
  if(this.held||this.speaking||!this.card.cameraSession||this.card.cameraStatus!=='connected')return;
  const text=this.text?.value.trim();if(!text){this.message('Type the text to speak.');return;}
  if(text.length>300){this.message('Type at most 300 characters.');return;}
  this.stopListen();this.speaking=true;this.held=true;
  const generation=++this.micGeneration,session=this.card.cameraSession;
  const active=()=>generation===this.micGeneration&&session===this.card.cameraSession&&this.held;
  this.refresh();this.message('Preparing speech…');
  try{
   const hass=this.card._hass;
   const {provider}=await hass.callWS({type:'tts/engine/get',engine_id:this.card.config.tts||'tts.piper'});
   if(!active())return;
   const language=provider.supported_languages?.find(value=>{const want=String(this.card.config.tts_language||'en').toLowerCase();return value.toLowerCase()===want||value.toLowerCase().startsWith(want+'-')||value.toLowerCase().startsWith(want+'_');});
   if(!language)throw new Error('The TTS engine has no voice for the configured language (tts_language).');
   const result=await hass.callApi('POST','tts_get_url',{engine_id:provider.engine_id,message:text,language,cache:false});
   if(!active())return;
   // Fetch only the local path returned by HA, never a cloud URL or credentials.
   if(typeof result.path!=='string'||!/^\/api\/tts_proxy\/[a-zA-Z0-9_.-]+$/.test(result.path))throw new Error('Invalid sound address from Home Assistant');
   this.ttsAbort=new AbortController();
   const response=await fetch(result.path,{signal:this.ttsAbort.signal,credentials:'same-origin'});
   if(!response.ok)throw new Error('Could not fetch speech from the TTS engine');
   const data=await response.arrayBuffer();if(!active())return;
   if(data.byteLength>4*1024*1024)throw new Error('The generated speech is too long');
   const decoder=new OfflineAudioContext(1,1,16000),decoded=await decoder.decodeAudioData(data);
   if(!active())return;
   if(decoded.duration>30)throw new Error('Speech can be at most 30 seconds');
   const offline=new OfflineAudioContext(1,Math.ceil(decoded.duration*16000),16000),source=offline.createBufferSource();
   source.buffer=decoded;source.connect(offline.destination);source.start();
   const rendered=await offline.startRendering();if(!active())return;
   const talk=await this.command('talk_start',{},session);
   if(!active()){await this.command('talk_stop',{talk_id:talk.talk_id},session).catch(()=>{});return;}
   this.talkId=talk.talk_id;this.talkSession=session;this.seq=0;this.pcmQueue=[];this.sending=false;this.refresh();
   this.message('Sending speech to the robot. Confirm playback by listening.');
   const samples=rendered.getChannelData(0),start=performance.now();
   // Include a short silent tail so the AAC encoder emits the final syllable.
   for(let offset=0;offset<samples.length+3200&&active();offset+=1600){
    const delay=start+offset/16-performance.now();
    if(delay>0)await new Promise(resolve=>setTimeout(resolve,delay));
    if(!active())return;
    if(performance.now()-(start+offset/16)>300)throw new Error('Connection too slow, talking stopped');
    const pcm=new ArrayBuffer(3200),view=new DataView(pcm);
    for(let i=0;i<1600;i++)view.setInt16(i*2,Math.round(Math.max(-1,Math.min(1,samples[offset+i]||0))*32767),true);
    await this.sendPCM(pcm,generation);
   }
   if(active()){await new Promise(resolve=>setTimeout(resolve,200));if(active()){this.stopTalk();this.message('Speech sent. Check that the robot played it.');}}
  }catch(e){if(active()){this.stopTalk();this.message(e.message||'Could not send speech to the robot');}}
 }
 async sendPCM(buffer,generation){
  if(!this.held||!this.talkId||generation!==this.micGeneration)return;
  this.pcmQueue??=[];this.pcmQueue.push({buffer,at:performance.now()});
  if(this.pcmQueue.length>3){this.stopTalk();this.message('Connection too slow, microphone muted.');return;}
  if(this.sending)return;
  this.sending=true;
  try{while(this.pcmQueue.length&&this.held&&generation===this.micGeneration){
   const next=this.pcmQueue.shift();if(performance.now()-next.at>300)throw new Error('Connection too slow, microphone muted.');
   const bytes=new Uint8Array(next.buffer),pcm=btoa(String.fromCharCode(...bytes));
   await this.command('talk_pcm',{talk_id:this.talkId,seq:this.seq++,pcm},this.talkSession);
  }}
  catch(e){if(generation===this.micGeneration){this.stopTalk();this.message(e.message||'Microphone muted after the connection was lost');}}
  finally{if(generation===this.micGeneration)this.sending=false;}
 }
 stopTalk(){
  this.micGeneration++;this.held=false;this.speaking=false;this.ttsAbort?.abort();this.ttsAbort=null;this.micStream?.getTracks().forEach(t=>t.stop());this.micStream=null;this.micSource?.disconnect();this.micNode?.disconnect();this.micNode=null;this.micSource=null;
  this.micContext?.close().catch(()=>{});this.micContext=null;
  const id=this.talkId,session=this.talkSession;this.talkId=null;this.talkSession=null;this.sending=false;this.pcmQueue=[];
  if(id&&session)this.command('talk_stop',{talk_id:id},session).catch(()=>{});this.refresh();
 }
 stop(){this.stopTalk();this.stopListen();this.message('');}
 destroy(){this.stop();window.removeEventListener('blur',this.release);window.removeEventListener('pagehide',this.shutdown);}
}
