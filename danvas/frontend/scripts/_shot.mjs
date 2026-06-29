import puppeteer from 'puppeteer-core'
const CHROME='C:/Program Files/Google/Chrome/Application/chrome.exe'
const sleep=ms=>new Promise(r=>setTimeout(r,ms))
const b=await puppeteer.launch({executablePath:CHROME,headless:'new',args:['--no-sandbox','--disable-gpu']})
const page=await b.newPage(); await page.setViewport({width:1100,height:720})
await page.goto('http://localhost:8000',{waitUntil:'networkidle2',timeout:25000}); await sleep(4500)
await page.evaluate(()=>{const r=document.querySelector('input[type="range"]');const set=Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,'value').set;set.call(r,'135');r.dispatchEvent(new Event('input',{bubbles:true}));r.dispatchEvent(new Event('change',{bubbles:true}))})
await sleep(1200)
await page.screenshot({path:'c:/Users/h/Desktop/my_danvas/scripts/_arrowtext.png'})
await b.close(); process.exit(0)
