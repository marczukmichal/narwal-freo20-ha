// 100ms chunks of 16 kHz mono PCM, independent of the device sample rate.
class NarwalMic extends AudioWorkletProcessor {
 constructor(){super();this.sum=0;this.count=0;this.phase=0;this.samples=[];}
 process(inputs){
  const input=inputs[0]?.[0];if(!input)return true;
  for(const sample of input){
   this.sum+=sample;this.count++;this.phase+=16000;
   if(this.phase>=sampleRate){
    this.phase-=sampleRate;this.samples.push(Math.round(Math.max(-1,Math.min(1,this.sum/this.count))*32767));this.sum=0;this.count=0;
    if(this.samples.length===1600){const pcm=new Int16Array(this.samples);this.samples=[];this.port.postMessage(pcm.buffer,[pcm.buffer]);}
   }
  }
  return true;
 }
}
registerProcessor('narwal-mic',NarwalMic);
