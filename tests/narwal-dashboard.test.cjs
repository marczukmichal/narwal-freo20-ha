const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
let Card;
const sandbox={HTMLElement:class{attachShadow(){this.shadowRoot={querySelector:()=>null};}},customElements:{get:()=>false,define:(_,klass)=>Card=klass},console,document:{hidden:false},setInterval,clearInterval};
vm.runInNewContext(fs.readFileSync('www/narwal/dashboard.js','utf8'),sandbox);
const plain=x=>JSON.parse(JSON.stringify(x));
function setup(){
 const card=new Card(),calls=[];
 card.config={vacuum:'vacuum.local',matter:'vacuum.matter'};
 card.renderPanel=()=>{};card.message=()=>{};
 card._hass={states:{'select.freo_20_clean_mode':{entity_id:'select.freo_20_clean_mode',state:'Auto',attributes:{options:['Auto']}}},callService:async(...args)=>calls.push(plain(args))};
 card.rooms=[{id:'1',name:'Kuchnia'},{id:'3',name:'Sypialnia'}];
 return {card,calls};
}
const click=(card,dataset)=>card.handle({dataset});
test('changing mode, power and rooms never starts cleaning',async()=>{
 const {card,calls}=setup();
 await click(card,{mode:'vacuum'});await click(card,{option:'suction',value:'strong'});await click(card,{room:'3'});
 await click(card,{action:'prepare-clean'});assert.equal(calls.length,0);
 await click(card,{action:'cancel'});assert.equal(calls.length,0);assert.equal(card.pending,null);
});
test('manual start sends chosen mode, power and exact rooms to cloud service',async()=>{
 const {card,calls}=setup();card.draft.mode='vacuum_and_mop';card.draft.sequence=true;card.draft.suction='quiet';card.draft.water='wet';card.draft.passes='2';card.selected.add('3');
 await click(card,{action:'prepare-clean'});await click(card,{action:'confirm'});
 assert.deepEqual(calls,[['narwal_chmura','sprzataj_pokoje',{rooms:[3],mode:'vacuum_then_mop',suction:'quiet',water:'wet',mop_strength:'normal',passes:2,route:'standard'}]]);
});
test('Auto always targets all home, uses Matter Auto then starts',async()=>{
 const {card,calls}=setup();card.selected.add('3');await click(card,{action:'prepare-clean'});
 assert.deepEqual(plain(card.pending.data.rooms),['all']);await click(card,{action:'confirm'});
 assert.deepEqual(calls,[['select','select_option',{entity_id:'select.freo_20_clean_mode',option:'Auto'}],['vacuum','start',{entity_id:'vacuum.matter'}]]);
});
test('a failed mode change never sends start',async()=>{
 const {card,calls}=setup();card._hass.callService=async(...args)=>{calls.push(args);throw new Error('offline')};await click(card,{action:'prepare-clean'});await click(card,{action:'confirm'});
 assert.equal(calls.length,1);assert.equal(card.busy,false);assert.ok(card.pending);
});
test('dock commands require confirmation and explicitly start or stop',async()=>{
 const {card,calls}=setup();const id='button.narwal_dry_mop';
 await click(card,{task:'dry_mop'});assert.equal(calls.length,0);await click(card,{action:'confirm'});assert.deepEqual(calls[0],['button','press',{entity_id:id}]);
 card._hass.states['sensor.narwal_mop_drying_time_left']={state:'10'};await click(card,{task:'dry_mop'});await click(card,{action:'confirm'});assert.deepEqual(calls[1],['vacuum','stop',{entity_id:'vacuum.local'}]);
});
test('mode switch cancels pending start; map controls issue no HA requests',async()=>{
 const {card,calls}=setup();card.transform=()=>{};await click(card,{action:'prepare-clean'});await click(card,{mode:'mop'});await click(card,{action:'confirm'});assert.equal(calls.length,0);
 await click(card,{action:'zoom-in'});assert.ok(card.zoom>1);await click(card,{action:'zoom-reset'});assert.equal(card.zoom,1);assert.equal(calls.length,0);
});

test('AI suction is passed through without overriding other chosen parameters',async()=>{
 const {card,calls}=setup();card.draft.mode='vacuum';await click(card,{option:'suction',value:'ai'});await click(card,{action:'prepare-clean'});await click(card,{action:'confirm'});
 assert.equal(calls[0][2].suction,'ai');assert.equal(calls[0][2].mode,'vacuum');assert.equal(calls[0][2].passes,1);
});

const key=(value,target={matches:()=>false})=>({key:value,target,preventDefault(){this.prevented=true;},composedPath(){return [target];}});
function driving(){const {card,calls}=setup();card.ctrlOn=true;card.tab='camera';card.cameraSession='viewer';card.cameraStatus='connected';card.steruj=async data=>calls.push(plain(data));return {card,calls};}
test('arrow hold moves once and release stops even after focus changes',async()=>{
 const {card,calls}=driving();const down=key('ArrowUp');card.handleDriveKey(down,true);card.handleDriveKey({...key('ArrowUp'),repeat:true},true);
 assert.equal(down.prevented,true);assert.equal(calls.length,1);
 card.handleDriveKey(key('ArrowUp',{matches:()=>true}),false);await card.driveStopping;
 assert.deepEqual(calls,[{akcja:'ruch',kierunek:'przod',predkosc:40},{akcja:'ruch',kierunek:'stop'}]);
});
test('text inputs, modifiers, disconnected video and inactive control never move',()=>{
 const {card,calls}=driving();card.handleDriveKey(key('ArrowDown',{matches:()=>true}),true);card.handleDriveKey({...key('ArrowUp'),ctrlKey:true},true);
 card.cameraStatus='connecting';card.handleDriveKey(key('ArrowUp'),true);card.cameraStatus='connected';card.ctrlOn=false;card.handleDriveKey(key('ArrowLeft'),true);
 assert.equal(calls.length,0);
});
test('release orders stop after in-flight velocity and never queues held moves',async()=>{
 const {card,calls}=driving();let resolve;card.steruj=data=>{calls.push(plain(data));return data.kierunek==='stop'?Promise.resolve():new Promise(r=>resolve=r);};
 card.driveStart('przod');const stopped=card.driveStop();assert.equal(calls.length,1);resolve();await stopped;
 assert.equal(calls[1].kierunek,'stop');assert.equal(card.driveTimer,null);
});
test('leaving camera while manual mode enables sends off after late enable',async()=>{
 const {card,calls}=driving();card.ctrlOn=false;let resolve;
 card.steruj=data=>{calls.push(plain(data));return data.tryb==='joystick'?new Promise(r=>resolve=r):Promise.resolve();};
 const enabling=card.toggleControl();await Promise.resolve();const off=card.ctrlOff();resolve();await enabling;await off;
 assert.equal(card.ctrlOn,false);assert.deepEqual(calls.map(x=>x.tryb),['joystick','wylacz']);
});
test('return to dock disables manual mode first and falls back to local entity',async()=>{
 const {card,calls}=driving();card._hass.states['vacuum.local']={state:'idle'};await click(card,{action:'dock'});
 assert.deepEqual(calls,[{akcja:'tryb',tryb:'wylacz'},['vacuum','return_to_base',{entity_id:'vacuum.local'}]]);
});
test('live dock sensor wins over stale Matter docked state',()=>{
 const {card}=setup();card._hass.states['vacuum.matter']={state:'docked'};card._hass.states['binary_sensor.narwal_docked']={state:'off'};
 assert.equal(card.robotState(),'idle');card._hass.states['binary_sensor.narwal_docked'].state='on';assert.equal(card.robotState(),'docked');
});
test('Freo Mind selects include real Smart options and persist through select service',async()=>{
 const {card,calls}=setup(),id='select.narwal_freo_mind_mop_humidity';card._hass.states[id]={state:'smart',attributes:{options:['smart','dry','normal','wet']}};card.openSettingSelect=id;
 assert.match(card.freoSettings(),/Slightly dry/);assert.match(card.freoSettings(),/Smart/);
 await click(card,{settingOption:id,value:'dry'});assert.deepEqual(calls,[['select','select_option',{entity_id:id,option:'dry'}]]);
});
