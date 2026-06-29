import puppeteer from 'puppeteer-core'
const CHROME='C:/Program Files/Google/Chrome/Application/chrome.exe'
const sleep=ms=>new Promise(r=>setTimeout(r,ms))
const b=await puppeteer.launch({executablePath:CHROME,headless:'new',args:['--no-sandbox','--disable-gpu']})
const page=await b.newPage(); await page.setViewport({width:1100,height:760,deviceScaleFactor:2})
await page.goto('http://localhost:8000',{waitUntil:'networkidle2',timeout:25000}); await sleep(4000)
const mid=await page.evaluate(()=>{const p=[...document.querySelectorAll('[data-pc-drawings] path')].find(e=>e.getAttribute('fill')==='none'&&e.getAttribute('marker-end'));if(!p)return null;const pt=p.getPointAtLength(p.getTotalLength()/2);const m=p.getScreenCTM();return{x:Math.round(pt.x*m.a+pt.y*m.c+m.e),y:Math.round(pt.x*m.b+pt.y*m.d+m.f)}})
await page.click('[data-pc-tool="select"]'); await sleep(60); await page.mouse.click(mid.x,mid.y); await sleep(250)
// crop around the SERVO/STATUS panels + arrow
await page.screenshot({path:'scripts/_as.png', clip:{x:120,y:430,width:520,height:300}})
await b.close(); process.exit(0)
