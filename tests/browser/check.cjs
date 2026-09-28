const { chromium } = require(process.env.SPARROW_PLAYWRIGHT || require.resolve('playwright',{paths:[process.cwd()+'/frontend']}));
const fs=require('node:fs');
const path=require('node:path');
const out=path.resolve(process.env.SPARROW_VISUAL_OUT || 'docs/product-validation');fs.mkdirSync(out,{recursive:true});
(async()=>{
const browser=await chromium.launch({executablePath:process.env.SPARROW_CHROME||'/usr/bin/google-chrome',headless:true,args:['--no-sandbox','--autoplay-policy=no-user-gesture-required']});
const context=await browser.newContext({viewport:{width:1440,height:1000},reducedMotion:'reduce'});
const page=await context.newPage();const errors=[];const responses=[];
page.on('pageerror',e=>errors.push(e.message));page.on('response',r=>{if(r.status()>=400&&r.url().includes('/api/'))responses.push({path:new URL(r.url()).pathname,status:r.status()})});
const url=process.env.SPARROW_BROWSER_URL || 'http://127.0.0.1:8891';
await page.goto(url);await page.getByRole('link',{name:/^(Sign in|Set up Sparrow)$/}).click();await page.getByLabel('Username',{exact:true}).waitFor();
const needsSetup=await page.getByLabel('Setup code',{exact:true}).count();
if(needsSetup){
 await page.getByRole('heading',{name:'Set up your Sparrow server',exact:true}).waitFor();
 await page.screenshot({path:path.join(out,'owner-setup-desktop.png')});
 await page.setViewportSize({width:390,height:844});await page.screenshot({path:path.join(out,'administrator-setup-mobile.png'),fullPage:true});await page.setViewportSize({width:1440,height:1000});
 const password=page.getByLabel('Password',{exact:true});
 await password.fill('seven77');if(await password.evaluate(el=>el.checkValidity()))throw new Error('Seven-character password accepted');
 await password.fill('movienow');if(!await password.evaluate(el=>el.checkValidity()))throw new Error('Eight-character password rejected');
 await page.getByRole('button',{name:'Show password',exact:true}).click();
 if(await password.getAttribute('type')!=='text'||await password.inputValue()!=='movienow')throw new Error('Show password lost the entered value');
 await page.getByRole('button',{name:'Hide password',exact:true}).click();
 if(await password.getAttribute('type')!=='password')throw new Error('Hide password failed');
 await password.fill('');

 const setupCode=fs.readFileSync(path.join(process.env.SPARROW_BROWSER_STATE || '/tmp/sparrow-browser-check', 'owner-setup-code'),'utf8').trim();
 await page.goto(url+'/#setup_code='+encodeURIComponent(setupCode));
 await page.getByLabel('Setup code',{exact:true}).waitFor();
 await page.waitForFunction(()=>!location.hash);
 if(await page.getByLabel('Setup code',{exact:true}).inputValue()!==setupCode)throw new Error('The setup link did not fill its code');
 if(!(await (await page.request.get(url+'/api/v1/auth/status')).json()).needs_setup)throw new Error('Opening a setup link created an account');
 await page.getByLabel('Your name',{exact:true}).fill('Chris');
}
await page.getByLabel('Username',{exact:true}).fill('owner');await page.getByLabel('Password',{exact:true}).fill('fixture-password-123');await page.getByRole('button',{name:needsSetup?'Create administrator account':'Sign in',exact:true}).click();
if(needsSetup){await page.getByRole('button',{name:'Continue setup',exact:true}).waitFor();await page.screenshot({path:path.join(out,'owner-defaults-desktop.png')});await page.getByRole('button',{name:'Continue setup',exact:true}).click();await page.getByRole('button',{name:'Finish later',exact:true}).click();}
await page.getByRole('heading',{name:/^(Good (morning|afternoon|evening), Chris\.|Still up, Chris\?)$/}).waitFor({timeout:10000}).catch(async()=>{const welcome=page.getByRole('button',{name:'Continue to Sparrow'});if(await welcome.isVisible())await welcome.click();});
await page.getByRole('link',{name:'Open The Quiet Planet'}).waitFor();
await page.screenshot({path:path.join(out,'home-desktop.png'),fullPage:true});
await page.getByRole('link',{name:'Open The Quiet Planet'}).click();await page.getByRole('heading',{name:'The Quiet Planet',exact:true}).waitFor();
await page.screenshot({path:path.join(out,'movie-desktop.png'),fullPage:true});
await page.getByRole('link',{name:/^Play$|^Resume$/}).first().click();
await page.locator('video').waitFor();await page.waitForFunction(()=>document.querySelector('video')?.readyState>=2,{timeout:15000});
await page.locator('video').evaluate(v=>{v.pause();v.currentTime=12;});
await page.waitForFunction(()=>Math.abs(document.querySelector('video').currentTime-12)<.5);
await page.getByLabel('Subtitles',{exact:true}).selectOption({index:1});
await page.waitForFunction(()=>Array.from(document.querySelector('video').textTracks).some(t=>t.cues?.length===3));
await page.screenshot({path:path.join(out,'player-desktop.png'),fullPage:true});
// Switch to the second audio track: the real node must prepare HLS and retain the seek.
await page.getByLabel('Audio',{exact:true}).selectOption({index:1});
// Chrome may use native HLS or MediaSource. Both must load the converted audio.
await page.waitForFunction(()=>{const v=document.querySelector('video');return v&&(/blob:|\/hls\//.test(v.src))&&v.readyState>=2},null,{timeout:25000});
await page.locator('video').evaluate(v=>{v.pause();v.currentTime=18;});
await page.waitForFunction(()=>document.querySelector('video')?.readyState>=2&&!document.querySelector('video')?.seeking,{timeout:15000});
await page.screenshot({path:path.join(out,'player-converted-desktop.png'),fullPage:true});
await page.getByRole('link',{name:'Back to title'}).click();
const saved=await page.request.get(url+'/api/v1/catalogue');const items=await saved.json();
const watching=items.find(i=>i.title==='The Quiet Planet').assets[0].watch;
if(!watching||watching.position<10)throw new Error('Resume progress was not saved: '+JSON.stringify(watching));
for(const [route,name] of [['/library','library'],['/activity','activity'],['/settings','preferences'],['/settings/storage','storage'],['/settings/people','people'],['/settings/server','server'],['/settings/defaults','defaults'],['/discover','discover']]){
 await page.goto(url+route);await page.locator('h1').waitFor();await page.waitForTimeout(150);
 await page.screenshot({path:path.join(out,name+'-desktop.png'),fullPage:true});
 await page.setViewportSize({width:390,height:844});await page.screenshot({path:path.join(out,name+'-mobile.png'),fullPage:true});
 const overflow=await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth);if(overflow)throw new Error('Horizontal overflow: '+route);
 await page.setViewportSize({width:1440,height:1000});
}
await page.goto(url+'/title/tv/101');await page.getByRole('button',{name:/Choose episodes/}).click();await page.getByRole('dialog').waitFor();
await page.getByRole('checkbox').first().check();await page.setViewportSize({width:390,height:844});await page.screenshot({path:path.join(out,'request-episodes-mobile.png'),fullPage:true});
await page.getByRole('button',{name:'Request 1 episode',exact:true}).click();await page.getByRole('dialog').waitFor({state:'hidden'});
const jobs=await (await page.request.get(url+'/api/v1/jobs')).json();if(JSON.stringify(jobs[0].wanted_episodes)!=='{"1":[1]}')throw new Error('Incorrect exact scope');
await page.goto(url+'/activity');await page.getByRole('heading',{name:'Requests',exact:true}).waitFor();await page.waitForTimeout(250);if(await page.getByRole('button',{name:'Resume',exact:true}).first().isVisible())await page.getByRole('button',{name:'Resume',exact:true}).first().click();await page.getByRole('button',{name:'Pause',exact:true}).first().click();await page.getByRole('button',{name:'Resume',exact:true}).first().waitFor();
await page.screenshot({path:path.join(out,'activity-paused-mobile.png'),fullPage:true});
fs.writeFileSync(path.join(out,'browser-results.json'),JSON.stringify({passed:true,errors,responses,watching,exact_scope:jobs[0].wanted_episodes},null,2));
if(errors.length||responses.length)throw new Error(JSON.stringify({errors,responses}));
console.log(JSON.stringify({passed:true,screenshots:fs.readdirSync(out).filter(p=>p.endsWith('.png')).length,responses}));
await browser.close();
})().catch(e=>{console.error(e);process.exit(1)});
