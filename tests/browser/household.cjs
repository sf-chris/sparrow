const {chromium}=require(process.env.SPARROW_PLAYWRIGHT||require.resolve('playwright',{paths:[process.cwd()+'/frontend']}));
const AxeBuilder=require(require.resolve('@axe-core/playwright',{paths:[process.cwd()+'/frontend']})).default;
const fs=require('node:fs');
const out=process.env.SPARROW_VISUAL_OUT || 'docs/product-validation';
fs.mkdirSync(out,{recursive:true});
(async()=>{
 const browser=await chromium.launch({executablePath:process.env.SPARROW_CHROME||'/usr/bin/google-chrome',headless:true,args:['--no-sandbox']});
 const owner=await browser.newContext({viewport:{width:390,height:844},reducedMotion:'reduce'});
 const base=process.env.SPARROW_BROWSER_URL || 'http://127.0.0.1:8891',headers={'X-Sparrow-Request':'1'};
 const login=await owner.request.post(base+'/api/v1/auth/login',{headers,data:{username:'owner',password:'fixture-password-123'}});
 if(login.status()!==200)throw new Error('Fixture login failed: '+await login.text());
 const page=await owner.newPage(),errors=[],audits=[];page.on('pageerror',e=>errors.push(e.message));
 async function audit(name){
 const modal=await page.getByRole('dialog').count();
 if(modal){
  await page.keyboard.press('Tab');
  if(!await page.getByRole('dialog').evaluate(d=>d.contains(document.activeElement)))throw new Error('Keyboard focus escaped the dialog');
  const action=page.getByRole('button',{name:'Save collection care',exact:true});
  if(await action.count()){const b=await action.boundingBox();if(!b||b.y+b.height>844)throw new Error('Care action is outside the phone viewport');}
 }
 await page.screenshot({path:`${out}/${name}-mobile.png`,fullPage:!modal});
 const a=await new AxeBuilder({page}).withTags(['wcag2a','wcag2aa','wcag21aa']).analyze();
 audits.push({name,violations:a.violations.map(v=>({id:v.id,impact:v.impact,nodes:v.nodes.map(n=>n.target)}))});
 }
 await page.goto(base+'/settings/people');await page.getByRole('button',{name:'Invite someone',exact:true}).click();
 await page.getByLabel('What can they do?',{exact:true}).selectOption('requester');await page.getByRole('button',{name:'Create invitation',exact:true}).click();
 const invitation=await page.getByLabel('Invitation link',{exact:true}).inputValue();await audit('invite-link');
 const guest=await browser.newContext({viewport:{width:390,height:844},reducedMotion:'reduce'});const person=await guest.newPage();person.on('pageerror',e=>errors.push(e.message));
 const username='person'+Date.now();await person.goto(invitation);await person.getByRole('heading',{name:'Welcome to the collection.',exact:true}).waitFor();await person.screenshot({path:out+'/invitation-mobile.png',fullPage:true});
 await person.getByLabel('Your name',{exact:true}).fill('Alex');await person.getByLabel('Username',{exact:true}).fill(username);await person.getByLabel('Password',{exact:true}).fill('movienow');await person.getByRole('button',{name:'Create account',exact:true}).click();
 await person.getByRole('button',{name:'Continue to Sparrow',exact:true}).waitFor();await person.getByLabel('Preferred audio',{exact:true}).selectOption('es');await person.getByRole('button',{name:'Continue to Sparrow',exact:true}).click();
 await person.getByRole('heading',{name:'What’s on tonight, Alex?',exact:true}).waitFor();
 const privatePrefs=await (await guest.request.get(base+'/api/v1/preferences')).json(),ownerPrefs=await (await owner.request.get(base+'/api/v1/preferences')).json();
 if(privatePrefs.effective.values.audio_pref!=='es'||ownerPrefs.effective.values.audio_pref==='es')throw new Error('Personal preference leaked or failed to save');
 if((await guest.request.get(base+'/api/v1/admin/users')).status()!==403)throw new Error('Requester reached admin accounts');
 await person.goto(base+'/settings/security');await person.getByText('Change your password',{exact:true}).click();await person.getByLabel('Current password',{exact:true}).fill('movienow');await person.getByLabel('New password',{exact:true}).fill('newmovie');await person.getByLabel('Repeat new password',{exact:true}).fill('newmovie');await person.getByRole('button',{name:'Change password',exact:true}).click();await person.getByText('Password changed.',{exact:true}).waitFor();
 await page.goto(base+'/title/tv/104');await page.getByRole('button',{name:/^(Edit care|Follow this show)$/}).first().click();await page.getByLabel('Episode scope',{exact:true}).selectOption('backfill');await audit('collection-care-dialog');await page.getByRole('button',{name:'Save collection care',exact:true}).click();await page.getByRole('dialog').waitFor({state:'hidden'});await page.getByRole('button',{name:'Edit care',exact:true}).waitFor();await audit('following-show');
 const items=await (await owner.request.get(base+'/api/v1/catalogue')).json();const asset=items.find(i=>i.tmdb_id===103).assets[0];
 await page.goto(base+'/watch/'+asset.id);await page.getByRole('heading',{name:'Subtitle care',exact:true}).waitFor();await audit('subtitle-repair');
 await page.getByLabel('Use your own subtitle file',{exact:true}).setInputFiles({name:'invalid.srt',mimeType:'text/plain',buffer:Buffer.from('This is not a subtitle file.')});await page.getByText('Subtitles need attention',{exact:true}).waitFor({timeout:20000});await audit('subtitle-repair-failure');
 // Mark subtitles mandatory: the library must remain honest while playback is usable.
 await owner.request.patch(base+'/api/v1/preferences',{headers,data:{values:{require_subtitles:true}}});
 const catalogue=await (await owner.request.get(base+'/api/v1/catalogue')).json();if(!catalogue.some(i=>i.state==='subtitles_pending'))throw new Error('Required subtitles appeared ready without verification');
 await owner.request.patch(base+'/api/v1/preferences',{headers,data:{values:{require_subtitles:null}}});
 // Explicit access revocation closes an existing browser session immediately.
 const users=await (await owner.request.get(base+'/api/v1/admin/users')).json();const user=users.find(u=>u.username===username);
 await owner.request.patch(base+'/api/v1/admin/users/'+user.id,{headers,data:{role:'viewer',library_scope:[],disabled:false}});
 if((await guest.request.get(base+'/api/v1/catalogue')).status()!==401)throw new Error('Revoked session remained active');
 fs.writeFileSync(out+'/household-results.json',JSON.stringify({errors,audits,invitation:true,personal_preferences:true,password_change:true,scope_revocation:true,subscription:true,repair_failure:true},null,2));
 await browser.close();if(errors.length||audits.some(a=>a.violations.length))throw new Error('Household checks failed: '+JSON.stringify({errors,audits}));console.log('Household, subscription and subtitle recovery checks passed.');
})().catch(e=>{console.error(e);process.exit(1)});
