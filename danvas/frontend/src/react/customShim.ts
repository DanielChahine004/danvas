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
export function customHelper(cid: string, forwardWheel: boolean): string {
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
    '</script>'
  )
}

/**
 * The full srcdoc for a Custom iframe: pass owner-wrapped documents through
 * untouched; give everything else the helper (and, for fragments, the base
 * reset) with the local composed id.
 */
export function prepareCustomDoc(html: string, cid: string,
                                 forwardWheel: boolean): string {
  if (html.includes(CUSTOM_SHIM_MARKER)) return html
  const body = isFullDocument(html) ? html : FRAGMENT_RESET + html
  return customHelper(cid, forwardWheel) + body
}
