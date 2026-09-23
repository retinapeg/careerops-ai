/* Run with node src/careerops/static/app.smoke.cjs. Browser layout is checked separately. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
class Element {
  constructor(tag) { this.tagName = tag; this.children = []; this.attributes = {}; this.dataset = {}; this.events = {}; this.value = ''; this.checked = false; this.disabled = false; this._text = ''; this.classList = {toggle: () => {}}; }
  append(...nodes) { for (const node of nodes) { this.children.push(node); node.parent = this; } }
  replaceChildren(...nodes) { this.children = []; this.append(...nodes); }
  setAttribute(key, value) { this.attributes[key] = value; if (key.startsWith('data-')) this.dataset[key.slice(5).replace(/-([a-z])/g, (_, c) => c.toUpperCase())] = value; }
  removeAttribute(key) { delete this.attributes[key]; }
  close() { this.open = false; }
  showModal() { this.open = true; }
  focus() {}
  scrollIntoView() {}
  querySelectorAll(selector) { return descend(this).slice(1).filter(node => node.tagName === selector); }
  addEventListener(name, handler) { this.events[name] = handler; }
  set textContent(value) { this._text = String(value); this.children = []; }
  get textContent() { return this._text + this.children.map(node => node.textContent).join(''); }
  querySelector(selector) { return descend(this).find(node => selector === 'button[type=submit]' ? node.tagName === 'button' && node.attributes.type === 'submit' : node.tagName === selector) || null; }
}
function descend(node) { return [node, ...node.children.flatMap(descend)]; }
const nodes = new Map();
const document = {createElement: tag => new Element(tag), createTextNode: text => Object.assign(new Element('#text'), {_text: text}), querySelector: selector => { if (!nodes.has(selector)) nodes.set(selector, new Element('div')); return nodes.get(selector); }, querySelectorAll: () => []};
const context = vm.createContext({document, Node: Element, URL, URLSearchParams, Intl, Date, console, setTimeout: () => 1, clearTimeout: () => {}, structuredClone, crypto: require('node:crypto'), requestAnimationFrame: callback => callback(), navigator: {}, window: {scrollY: 250, scrollTo: () => {}}, location: {hash: '#all'}});
const source = fs.readFileSync(__dirname + '/app.js', 'utf8').replace(/\ninit\(\);\s*$/, '\n');
vm.runInContext(source, context);
const run = expression => vm.runInContext(expression, context);
assert.equal(run("normalPage('search')"), 'all');
assert.equal(run("normalPage('pipeline')"), 'all');
assert.equal(run("normalPage('today')"), 'all');
assert.equal(run("normalPage('jobs')"), 'all');
assert.equal(run("normalPage('cv/7')"), 'cv-workflow');
run("state.data={inventory_counts:{all:{indexed:1232,recommended:372},london:{recommended:20}},jobs:[{recommendation_status:'not_recommended'}]}; renderNavigationCounts()");
assert.equal(nodes.get('#recommended-count').textContent, '372');
assert.equal(nodes.get('#pipeline-count').textContent, '1,232');
run("state.data={inventory_counts:{all:{indexed:0,recommended:0}}}; renderNavigationCounts()");
assert.equal(nodes.get('#recommended-count').textContent, '0');
run("state.data={jobs:[],evidence_ready:true}; renderNavigationCounts()");
assert.equal(nodes.get('#recommended-count').textContent, '—');
assert.equal(run("safeURL('javascript:alert(1)')"), null);
const refreshProgress = run("runProgress({found:262,new_unique:0,duplicates:262,checked:262})");
assert.ok(refreshProgress.includes('262 existing records refreshed'));
assert.ok(!refreshProgress.includes('duplicates removed'));
const card = run("jobCard({id:7,title:'<script>unsafe</script>',company:'Fixture',url:'https://example.com/jobs/7',location:'London',first_seen:'2026-09-11',evaluation:{eligibility:'blocked'},inventory:{work_authorisation:'clear'},candidacy:{band:'plausible',why:['Supported experience'],gaps:['Domain gap']}})");
assert.ok(card.textContent.includes('<script>unsafe</script>'));
assert.equal(descend(card).filter(node => node.tagName === 'script').length, 0);
for (const label of ['Why it could fit', 'Supported experience', 'Domain gap', 'Known eligibility blocker', 'Prepare application', 'Save']) assert.ok(card.textContent.includes(label), label);
for (const label of ['Generate tailored CV', 'Open application', 'Hide']) assert.ok(!card.textContent.includes(label), label);
assert.equal(descend(card).filter(node => node.tagName === 'button').length, 3, 'Two actions plus the clickable title');
const languageCard = run("jobCard({id:8,title:'AI trainer - Greek',company:'Fixture',presentation:{title:'AI trainer',notice:'Talent network — no current vacancy promised.',language:[{label:'Greek required — native or near-native. Your fluency is unconfirmed.'}]},candidacy:{band:'plausible',why:['Supported experience']}})");
assert.ok(!languageCard.textContent.includes('AI trainer - Greek'));
assert.ok(languageCard.textContent.indexOf('Greek required') < languageCard.textContent.indexOf('Plausible match'));
assert.ok(languageCard.textContent.includes('Talent network — no current vacancy promised.'));
const blockedLanguage = run("jobNotices({presentation:{language:[{label:'Greek required — requirement not met.',candidate_status:'not_met'}]}}).filter(Boolean)[0]");
assert.ok(blockedLanguage.className.includes('language-blocked'));
assert.equal(run('jobNotices({}).length'),0,'An absent notice must not become raw null text in replaceChildren');
const languageGapCard = run("jobCard({id:9,title:'Analyst - Greek',company:'Fixture',presentation:{title:'Analyst',language:[{label:'Greek required — your fluency is unconfirmed.'}]},evaluation:{eligibility:'needs_checking'},candidacy:{gaps:['Mandatory language: Greek','No verified production data ownership.']}})");
assert.ok(languageGapCard.textContent.includes('Greek required'));
assert.ok(languageGapCard.textContent.includes('No verified production data ownership.'));
assert.ok(!languageGapCard.textContent.includes('Mandatory language: Greek'));
const blockerGapCard = run("jobCard({id:10,title:'Analyst - Greek',presentation:{title:'Analyst',language:[{label:'Greek required — your fluency is unconfirmed.'}]},evaluation:{eligibility:'blocked',blockers:['A required qualification is not met.']},candidacy:{gaps:['Mandatory language: Greek','A less important presentation gap.']}})");
assert.ok(blockerGapCard.textContent.includes('A required qualification is not met.'));
assert.ok(!blockerGapCard.textContent.includes('A less important presentation gap.'));
const visaGapCard = run("jobCard({id:11,title:'Analyst - Greek',presentation:{title:'Analyst',language:[{label:'Greek required — your fluency is unconfirmed.'}]},evaluation:{eligibility:'blocked',blockers:['Mandatory language: Greek','Existing work authorisation is required.']},candidacy:{gaps:['Presentation could be stronger.']}})");
assert.ok(visaGapCard.textContent.includes('Existing work authorisation is required.'));
const unlabelledLanguageCard = run("jobCard({id:12,title:'Analyst',evaluation:{eligibility:'needs_checking'},candidacy:{gaps:['Mandatory language: Greek']}})");
assert.ok(unlabelledLanguageCard.textContent.includes('Mandatory language: Greek'), 'Language gaps are removed only when the separate notice represents them');
run("renderJob({id:13,title:'AI trainer - Greek',company:'Fixture',location:'Remote',url:'https://example.com/jobs/13',presentation:{title:'AI trainer',notice:'Talent network — no current vacancy promised.',notice_source_quote:'This is not an active job opening.',language:[{label:'Greek required — fluency unconfirmed.',source_quote:'Native or near-native proficiency in Greek.',source_url:'https://example.com/jobs/13'}]}})");
const jobDetails = nodes.get('#job-dialog-body');
assert.ok(jobDetails.children.every(node => node instanceof Element), 'Raw notice arrays must not reach native replaceChildren');
assert.equal(jobDetails.children[1].tagName, 'p');
assert.equal(jobDetails.children[1].textContent, 'Talent network — no current vacancy promised.');
assert.equal(jobDetails.children[2].textContent, 'Greek required — fluency unconfirmed.');
for (const label of ['Original advert title','AI trainer - Greek','This is not an active job opening.','Native or near-native proficiency in Greek.']) assert.ok(jobDetails.textContent.includes(label),label);
assert.ok(descend(jobDetails).some(node => node.tagName === 'blockquote' && node.textContent === 'Native or near-native proficiency in Greek.'));
assert.ok(descend(jobDetails).some(node => node.tagName === 'a' && node.attributes.href === 'https://example.com/jobs/13' && node.textContent.includes('language requirement')));
assert.ok(!jobDetails.textContent.includes('[object '));
run("inventoryState.location='remote'; inventoryState.view='all'; resetInventoryFilters(false)");
assert.equal(run('inventoryFilters().status'), 'all');
assert.equal(run('inventoryFilters().include_stretch'), true);
assert.equal(run('inventoryFilters().date_field'), 'discovered');
assert.equal(run('inventoryFilters().location'), '');
run("renderUniverseCounts({indexed:438,universe_visible:430,recommended:31,strong:7,plausible:18,stretch:6,by_location:{IL:{indexed:438,recommended:31,strong:7,plausible:18,stretch:6}}})");
assert.ok(nodes.get('#universe-summary').textContent.includes('438 indexed'));
assert.ok(nodes.get('#universe-summary').textContent.includes('31 recommended'));
assert.equal(nodes.get('#recommended-count').textContent, '—', 'A filtered inventory count must not overwrite the global sidebar badge');
run("inventoryState.view='recommended'; renderUniverseCounts({indexed:438,recommended:31,by_location:{IL:{indexed:438,recommended:31}}})");
assert.equal(descend(nodes.get('#universe-summary')).filter(node => node.tagName === 'article').length, 0);
assert.ok(nodes.get('#universe-summary').textContent.includes('438 indexed'));
assert.ok(nodes.get('#universe-summary').textContent.includes('31 recommended'));
assert.ok(fs.readFileSync(__dirname + '/index.html', 'utf8').includes('<details id="advanced-inventory-filters"'));
const workflow = {job_id:7,latest_material_id:5,max_automatic_rounds:2,versions:[{id:5,version:2,parent_id:4,cv_text:'Verified source text',formats:['txt'],analysis:{evidence_match:{score:62,label:'Partial; mandatory requirements unassessed',gaps:['Genuine domain gap']},ats_compatibility:{score:85}},diff:[{type:'delete',text:'Earlier wording'},{type:'insert',text:'Verified source text'}]}],reviews:[{id:'cv-review:5',material_id:5,round:1,status:'ready_to_revise',responses:{red:{findings:[]},blue:{findings:[],challenges:[{finding_id:'red:1',reason:'Transferable evidence is valid',evidence_ids:['E1']}]}},findings:[{id:'red:1',team:'red',severity:'high',cv_passage:'Earlier wording',advert_requirement:'Python',evidence_ids:['E1'],recommended_action:'Use verified evidence',validation:{status:'eligible',reason:'Source matches'}},{id:'blue:1',team:'blue',recommended_action:'Verify unsupported fact',validation:{status:'needs_verification',reason:'VERIFY evidence cannot be promoted'}}]}]};
context.workflowFixture = workflow;
run('renderCV(workflowFixture)');
const cv = nodes.get('#cv-dialog-body');
for (const label of ['Evidence / job match', 'Local document compatibility', 'Genuine domain gap', 'Partial; mandatory requirements unassessed', 'Transferable evidence is valid', 'Download red-team packet', 'Download blue-team packet', 'Review again', 'Automatic revision limit: 2']) assert.ok(cv.textContent.includes(label), label);
assert.equal(descend(cv).find(node => node.attributes['aria-label'] === 'Accept finding red:1').disabled, false);
assert.equal(descend(cv).find(node => node.attributes['aria-label'] === 'Accept finding blue:1').disabled, true);
assert.equal(descend(cv).filter(node => node.tagName === 'ins').length, 1);
assert.equal(descend(cv).filter(node => node.tagName === 'del').length, 1);


// Discovery remains the default; filtered recommendations never hide the primary search.
assert.equal(run("normalPage('')"), 'all');
assert.equal(run("pageName('all')"), 'Discover');
assert.equal(run("pageName('your-cv')"), 'Your profile');
const html = fs.readFileSync(__dirname + '/index.html', 'utf8');
assert.ok(!html.includes('id="page-today"'), 'Retired summary dashboard is removed');
assert.ok(html.includes('id="search-button">Find jobs'));
assert.ok(!html.includes('inventory-filter-details" open'));
assert.ok(html.indexOf('id="search-button"') < html.indexOf('id="advanced-inventory-filters"'));
run("state.data.profile={evidence:[]}; state.data.evidence_ready=false; inventoryState.view='all'; renderOnboarding()");
assert.equal(nodes.get('#onboarding-next-step').hidden,false);
assert.ok(run("jobCard({id:20,title:'Example role',candidacy:{band:'not_suitable'}}).textContent").includes('Fit not assessed'));
assert.ok(!run("jobCard({id:20,title:'Example role',candidacy:{band:'not_suitable'}}).textContent").includes('Not suitable'));
run("state.data.profile={evidence:[{status:'VERIFIED',sensitive:true}]}; renderOnboarding()");
assert.equal(nodes.get('#onboarding-next-step').hidden,false,'The server rejects sensitive or excluded evidence regardless of its label');
assert.ok(run("jobCard({id:20,candidacy:{band:'strong'}}).textContent").includes('Fit not assessed'));
run("state.data.profile={evidence:[{status:'verified'}]}; state.data.evidence_ready=true; renderOnboarding()");
assert.equal(nodes.get('#onboarding-next-step').hidden,true,'Approved lowercase status is recognised by the shared server policy');
assert.ok(run("jobCard({id:20,candidacy:{band:'strong'}}).textContent").includes('Strong match'));
assert.ok(!run("jobCard({id:20,candidacy:{band:'strong'}}).textContent").includes('Fit not assessed'));
run("delete state.data.profile");
const packRun = {id:12,receipts:{purple:{role:'purple',status:'completed',mocked:true}},application_packs:{'5':{material_id:5,summary:'Two reviews reconciled against evidence.',decisions:[{finding_id:'red:1',decision:'defer',reason:'Needs a verified source.'}],outstanding_questions:['Confirm the requested qualification.'],cover_letter:{text:'Dear hiring team,\\nSynthetic evidence-bound letter.'},formats:['txt','docx'],mocked:true}}};
context.packRunFixture = packRun;
const packView = run('applicationPack(packRunFixture,{id:5})');
for (const label of ['Your cover letter','Test document — mocked synthesis','Download test cover letter DOCX','Confirm the requested qualification.','Needs a verified source.']) assert.ok(packView.textContent.includes(label),label);
assert.ok(descend(packView).some(node => node.attributes.href === '/api/cv-runs/12/application-pack/5/docx'));
assert.equal(run('applicationPack(packRunFixture,{id:4})'),null,'Selecting another CV never displays the wrong cover letter');
assert.ok(!run('applicationProgress({receipts:{}},{})').textContent.includes('Purple team'),'Old runs do not acquire a fictional purple stage');
assert.ok(run('applicationProgress(packRunFixture,{})').textContent.includes('Purple team'));

// Product workflow keeps incomplete evidence, actual progress and per-job steering distinct.
const incomplete = run("resultCheck('Verified evidence fit', {status:'incomplete',score:99,label:'Partial',coverage:{assessed:6,total:9,unassessed_essential:3},advanced:{partial_score:99}})");
assert.ok(incomplete.textContent.includes('Assessment incomplete'));
assert.ok(incomplete.textContent.includes('6 of 9 requirements assessed'));
assert.ok(!incomplete.textContent.includes('99'));
run("state.data={settings:{positioning:'Verified global preference'},jobs:[{id:7,title:'AI trainer - Greek',presentation:{title:'AI trainer',notice:'Talent network — no current vacancy promised.',language:[{label:'Greek required — fluency unconfirmed.'}]}}]}; cvProduct.jobId=7; directionDraft(7).emphasis='applied_ai'; directionDraft(7).instruction='Lead with implementation'; renderCVProduct({job_id:7,base_cv_ready:false,connections_ready:false,runs:[],versions:[]})");
assert.equal(run('directionDraft(8).instruction'), '');
assert.equal(run('state.data.settings.positioning'), 'Verified global preference');
let product = nodes.get('#cv-workflow-body');
for (const label of ['Choose your base CV','Connect reviewers','Prepare application','Greek required','Talent network']) assert.ok(product.textContent.includes(label), label);
assert.equal(descend(product).find(node => node.tagName === 'button' && node.textContent === 'Prepare application').disabled, true);
const attentionPanel = run("cvAttentionSection({evaluation:{blockers:['Existing work authorisation is required.']}}, {blocked_proposals:[{reason:'An unsupported claim requires confirmation.'}],analysis:{evidence_match:{gaps:['Genuine experience gap']}}}, {needs_attention:['Routine question 1','Routine question 2','Routine question 3','Routine question 4','Routine question 5','Critical source concern','High severity question','Routine question 1'],suggestions:[{text:'Critical source concern',severity:'critical'},{text:'High severity question',severity:'high'}]}, true)");
const priorityList = attentionPanel.children.find(node => node.tagName === 'ul');
assert.equal(priorityList.children.length,5);
for (const text of ['Existing work authorisation is required.','An unsupported claim requires confirmation.','Critical source concern','High severity question']) assert.ok(priorityList.textContent.includes(text),text);
assert.ok(!priorityList.textContent.includes('Routine question 5'));
const allAttention = attentionPanel.children.find(node => node.tagName === 'details');
assert.ok(allAttention && !allAttention.open);
assert.equal(allAttention.children[0].textContent,'All gaps & questions (10)');
for (const text of ['Routine question 1','Routine question 2','Routine question 3','Routine question 4','Routine question 5','Critical source concern','High severity question','Genuine experience gap','Existing work authorisation is required.','An unsupported claim requires confirmation.']) assert.ok(allAttention.textContent.includes(text),text);
const productFixture = {job_id:7,base_cv_ready:true,connections_ready:true,selected_material_id:5,runs:[{id:10,status:'ready',label:'Ready for your review',material_ids:[4,5],selected_material_id:5,direction:{emphasis:'applied_ai'},direction_reason:'The advert asks for implementation.',what_improved:['Brought the implementation example earlier.'],needs_attention:['Work permission needs checking.'],suggestions:[{id:'document:1',material_id:5,category:'document_problem',text:'Make the customer example clearer.',status:'proposed'},{id:'gap:1',material_id:5,category:'experience_gap',text:'No verified team leadership.',status:'needs_answer'},{id:'question:1',material_id:5,category:'question',text:'Confirm current language fluency.',status:'needs_answer'},{id:'old:1',material_id:4,category:'document_problem',text:'A stale earlier suggestion.',status:'proposed'}]}],versions:[{id:4,version:1,cv_text:'Earlier version',formats:['pdf','docx'],analysis:{}},{id:5,version:2,cv_text:'Reviewed source-bound CV',formats:['pdf','docx'],analysis:{evidence_match:{status:'incomplete',score:null,label:'Assessment incomplete',coverage:{assessed:6,total:9,unassessed_essential:3},gaps:['Genuine experience gap']},evidence_presentation:{status:'passed',label:'Supported examples are clear.'},document_checks:{status:'passed',label:'Document checks completed.'}},diff:[{type:'delete',text:'Earlier version'},{type:'insert',text:'Reviewed source-bound CV'}]}]};
context.productFixture = productFixture;
productFixture.confirmed_qualifications = [{title:'Confirmed qualification',award_date:'2023-06-01'}];
run('renderCVProduct(productFixture)');
product = nodes.get('#cv-workflow-body');
assert.ok(product.textContent.includes('Award dates already confirmed in your profile: Confirmed qualification: 1 Jun 2023'));
for (const label of ['Download PDF','Download DOCX','What improved','Brought the implementation example earlier.','What still needs attention','Work permission needs checking.','Genuine experience gap','Assessment incomplete','Previous versions & undo','Revise selected changes','The advert asks for implementation.']) assert.ok(product.textContent.includes(label), label);
assert.ok(!product.textContent.includes('Download draft PDF'));
assert.ok(!product.textContent.includes('A stale earlier suggestion.'));
const selectChange = descend(product).find(node => node.attributes['aria-label'] === 'Select change: Make the customer example clearer.');
assert.equal(selectChange.disabled,false);
for (const label of ['No verified team leadership.','Confirm current language fluency.']) assert.equal(descend(product).find(node => node.attributes['aria-label'] === `Select change: ${label}`).disabled,true);
selectChange.events.change({target:{checked:true}});
assert.equal(run("cvProduct.selected.has('document:1')"), true);
const reject = descend(product).find(node => node.tagName === 'button' && node.textContent === 'Reject suggestion');
reject.events.click({currentTarget:reject});
assert.equal(run("cvProduct.selected.has('document:1')"),false);
assert.equal(run("cvProduct.rejected.has('document:1')"),true);
assert.equal(reject.textContent,'Rejected — undo');
const advanced = descend(product).find(node => node.tagName === 'details' && node.children[0]?.textContent === 'Advanced / diagnostics');
assert.ok(advanced && !advanced.open);
assert.ok(advanced.textContent.includes('Open manual review exchange'));
assert.equal(descend(product).filter(node => node.tagName === 'textarea').length,1,'Ordinary results have only the short direction instruction, with no JSON-import form');
run("cvProduct.selected.add('document:1'); productFixture.selected_material_id=4; renderCVProduct(productFixture)");
product = nodes.get('#cv-workflow-body');
assert.ok(product.textContent.includes('Download draft PDF'));
assert.ok(product.textContent.includes('Review required'));
assert.equal(run('cvProduct.selected.size'),0,'Historical version selection clears suggestion choices for the previous document');
assert.equal(run('cvProduct.rejected.size'),0,'Historical version selection clears rejections for the previous document');
assert.ok(!product.textContent.includes('Brought the implementation example earlier.'));
run("productFixture.selected_material_id=5; productFixture.runs[0].status='needs_answer'; productFixture.runs[0].label='Needs your answer'; renderCVProduct(productFixture)");
product = nodes.get('#cv-workflow-body');
assert.ok(product.textContent.includes('Reviewed — your answer needed'));
assert.ok(product.textContent.includes('Download PDF'));
run("productFixture.runs[0].status='failed'; productFixture.runs[0].label='Could not complete'; productFixture.runs[0].error='Reviewer connection failed'; renderCVProduct(productFixture)");
product = nodes.get('#cv-workflow-body');
for (const label of ['Could not complete','Reviewer connection failed','Download draft PDF','Retry unfinished stages']) assert.ok(product.textContent.includes(label),label);
assert.ok(!product.textContent.includes('Ready for your review'));
run("productFixture.runs[0].status='running'; productFixture.runs[0].label='Independent review'; productFixture.runs[0].error=null; renderCVProduct(productFixture)");
product = nodes.get('#cv-workflow-body');
assert.ok(product.textContent.includes('Independent review'));
assert.ok(product.textContent.includes('Progress is saved by the app.'));
assert.ok(descend(product).find(node => node.tagName === 'button' && node.textContent === 'Cancel'));
assert.equal(descend(product).find(node => node.tagName === 'button' && node.textContent === 'Revise with this direction').disabled,true);
(async () => {
  await run("renderBaseCV({selected:{id:2,filename:'My source.docx',uploaded_at:'2026-09-11',preview:'Readable selected source',conflicts:[{message:'Qualification differs from the verified evidence.'}]},versions:[{id:2,filename:'My source.docx'},{id:1,filename:'Earlier CV.pdf'}]})");
  const base = nodes.get('#base-cv-body');
  for (const label of ['My source.docx','Readable selected source','Replace base CV','Qualification differs','Use this base CV']) assert.ok(base.textContent.includes(label),label);
  run("state.data.jobs=[{id:7,title:'Job without notices'}]; productFixture.runs[0].status='ready'; productFixture.runs[0].label='Ready for your review'; productFixture.runs[0].receipts={generation:{mocked:true,status:'completed'}}; renderCVProduct(productFixture)");
  const testOutput = nodes.get('#cv-workflow-body');
  assert.ok(!testOutput.children.some(node => node.tagName === '#text' && node.textContent === 'null'), 'Absent notices do not render null as page text');
  for (const label of ['Test run — mocked model responses','Test document — mocked review','Download test PDF','Download test DOCX']) assert.ok(testOutput.textContent.includes(label),label);
  assert.ok(!testOutput.textContent.includes('Download PDF'));
  const calls=[];
  context.recordedApi=async (path,body) => { calls.push({path,body}); return productFixture; };
  run("api=recordedApi; state.page='recommended'; cvProduct.jobId=7");
  await run("cvProductAction('revise',{material_id:5,selected_findings:['document:1'],rejected_findings:['document:2']})");
  assert.equal(calls[0].path,'/api/jobs/7/cv-workflow');
  assert.equal(calls[0].body.emphasis,'applied_ai');
  assert.equal(calls[0].body.instruction,'Lead with implementation');
  assert.ok(calls[0].body.idempotency_key);
  await run("cvProductAction('select',{material_id:4})");
  assert.equal(calls[1].body.action,'select');
  assert.equal(calls[1].body.idempotency_key,undefined);
  assert.equal(calls.length,2,'No application or submission endpoint is used for document selection');
  // A timed-out normal search continues pending sources instead of replaying the first boards.
  calls.length = 0;
  run("refreshState=async () => {}; state.page='all'; state.data.runs=[{id:21,mode:'normal',scope:'london',status:'time_limit',stop_reason:'time_limit',checkpoint:{pending:[{url:'https://example.org/remaining'}]}}]; $('#search-scope').value='london'; renderDiscoveryStatus()");
  assert.equal(nodes.get('#search-button').textContent,'Continue finding jobs');
  await run("startSearch('normal')");
  assert.equal(calls[0].path,'/api/runs/21/resume');
  assert.equal(Object.keys(calls[0].body).length,0);
  await run("startSearch('deep')");
  assert.equal(calls[1].path,'/api/search');
  assert.equal(calls[1].body.mode,'deep');
  run("$('#search-scope').value='overseas'; renderDiscoveryStatus()");
  assert.equal(nodes.get('#search-button').textContent,'Find jobs ↗');
  await run("startSearch('normal')");
  assert.equal(calls[2].path,'/api/search');
  assert.equal(calls[2].body.scope,'overseas');
  run("$('#search-scope').value='london'; state.data.runs[0].resumed_by=22");
  assert.equal(run('continuableSearch()'),null);
  run("delete state.data.runs[0].resumed_by; state.data.runs[0].checkpoint.pending=[]");
  assert.equal(run('continuableSearch()'),null);
  run("state.data.runs[0].checkpoint.pending=[{}]; state.data.runs[0].status='completed'");
  assert.equal(run('continuableSearch()'),null);
  run("state.data.runs[0].status='time_limit'; state.data.runs.unshift({id:22,mode:'normal',scope:'london',status:'completed'})");
  assert.equal(run('continuableSearch()'),null,'A newer finished search supersedes an older interrupted run');
  run("state.data.runs[0].status='running'; renderDiscoveryStatus()");
  assert.equal(nodes.get('#search-button').disabled,true);
  console.log('UI smoke passed: Discover defaults, simple cards, preserved filters, honest profile readiness and stage progress, version-matched cover letters, CV history and manual fallback.');
})().catch(error => { console.error(error); process.exitCode=1; });
