const {chromium}=require(process.env.SPARROW_PLAYWRIGHT||require.resolve('playwright',{paths:[process.cwd()+'/frontend']}));
const AxeBuilder=require(require.resolve('@axe-core/playwright',{paths:[process.cwd()+'/frontend']})).default;
const fs=require('node:fs');
const out=process.env.SPARROW_VISUAL_OUT || 'docs/product-validation';
const base=process.env.SPARROW_BROWSER_URL || 'http://127.0.0.1:8891';
fs.mkdirSync(out,{recursive:true});
(async()=>{
 const browser=await chromium.launch({executablePath:process.env.SPARROW_CHROME||'/usr/bin/google-chrome',headless:true,args:['--no-sandbox']});
 const context=await browser.newContext({viewport:{width:390,height:844},reducedMotion:'reduce'});
 const login=await context.request.post(base+'/api/v1/auth/login',{headers:{'X-Sparrow-Request':'1'},data:{username:'owner',password:'fixture-password-123'}});
 if(login.status()!==200)throw new Error('Fixture login failed: '+await login.text());
 const page=await context.newPage();const results=[];
 for(const route of ['/','/library','/discover','/activity','/settings','/settings/storage','/settings/people','/settings/defaults','/settings/server','/title/tv/101']){
  await page.goto(base+route);await page.locator('h1').waitFor();await page.waitForTimeout(150);
  const audit=await new AxeBuilder({page}).withTags(['wcag2a','wcag2aa','wcag21aa']).analyze();
  const overflow=[];for(const width of [360,390,768,1440]){await page.setViewportSize({width,height:844});if(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth))overflow.push(width)}
  results.push({route,violations:audit.violations.map(v=>({id:v.id,impact:v.impact,nodes:v.nodes.map(n=>({target:n.target,summary:n.failureSummary}))})),overflow});
  await page.setViewportSize({width:390,height:844});
 }
 await page.getByRole('button',{name:/Choose episodes/}).click();await page.getByRole('dialog').waitFor();await page.getByRole('checkbox').first().check();
 const button=await page.getByRole('button',{name:'Request 1 episode',exact:true}).boundingBox();
 if(!button||button.y+button.height>844)throw new Error('Request footer is outside the phone viewport');
 await page.screenshot({path:out+'/request-episodes-mobile.png',fullPage:true});
 const dialog=await new AxeBuilder({page}).withTags(['wcag2a','wcag2aa','wcag21aa']).analyze();results.push({route:'episode-dialog',violations:dialog.violations.map(v=>({id:v.id,impact:v.impact,nodes:v.nodes.map(n=>({target:n.target,summary:n.failureSummary}))})),overflow:[]});
 const sw=await context.request.get('http://127.0.0.1:8891/sw.js');if(!sw.headers()['content-type'].includes('javascript'))throw new Error('Service worker has the wrong content type');
 const manifest=await context.request.get('http://127.0.0.1:8891/manifest.webmanifest');if((await manifest.json()).name!=='Sparrow')throw new Error('PWA manifest missing');
 fs.writeFileSync(out+'/accessibility-results.json',JSON.stringify(results,null,2));
 console.log(JSON.stringify(results.filter(r=>r.violations.length||r.overflow.length)));await browser.close();
 if(results.some(r=>r.violations.length||r.overflow.length))process.exitCode=1;
})().catch(e=>{console.error(e);process.exit(1)});
