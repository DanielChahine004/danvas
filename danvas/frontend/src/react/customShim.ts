// The Custom-panel iframe helper, injected by CustomView for any document
// that doesn't already carry one (the `window.canvas=` marker) — so a panel
// registered by ANY SDK gets the full in-iframe `canvas` API and the canvas
// interaction forwarding, with the *browser-local composed id* baked in.
//
// This is the frontend-owned successor of the script danvas/components/
// custom.py used to build server-side. Owner-side injection had two costs the
// move removes: every SDK had to ship its own copy of the script, and the
// baked id was the OWNER's — which a hub namespaces, so canvas.send() from a
// Python-authored iframe couldn't route back through a broker. Documents that
// still carry an owner-injected helper (older Python wheels, persisted
// canvases) are left untouched. The parent halves of every message live in
// bridge.ts's global window listener; the auto-fit script stays owner-side
// (it depends on the owner's h="auto"/w="auto" flags and is matched by source
// window, not id).

/** The marker an already-wrapped document carries. */
export const CUSTOM_SHIM_MARKER = 'window.canvas='

/** True when `html` is a full page that owns its own document structure. */
export function isFullDocument(html: string): boolean {
  const l = html.toLowerCase()
  return l.includes('<!doctype') || l.includes('<html') || l.includes('<body')
}

// The shared base reset a fragment is wrapped with (the frontend twin of
// Custom.compose): sane margins, box-sizing, transparent background, content
// centred in the frame.
const FRAGMENT_RESET =
  '<style>* { box-sizing: border-box; margin: 0; padding: 0;' +
  ' font-family: system-ui, sans-serif; }' +
  'body { background: transparent; display: flex;' +
  ' justify-content: center; align-items: center;' +
  ' min-height: 100vh; overflow: hidden; }</style>'

/**
 * The `<script>` prelude for a Custom iframe: the `canvas` API
 * (send/sendBinary/onPush/request/setView/viewport/chat/camera/mic), the
 * theme listener, error reporting, and the canvas gesture forwarding
 * (wheel-zoom unless `forwardWheel` is off, right-drag pan, context menu,
 * tool shortcuts).
 */
export function customHelper(cid: string, forwardWheel: boolean, sync = false): string {
  const id = JSON.stringify(cid)
  const wheel = forwardWheel
    ? "window.addEventListener('wheel',function(e){e.preventDefault();" +
      "parent.postMessage({__danvas_wheel:{x:e.clientX,y:e.clientY,d:e.deltaY}},'*');" +
      '},{passive:false,capture:true});'
    : ''
  return (
    '<script>window.canvas={' +
    'send:function(data){' +
    `parent.postMessage({__danvas:${id},data:data},'*');` +
    '},' +
    'sendBinary:function(buf){' +
    'var ab=buf instanceof ArrayBuffer?buf:(buf.buffer||buf);' +
    `parent.postMessage({__danvas_binary:${id},data:ab},'*',[ab]);` +
    '},' +
    "onPush:function(fn){window.addEventListener('message',function(e){" +
    'if(e.data&&e.data.__danvas!==undefined){fn(e.data.__danvas);}' +
    '});},' +
    // Shared state: one dict per panel, the same for every viewer. `state` is
    // kept current by the parent; setState merges, applies locally, and writes
    // through the property plane (a set_props frame) so every other viewer —
    // and Python (panel.state / @on_state) — converge on it.
    'state:{},' +
    "onState:function(fn){window.addEventListener('message',function(e){" +
    'if(e.data&&e.data.__danvas_state!==undefined){fn(window.canvas.state);}' +
    '});if(window.canvas._stateReady){setTimeout(function(){fn(window.canvas.state);},0);}},' +
    'setState:function(patch){' +
    'var s=Object.assign({},window.canvas.state||{},patch||{});window.canvas.state=s;' +
    `parent.postMessage({__danvas_set_state:${id},state:s},'*');` +
    '},' +
    'request:function(data){return new Promise(function(res,rej){' +
    "var rid='r'+Math.random().toString(36).slice(2)+Date.now();" +
    'function h(e){if(e.data&&e.data.__danvas_response===rid){' +
    "window.removeEventListener('message',h);" +
    "if(e.data.ok){res(e.data.data);}else{rej(new Error(e.data.error||'request failed'));}}}" +
    "window.addEventListener('message',h);" +
    `parent.postMessage({__danvas_request:${id},reqId:rid,data:data},'*');` +
    '});},' +
    'setView:function(view){' +
    "parent.postMessage({__danvas_setview:view||{}},'*');" +
    '},' +
    'viewport:function(cb){' +
    'function h(e){if(e.data&&e.data.__danvas_viewport!==undefined){cb(e.data.__danvas_viewport);}}' +
    "window.addEventListener('message',h);" +
    `parent.postMessage({__danvas_viewport:${id},action:'sub'},'*');` +
    "return function(){window.removeEventListener('message',h);" +
    `parent.postMessage({__danvas_viewport:${id},action:'unsub'},'*');};` +
    '},' +
    'chat:{' +
    "send:function(text){parent.postMessage({__danvas_chat:{action:'send',text:text}},'*');}," +
    "setName:function(name){parent.postMessage({__danvas_chat:{action:'setName',name:name}},'*');}," +
    'history:function(){return new Promise(function(res){' +
    "var rid='c'+Math.random().toString(36).slice(2)+Date.now();" +
    'function h(e){if(e.data&&e.data.__danvas_chat_reply===rid){' +
    "window.removeEventListener('message',h);res(e.data.log||[]);}}" +
    "window.addEventListener('message',h);" +
    "parent.postMessage({__danvas_chat:{action:'history',reqId:rid}},'*');});}," +
    'subscribe:function(cb){' +
    'function h(e){if(e.data&&e.data.__danvas_chat_msg!==undefined){cb(e.data.__danvas_chat_msg);}}' +
    "window.addEventListener('message',h);" +
    "parent.postMessage({__danvas_chat:{action:'sub'}},'*');" +
    "return function(){window.removeEventListener('message',h);" +
    "parent.postMessage({__danvas_chat:{action:'unsub'}},'*');};}," +
    'identity:function(cb){' +
    'function h(e){if(e.data&&e.data.__danvas_chat_identity!==undefined){cb(e.data.__danvas_chat_identity);}}' +
    "window.addEventListener('message',h);" +
    "parent.postMessage({__danvas_chat:{action:'idsub'}},'*');" +
    "return function(){window.removeEventListener('message',h);" +
    "parent.postMessage({__danvas_chat:{action:'idunsub'}},'*');};}" +
    '},' +
    'requestCamera:function(opts){' +
    `parent.postMessage({__danvas_camera:${id},action:'start',opts:opts||{}},'*');` +
    '},' +
    'releaseCamera:function(){' +
    `parent.postMessage({__danvas_camera:${id},action:'stop'},'*');` +
    '},' +
    'requestMicrophone:function(opts){' +
    `parent.postMessage({__danvas_mic:${id},action:'start',opts:opts||{}},'*');` +
    '},' +
    'releaseMicrophone:function(){' +
    `parent.postMessage({__danvas_mic:${id},action:'stop'},'*');` +
    '},' +
    // canvas.onSnapshot(fn): the panel supplies its own raster for exports
    // (fn -> dataURL or a Promise of one). Panels with WebGL should redraw
    // and capture in the same task (a presented GL buffer reads blank).
    'onSnapshot:function(fn){window.canvas._snapProvider=fn;}' +
    '};' +
    // Export raster: the parent can't read a sandboxed iframe's pixels, so
    // screenshots/PNG/SVG exports ask the iframe to rasterize ITSELF. The
    // default composites every same-origin <canvas> at its layout position
    // over the body background (HTML text isn't captured — panels that
    // need more register canvas.onSnapshot).
    "window.addEventListener('message',function(e){" +
    'if(e.data&&e.data.__danvas_state!==undefined){window.canvas.state=e.data.__danvas_state||{};window.canvas._stateReady=true;}' +
    '});' +
    "window.addEventListener('message',function(e){" +
    'if(!(e.data&&e.data.__danvas_snap))return;' +
    'var tok=e.data.__danvas_snap;' +
    'function reply(url){' +
    "parent.postMessage({__danvas_snap_result:{token:tok,dataUrl:url}},'*');}" +
    'function fallback(){try{' +
    'var dpr=devicePixelRatio||1,W=innerWidth,H=innerHeight;' +
    "var out=document.createElement('canvas');" +
    'out.width=Math.max(1,W*dpr);out.height=Math.max(1,H*dpr);' +
    "var g=out.getContext('2d');g.scale(dpr,dpr);" +
    'var bg=getComputedStyle(document.body).backgroundColor;' +
    "if(bg&&bg!=='rgba(0, 0, 0, 0)'){g.fillStyle=bg;g.fillRect(0,0,W,H);}" +
    "var cs=document.querySelectorAll('canvas');" +
    'for(var i=0;i<cs.length;i++){var c=cs[i],r=c.getBoundingClientRect();' +
    'if(r.width>0&&r.height>0){try{g.drawImage(c,r.left,r.top,r.width,r.height);}catch(_){}}}' +
    "return out.toDataURL('image/png');}catch(_){return null;}}" +
    'try{' +
    'if(window.canvas._snapProvider){' +
    'Promise.resolve(window.canvas._snapProvider())' +
    '.then(function(u){reply(u||fallback());},function(){reply(fallback());});' +
    '}else{reply(fallback());}' +
    '}catch(_){reply(fallback());}' +
    '});' +
    // themed=True: apply the parent-forwarded --pc-* variables + dark flag.
    "window.addEventListener('message',function(e){" +
    'if(e.data&&e.data.__danvas_theme){' +
    'var t=e.data.__danvas_theme,r=document.documentElement;' +
    'for(var k in t.vars){r.style.setProperty(k,t.vars[k]);}' +
    "r.style.colorScheme=t.dark?'dark':'light';}});" +
    // JS errors and unhandled rejections surface at the owner's terminal.
    'window.onerror=function(msg,src,line,col,err){' +
    `parent.postMessage({__danvas_error:{id:${id},` +
    "msg:msg+(src?' ('+src+':'+line+')':'')}},'*');" +
    'return false;};' +
    "window.addEventListener('unhandledrejection',function(e){" +
    'var r=e.reason;' +
    `parent.postMessage({__danvas_error:{id:${id},` +
    "msg:'Unhandled rejection: '+(r&&r.message||String(r))}},'*');});" +
    // Canvas gestures over the iframe (cross-document, so forwarded).
    wheel +
    'var _pan=false,_sx=0,_sy=0,_pm=0;' +
    "window.addEventListener('pointerdown',function(e){" +
    'if(e.button===2){_pan=true;_sx=e.screenX;_sy=e.screenY;_pm=0;' +
    'try{document.documentElement.setPointerCapture(e.pointerId);}catch(_){}}' +
    '},true);' +
    "window.addEventListener('pointermove',function(e){" +
    'if(!_pan)return;var dx=e.screenX-_sx,dy=e.screenY-_sy;_sx=e.screenX;_sy=e.screenY;' +
    '_pm+=Math.abs(dx)+Math.abs(dy);' +
    "parent.postMessage({__danvas_pan:{dx:dx,dy:dy}},'*');" +
    '},true);' +
    "window.addEventListener('pointerup',function(e){" +
    'if(e.button===2){_pan=false;' +
    "if(_pm<=4)parent.postMessage({__danvas_menu:{x:e.clientX,y:e.clientY}},'*');}" +
    '},true);' +
    "window.addEventListener('contextmenu',function(e){e.preventDefault();},true);" +
    "var _shortcuts='vhdrolatnep';" +
    "window.addEventListener('keydown',function(e){" +
    'if(e.ctrlKey||e.metaKey||e.altKey)return;' +
    "var t=e.target||{};var tn=(t.tagName||'');" +
    "if(tn==='INPUT'||tn==='TEXTAREA'||tn==='SELECT'||t.isContentEditable)return;" +
    "var k=e.key.length===1?e.key.toLowerCase():e.key;" +
    "if(k==='Escape'||_shortcuts.indexOf(k)>=0)" +
    "parent.postMessage({__danvas_key:{key:e.key}},'*');" +
    '});' +
    (sync ? AUTO_SYNC : '') +
    '</script>'
  )
}

// Custom(sync=True): share the page's NATIVE controls and button clicks
// through canvas.state, by DOM identity, with no change to the page.
//  - a control (input/select/textarea/contenteditable) with an id or name:
//    its value lives in state[key]; a change writes it, an incoming value
//    sets it and dispatches input+change so the page's own listeners run.
//  - a button (button / [role=button] / input[type=button|submit]): a click
//    is replicated to the other viewers via a bounded log in state._clicks
//    (element id, else a structural path); late joiners replay it in order,
//    so deterministic toggles converge.
//  - Plotly graphs (when the page has Plotly loaded): the live view — 3D
//    camera, 2D axis ranges — of the graph the user is TOUCHING is polled
//    every 100 ms (Plotly's own relayout fires only on release, and a 3D
//    drag can slip past it) and shared under state["_plotly:<graph id or
//    path>"], applied with Plotly.relayout on the other side. Only the
//    touched graph is shared: a page that links its graphs' cameras itself
//    replicates to the siblings on the far side, as it does locally — sharing
//    every graph raced that linking and left siblings misaligned.
// What no generic hook can see — state that lives only in JS and is driven
// by dragging in a library we don't know — a page shares itself with
// canvas.setState.
const AUTO_SYNC =
  '(function(){' +
  'var applying=false,seen=0;' +
  "var CTRL='input,select,textarea,[contenteditable=\"\"],[contenteditable=true]';" +
  "var BTN='button,[role=button],input[type=button],input[type=submit]';" +
  'function keyOf(el){return el.id||el.getAttribute("name")||null;}' +
  'function pathOf(el){var p=[];while(el&&el.nodeType===1&&el!==document.body){' +
  'if(el.id){p.unshift("#"+el.id);break;}var i=1,s=el;while((s=s.previousElementSibling))i++;' +
  'p.unshift(el.tagName.toLowerCase()+":nth-child("+i+")");el=el.parentElement;}return p.join(">");}' +
  'function isBtn(el){return !!(el.closest&&el.closest(BTN));}' +
  'function valOf(el){var t=el.type;if(t==="checkbox")return !!el.checked;' +
  'if(t==="radio"){var g=document.querySelector("input[type=radio][name="+JSON.stringify(el.name)+"]:checked");return g?g.value:null;}' +
  'if(el.isContentEditable)return el.innerHTML;return el.value;}' +
  'function setVal(el,v){var t=el.type;' +
  'if(t==="checkbox"){if(!!el.checked===!!v)return false;el.checked=!!v;return true;}' +
  'if(t==="radio"){var r=document.querySelector("input[type=radio][name="+JSON.stringify(el.name)+"][value="+JSON.stringify(String(v))+"]");' +
  'if(!r||r.checked)return false;r.checked=true;return true;}' +
  'if(el.isContentEditable){if(el.innerHTML===v)return false;el.innerHTML=v;return true;}' +
  'if(String(el.value)===String(v))return false;el.value=v;return true;}' +
  'function fire(el){el.dispatchEvent(new Event("input",{bubbles:true}));el.dispatchEvent(new Event("change",{bubbles:true}));}' +
  'function controls(){var out={};document.querySelectorAll(CTRL).forEach(function(el){' +
  'if(isBtn(el)||el.type==="button"||el.type==="submit"||el.type==="file"||el.type==="password")return;' +
  'var k=keyOf(el);if(k&&!(k in out))out[k]=el;});return out;}' +
  'document.addEventListener("input",function(e){onEdit(e);},true);' +
  'document.addEventListener("change",function(e){onEdit(e);},true);' +
  'function onEdit(e){if(applying)return;var el=e.target;if(!el||!el.matches||!el.matches(CTRL)||isBtn(el))return;' +
  'var k=keyOf(el);if(!k)return;var p={};p[k]=valOf(el);window.canvas.setState(p);}' +
  'document.addEventListener("click",function(e){if(applying)return;var b=e.target&&e.target.closest&&e.target.closest(BTN);' +
  'if(!b)return;var log=(window.canvas.state&&window.canvas.state._clicks)||[];' +
  'log=log.concat([pathOf(b)]);if(log.length>200)log=log.slice(-200);seen=log.length;window.canvas.setState({_clicks:log});},true);' +
  'var PK="_plotly:";' +
  'function plotlyKey(gd){return PK+(gd.id||pathOf(gd));}' +
  'function plotlyOf(k){var sel=k.slice(PK.length);var el=null;' +
  'try{el=sel.indexOf(">")<0&&sel.charAt(0)!=="#"?document.getElementById(sel):document.querySelector(sel.charAt(0)==="#"?sel:sel);}catch(_){}' +
  'return el&&el.on?el:null;}' +
  // round so a re-applied camera compares equal (no echo ping-pong)
  'function rnd(v){return JSON.parse(JSON.stringify(v,function(k,x){return typeof x==="number"?Math.round(x*1e6)/1e6:x;}));}' +
  'function viewOf(gd){var fl=gd._fullLayout;if(!fl)return null;var d={};' +
  'for(var n in fl){var o=fl[n];if(!o||typeof o!=="object")continue;' +
  'if(n.indexOf("scene")===0&&o._scene&&o._scene.getCamera){try{d[n+".camera"]=o._scene.getCamera();}catch(_){}}' +
  'else if((n.indexOf("xaxis")===0||n.indexOf("yaxis")===0)&&o.range&&!o._isSubplotObj){d[n+".range"]=o.range.slice();}}' +
  'return rnd(d);}' +
  'var activeGd=null,activeAt=0;' +
  'function touch(e){var gd=e.target&&e.target.closest&&e.target.closest(".js-plotly-plot");if(gd){activeGd=gd;activeAt=Date.now();}}' +
  'document.addEventListener("pointerdown",touch,true);document.addEventListener("wheel",touch,true);' +
  'document.addEventListener("pointermove",function(e){if(e.buttons)touch(e);},true);' +
  'function pollPlotly(){if(applying||!window.Plotly||!activeGd)return;' +
  'if(Date.now()-activeAt>1500){activeGd=null;return;}' +
  'var gd=activeGd;var d=viewOf(gd);if(!d)return;var j=JSON.stringify(d);' +
  'if(j===gd.__dvPoll)return;gd.__dvPoll=j;if(j===gd.__dvLast)return;' +
  'var k=plotlyKey(gd);var p={};p[k]=d;gd.__dvLast=j;window.canvas.setState(p);}' +
  'function hookPlotly(){if(!window.Plotly||window.__dvPlotlyPoll)return;window.__dvPlotlyPoll=setInterval(pollPlotly,100);}' +
  'function applyPlotly(s){if(!window.Plotly)return;for(var k in s){if(k.indexOf(PK)!==0)continue;' +
  'var gd=plotlyOf(k);if(!gd)continue;var j=JSON.stringify(rnd(s[k]));if(gd.__dvLast===j)continue;gd.__dvLast=j;' +
  'try{window.Plotly.relayout(gd,s[k]);}catch(_){}var v=viewOf(gd);if(v)gd.__dvPoll=JSON.stringify(v);}}' +
  'function apply(s){if(!s)return;applying=true;try{' +
  'var cs=controls();for(var k in cs){if(k in s&&k!=="_clicks"){if(setVal(cs[k],s[k]))fire(cs[k]);}}' +
  'hookPlotly();applyPlotly(s);' +
  'var log=s._clicks||[];for(var i=seen;i<log.length;i++){var sel=log[i];var el=null;' +
  'try{el=sel.charAt(0)==="#"&&sel.indexOf(">")<0?document.getElementById(sel.slice(1)):document.querySelector(sel);}catch(_){}' +
  'if(el)el.click();}seen=log.length;}finally{applying=false;}}' +
  'window.canvas.onState(apply);' +
  'if(document.readyState==="loading"){document.addEventListener("DOMContentLoaded",function(){apply(window.canvas.state);});}' +
  // graphs can be created any time after load: keep hooking new ones
  'setInterval(hookPlotly,1000);' +
  '})();'


/**
 * The full srcdoc for a Custom iframe: pass owner-wrapped documents through
 * untouched; give everything else the helper (and, for fragments, the base
 * reset) with the local composed id.
 */
export function prepareCustomDoc(html: string, cid: string,
                                 forwardWheel: boolean, sync = false): string {
  if (html.includes(CUSTOM_SHIM_MARKER)) return html
  const body = isFullDocument(html) ? html : FRAGMENT_RESET + html
  return customHelper(cid, forwardWheel, sync) + body
}
