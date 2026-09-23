/* CareerOps AI: local, evidence-led workflows. No third-party client dependencies. */
'use strict';

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const state = {data: null, token: '', page: 'all', workflowJobId: null, returnToJobs: null, restoreInventory: false, selectedJob: null, skipJob: null, loading: false, poll: null};
const laneNames = {mediterranean: 'Mediterranean', overseas_quant: 'Overseas quant', london: 'London professional', exceptional: 'Exceptional upside', cashflow: 'Immediate income', overseas_quant_worldwide: 'Overseas quant worldwide'};
const statusNames = {new: 'New discovery', saved: 'Saved', materials_ready: 'Materials ready', applied: 'Applied', interview: 'Interview', offer: 'Offer', closed: 'Closed', dismissed: 'Skipped'};
const countryNames = {CH: 'Switzerland', CY: 'Cyprus', DE: 'Germany', ES: 'Spain', FR: 'France', GB: 'United Kingdom', GR: 'Greece', IE: 'Ireland', IL: 'Israel', IT: 'Italy', MT: 'Malta', NL: 'Netherlands', PL: 'Poland', PT: 'Portugal', SG: 'Singapore', US: 'United States'};
const activeStatuses = ['saved', 'materials_ready', 'applied', 'interview', 'offer'];
let profileDraft = {};
let settingsDraft = {};
let profileDirty = false;
let settingsDirty = false;
let modelConnectionsDirty = false;
const inventoryState = {view: 'all', region: 'london', page: 1, perPage: 50, response: null, request: 0, selected: new Map(), preview: null, searchTimer: null, location: ''};
const fitNames = {strong: 'Strong match', plausible: 'Plausible match', stretch: 'Stretch', low: 'Not suitable / lower fit', not_suitable: 'Not suitable', 'not_suitable,low': 'Not suitable / lower fit'};
const applicationStages = {not_started: 'Not started', in_progress: 'In progress', applied: 'Applied', screening: 'Screening', interview: 'Interview', offer: 'Offer', rejected: 'Rejected', withdrawn: 'Withdrawn'};
const bookmarkPending = new Set();
let importPreview = null;
let applicationDirty = false;
const preparationNames = {draft_generated: 'Draft generated', reviewed_ready: 'Reviewed / ready'};
const batchActiveStatuses = ['pending', 'queued', 'running', 'cancelling'];

function el(tag, attributes = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attributes)) {
    if (value === undefined || value === null) continue;
    if (key === 'class') node.className = value;
    else if (key === 'text') node.textContent = value;
    else if (key.startsWith('on') && typeof value === 'function') node.addEventListener(key.slice(2), value);
    else if (key === 'checked') node.checked = !!value;
    else if (key === 'value') node.value = value;
    else node.setAttribute(key, String(value));
  }
  for (const child of children.flat(Infinity)) if (child !== null && child !== undefined) node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  return node;
}
function button(label, handler, kind = 'button-quiet', attributes = {}) {
  return el('button', {type: 'button', class: `button ${kind}`, onclick: handler, ...attributes}, label);
}
function badge(label, tone = '') { return el('span', {class: `badge ${tone ? `badge-${tone}` : ''}`}, label); }
function safeURL(value) {
  if (!value || typeof value !== 'string') return null;
  try { const url = new URL(value); return ['http:', 'https:'].includes(url.protocol) && !url.username && !url.password ? url.href : null; } catch { return null; }
}
function sourceLink(value, label = 'View source ↗') {
  const url = safeURL(value);
  return url ? el('a', {href: url, target: '_blank', rel: 'noopener noreferrer', class: 'text-link'}, label) : el('span', {class: 'source-label'}, value || 'Source not recorded');
}
function human(value) { return String(value || '').replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase()); }
function array(value) { return Array.isArray(value) ? value : []; }
function stringify(value) { return typeof value === 'string' ? value : JSON.stringify(value, null, 2); }
function number(value) { return Number.isFinite(Number(value)) && value !== null && value !== '' ? Math.round(Number(value)) : null; }
function score(value) { const n = number(value); return n === null ? '—' : String(n); }
function date(value, withTime = false) {
  if (!value) return 'Unknown';
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? String(value) : parsed.toLocaleDateString('en-GB', {day: 'numeric', month: 'short', year: 'numeric', ...(withTime ? {hour: '2-digit', minute: '2-digit'} : {})});
}
function salary(job) {
  const min = number(job.salary_min), max = number(job.salary_max);
  if (min === null && max === null) return 'Not advertised';
  const currency = job.salary_currency || '';
  const format = value => {
    try { return currency ? new Intl.NumberFormat('en-GB', {style: 'currency', currency, maximumFractionDigits: 0}).format(value) : new Intl.NumberFormat('en-GB').format(value); } catch { return `${currency} ${value.toLocaleString('en-GB')}`; }
  };
  const amount = min !== null && max !== null && min !== max ? `${format(min)}–${format(max)}` : min !== null ? `${format(min)}${max === null ? '+' : ''}` : `Up to ${format(max)}`;
  const period = {annual: '/ year', month: '/ month', hour: '/ hour', day: '/ day'}[job.salary_period] || '· period unknown';
  const type = job.salary_type === 'base' ? 'base' : job.salary_type === 'ote' ? 'OTE' : 'type unknown';
  return `${amount} ${period} · ${type}`;
}
function office(job) {
  if (job.office_days === 0) return 'No office days stated';
  if (job.office_days !== null && job.office_days !== undefined) return `${job.office_days} office day${job.office_days === 1 ? '' : 's'} / week`;
  const pattern = String(job.work_pattern || '').trim();
  return pattern && !['unknown', 'not_stated', 'not stated'].includes(pattern.toLowerCase()) ? `${human(pattern)} · office frequency unknown` : 'Office attendance unknown';
}
function cardWarning(value) {
  const characters = Array.from(String(value).replace(/\s+/g, ' ').trim());
  return characters.length > 180 ? `${characters.slice(0, 179).join('')}…` : characters.join('');
}
function eligibility(job) { return {clear: 'Eligibility clear', needs_checking: 'Eligibility needs checking', blocked: 'Confirmed blocker'}[job.evaluation?.eligibility] || 'Eligibility needs checking'; }
function workAuthorisation(job) { return {clear: 'Work authorisation supported', needs_checking: 'Work authorisation needs checking', blocked: 'Work authorisation blocker'}[job.inventory?.work_authorisation || job.work_authorisation_status] || 'Work authorisation needs checking'; }
function sponsorship(job) { return {advertised: 'Sponsorship advertised', required_authorisation: 'Existing work authorisation required', unavailable: 'Sponsorship unavailable', unknown: 'Sponsorship not stated'}[job.sponsorship] || 'Sponsorship not stated'; }
function announce(message, error = false) {
  const region = $('#toast-region');
  region.replaceChildren(el('div', {class: `toast${error ? ' error' : ''}`}, message));
  clearTimeout(announce.timer); announce.timer = setTimeout(() => region.replaceChildren(), error ? 10000 : 6500);
}
function showError(target, error) { target.textContent = error.message || String(error); target.hidden = false; }
async function api(path, body) {
  const options = {headers: {'Accept': 'application/json'}, credentials: 'same-origin', cache: 'no-store'};
  if (body !== undefined) {
    if (!state.token) throw new Error('The local app is not connected yet. Retry after the workspace has loaded.');
    options.method = 'POST'; options.headers['Content-Type'] = 'application/json'; options.headers['X-CareerOps-Token'] = state.token; options.body = JSON.stringify(body);
  }
  const response = await fetch(path, options);
  let data;
  try { data = await response.json(); } catch { throw new Error(`The local app returned an unreadable response (${response.status}).`); }
  if (!response.ok) { const error = new Error(data.error || `Request failed (${response.status}).`); error.status = response.status; throw error; }
  return data;
}
async function busy(control, action) {
  if (control?.disabled) return;
  if (control) { control.disabled = true; control.setAttribute('aria-busy', 'true'); }
  try { return await action(); } catch (error) { announce(error.message, true); } finally { if (control) { control.disabled = false; control.removeAttribute('aria-busy'); } }
}
function openDialog(dialog) { $$('dialog[open]').filter(item => item !== dialog).forEach(closeDialog); if (!dialog.open) dialog.showModal(); }
function closeDialog(dialog) { dialog.close(); }
function empty(title, body, action, compact = false) {
  return el('div', {class: `empty-state${compact ? ' compact' : ''}`}, !compact ? el('div', {class: 'empty-icon', 'aria-hidden': 'true'}, '↗') : null, el('h3', {}, title), el('p', {}, body), action);
}
function getJobs() { return array(state.data?.jobs); }
function getShortlist(type) { return array(state.data?.shortlist?.[type]).map(job => typeof job === 'object' ? job : getJobs().find(item => String(item.id) === String(job))).filter(Boolean); }
function normalPage(page) {
  if (/^cv\/\d+$/.test(page)) return 'cv-workflow';
  if (['today', 'jobs', ''].includes(page)) return 'all';
  return ['pipeline', 'search', 'all-jobs'].includes(page) ? 'all' : page;
}
function pageName(page) { return {'all': 'Discover', 'needs_checking': 'Needs checking', 'your-cv': 'Your profile', 'cv-workflow': 'Prepare application'}[page] || human(page); }
function changePage(page, focus = false) {
  page = normalPage(page);
  if (!['recommended', 'all', 'needs_checking', 'saved', 'applications', 'settings', 'your-cv', 'cv-workflow'].includes(page)) return;
  state.page = page;
  const inventoryPage = ['recommended', 'all', 'needs_checking', 'saved', 'applications'].includes(page);
  $$('.page').forEach(node => { node.hidden = node.id !== (inventoryPage ? 'page-pipeline' : `page-${page}`); });
  $$('.primary-nav a').forEach(node => { if (node.dataset.page === page || node.dataset.page === 'jobs' && ['recommended', 'all', 'needs_checking'].includes(page)) node.setAttribute('aria-current', 'page'); else node.removeAttribute('aria-current'); });
  $$('[data-view-switch]').forEach(node => { if (node.dataset.viewSwitch === page) node.setAttribute('aria-current', 'page'); else node.removeAttribute('aria-current'); });
  $('.jobs-view-switch').hidden = !['recommended', 'all', 'needs_checking'].includes(page);
  $('#breadcrumb-page').textContent = pageName(page); document.title = `CareerOps AI — ${pageName(page)}`;
  if (inventoryPage) {
    if (inventoryState.view !== page) { inventoryState.view = page; inventoryState.region = ['saved', 'applications'].includes(page) ? 'all' : $('#search-scope').value || 'london'; resetInventoryFilters(false); }
    if (!state.restoreInventory) renderPipeline();
    state.restoreInventory = false;
  }
  if (page === 'settings' && state.data) renderSettings();
  if (page === 'your-cv' && state.data) { renderProfile(); renderBaseCV(); }
  if (page === 'cv-workflow') {
    const match = location.hash.match(/^#cv\/(\d+)$/); if (match) state.workflowJobId = Number(match[1]);
    if (state.data && state.workflowJobId) loadCVWorkflow(state.workflowJobId);
  } else { clearTimeout(cvProduct.poll); }
  if (focus) $('#main').focus({preventScroll: true});
}
function navigate(page) { const normal = normalPage(page); if (normalPage(location.hash.slice(1)) !== normal || page.startsWith('cv/')) location.hash = page; else changePage(normal); }
async function refreshState({renderForms = false} = {}) {
  const data = await api('/api/state');
  state.data = data; state.token = data.token || state.token;
  $('#connection-label').textContent = 'Connected to local app'; $('#connection-dot').className = 'connection-dot connected';
  $('#global-error').hidden = true;
  $('#saved-count').textContent = getJobs().filter(job => job.bookmarked).length;
  $('#applications-count').textContent = getJobs().filter(job => job.application && job.application.stage !== 'not_started').length;
  renderNavigationCounts();
  if (['recommended', 'all', 'needs_checking', 'saved', 'applications'].includes(state.page)) renderPipeline(); renderRuns(); renderProviders(); renderPreparationBatches(); renderDiscoveryStatus();
  if (state.page === 'settings') renderSettings();
  if (state.page === 'your-cv') { renderProfile(); if (renderForms) renderBaseCV(); }
  if (renderForms && state.page === 'cv-workflow' && state.workflowJobId) loadCVWorkflow(state.workflowJobId);
  const activeRun = array(data.runs).some(run => ['pending', 'queued', 'running', 'cancelling'].includes(run.status)) || array(data.preparation_batches).some(batch => batchActiveStatuses.includes(batch.status));
  clearTimeout(state.poll);
  if (activeRun) state.poll = setTimeout(() => refreshState().catch(error => { announce(error.message, true); }), 3500);
  return data;
}
function renderNavigationCounts() {
  const counts = state.data?.inventory_counts?.all || {};
  for (const [selector, key] of [['#pipeline-count', 'indexed'], ['#recommended-count', 'recommended']]) {
    $(selector).textContent = number(counts[key]) === null ? '—' : Number(counts[key]).toLocaleString('en-GB');
  }
}
function candidacy(job) { return job.candidacy || job.evaluation?.candidacy || {}; }
function whyFits(job) { const match = candidacy(job); return array(match.why).join(' ') || (typeof match.why === 'string' ? match.why : '') || job.evaluation?.why || 'Review the requirements against your professional evidence.'; }
function candidacyGaps(job) { return [...array(job.evaluation?.blockers), ...array(candidacy(job).gaps), ...array(job.evaluation?.gaps)].filter((item, index, all) => all.indexOf(item) === index); }
function candidacyBand(job) { return candidacy(job).band || job.inventory?.fit_group || ({strong_match: 'strong', credible_stretch: 'stretch', weak_match: 'not_suitable'}[job.evaluation?.match]) || 'needs_review'; }
function displayTitle(job) { return job.presentation?.title || job.title || 'Untitled opportunity'; }
function languageLabels(job) { return array(job.presentation?.language).filter(item => item.label); }
function jobNotices(job) { return [job.presentation?.notice ? el('p', {class: 'job-language-note'}, job.presentation.notice) : null, ...languageLabels(job).map(item => el('p', {class: `job-language-note${item.candidate_status === 'not_met' ? ' language-blocked' : ''}`}, item.label))].filter(Boolean); }
function applicationStage(job) { return job.application?.stage || 'not_started'; }
function bookmarkButton(job, small = false) {
  const saved = job.bookmarked === true;
  const control = button(saved ? '★ Saved' : '☆ Save', event => toggleBookmark(job.id, event.currentTarget), `button-quiet bookmark-button${small ? ' button-small' : ''}`, {'data-bookmark-id': job.id, 'data-bookmarked': saved, 'aria-pressed': saved, 'aria-label': `${saved ? 'Unsave' : 'Save'} ${job.title || 'opportunity'}`});
  control.disabled = bookmarkPending.has(String(job.id)); return control;
}
async function toggleBookmark(id, control) {
  const key = String(id); if (bookmarkPending.has(key)) return;
  const desired = control.dataset.bookmarked !== 'true'; bookmarkPending.add(key);
  const buttons = $$('[data-bookmark-id]').filter(node => node.dataset.bookmarkId === key);
  buttons.forEach(node => { node.disabled = true; node.textContent = desired ? 'Saving…' : 'Removing…'; node.setAttribute('aria-busy', 'true'); });
  try {
    const result = await api(`/api/jobs/${encodeURIComponent(id)}/action`, {action: 'bookmark', bookmarked: desired});
    const updated = result.job || result;
    const saved = typeof updated.bookmarked === 'boolean' ? updated.bookmarked : desired;
    buttons.forEach(node => { node.dataset.bookmarked = String(saved); node.textContent = saved ? '★ Saved' : '☆ Save'; node.setAttribute('aria-pressed', String(saved)); node.setAttribute('aria-label', saved ? 'Unsave this job' : 'Save this job'); });
    announce(saved ? 'Saved. Find this opportunity in Saved, even if its advert or application stage changes.' : 'Removed from Saved. The job and its application history are preserved.');
    bookmarkPending.delete(key); await refreshState();
  } catch (error) { buttons.forEach(node => { node.textContent = node.dataset.bookmarked === 'true' ? '★ Saved' : '☆ Save'; }); announce(`Saving change failed: ${error.message}`, true); }
  finally { bookmarkPending.delete(key); buttons.forEach(node => { node.disabled = false; node.removeAttribute('aria-busy'); }); }
}
function openApplicationLink(job, small = false) {
  const url = safeURL(job.url); if (!url) return null;
  return el('a', {class: `button button-quiet${small ? ' button-small' : ''}`, href: url, target: '_blank', rel: 'noopener noreferrer', onclick: () => { api(`/api/jobs/${encodeURIComponent(job.id)}/action`, {action: 'open'}).then(() => { announce('Application page opened. It has not been marked Applied.'); refreshState().catch(() => {}); }).catch(error => announce(`Page opened, but the visit could not be recorded: ${error.message}`, true)); }}, 'Open application ↗');
}
function hiddenButton(job, small = false) {
  return button(job.hidden ? 'Unhide' : 'Hide', event => busy(event.currentTarget, async () => {
    await api(`/api/jobs/${encodeURIComponent(job.id)}/action`, {action: 'hidden', hidden: !job.hidden});
    announce(job.hidden ? 'Visible again in All Jobs.' : 'Hidden from discovery views. Your saved status and application record are preserved. Use Visibility → Hidden by me to restore it.');
    await refreshState();
    if ($('#job-dialog').open && String(state.selectedJob) === String(job.id)) await openJob(job.id);
  }), `button-quiet${small ? ' button-small' : ''}`, {'aria-label': `${job.hidden ? 'Unhide' : 'Hide'} ${job.title || 'job'}`});
}
function generateCVButton(job, small = false) {
  return button('Prepare application', () => showCVWorkflow(job.id, true), `button-primary${small ? ' button-small' : ''}`);
}
function jobCard(job, stretch = false) {
  const match = candidacy(job), gaps = candidacyGaps(job), band = candidacyBand(job), blocked = job.evaluation?.eligibility === 'blocked';
  const profileMissing = state.data?.evidence_ready !== true;
  const strength = array(match.why)[0] || whyFits(job), languages = languageLabels(job);
  const remainingGaps = languages.length ? gaps.filter(gap => !/^Mandatory language\s*:/i.test(String(gap).trim())) : gaps;
  const gap = remainingGaps[0] || (job.evaluation?.eligibility !== 'clear' ? `${workAuthorisation(job)}. ${sponsorship(job)}.` : '');
  return el('article', {class: 'job-card compact-job-card', 'data-job-id': job.id, 'aria-label': `${job.title || 'Opportunity'} at ${job.company || 'Unknown employer'}`},
    el('div', {class: 'card-top'}, el('div', {class: 'company-name'}, job.company || 'Employer not recorded'), inventoryState.view === 'all' ? el('label', {class: 'bulk-card-select'}, inventoryCheckbox(job), 'Select for batch') : null),
    el('button', {type: 'button', class: 'card-title', onclick: () => openJob(job.id)}, displayTitle(job)),
    jobNotices(job),
    badge(profileMissing ? 'Fit not assessed' : fitNames[band] || human(band), profileMissing ? 'neutral' : blocked || band === 'not_suitable' || band === 'low' ? 'red' : stretch || band === 'stretch' ? 'amber' : ''),
    el('p', {class: 'card-location'}, `${job.location || countryNames[job.country] || 'Location not established'} · ${office(job)}`),
    el('p', {class: 'compact-job-pay'}, `Salary: ${salary(job)}`),
    blocked ? badge('Known eligibility blocker', 'red') : job.sample ? badge('Sample / fixture', 'amber') : null,
    el('dl', {class: 'recommendation-reasons'}, profileMissing ? null : el('div', {}, el('dt', {}, 'Why it could fit'), el('dd', {}, cardWarning(strength))), gap ? el('div', {class: blocked ? 'important-gap' : ''}, el('dt', {}, blocked ? 'Needs attention' : 'Worth checking'), el('dd', {}, cardWarning(gap))) : null),
    el('div', {class: 'card-footer dashboard-card-footer'}, bookmarkButton(job, true), generateCVButton(job, true)));
}
function renderUniverseCounts(counts) {
  const root = $('#universe-summary');
  root.className = `universe-summary${['recommended', 'all', 'needs_checking'].includes(inventoryState.view) ? ' universe-summary-compact' : ''}`;
  if (counts.indexed === undefined) { root.replaceChildren(); return; }
  if (['recommended', 'all', 'needs_checking'].includes(inventoryState.view)) {
    const selectedCountry = $('#inventory-country').value || {israel: 'IL', greece: 'GR', cyprus: 'CY'}[inventoryState.location];
    const selectedLabel = inventoryState.location ? human(inventoryState.location) : selectedCountry ? countryNames[selectedCountry] || selectedCountry : inventoryState.region === 'all' ? 'All locations' : human(inventoryState.region);
    const selectedCounts = selectedCountry && counts.by_location?.[selectedCountry];
    root.replaceChildren(el('p', {class: 'coverage-inline'}, el('strong', {}, Number(counts.indexed || 0).toLocaleString('en-GB')), ' indexed · ', el('strong', {}, Number(counts.recommended || 0).toLocaleString('en-GB')), ' recommended', el('span', {class: 'coverage-selected'}, `${selectedLabel}${selectedCounts ? ` · ${selectedCounts.indexed} indexed / ${selectedCounts.recommended} recommended` : ''}`)), button('Explore All Jobs →', () => openInventory('all'), 'text-link'));
    return;
  }
  const countCard = (label, data, country) => el('article', {class: 'coverage-card'}, el('h3', {}, label), el('p', {class: 'coverage-main'}, el('strong', {}, Number(data.indexed || 0).toLocaleString('en-GB')), ' indexed'), el('p', {}, el('strong', {}, Number(data.recommended || 0).toLocaleString('en-GB')), ' recommended'), el('p', {class: 'coverage-bands'}, `${data.strong || 0} strong · ${data.plausible || 0} plausible · ${data.stretch || 0} stretch`), country ? button(`Browse ${label}`, () => { inventoryState.view = 'all'; inventoryState.region = 'all'; resetInventoryFilters(false); $('#inventory-country').value = country; navigate('all'); if (state.page === 'all') renderPipeline(); }, 'text-link') : el('p', {class: 'field-help'}, `${data.universe_visible ?? '—'} visible in All Jobs`));
  const locations = counts.by_location || {};
  const preferred = ['GB', 'IL', 'GR', 'CY', 'FR'];
  root.replaceChildren(countCard(inventoryState.region === 'all' ? 'All indexed locations' : `${human(inventoryState.region)} inventory`, counts), ...preferred.filter(code => locations[code]).map(code => countCard(countryNames[code] || code, locations[code], code)), el('p', {class: 'coverage-caption'}, 'Indexed means collected canonical records, including preserved hidden and closed adverts. Recommended is the selected subset; the band counts describe that subset. Filters below narrow the displayed list.'));
}
function continuableSearch(scope = $('#search-scope').value) {
  const latest = array(state.data?.runs).find(run => run.mode === 'normal' && run.scope === scope);
  return latest && !latest.resumed_by && !batchActiveStatuses.includes(latest.status) && !['completed', 'complete', 'succeeded'].includes(latest.status)
    && (latest.status === 'time_limit' || latest.stop_reason === 'time_limit') && array(latest.checkpoint?.pending).length ? latest : null;
}
function renderDiscoveryStatus() {
  const latest = array(state.data?.runs)[0], active = array(state.data?.runs).some(run => batchActiveStatuses.includes(run.status)), continuation = continuableSearch();
  $('#discovery-last-run').textContent = active ? `Checking employer sources${latest?.found ? ` · ${Number(latest.found).toLocaleString('en-GB')} roles found so far` : '…'}` : continuation ? 'Some sources are still waiting. Continue from where the last search stopped.' : latest ? `Last search ${date(latest.finished_at || latest.created_at, true)} · ${human(latest.status)}${latest.message ? ` — ${latest.message}` : ''}` : 'Search public employer sources, then choose what is worth your time.';
  $('#search-button').disabled = active; $('#search-button').textContent = active ? 'Finding jobs…' : continuation ? 'Continue finding jobs' : 'Find jobs ↗';
  $('#deep-search-button').disabled = active; $('#bootstrap-search-button').disabled = active;
}
function renderOnboarding() {
  const ready = state.data?.evidence_ready === true;
  $('#onboarding-next-step').replaceChildren(...(ready ? [] : [el('div', {}, el('strong', {}, 'Make your applications personal'), el('p', {}, 'You can discover jobs now. Add your CV and verified experience when you are ready to apply.')), el('a', {href: '#your-cv', class: 'text-link'}, 'Set up your profile →')]));
  $('#onboarding-next-step').hidden = ready || !['all', 'recommended', 'needs_checking'].includes(inventoryState.view);
}
function inventoryFilters() {
  return {work_authorisation: $('#inventory-work-authorisation').value, location: inventoryState.location, seniority: $('#inventory-seniority').value, source: $('#inventory-source').value, saved: $('#inventory-saved').value, hidden: $('#inventory-hidden').value, date_field: $('#inventory-date-field').value, date_from: $('#inventory-date-from').value, date_to: $('#inventory-date-to').value, view: inventoryState.view, region: inventoryState.region, salary: $('#inventory-salary').value, work_pattern: $('#inventory-work-pattern').value, sponsorship: $('#inventory-sponsorship').value, relocation: $('#inventory-relocation').value, application_stage: $('#inventory-application-stage').value, country: $('#inventory-country').value, role_family: $('#inventory-family').value, fit: $('#inventory-fit').value, eligibility: $('#inventory-eligibility').value, verification: $('#inventory-verification').value, status: $('#inventory-status').value, q: $('#inventory-search').value.trim(), include_stretch: $('#inventory-stretches').checked, include_excluded: $('#inventory-excluded').checked};
}
function inventoryFilterText(filters = inventoryFilters()) {
  const names = {overseas: 'Overseas', london: 'London', all: 'All regions'};
  const labels = [pageName(filters.view || 'all'), names[filters.region] || human(filters.region)];
  for (const key of ['salary', 'work_pattern', 'sponsorship', 'relocation', 'seniority', 'source', 'saved', 'hidden', 'date_from', 'date_to']) if (filters[key]) labels.push(`${human(key)}: ${human(filters[key])}`);
  if (filters.application_stage) labels.push(applicationStages[filters.application_stage] || human(filters.application_stage));
  if (filters.date_from || filters.date_to) labels.push(`Date basis: ${human(filters.date_field)}`);
  if (filters.location) labels.push(human(filters.location));
  if (filters.country) labels.push(countryNames[filters.country] || filters.country);
  if (filters.role_family) labels.push(human(filters.role_family));
  if (filters.fit) labels.push(fitNames[filters.fit] || human(filters.fit));
  if (filters.eligibility) labels.push(`Eligibility: ${human(filters.eligibility)}`);
  if (filters.work_authorisation) labels.push(`Work authorisation: ${human(filters.work_authorisation)}`);
  if (filters.verification) labels.push(human(filters.verification));
  labels.push(filters.status === 'actionable' ? 'Opportunities to review' : statusNames[filters.status] || human(filters.status));
  if (filters.q) labels.push(`Search: ${filters.q}`);
  if (!filters.include_stretch) labels.push(filters.fit ? 'Explicit fit filter' : 'Main matches only');
  if (filters.include_excluded) labels.push('Hidden / closed records included');
  return labels.join(' · ');
}
function openInventory(region = 'all', options = {}) {
  inventoryState.view = options.view || 'all';
  inventoryState.region = region; inventoryState.page = 1;
  if (options.reset !== false) resetInventoryFilters(false);
  if (options.status) $('#inventory-status').value = options.status;
  if (options.includeExcluded) $('#inventory-excluded').checked = true;
  if (state.page === inventoryState.view) renderPipeline(); else navigate(inventoryState.view);
}
function resetInventoryFilters(refresh = true) {
  ['#inventory-search', '#inventory-country', '#inventory-family', '#inventory-fit', '#inventory-eligibility', '#inventory-work-authorisation', '#inventory-verification', '#inventory-salary', '#inventory-work-pattern', '#inventory-sponsorship', '#inventory-relocation', '#inventory-application-stage', '#inventory-seniority', '#inventory-source', '#inventory-saved', '#inventory-hidden', '#inventory-date-from', '#inventory-date-to'].forEach(selector => { $(selector).value = ''; });
  $('#inventory-status').value = 'all'; $('#inventory-stretches').checked = true; $('#inventory-excluded').checked = false;
  inventoryState.page = 1; inventoryState.location = ''; $('#inventory-date-field').value = 'discovered';
  if (refresh) renderPipeline();
}
function syncFacet(select, counts, fallbackLabel, names = {}) {
  const selected = select.value;
  const choices = Object.entries(counts || {}).sort(([a], [b]) => a.localeCompare(b));
  if (selected && !choices.some(([value]) => value === selected)) choices.push([selected, 0]);
  const signature = JSON.stringify(choices);
  if (select.dataset.choices === signature) return;
  select.replaceChildren(el('option', {value: ''}, fallbackLabel), ...choices.map(([value, count]) => el('option', {value}, `${names[value] || human(value)} (${count})`)));
  select.value = selected; select.dataset.choices = signature;
}
async function renderPipeline() {
  if (!state.data) return;
  const view = inventoryState.view, filters = inventoryFilters(), request = ++inventoryState.request;
  const copy = {
    all: ['YOUR NEXT CHAPTER', 'Discover your next move.', 'Find opportunities. Save the promising ones. Make each application count.', 'All collected vacancies stay available here, including jobs that need a closer look.'],
    recommended: ['A CLOSER MATCH', 'Recommended for you.', 'A focused view based on the experience you have shared.', ''],
    needs_checking: ['KEEP THE UNCERTAINTY VISIBLE', 'Needs checking.', 'Jobs with language, work permission, sponsorship or other requirements to clarify.', 'Unknown does not mean unsuitable. Confirmed blockers stay clearly labelled.'],
    saved: ['YOUR SHORTLIST, YOUR CHOICE', 'Saved jobs.', 'Every job you have bookmarked, independent of its application stage.', 'Saved jobs remain here when an advert closes or your application progresses. Remove a bookmark with Saved on its card.'],
    applications: ['KEEP YOUR NEXT STEP CLEAR', 'Applications.', 'Track the work you have started, the conversations ahead and the outcomes.', 'Your application records remain visible across all locations and advert states. Opening an application page never marks it Applied.']
  }[view];
  $('#inventory-eyebrow').textContent = copy[0]; $('#pipeline-heading').textContent = copy[1]; $('#inventory-page-description').textContent = copy[2]; $('#inventory-description').textContent = copy[3];
  $('#discovery-toolbar').hidden = !['all', 'recommended', 'needs_checking'].includes(view); renderOnboarding(); $('#inventory-status').closest('label').hidden = view !== 'all';
  $('#inventory-excluded').closest('label').hidden = true; $('#exclusion-diagnostics').hidden = view !== 'all';
  $('#inventory-stretches').closest('label').hidden = view !== 'recommended'; $('#inventory-density').closest('label').hidden = !['saved', 'applications'].includes(view);
  $('.companies-section').hidden = view !== 'saved';
  $('#bulk-preparation-tools').hidden = view !== 'all';
  const advancedFilters = $('#advanced-inventory-filters');
  if (advancedFilters.dataset.view !== view) { advancedFilters.open = false; advancedFilters.dataset.view = view; }
  $$('[data-region]').forEach(control => control.setAttribute('aria-pressed', !inventoryState.location && control.dataset.region === inventoryState.region));
  $$('[data-location]').forEach(control => control.setAttribute('aria-pressed', control.dataset.location === inventoryState.location));
  const stageEntries = Object.entries(applicationStages).filter(([stage]) => view !== 'applications' || stage !== 'not_started');
  $('#pipeline-stages').hidden = !['saved', 'applications'].includes(view);
  $('#pipeline-stages').replaceChildren(...stageEntries.map(([stage, label]) => el('button', {type: 'button', class: 'stage-button', 'aria-pressed': filters.application_stage === stage, onclick: () => { $('#inventory-application-stage').value = filters.application_stage === stage ? '' : stage; inventoryState.page = 1; renderPipeline(); }}, label)));
  $('#active-inventory-filters').textContent = inventoryFilterText(filters);
  $('#inventory-error').hidden = true; $('#inventory-list').setAttribute('aria-busy', 'true');
  $('#inventory-count').textContent = 'Loading matching jobs…';
  $$('[data-prepare-next]').forEach(control => { control.disabled = true; });
  try {
    const params = new URLSearchParams({...filters, page: inventoryState.page, per_page: inventoryState.perPage});
    const result = await api(`/api/inventory?${params}`);
    if (request !== inventoryState.request) return;
    inventoryState.response = result; inventoryState.page = result.page || inventoryState.page;
    syncFacet($('#inventory-country'), result.counts?.countries, 'All countries', countryNames);
    syncFacet($('#inventory-family'), result.counts?.role_families, 'All role families');
    syncFacet($('#inventory-source'), result.counts?.sources || Object.fromEntries(Object.entries(result.counts?.by_source || {}).map(([key, value]) => [key, value.indexed])), 'All sources');
    syncFacet($('#inventory-seniority'), result.counts?.seniority || result.counts?.seniorities || {entry: '', mid: '', senior: '', lead: '', unknown: ''}, 'All seniority levels');
    renderUniverseCounts(result.counts || {});
    const matchedJobs = array(result.jobs), total = Number(result.total) || 0;
    $('#inventory-count').textContent = `${total.toLocaleString('en-GB')} ${view === 'applications' ? `application${total === 1 ? '' : 's'}` : total === 1 ? 'opportunity' : 'opportunities'}${['saved', 'applications'].includes(view) ? '' : ` · ${inventoryState.region === 'all' ? 'All locations' : inventoryState.region === 'london' ? 'London' : 'Other locations'}`}`;
    const noRecords = view === 'saved' ? ['No saved jobs match this view.', 'Use Save on any job card to keep it here, or reset your filters.'] : view === 'applications' ? ['No application records match this view.', 'Choose Update status on a job when you start an application. Use Applied only after submitting it yourself.'] : Number(result.counts?.indexed || 0) === 0 ? ['Your next opportunity starts here.', 'Find jobs to collect current vacancies. You can also add a role you have already found.'] : ['No jobs match this view.', 'Try a different keyword or reset your filters to see more opportunities.'];
    $('#inventory-list').replaceChildren(...(matchedJobs.length ? matchedJobs.map(job => ['recommended', 'all', 'needs_checking'].includes(view) ? jobCard(job, candidacyBand(job) === 'stretch') : inventoryRow(job)) : [empty(...noRecords, Number(result.counts?.indexed || 0) === 0 && ['all', 'recommended', 'needs_checking'].includes(view) ? button('Find jobs', event => startSearch('normal', event.currentTarget), 'button-primary') : button('Reset filters', () => resetInventoryFilters()))]));
    $('#inventory-list').classList.toggle('recommendation-feed', true);
    $('#inventory-list').classList.toggle('batch-selection', $('#bulk-preparation-tools').open);
    $('#inventory-list').classList.toggle('compact-density', $('#inventory-density').value === 'compact');
    renderPagination(result); renderSelection(); renderExclusionSummary(result.counts || {});
    $$('[data-prepare-next]').forEach(control => { control.disabled = !total; });
  } catch (error) {
    if (request !== inventoryState.request) return;
    showError($('#inventory-error'), error); $('#inventory-count').textContent = 'Jobs could not load';
    $('#inventory-list').replaceChildren(empty('The request did not complete.', 'Your records remain preserved. Retry to load this view.', button('Retry', renderPipeline)));
    $('#inventory-pagination').replaceChildren();
  } finally { if (request === inventoryState.request) $('#inventory-list').setAttribute('aria-busy', 'false'); }
  const companies = array(state.data.companies);
  $('#companies-list').replaceChildren(...(companies.length ? companies.map(company => el('article', {class: 'company-card'}, badge('Speculative target', 'neutral'), el('h3', {}, company.name || 'Unnamed company'), el('p', {}, company.notes || 'No notes yet.'), sourceLink(company.url, 'Visit company ↗'))) : [empty('Keep promising companies in view.', 'Save an employer even when there is no verified vacancy. It stays here as a research target.', null, true)]));
}
function jobPreparationLabel(job) {
  return preparationNames[job.preparation_status] || (job.status === 'materials_ready' ? 'Draft generated' : 'Not prepared');
}
function inventoryCheckbox(job) {
  const selectable = job.inventory?.accessible === true && !['applied', 'interview', 'offer', 'closed', 'dismissed'].includes(job.status);
  const checkbox = el('input', {type: 'checkbox', checked: inventoryState.selected.has(String(job.id)), 'aria-label': `Select ${job.title || 'opportunity'} at ${job.company || 'unknown company'}`, onchange: event => { if (event.target.checked) inventoryState.selected.set(String(job.id), job); else inventoryState.selected.delete(String(job.id)); renderSelection(); }});
  checkbox.disabled = !selectable; return checkbox;
}
function inventoryRow(job) {
  const card = jobCard(job), app = job.application || {};
  card.append(el('div', {class: 'application-card-status'}, badge(applicationStages[applicationStage(job)], 'neutral'), app.next_action ? el('p', {}, app.next_action) : null, app.follow_up_date ? el('p', {class: 'follow-up-date'}, `Follow up ${date(app.follow_up_date)}`) : null, button('Update status', () => openApplicationTracker(job.id), 'text-link')));
  return card;
}
function renderPagination(result) {
  const total = Number(result.total) || 0, pageSize = Number(result.per_page) || 50, pages = Math.max(1, Math.ceil(total / pageSize)), page = Number(result.page) || 1;
  const previous = button('← Previous', () => { inventoryState.page = Math.max(1, page - 1); renderPipeline(); }, 'button-quiet button-small'); previous.disabled = page <= 1;
  const next = button('Next →', () => { inventoryState.page = page + 1; renderPipeline(); }, 'button-quiet button-small'); next.disabled = page >= pages;
  const jump = el('input', {type: 'number', min: 1, max: pages, value: page, 'aria-label': 'Inventory page number'});
  const go = button('Go', () => { const value = Number(jump.value); if (Number.isInteger(value) && value >= 1 && value <= pages) { inventoryState.page = value; renderPipeline(); } else announce(`Choose a page from 1 to ${pages}.`, true); }, 'button-quiet button-small');
  $('#inventory-pagination').replaceChildren(el('span', {}, total ? `Showing ${(page - 1) * pageSize + 1}–${Math.min(page * pageSize, total)} of ${total}` : 'No matching rows'), el('div', {class: 'pagination-controls'}, previous, el('span', {}, `Page ${page} of ${pages}`), next, el('label', {}, 'Go to ', jump), go));
}
function renderSelection() {
  const jobs = array(inventoryState.response?.jobs).filter(job => job.inventory?.accessible && !['applied', 'interview', 'offer', 'closed', 'dismissed'].includes(job.status));
  const selectedOnPage = jobs.filter(job => inventoryState.selected.has(String(job.id))).length;
  const selectPage = $('#select-inventory-page'); selectPage.checked = jobs.length > 0 && selectedOnPage === jobs.length; selectPage.indeterminate = selectedOnPage > 0 && selectedOnPage < jobs.length; selectPage.disabled = !jobs.length;
  const elsewhere = inventoryState.selected.size - selectedOnPage;
  $('#selected-job-count').textContent = `${inventoryState.selected.size} selected${elsewhere ? ` · ${elsewhere} beyond this page` : ''}`;
  $('#prepare-selected-button').disabled = !inventoryState.selected.size;
  $('#clear-selection-button').disabled = !inventoryState.selected.size;
}
function renderExclusionSummary(counts) {
  $('#exclusion-summary').replaceChildren(el('p', {class: 'muted'}, `${counts.indexed ?? counts.total ?? '—'} indexed records · ${counts.universe_visible ?? '—'} visible in All Jobs · ${counts.recommended ?? '—'} recommended. A low score, missing salary or eligibility mismatch does not remove a vacancy from All Jobs.`), el('p', {class: 'field-help'}, 'Duplicates, confirmed closed adverts, unusable records and jobs you hide are omitted from the default list. Saved and Applications retain your history. Use Visibility → Hidden by me to restore hidden jobs, or Verification → Confirmed closed to inspect closed adverts.'));
}
async function previewPreparation(request, control) {
  return busy(control, async () => {
    inventoryState.preview = null; $('#confirm-preparation-button').disabled = true; $('#preparation-preview-error').hidden = true;
    $('#preparation-preview-body').replaceChildren(el('p', {class: 'loading-state'}, 'Resolving the exact job and profile scope…')); openDialog($('#preparation-preview-dialog'));
    try {
      const preview = await api('/api/preparation/preview', request);
      inventoryState.preview = preview;
      const scope = request.filters ? inventoryFilterText(request.filters) : `${request.job_ids.length} explicitly selected job IDs, across the pages and filters you selected`;
      const jobs = array(preview.jobs);
      const safeBudget = preview.budget_usd !== undefined && preview.budget_usd !== null && Number.isFinite(Number(preview.budget_usd));
      $('#preparation-preview-body').replaceChildren(el('p', {class: 'preview-scope'}, scope), el('div', {class: 'banner banner-info'}, `${preview.count ?? jobs.length} exact opportunities · Billing: ${human(preview.billing_mode || 'not reported')} · Authorised budget: ${safeBudget ? `$${Number(preview.budget_usd).toFixed(2)} USD` : 'not reported'}`), el('p', {class: 'muted'}, 'This prepares versioned drafts only. You review each pack and submit the application yourself. The preview fixes the job list, job contents and candidate profile used.'), array(preview.warnings).length ? el('div', {class: 'banner banner-warning'}, list(preview.warnings, '')) : '', el('ol', {class: 'preview-jobs'}, jobs.map(job => el('li', {}, el('strong', {}, `${job.title || 'Untitled opportunity'} · ${job.company || 'Employer unknown'}`), el('span', {}, `ID ${job.id ?? job.job_id}${job.location ? ` · ${job.location}` : ''}`)))));
      $('#confirm-preparation-button').disabled = !preview.preview_id || !jobs.length || !safeBudget || !preview.billing_mode;
    } catch (error) { showError($('#preparation-preview-error'), error); $('#preparation-preview-body').replaceChildren(el('p', {class: 'muted'}, 'The batch has not started. Resolve the issue and preview the scope again.')); }
  });
}
async function confirmPreparation(control) {
  const preview = inventoryState.preview;
  if (!preview?.preview_id) return;
  return busy(control, async () => {
    $('#preparation-preview-error').hidden = true;
    try { await api('/api/preparation/start', {preview_id: preview.preview_id}); inventoryState.preview = null; closeDialog($('#preparation-preview-dialog')); announce('Preparation started for the confirmed scope. Applications remain unsubmitted.'); await refreshState(); }
    catch (error) { showError($('#preparation-preview-error'), error); }
  });
}
function preparationBatchCard(batch, compact = false) {
  const done = Number(batch.completed) || 0, failed = Number(batch.failed) || 0, total = Number(batch.total) || 0;
  const active = batchActiveStatuses.includes(batch.status);
  const card = el('article', {class: 'batch-card'}, el('div', {class: 'run-head'}, el('h4', {}, `Preparation batch ${batch.id} · ${date(batch.created_at, true)}`), badge(human(batch.status || 'unknown'), failed ? 'amber' : 'neutral')), el('p', {class: 'muted'}, `${done} prepared · ${failed} failed · ${Math.max(0, total - done - failed)} remaining · ${total} total`), el('progress', {max: Math.max(1, total), value: Math.min(total, done + failed), 'aria-label': `Preparation progress: ${done} prepared, ${failed} failed, ${total} total`}));
  const actions = el('div', {class: 'run-actions'});
  if (active) actions.append(button('Cancel batch', event => busy(event.currentTarget, async () => { await api(`/api/preparation/${encodeURIComponent(batch.id)}/cancel`, {}); announce('Preparation cancellation requested. Completed drafts are preserved.'); await refreshState(); }), 'button-quiet button-small'));
  else if (!['completed', 'complete'].includes(batch.status) || failed) actions.append(button('Resume unfinished jobs', event => busy(event.currentTarget, async () => { await api(`/api/preparation/${encodeURIComponent(batch.id)}/resume`, {}); announce('Preparation resumed for unfinished jobs.'); await refreshState(); }), 'button-quiet button-small'));
  card.append(actions);
  if (!compact) card.append(el('details', {class: 'batch-results'}, el('summary', {}, 'Per-job results, failures & application packs'), el('div', {}, array(batch.items).map(item => el('div', {class: 'batch-item'}, el('div', {}, el('strong', {}, `${item.title || 'Opportunity'} · ${item.company || 'Employer unknown'}`), el('span', {class: 'muted'}, human(item.status || 'pending')), item.error ? el('p', {class: 'batch-error'}, String(item.error)) : null), button(item.material_id ? 'Preview pack' : 'Review job', () => openJob(item.job_id, item.material_id ? 'materials' : undefined), 'button-quiet button-small'))))));
  return card;
}
function renderPreparationBatches() {
  const batches = array(state.data?.preparation_batches);
  $('#preparation-batches').replaceChildren(...(batches.length ? batches.map(batch => preparationBatchCard(batch)) : [empty('Select opportunities and prepare them together.', 'Choose individual roles across pages, or the next 10, 25 or 50 from your filtered view. Confirm the exact scope before a batch starts.', null, true)]));

}
async function openJob(id, section) {
  state.selectedJob = id;
  const dialog = $('#job-dialog'), body = $('#job-dialog-body');
  body.replaceChildren(el('div', {class: 'loading-state', role: 'status'}, el('span', {class: 'spinner'}), 'Loading the source evidence…')); openDialog(dialog);
  try {
    const result = await api(`/api/jobs/${encodeURIComponent(id)}`);
    if (String(state.selectedJob) !== String(id)) return;
    const job = result.job || result;
    if (!job.inventory) job.inventory = array(inventoryState.response?.jobs).find(item => String(item.id) === String(id))?.inventory;
    renderJob(job);
    if (section) setTimeout(() => $(`#detail-${section}`)?.scrollIntoView({behavior: 'smooth', block: 'start'}), 50);
  } catch (error) { body.replaceChildren(el('div', {class: 'banner banner-error', role: 'alert'}, error.message), button('Try again', () => openJob(id))); }
}
function detailSection(title, ...children) { return el('section', {class: 'detail-section'}, el('h3', {}, title), children); }
function list(items, fallback) { return items.length ? el('ul', {class: 'detail-list'}, items.map(item => el('li', {}, typeof item === 'string' ? item : item.text || item.message || stringify(item)))) : el('p', {class: 'muted'}, fallback); }
function professionalCandidacySection(job) {
  const match = candidacy(job), components = match.components || {}, labels = {core_skills: 'Core skills', responsibilities: 'Responsibilities', seniority: 'Seniority', domain: 'Domain familiarity', location: 'Location fit'};
  const section = detailSection('Professional candidacy', el('div', {class: 'professional-match-summary'}, el('div', {}, badge(fitNames[match.band] || 'Needs review', match.band === 'stretch' ? 'amber' : match.band === 'not_suitable' ? 'red' : ''), el('p', {class: 'field-help'}, 'An evidence-based match to this role. This heuristic is not an interview or acceptance probability.')), el('div', {class: 'professional-match-score'}, el('strong', {}, score(match.score)), el('span', {}, '/ 100'))));
  const rows = Object.entries(labels).filter(([key]) => typeof components[key] === 'number' && Number.isFinite(components[key]));
  if (rows.length) section.append(el('div', {class: 'professional-components'}, rows.map(([key, label]) => {
    const value = components[key], weight = components.weights?.[key];
    return el('div', {class: 'professional-component'}, el('div', {}, el('span', {}, label), el('strong', {}, `${score(value)} / 100`)), el('progress', {max: 100, value: Math.max(0, Math.min(100, value)), 'aria-label': `${label}: ${score(value)} out of 100`}), typeof weight === 'number' && Number.isFinite(weight) ? el('small', {}, `${Math.round(weight * 100)}% of the weighted assessment`) : null);
  })));
  else section.append(el('p', {class: 'muted'}, 'Professional component scores are not recorded yet. Review the advertised requirements and candidate evidence.'));
  const breakdown = match.score_breakdown;
  if (breakdown) section.append(el('div', {class: 'score-adjustments'}, el('p', {}, `Weighted component score: ${score(breakdown.weighted_base)}. Final score: ${score(breakdown.final_score)}.`), list(array(breakdown.adjustments).map(item => `${item.reason || human(item.type)}${item.before !== undefined && item.after !== undefined ? ` (${score(item.before)} → ${score(item.after)})` : ''}`), 'No additional caps or penalties.'), el('p', {class: 'field-help'}, `${breakdown.formula || ''} · Rubric ${breakdown.rubric_version || 'not recorded'}`)));
  return section;
}
function renderJob(job) {
  const ev = job.evaluation || {}, match = candidacy(job), body = $('#job-dialog-body'), blocked = ev.eligibility === 'blocked';
  const professional = state.data?.settings?.strategy?.mode === 'professional_london_first' || !!match.band;
  const facts = [
    ['Original advert title', job.title || 'Not recorded'], ['Compensation', salary(job)], ['Office attendance', office(job)], ['Sponsorship', sponsorship(job)], ['Permitted remote countries', array(job.remote_countries).length ? job.remote_countries.map(country => countryNames[country] || country).join(', ') : 'Not established — remote does not mean any country'],
    ['Relocation', human(job.relocation || 'Not stated')], ['Local language requirement', job.language_requirement || array(job.required_languages).join(', ') || 'Not stated — confirm with the employer'], ['Work authorisation', workAuthorisation(job)], ['Timezone requirement', job.timezone_requirement || 'Not stated'], ['Posted date', job.posted_at ? date(job.posted_at) : 'Unknown — first seen is not the posting date'], ['First seen', date(job.first_seen)], ['Last verified', date(job.last_verified)], ['Requisition', job.requisition_id || 'Not recorded'], ['Application stage', applicationStages[applicationStage(job)]], ['Preparation stage', jobPreparationLabel(job)], ['Application opened', job.application_opened_at ? date(job.application_opened_at, true) : 'Not recorded']
  ];
  if (job.applied_at) facts.push(['Application date', date(job.applied_at)]);
  const actions = el('div', {class: 'detail-actions'}, generateCVButton(job), bookmarkButton(job), hiddenButton(job), button('Update application status', () => openApplicationTracker(job.id), 'button-primary'), openApplicationLink(job));
  actions.append(button('Existing application packs', () => $('#detail-materials')?.scrollIntoView({behavior: 'smooth'}), 'button-quiet'));
  const applicationURL = safeURL(job.url);
  if (job.status === 'dismissed' || job.status === 'closed') actions.append(button('Restore legacy status', event => act(job.id, 'restore', event.currentTarget), 'button-quiet'));
  if (applicationURL) actions.append(button('Reverify source', event => act(job.id, 'reverify', event.currentTarget), 'button-quiet'));
  const content = [el('div', {class: 'job-detail-heading'}, el('div', {class: 'badges'}, professional ? null : array(ev.lanes).map(lane => badge(laneNames[lane] || human(lane))), job.sample ? badge('Sample / fixture', 'amber') : null), el('h2', {id: 'job-dialog-title'}, displayTitle(job)), el('p', {class: 'job-detail-company'}, `${job.company || 'Employer not recorded'} · ${job.location || countryNames[job.country] || 'Location unknown'}`)),
    jobNotices(job),
    el('div', {class: 'detail-meta'}, badge(eligibility(job), blocked ? 'red' : ev.eligibility === 'clear' ? '' : 'amber'), professional ? null : badge(fitNames[candidacyBand(job)] || human(candidacyBand(job)), 'neutral'), badge(applicationStages[applicationStage(job)], 'neutral')),
    blocked ? el('div', {class: 'banner banner-error'}, 'Confirmed blockers require attention. Saving or preparing a draft does not remove these restrictions.', list(array(ev.blockers), 'Review the failed eligibility conditions below.')) : null,
    el('div', {class: 'why-box'}, whyFits(job)),
    actions,
    details('Optional: choose CV emphasis before creating', directionFields(job.id)),
    el('p', {class: 'field-help'}, 'Opening the employer page records a visit only. Update the stage to Applied after you have submitted the application yourself.'),
    professional ? details('Match details & scoring explanation', professionalCandidacySection(job)) : null,
    detailSection('Your next useful step', el('p', {}, candidacy(job).next_action || ev.next_action || 'Check the source requirements and resolve important unknowns before applying.')),
    detailSection('The facts, with the unknowns kept visible', el('dl', {class: 'detail-facts'}, facts.map(([name, value]) => el('div', {}, el('dt', {}, name), el('dd', {}, value)))))
  ];
  if (job.presentation?.notice) content.push(detailSection('Vacancy availability', el('p', {}, job.presentation.notice), job.presentation.notice_source_quote ? el('blockquote', {}, job.presentation.notice_source_quote) : null, sourceLink(job.url, 'Check the employer’s current source ↗')));
  if (languageLabels(job).length) content.push(detailSection('Language requirements', languageLabels(job).map(item => el('article', {class: 'evidence-item'}, el('h4', {}, item.label), item.source_quote ? el('blockquote', {}, item.source_quote) : el('p', {class: 'muted'}, 'The advert has not confirmed this requirement.'), item.source_url ? sourceLink(item.source_url, 'Read the language requirement in the original source ↗') : null))));
  content.push(detailSection('Gaps & uncertainties', list(candidacyGaps(job), 'No additional gaps recorded. Check the source advert and your current eligibility.')));
  if (array(job.review?.findings).length) content.push(detailSection('Targeted review findings', job.review.findings.map(finding => el('article', {class: 'evidence-item'}, el('header', {}, human(finding.kind), badge(finding.status || 'UNKNOWN', finding.status === 'FAIL' ? 'red' : finding.status === 'PASS' ? '' : 'amber')), el('p', {class: 'muted'}, finding.finding), finding.job_quote ? el('blockquote', {}, finding.job_quote) : null, array(finding.candidate_evidence_ids).length ? el('span', {class: 'source-label'}, `Candidate evidence: ${finding.candidate_evidence_ids.join(', ')}`) : null)), list(array(job.review.questions), 'No additional review questions recorded.')));
  const conditions = array(ev.conditions).length ? ev.conditions : array(job.conditions);
  if (conditions.length) content.push(detailSection('Eligibility evidence', conditions.map(condition => el('article', {class: 'evidence-item'}, el('header', {}, human(condition.name || condition.field || 'Condition'), badge(condition.status || 'UNKNOWN', condition.status === 'FAIL' ? 'red' : condition.status === 'PASS' ? '' : 'amber')), el('blockquote', {}, condition.evidence || condition.quote || 'No supporting evidence recorded.'), sourceLink(condition.source)))));
  const internal = details('Internal matching details', el('p', {class: 'field-help'}, 'Earlier fit, value and priority scores are retained for diagnostics. Professional candidacy is the current assessment shown above.'), el('dl', {class: 'detail-facts'}, [['Legacy fit', ev.fit], ['Legacy value', ev.value], ['Legacy priority', ev.priority], ['Evidence confidence', ev.confidence]].map(([label, value]) => el('div', {}, el('dt', {}, label), el('dd', {}, `${score(value)} / 100`)))), el('details', {class: 'details-box'}, el('summary', {}, 'Inspect recorded scoring components'), el('pre', {class: 'diagnostics-pre'}, JSON.stringify({professional: match.components || {}, legacy: ev.components || {}, lanes: ev.lane_evaluations || {}}, null, 2))));
  content.push(internal);
  const filterReasons = job.inventory ? array(job.inventory.exclusion_reasons) : array(ev.filtered_reasons);
  const filterBox = el('details', {class: 'details-box'}, el('summary', {}, 'Why was this filtered?'), list(filterReasons, job.inventory?.accessible ? 'This opportunity is accessible in the inventory. Optional Top picks and their caps do not restrict access.' : job.inventory ? 'No additional inventory exclusion reason recorded.' : 'These are the recorded scoring-policy reasons. Check the full inventory for current accessibility; overseas scores order results rather than gate them.'));
  content.push(filterBox);
  content.push(materialsSection(job));
  const evidence = array(job.evidence);
  content.push(detailSection('Source evidence', evidence.length ? evidence.map(item => el('article', {class: 'evidence-item'}, el('header', {}, human(item.field || 'Evidence'), item.status ? badge(human(item.status), 'neutral') : null), el('blockquote', {}, item.quote || item.text || 'No excerpt recorded.'), sourceLink(item.source))) : el('p', {class: 'muted'}, 'No extracted excerpts recorded. Review the original advert below.')));
  const sources = array(job.sources);
  if (sources.length || job.url) content.push(detailSection('Original sources', job.url ? sourceLink(job.url, 'Direct application page ↗') : null, sources.map(source => el('p', {class: 'muted spaced-top'}, sourceLink(source.url || source.source_url || source.source, source.name || source.title || source.provider || 'Source page ↗'), source.last_verified ? ` · Verified ${date(source.last_verified)}` : ''))));
  content.push(el('details', {class: 'details-box'}, el('summary', {}, 'Read the original advert'), el('pre', {class: 'original-advert'}, job.description || 'No original description saved. Import the advert text to strengthen the evidence.')));
  content.push(notesSection(job));
  if (array(job.history).length) content.push(el('details', {class: 'details-box'}, el('summary', {}, 'Activity history'), list(job.history.map(item => `${date(item.created_at || item.at, true)} · ${human(item.action || item.kind || item.event || item.status)}${item.notes || item.data?.notes ? ` · ${item.notes || item.data.notes}` : ''}`), '')));
  body.replaceChildren(...content.flat(Infinity).filter(Boolean));
}
function materialsSection(job) {
  const section = detailSection('Application materials'); section.id = 'detail-materials';
  const materials = array(job.materials);
  if (!materials.length) { section.append(el('p', {}, 'Prepare a versioned draft when this opportunity is worth pursuing. Original CVs stay preserved.'), button('Prepare application', event => act(job.id, 'prepare', event.currentTarget), 'button-primary', {class: 'button button-primary spaced-top'})); return section; }
  const latest = [...materials].sort((a, b) => (Number(b.version) || 0) - (Number(a.version) || 0))[0];
  section.append(el('div', {class: 'banner banner-warning'}, `Draft version ${latest.version || materials.length} · ${human(latest.status || 'draft')}. Review all facts and unresolved answers before submitting.`));
  if (array(latest.warnings).length) section.append(list(latest.warnings, ''));
  const text = latest.text || latest.cv_text || 'The draft is available in the export files.';
  section.append(el('div', {class: 'material-preview', tabindex: 0, 'aria-label': 'Application draft preview'}, text));
  const links = el('div', {class: 'materials-actions'});
  links.append(badge(jobPreparationLabel(job), job.preparation_status === 'reviewed_ready' ? '' : 'neutral'));
  if (job.preparation_status !== 'reviewed_ready') links.append(button('Mark reviewed / ready', event => act(job.id, 'mark_reviewed', event.currentTarget), 'button-primary button-small'));
  if (job.application_opened_at) section.append(el('p', {class: 'muted'}, `Application page opened ${date(job.application_opened_at, true)}. Opening alone is not submission.`));
  links.append(button('Copy draft', event => busy(event.currentTarget, async () => { await navigator.clipboard.writeText(text); announce('Draft copied. Review it before submitting.'); }), 'button-quiet button-small'));
  const answers = array(latest.answers);
  if (answers.length) links.append(button('Copy answers', event => busy(event.currentTarget, async () => { await navigator.clipboard.writeText(answers.map(item => typeof item === 'string' ? item : `${item.question || ''}\n${item.answer || item.text || 'ANSWER NEEDED'}`).join('\n\n')); announce('Application answers copied.'); }), 'button-quiet button-small'));
  for (const format of array(latest.formats).filter(format => ['txt', 'docx', 'pdf'].includes(format))) links.append(el('a', {class: 'button button-quiet button-small', href: `/api/materials/${encodeURIComponent(latest.id)}/${format}`, download: ''}, `Export ${format.toUpperCase()} ↓`));
  section.append(links);
  if (array(latest.evidence_bullets).length) section.append(el('h4', {class: 'spaced-top'}, 'Evidence to lead with'), list(latest.evidence_bullets.map(item => `${item.text}${array(item.evidence_ids).length ? ` [${item.evidence_ids.join(', ')}]` : ''}`), ''));
  if (array(latest.interview_notes).length) section.append(el('h4', {class: 'spaced-top'}, 'Prepare for the conversation'), list(latest.interview_notes, ''));
  const edit = el('details', {class: 'details-box'}, el('summary', {}, 'Edit and save a new draft version'));
  const editor = el('textarea', {rows: 12, value: text, 'aria-label': 'Editable application draft'});
  edit.append(el('label', {class: 'field'}, 'Application draft', editor), el('p', {class: 'field-help'}, 'Manual edits are saved as a new revision and need your factual review.'), button('Save edited draft', event => busy(event.currentTarget, async () => { await api(`/api/materials/${encodeURIComponent(latest.id)}`, {text: editor.value}); announce('Edited draft saved as a new revision.'); await refreshState(); await openJob(job.id, 'materials'); }), 'button-primary button-small'));
  section.append(edit); return section;
}
const cvProduct = {jobId: null, data: null, poll: null, request: 0, pending: false, autoStart: null, drafts: new Map(), selected: new Set(), rejected: new Set(), displayedRun: null, signature: ''};
let baseCVRequest = 0;
async function renderBaseCV(response) {
  const body = $('#base-cv-body'), request = ++baseCVRequest;
  if (!response) body.replaceChildren(el('p', {class: 'loading-state'}, 'Loading your reusable base CV…'));
  try {
    const data = response || await api('/api/base-cv'); if (request !== baseCVRequest) return;
    const selected = data.selected, error = el('div', {class: 'banner banner-error', role: 'alert', hidden: true});
    const upload = el('input', {type: 'file', accept: '.pdf,.docx,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document', 'aria-label': selected ? 'Replace base CV file' : 'Upload your base CV file'});
    const submit = button(selected ? 'Replace base CV' : 'Upload base CV', event => busy(event.currentTarget, async () => {
      error.hidden = true; const file = upload.files[0];
      if (!file || !/\.(pdf|docx)$/i.test(file.name)) { showError(error, new Error('Choose one PDF or DOCX file.')); return; }
      if (file.size > (data.max_upload_bytes || 8388608)) { showError(error, new Error('Choose a CV smaller than 8 MB.')); return; }
      try {
        const content = await new Promise((resolve, reject) => { const reader = new FileReader(); reader.onload = () => resolve(String(reader.result).split(',')[1]); reader.onerror = () => reject(new Error('The selected file could not be read.')); reader.readAsDataURL(file); });
        submit.textContent = 'Saving your base CV…';
        const result = await api('/api/base-cv', {filename: file.name, content_base64: content});
        await renderBaseCV(result); announce('Base CV saved. You can reuse it for every job. Existing application versions remain unchanged.');
      } catch (problem) { showError(error, problem); submit.textContent = selected ? 'Replace base CV' : 'Upload base CV'; }
    }), 'button-primary');
    const uploader = el('section', {class: 'base-cv-upload'}, el('h2', {}, selected ? 'Update your source CV' : 'Start with the CV you already have'), el('p', {class: 'muted'}, 'PDF or DOCX, up to 8 MB. Supported facts are reconciled with your existing evidence.'), el('label', {class: 'field'}, selected ? 'Replacement file' : 'Your CV file', upload), submit, error);
    const content = [];
    if (selected) {
      content.push(el('section', {class: 'settings-card base-cv-selected'}, el('div', {class: 'section-heading'}, el('div', {}, badge('Selected base CV', ''), el('h2', {class: 'spaced-top'}, selected.filename), el('p', {class: 'muted'}, `${selected.source === 'existing' ? 'Existing source reused' : 'Uploaded'} · ${date(selected.uploaded_at, true)}`)), button(state.workflowJobId ? 'Return to this job →' : 'Choose a job →', () => state.workflowJobId ? showCVWorkflow(state.workflowJobId) : navigate('all'), 'button-primary')), array(selected.conflicts).length ? el('div', {class: 'banner banner-warning'}, el('strong', {}, 'A few facts need checking'), list(selected.conflicts.map(item => item.message || `${human(item.field)}: ${item.source_quote || 'New source'} differs from ${stringify(item.verified_value)}`), ''), el('p', {class: 'field-help'}, 'Your existing verified facts remain authoritative. This upload has not overwritten them.'), button('Review your evidence', () => $('#profile-settings').scrollIntoView({behavior: 'smooth'}), 'button-quiet button-small')) : el('p', {class: 'field-help'}, 'This source is reused across applications. Tailored versions never replace the original file.'), el('div', {class: 'material-preview base-cv-preview', tabindex: 0, 'aria-label': 'Selected base CV preview'}, selected.preview || selected.text || 'No readable preview is available.')));
      content.push(details('Replace base CV', uploader));
    } else content.push(uploader);
    const versions = array(data.versions);
    if (versions.length > 1) {
      const previous = el('ul', {class: 'history-list'});
      for (const version of versions) previous.append(el('li', {}, el('strong', {}, version.filename), el('p', {class: 'muted'}, date(version.uploaded_at, true)), String(version.id) === String(selected?.id) ? badge('Selected', '') : button('Use this base CV', event => busy(event.currentTarget, async () => { await renderBaseCV(await api('/api/base-cv/select', {version_id: version.id})); announce('Base CV selected. Historical application documents are preserved.'); }), 'button-quiet button-small')));
      content.push(details('Earlier base CVs', previous));
    }
    body.replaceChildren(...content);
  } catch (error) { if (request === baseCVRequest) body.replaceChildren(el('div', {class: 'banner banner-error', role: 'alert'}, error.message), button('Retry loading Your CV', () => renderBaseCV())); }
}
let modelConnectionsRequest = 0;
async function renderModelConnections() {
  const body = $('#model-connections-body'), request = ++modelConnectionsRequest;
  $('#return-to-cv-setup').replaceChildren(...(state.workflowJobId ? [button('← Return to this job’s CV', () => showCVWorkflow(state.workflowJobId), 'text-link', {class: 'text-link back-to-jobs'})] : []));
  if (!body.children.length) body.replaceChildren(el('p', {class: 'muted'}, 'Checking available model connections…'));
  try {
    const data = await api('/api/model-connections'); if (request !== modelConnectionsRequest) return;
    const roles = data.roles || {}, draft = structuredClone(data.configuration || data.config || state.data?.settings?.model_connections || {}), error = el('div', {class: 'banner banner-error', role: 'alert', hidden: true});
    const form = el('form', {class: 'model-connections-form'}), controls = {};
    for (const [key, title] of [['generator', 'Generator'], ['red', 'Critical reviewer / red team'], ['blue', 'Independent reviewer / blue team'], ['purple', 'Final synthesis / purple team']]) {
      const role = roles[key] || {}, current = draft[key] || role;
      const provider = el('select', {'aria-label': `${title} connection`}, el('option', {value: ''}, 'Choose a connection'), el('option', {value: 'codex_cli'}, 'Authenticated Codex CLI'), el('option', {value: 'claude_cli'}, 'Authenticated Claude CLI'));
      provider.value = current.provider || '';
      const model = el('input', {type: 'text', value: current.model || '', required: true, placeholder: 'Exact supported model ID', 'aria-label': `${title} model`});
      const effort = el('select', {'aria-label': `${title} reasoning effort`}, ['low', 'medium', 'high', 'xhigh', 'max'].map(value => el('option', {value}, human(value)))); effort.value = current.effort || 'high';
      controls[key] = {provider, model, effort};
      for (const control of Object.values(controls[key])) control.addEventListener(control === model ? 'input' : 'change', () => { modelConnectionsDirty = true; });
      form.append(el('section', {class: 'model-role-card'}, el('div', {class: 'section-heading'}, el('h3', {}, title), badge(role.ready ? 'Connected' : 'Needs connection', role.ready ? '' : 'amber')), el('p', {class: 'muted'}, role.reason || (role.ready ? 'Configured connection is available.' : 'Choose an available authenticated connection.')), role.actual_model ? el('p', {class: 'field-help'}, `Actual model: ${role.actual_model}`) : null, el('p', {class: 'field-help'}, role.live_verified ? 'A completed model call has been verified.' : 'Connection readiness only; no completed model call has been verified for this configuration.'), el('div', {class: 'form-grid three'}, el('label', {class: 'field'}, 'Connection', provider), el('label', {class: 'field'}, 'Model', model), el('label', {class: 'field'}, 'Reasoning effort', effort))));
    }
    form.append(el('p', {class: 'field-help'}, 'Saving configures the supported connection; it does not buy credits, increase spending limits or sign into an account. Existing subscriptions and API billing remain separate.'), data.notice ? el('p', {class: 'muted'}, data.notice) : null, error, el('div', {class: 'form-footer'}, button('Refresh connection status', () => renderModelConnections(), 'button-quiet'), el('button', {type: 'submit', class: 'button button-primary'}, 'Save model connections')));
    form.addEventListener('submit', event => {
      event.preventDefault(); error.hidden = true;
      busy($('button[type=submit]', form), async () => {
        try {
          for (const [key, fields] of Object.entries(controls)) draft[key] = {...(draft[key] || {}), provider: fields.provider.value, model: fields.model.value.trim(), effort: fields.effort.value};
          const result = await api('/api/model-connections', {model_connections: draft});
          state.data.settings.model_connections = result.configuration || result.config || draft; settingsDraft.model_connections = structuredClone(state.data.settings.model_connections); modelConnectionsDirty = false; await renderModelConnections(); announce('Model connections saved. Readiness reflects the configured connections.');
        } catch (problem) { showError(error, problem); }
      });
    });
    body.replaceChildren(form, details('Advanced: detected connections', el('pre', {class: 'diagnostics-pre'}, JSON.stringify(data.detected || data.connections || {}, null, 2))));
  } catch (error) { if (request === modelConnectionsRequest) body.replaceChildren(el('div', {class: 'banner banner-error', role: 'alert'}, error.message), button('Retry connection check', () => renderModelConnections())); }
}

const emphasisOptions = [['balanced', 'Balanced for this job'], ['applied_ai', 'Applied AI / implementation'], ['client_solutions', 'Client-facing solutions'], ['technical_depth', 'Technical depth / engineering'], ['quantitative', 'Quantitative problem-solving'], ['concise', 'More concise']];
function directionChoices(jobId) {
  const options = String(cvProduct.data?.job_id) === String(jobId) ? array(cvProduct.data?.directions) : [];
  return options.length ? options.map(item => [item.value, item.label]) : emphasisOptions;
}
function directionDraft(jobId) {
  if (!cvProduct.drafts.has(String(jobId))) cvProduct.drafts.set(String(jobId), {emphasis: 'balanced', instruction: ''});
  return cvProduct.drafts.get(String(jobId));
}
function directionFields(jobId, locked = false) {
  const draft = directionDraft(jobId), select = el('select', {'aria-label': 'CV emphasis'}, directionChoices(jobId).map(([value, label]) => el('option', {value}, label)));
  select.value = draft.emphasis; select.disabled = locked; select.addEventListener('change', () => { draft.emphasis = select.value; });
  const instruction = el('textarea', {rows: 2, maxlength: 1000, value: draft.instruction, placeholder: 'For example: emphasise understanding customer problems and implementing AI solutions.', 'aria-label': 'Optional CV direction instruction'});
  instruction.disabled = locked; instruction.addEventListener('input', () => { draft.instruction = instruction.value; });
  return el('div', {class: 'cv-direction-fields'}, el('label', {class: 'field'}, 'Emphasis', select), el('label', {class: 'field'}, 'A short instruction (optional)', instruction), el('p', {class: 'field-help'}, 'This changes the emphasis for this job. Your facts and overall career preferences stay unchanged.'));
}
function showCVWorkflow(jobId, autoStart = false) {
  if (['recommended', 'all', 'needs_checking', 'saved', 'applications'].includes(state.page)) state.returnToJobs = {page: state.page, hash: location.hash, scroll: window.scrollY};
  $$('dialog[open]').forEach(closeDialog);
  state.workflowJobId = Number(jobId); cvProduct.jobId = Number(jobId); cvProduct.signature = '';
  if (autoStart && (!cvProduct.autoStart || cvProduct.autoStart.jobId !== Number(jobId))) cvProduct.autoStart = {jobId: Number(jobId), key: crypto.randomUUID()};
  if (state.page === 'cv-workflow' && location.hash === `#cv/${jobId}`) loadCVWorkflow(jobId); else navigate(`cv/${jobId}`);
}
function backToJobs() {
  clearTimeout(cvProduct.poll); cvProduct.autoStart = null;
  const previous = state.returnToJobs || {page: 'all', hash: '#all', scroll: 0};
  state.restoreInventory = !!inventoryState.response && inventoryState.view === previous.page;
  navigate(previous.page);
  requestAnimationFrame(() => requestAnimationFrame(() => window.scrollTo({top: previous.scroll || 0, behavior: 'auto'})));
}
function isMockedCVRun(run) { return Object.values(run?.receipts || {}).some(receipt => receipt.mocked === true); }
function currentCVRun(workflow) {
  if (workflow.active_run && typeof workflow.active_run === 'object') return workflow.active_run;
  const runs = [...array(workflow.runs)].sort((a, b) => String(b.created_at || '').localeCompare(String(a.created_at || '')));
  return runs.find(run => String(run.id) === String(workflow.active_run)) || runs[0] || null;
}
async function loadCVWorkflow(jobId) {
  const request = ++cvProduct.request; clearTimeout(cvProduct.poll);
  if (cvProduct.jobId !== Number(jobId)) { cvProduct.jobId = Number(jobId); cvProduct.signature = ''; }
  const known = getJobs().find(job => String(job.id) === String(jobId));
  $('#cv-workflow-heading').textContent = known ? displayTitle(known) : 'Prepare your application.';
  $('#cv-workflow-company').textContent = known ? `${known.company || 'Employer'} · ${known.location || 'Location not recorded'}` : 'A separate, versioned CV for this opportunity.';
  if (!cvProduct.signature) $('#cv-workflow-body').replaceChildren(el('p', {class: 'loading-state', role: 'status'}, 'Loading your application workspace…'));
  try {
    const workflow = await api(`/api/jobs/${encodeURIComponent(jobId)}/cv-workflow`);
    if (request !== cvProduct.request || state.page !== 'cv-workflow') return;
    cvProduct.data = workflow; const run = currentCVRun(workflow), signature = JSON.stringify(workflow);
    if (!cvProduct.drafts.has(String(jobId))) {
      const version = array(workflow.versions).find(item => String(item.id) === String(workflow.selected_material_id)), prior = version?.direction || run?.direction || {};
      cvProduct.drafts.set(String(jobId), {emphasis: prior.emphasis || 'balanced', instruction: prior.instruction || ''});
    }
    if (signature !== cvProduct.signature) { cvProduct.signature = signature; renderCVProduct(workflow); }
    const intent = cvProduct.autoStart;
    if (intent?.jobId === Number(jobId)) {
      cvProduct.autoStart = null;
      if (!array(workflow.runs).length && workflow.base_cv_ready && workflow.connections_ready && !array(workflow.setup_blockers).length) { await cvProductAction('start', {}, null, intent.key); return; }
    }
    if (run && ['queued', 'running'].includes(run.status)) cvProduct.poll = setTimeout(() => loadCVWorkflow(jobId), 1800);
  } catch (error) {
    if (request !== cvProduct.request || state.page !== 'cv-workflow') return;
    $('#cv-workflow-body').replaceChildren(el('div', {class: 'banner banner-error', role: 'alert'}, `This workspace could not load: ${error.message}`), button('Retry loading progress', () => loadCVWorkflow(jobId), 'button-primary'));
  }
}
async function cvProductAction(action, extra = {}, control, idempotencyKey) {
  if (cvProduct.pending) return;
  cvProduct.pending = true; if (control) { control.disabled = true; control.setAttribute('aria-busy', 'true'); }
  try {
    const jobId = cvProduct.jobId, draft = directionDraft(jobId);
    const body = {action, ...(['start', 'revise', 'compare'].includes(action) ? {emphasis: draft.emphasis, instruction: draft.instruction} : {}), ...extra};
    if (['start', 'revise', 'compare', 'review_again'].includes(action)) body.idempotency_key = idempotencyKey || crypto.randomUUID();
    const workflow = await api(`/api/jobs/${encodeURIComponent(jobId)}/cv-workflow`, body);
    if (state.page === 'cv-workflow' && Number(state.workflowJobId) === Number(jobId)) { cvProduct.data = workflow; cvProduct.signature = ''; renderCVProduct(workflow); await loadCVWorkflow(jobId); }
    if (action === 'select') announce('This CV version is selected. No new model call or application submission was made.');
  } catch (error) { announce(error.message, true); }
  finally { cvProduct.pending = false; if (control) { control.disabled = false; control.removeAttribute('aria-busy'); } }
}
function cvFindingText(item) { return typeof item === 'string' ? item : item.text || item.message || item.recommended_action || item.reason || item.finding || stringify(item); }
function cvAttentionSection(job, version, run, reviewed, qualifications = []) {
  const ranked = new Map(), severityOrder = {critical: 1, high: 2, medium: 3, low: 4};
  function include(item, priority) {
    if (!item) return;
    const text = cvFindingText(item), rank = priority ?? severityOrder[String(item.severity || '').toLowerCase()] ?? 5;
    if (!ranked.has(text)) ranked.set(text, {text, rank});
    else ranked.get(text).rank = Math.min(ranked.get(text).rank, rank);
  }
  [...array(run?.needs_attention), ...array(version.analysis?.evidence_match?.gaps)].forEach(item => include(item));
  // Use recorded severity and protected-source flags; do not infer new facts from wording.
  for (const finding of array(run?.suggestions)) if (ranked.has(cvFindingText(finding))) include(finding);
  array(job?.evaluation?.blockers).forEach(item => include(item, 0));
  array(version.blocked_proposals).forEach(item => include(typeof item === 'string' ? item : item.reason || item.message, 0));
  array(version.base_cv_conflicts).forEach(item => include(typeof item === 'string' ? item : `Base CV conflict: ${item.message || item.reason || item.field || 'A protected fact needs confirmation.'}`, 0));
  const all = [...ranked.values()].sort((a, b) => a.rank - b.rank).map(item => item.text);
  const knownDates = array(qualifications).filter(item => item.award_date).map(item => `${item.title}: ${date(item.award_date)}`);
  return detailSection('What still needs attention', knownDates.length ? el('p', {class: 'banner banner-info'}, `Award dates already confirmed in your profile: ${knownDates.join('; ')}. You do not need to reconfirm these dates if an older review asks.`) : null, list(all.slice(0, 5), reviewed ? 'No outstanding questions were reported. Check the original advert and final document before use.' : 'The review has not completed. Questions may still remain.'), all.length > 5 ? el('p', {class: 'field-help'}, `Showing 5 of ${all.length}. Every gap and question is retained below.`) : null, all.length > 5 ? details(`All gaps & questions (${all.length})`, list(all, '')) : null);
}
function resultCheck(label, data) {
  const metric = data || {}, incomplete = metric.status === 'incomplete' || Number(metric.unassessed_requirements) > 0 || Number(metric.coverage?.unassessed_essential) > 0;
  const warning = incomplete || ['warning', 'needs_review', 'needs_attention', 'incomplete', 'failed'].includes(metric.status);
  const text = incomplete ? 'Assessment incomplete — essential requirements need checking.' : metric.label || 'Assessment not recorded.';
  const checks = array(metric.checks);
  return el('section', {class: 'result-check'}, el('h3', {}, label), badge(incomplete ? 'Needs checking' : metric.status ? human(metric.status) : 'Not assessed', warning ? 'amber' : 'neutral'), el('p', {}, text), metric.coverage ? el('p', {class: 'field-help'}, `${metric.coverage.assessed ?? '—'} of ${metric.coverage.total ?? '—'} requirements assessed`) : null, checks.length ? details('Inspect checks', list(checks.map(check => typeof check === 'string' ? check : `${check.passed ? 'Pass' : 'Needs attention'} · ${check.name || check.label || check.text || 'Check'}`), '')) : null);
}
function applicationProgress(run, workflow) {
  const receipts = Object.values(run?.receipts || {}), steps = [['generator', 'Draft'], ['red', 'Red team'], ['blue', 'Blue team']];
  if (array(workflow.workflow_roles).includes('purple') || array(run?.workflow_roles).includes('purple') || receipts.some(item => item.role === 'purple') || run?.application_packs) steps.push(['purple', 'Purple team']);
  return el('ol', {class: 'application-progress', 'aria-label': 'Application preparation stages'}, steps.map(([role, label]) => {
    const records = receipts.filter(item => item.role === role), done = records.length > 0 && records.every(item => item.status === 'completed');
    const failed = records.some(item => ['failed', 'uncertain'].includes(item.status));
    const running = records.some(item => item.status === 'dispatching');
    return el('li', {class: `${role}${done ? ' complete' : running ? ' active' : failed ? ' failed' : ''}`}, el('span', {'aria-hidden': 'true'}, done ? '✓' : running ? '◌' : '○'), label, el('span', {class: 'sr-only'}, done ? ' complete' : failed ? ' needs attention' : running ? ' working' : ' not started'));
  }));
}
function applicationPack(run, version) {
  const pack = run?.application_packs?.[String(version.id)];
  if (!pack || String(pack.material_id) !== String(version.id)) return null;
  const mocked = pack.mocked === true || isMockedCVRun(run), letter = pack.cover_letter?.text || array(pack.cover_letter?.paragraphs).join('\n\n');
  const downloads = el('div', {class: 'cv-download-actions'}, button(`Copy ${mocked ? 'test ' : ''}cover letter`, event => busy(event.currentTarget, async () => { await navigator.clipboard.writeText(letter); announce('Cover letter copied. Review it before sending.'); }), 'button-quiet'));
  for (const format of array(pack.formats).filter(item => ['txt', 'docx', 'pdf'].includes(item))) downloads.append(el('a', {class: 'button button-quiet', href: `/api/cv-runs/${encodeURIComponent(run.id)}/application-pack/${encodeURIComponent(version.id)}/${format}`, download: ''}, `Download ${mocked ? 'test ' : ''}cover letter ${format.toUpperCase()}`));
  return el('section', {class: 'cv-finished-document cover-letter-document'}, el('div', {class: 'section-heading'}, el('div', {}, el('p', {class: 'eyebrow'}, 'PURPLE TEAM SYNTHESIS'), el('h2', {}, 'Your cover letter')), badge(mocked ? 'Test document — mocked synthesis' : 'Ready for your review', mocked ? 'amber' : 'neutral')), pack.summary ? el('p', {class: 'muted'}, pack.summary) : null, downloads, el('div', {class: 'material-preview product-cv-preview', tabindex: 0, 'aria-label': 'Cover letter preview'}, letter), array(pack.outstanding_questions).length ? details('Questions to resolve before applying', list(pack.outstanding_questions.map(cvFindingText), '')) : null, details('How the reviews were resolved', list(array(pack.decisions).map(item => `${human(item.decision)}: ${item.reason}`), 'No additional reviewer decisions were recorded.')), el('p', {class: 'field-help'}, 'Review the CV and cover letter, then submit them through the employer’s application page.'));
}
function renderCVProduct(workflow) {
  const jobId = workflow.job_id || cvProduct.jobId, run = currentCVRun(workflow), active = !!run && ['queued', 'running'].includes(run.status);
  const versions = array(workflow.versions), selectedId = workflow.selected_material_id || run?.selected_material_id || array(run?.material_ids).slice(-1)[0];
  const version = versions.find(item => String(item.id) === String(selectedId)) || null;
  const body = $('#cv-workflow-body'), content = [], job = getJobs().find(item => String(item.id) === String(jobId));
  const selectionKey = `${run?.id || ''}:${version?.id || ''}`;
  if (cvProduct.displayedRun !== selectionKey) { cvProduct.displayedRun = selectionKey; cvProduct.selected.clear(); cvProduct.rejected.clear(); }
  if (!workflow.base_cv_ready || !workflow.connections_ready || array(workflow.setup_blockers).length) {
    const setup = el('section', {class: 'cv-setup-card'}, el('h2', {}, 'A little setup. A stronger application.'), list(array(workflow.setup_blockers).map(cvFindingText), 'Your base CV and model connections are required.'));
    if (!workflow.base_cv_ready) setup.append(button('Choose your base CV', () => navigate('your-cv'), 'button-primary'));
    if (workflow.base_cv_ready && array(workflow.setup_blockers).length) setup.append(button('Add your verified experience', () => navigate('your-cv'), 'button-quiet'));
    if (!workflow.connections_ready) setup.append(button('Connect reviewers', () => { $('#model-connections-settings').open = true; navigate('settings'); }, 'button-primary'));
    content.push(setup);
  }
  if (job) content.push(...jobNotices(job));
  if (job?.evaluation?.eligibility === 'blocked') content.push(el('div', {class: 'banner banner-warning'}, el('strong', {}, 'Known eligibility blocker'), list(candidacyGaps(job), 'Review the employer’s requirement. A revised CV cannot remove this restriction.')));
  if (run) {
    if (isMockedCVRun(run)) content.push(el('div', {class: 'banner banner-warning'}, el('strong', {}, 'Test run — mocked model responses'), el('p', {}, 'These results test the workflow. They do not establish that live models generated or reviewed this CV.')));
    const label = run.label || {queued: 'Preparing draft', running: 'Preparing draft', ready: 'Ready for your review', needs_answer: 'Needs your answer', failed: 'Could not complete', uncertain: 'Could not complete', cancelled: 'Cancelled', needs_setup: 'Connection needed'}[run.status] || human(run.status);
    const status = el('section', {class: `cv-live-status${active ? ' active' : ''}`, role: 'status'}, active ? el('span', {class: 'spinner', 'aria-hidden': 'true'}) : null, el('div', {}, el('h2', {}, isMockedCVRun(run) ? `Test run · ${label}` : label), el('p', {}, active ? 'Progress is saved by the app. You can leave this page and return while it continues.' : ['ready', 'needs_answer'].includes(run.status) ? 'Review the finished document and any remaining questions before using it.' : 'Completed stages are preserved. A failed reviewer is not counted as a passed review.')));
    if (active) status.append(button('Cancel', event => cvProductAction('cancel', {run_id: run.id}, event.currentTarget), 'button-quiet button-small'));
    content.push(applicationProgress(run, workflow), status);
    if (run.error) content.push(el('div', {class: 'banner banner-error', role: 'alert'}, typeof run.error === 'string' ? run.error : cvFindingText(run.error)));
    if (['failed', 'uncertain', 'cancelled', 'needs_setup'].includes(run.status)) {
      const retry = el('section', {class: 'cv-retry'}), acknowledged = el('input', {type: 'checkbox'});
      if (run.requires_explicit_retry) retry.append(el('label', {class: 'checkbox-field'}, acknowledged, el('span', {}, 'I understand the previous model call may have completed and another call could repeat work.')));
      const retryButton = button('Retry unfinished stages', event => { if (run.requires_explicit_retry && !acknowledged.checked) { announce('Confirm the uncertain previous outcome before requesting another call.', true); return; } cvProductAction('retry', {run_id: run.id, explicit_uncertain: acknowledged.checked}, event.currentTarget); }, 'button-primary');
      retry.append(retryButton); content.push(retry);
    }
  }
  if (!run || ['needs_setup', 'cancelled'].includes(run.status)) {
    const start = detailSection('Prepare this application', el('p', {class: 'muted'}, 'Create a tailored CV, independent reviews and a cover letter grounded in your experience.'), directionFields(jobId), button('Prepare application', event => cvProductAction('start', {}, event.currentTarget), 'button-primary'));
    $('button', start).disabled = !workflow.base_cv_ready || !workflow.connections_ready || array(workflow.setup_blockers).length > 0;
    content.push(start);
  }
  if (version) {
    const versionRun = array(workflow.runs).find(item => array(item.material_ids).some(id => String(id) === String(version.id))) || null;
    const reviewedRun = array(workflow.runs).find(item => ['ready', 'needs_answer'].includes(item.status) && (String(item.selected_material_id || array(item.material_ids).slice(-1)[0]) === String(version.id) || array(item.comparison?.candidates).some(candidate => String(candidate.material_id) === String(version.id))));
    const resultRun = reviewedRun || versionRun, analysis = version.analysis || {}, testVersion = isMockedCVRun(resultRun);
    const improvements = array(reviewedRun?.what_improved).map(cvFindingText).slice(0, 5);
    const download = el('div', {class: 'cv-download-actions'});
    for (const format of ['pdf', 'docx'].filter(item => array(version.formats).includes(item))) download.append(el('a', {class: `button ${reviewedRun && !testVersion ? 'button-primary' : 'button-quiet'}`, href: `/api/materials/${encodeURIComponent(version.id)}/${format}`, download: ''}, `Download ${testVersion ? 'test ' : reviewedRun ? '' : 'draft '}${format.toUpperCase()}`));
    if (!download.children.length) download.append(el('p', {class: 'muted'}, 'The document is still being checked; PDF/DOCX downloads are not available yet.'));
    const adjust = details('Adjust direction & revise', directionFields(jobId, active)); adjust.id = 'cv-adjust-direction';
    const suggestions = array(resultRun?.suggestions).filter(item => !item.material_id || String(item.material_id) === String(version.id) || item.status === 'applied');
    if (suggestions.length) {
      adjust.append(el('h3', {}, 'Recommended changes'));
      for (const item of suggestions) {
        const category = String(item.category || item.type || '').toLowerCase(), gap = category === 'experience_gap', question = category === 'question' || item.status === 'needs_answer', applied = item.status === 'applied';
        const checkbox = el('input', {type: 'checkbox', checked: cvProduct.selected.has(item.id), 'aria-label': `Select change: ${cvFindingText(item)}`, onchange: event => { if (event.target.checked) { cvProduct.selected.add(item.id); cvProduct.rejected.delete(item.id); } else cvProduct.selected.delete(item.id); }}); checkbox.disabled = active || gap || question || applied;
        const choice = el('article', {class: 'product-suggestion'}, el('label', {class: 'finding-heading'}, checkbox, el('strong', {}, cvFindingText(item))), badge(gap ? 'Experience gap' : question ? 'Question' : applied ? 'Already improved' : 'Document improvement', gap || question ? 'amber' : 'neutral'), details('Why this change', item.cv_passage ? el('blockquote', {}, item.cv_passage) : null, el('p', {}, item.advert_requirement || 'Document quality and truthful positioning'), list(array(item.evidence_refs).map(ref => typeof ref === 'string' ? ref : ref.id || stringify(ref)), 'No supporting evidence reference supplied.')));
        if (!gap && !question && !applied) choice.append(button(cvProduct.rejected.has(item.id) ? 'Rejected — undo' : 'Reject suggestion', event => { const rejected = !cvProduct.rejected.has(item.id); if (rejected) { cvProduct.rejected.add(item.id); cvProduct.selected.delete(item.id); checkbox.checked = false; } else cvProduct.rejected.delete(item.id); event.currentTarget.textContent = rejected ? 'Rejected — undo' : 'Reject suggestion'; }, 'text-link'));
        if (gap) choice.append(el('p', {class: 'field-help'}, 'Rewriting cannot create this experience.'));
        adjust.append(choice);
      }
      adjust.append(button('Revise selected changes', event => { if (!cvProduct.selected.size && !cvProduct.rejected.size) { announce('Select or reject the changes you want to address.', true); return; } cvProductAction('revise', {material_id: version.id, selected_findings: [...cvProduct.selected], rejected_findings: [...cvProduct.rejected]}, event.currentTarget); }, 'button-primary'));
    }
    adjust.append(button('Revise with this direction', event => cvProductAction('revise', {material_id: version.id, selected_findings: [], rejected_findings: [...cvProduct.rejected]}, event.currentTarget), 'button-quiet', {class: 'button button-quiet spaced-top'}));
    if (active) $$('button', adjust).forEach(control => { control.disabled = true; });
    download.append(button('Adjust & revise', () => { adjust.open = true; adjust.scrollIntoView({behavior: 'smooth', block: 'start'}); }, 'button-quiet'));
    const versionDirection = version.direction?.emphasis || version.emphasis || resultRun?.direction?.emphasis || directionDraft(jobId).emphasis;
    const directionReason = version.direction?.reason || resultRun?.direction_reason;
    content.push(el('section', {class: 'cv-finished-document'}, el('div', {class: 'section-heading'}, el('div', {}, el('h2', {}, `Your CV · version ${version.version}`), el('p', {class: 'muted'}, `Direction: ${directionChoices(jobId).find(([key]) => key === versionDirection)?.[1] || human(versionDirection)}`), directionReason ? el('p', {class: 'field-help'}, directionReason) : null), badge(testVersion ? 'Test document — mocked review' : active ? 'Draft — checks in progress' : reviewedRun?.status === 'needs_answer' ? 'Reviewed — your answer needed' : reviewedRun ? 'Ready for your review' : 'Review required', active || !reviewedRun || testVersion ? 'amber' : '')), testVersion && !isMockedCVRun(run) ? el('p', {class: 'banner banner-warning'}, 'Test run — mocked model responses. This selected document has no verified live review.') : null, download, el('div', {class: 'material-preview product-cv-preview', tabindex: 0, 'aria-label': 'Tailored CV preview'}, version.cv_text || version.text || 'Preview not recorded.')));
    const pack = applicationPack(resultRun, version); if (pack) content.push(pack);
    content.push(el('div', {class: 'cv-result-columns'}, detailSection('What improved', list(improvements, reviewedRun ? 'No further material changes were recommended.' : 'Changes appear when the review stages complete.')), cvAttentionSection(job, version, resultRun, !!reviewedRun, workflow.confirmed_qualifications)));
    content.push(el('div', {class: 'cv-check-summary'}, resultCheck('Verified evidence fit', analysis.evidence_match), resultCheck('How the CV presents your evidence', analysis.evidence_presentation), resultCheck('Document checks', analysis.document_checks)));
    content.push(adjust);
    const changes = details('View changes from the previous version', array(version.diff).length ? el('div', {class: 'cv-diff', tabindex: 0, 'aria-label': 'CV changes'}, version.diff.map(part => el('div', {class: `cv-diff-line diff-${['equal', 'insert', 'delete'].includes(part.type) ? part.type : 'equal'}`}, el('span', {class: 'diff-label'}, {insert: 'Added', delete: 'Removed', equal: 'Unchanged'}[part.type] || 'Unchanged'), el(part.type === 'insert' ? 'ins' : part.type === 'delete' ? 'del' : 'span', {}, part.text || '')))) : el('p', {class: 'muted'}, 'No earlier version to compare.'));
    const previous = details('Previous versions & undo', el('p', {class: 'field-help'}, 'Select an earlier version to undo your choice. Every document and review stays preserved.'), el('ul', {class: 'history-list'}, versions.map(item => el('li', {}, el('strong', {}, `CV v${item.version} · ${date(item.created_at, true)}`), String(item.id) === String(version.id) ? badge('Selected', '') : button('Select this version', event => cvProductAction('select', {material_id: item.id}, event.currentTarget), 'button-quiet button-small')))));
    content.push(changes, previous);
    const comparison = resultRun?.comparison || run?.comparison;
    if (array(comparison?.candidates).length) content.push(details('Compare approaches', el('p', {}, comparison.summary || 'Both approaches use the same job and candidate evidence.'), el('div', {class: 'cv-comparison'}, comparison.candidates.map(item => el('article', {}, el('h3', {}, directionChoices(jobId).find(([key]) => key === item.emphasis)?.[1] || human(item.label || item.emphasis)), String(comparison.recommended_material_id) === String(item.material_id) ? badge('Reviewer preference', '') : null, list(array(comparison.preferences).filter(pref => String(pref.material_id) === String(item.material_id)).flatMap(pref => array(pref.reasons).map(reason => `${human(pref.team)}: ${reason}`)), 'No recorded reviewer preference.'), button('Select this approach', event => cvProductAction('select', {material_id: item.material_id}, event.currentTarget), 'button-quiet button-small')))), el('p', {class: 'field-help'}, 'Reviewer preferences are opinions, not measured hiring outcomes.')));
    const more = details('More options', button('Review again', event => cvProductAction('review_again', {material_id: version.id}, event.currentTarget), 'button-quiet'), button('Compare two approaches', event => cvProductAction('compare', {material_id: version.id}, event.currentTarget), 'button-quiet'), el('p', {class: 'field-help'}, 'These request another bounded run through your configured connections.'));
    if (active || !workflow.connections_ready) $$('button', more).forEach(control => { control.disabled = true; });
    content.push(more);
  }
  content.push(details('Advanced / diagnostics', el('p', {class: 'field-help'}, 'Manual reviewer exchange is a fallback. These tools do not replace a missing or failed automated review.'), button('Open manual review exchange', () => openCV(jobId, false), 'button-quiet button-small'), details('Run details and actual model receipts', el('pre', {class: 'diagnostics-pre'}, JSON.stringify(run ? {id: run.id, status: run.status, stage: run.stage, receipts: run.receipts, usage: run.usage, error: run.error} : {status: 'not_started'}, null, 2))), version ? details('Detailed scoring and rubric', el('pre', {class: 'diagnostics-pre'}, JSON.stringify(version.analysis || {}, null, 2))) : null));
  body.replaceChildren(...content);
}

let cvRequest = 0;
async function openCV(jobId, generate = false) {
  const dialog = $('#cv-dialog'), body = $('#cv-dialog-body'), request = ++cvRequest;
  dialog.dataset.jobId = String(jobId); body.replaceChildren(el('p', {class: 'loading-state', role: 'status'}, generate ? 'Preparing the versioned CV from your verified evidence…' : 'Loading your CV versions and reviews…')); openDialog(dialog);
  const job = getJobs().find(item => String(item.id) === String(jobId));
  $('#cv-dialog-title').textContent = job ? `${job.title} · ${job.company}` : 'Your tailored CV';
  try {
    const workflow = await api(`/api/jobs/${encodeURIComponent(jobId)}/cv`, generate ? {action: 'generate'} : undefined);
    if (request !== cvRequest) return;
    renderCV(workflow); if (generate) await refreshState();
  } catch (error) { if (request === cvRequest) body.replaceChildren(el('div', {class: 'banner banner-error', role: 'alert'}, error.message), button('Retry', () => openCV(jobId, generate))); }
}
function cvMeasurement(measurement, label) {
  const metric = measurement || {}, incomplete = metric.status === 'incomplete' || Number(metric.unassessed_requirements) > 0;
  const checks = Array.isArray(metric.checks) ? el('ul', {class: 'detail-list'}, metric.checks.map(item => el('li', {}, typeof item === 'string' ? item : `${item.passed ? 'Pass' : 'Needs attention'} · ${item.name || 'Check'} · ${item.passed || item.maximum ? item.points : 0} points${item.maximum ? ` of ${item.maximum}` : ''}`))) : metric.checks ? el('pre', {class: 'diagnostics-pre'}, stringify(metric.checks)) : null;
  const requirements = array(metric.requirements);
  return el('section', {class: 'cv-measure'}, el('h3', {}, label), el('p', {class: 'cv-measure-score'}, incomplete ? 'Assessment incomplete' : `${score(metric.score)} / 100`), el('p', {}, metric.label || 'No assessment recorded.'), el('p', {class: 'field-help'}, metric.method || 'Transparent heuristic assessment; no prediction of interview success.'), array(metric.gaps).length ? list(metric.gaps, '') : null, checks ? details('How this measurement was calculated', checks) : null, requirements.length ? details('Requirement-by-requirement evidence', el('ul', {class: 'detail-list'}, requirements.map(item => el('li', {}, el('strong', {}, `${item.requirement} · ${human(item.status)}`), el('p', {class: 'field-help'}, array(item.evidence_ids).length ? `Evidence: ${item.evidence_ids.join(', ')}` : 'No supporting evidence assigned.'))))) : null);
}
function renderCV(workflow, selectedMaterialId, selectedReviewId) {
  const body = $('#cv-dialog-body'), jobId = workflow.job_id || $('#cv-dialog').dataset.jobId;
  const versions = array(workflow.versions), selected = versions.find(item => String(item.id) === String(selectedMaterialId || workflow.latest_material_id)) || versions[versions.length - 1];
  if (!selected) { body.replaceChildren(el('p', {}, 'Create the first version using the advert, supported candidate evidence and the best matching base CV.'), button('Generate CV v1', () => openCV(jobId, true), 'button-primary')); return; }
  const reviews = array(workflow.reviews), related = reviews.filter(item => String(item.material_id) === String(selected.id)), review = related.find(item => item.id === selectedReviewId) || related[related.length - 1];
  const errorBox = el('div', {class: 'banner banner-error', role: 'alert', hidden: true});
  const update = async (payload, control) => {
    errorBox.hidden = true;
    await busy(control, async () => {
      try { const next = await api(`/api/jobs/${encodeURIComponent(jobId)}/cv`, payload); renderCV(next, payload.action === 'revise' ? next.latest_material_id : selected.id); await refreshState(); }
      catch (error) { showError(errorBox, error); errorBox.scrollIntoView({block: 'nearest'}); }
    });
  };
  const selector = el('select', {'aria-label': 'CV version'}, versions.map(item => el('option', {value: item.id}, `CV v${item.version} · ${date(item.created_at, true)}`)));
  selector.value = String(selected.id); selector.addEventListener('change', () => renderCV(workflow, selector.value));
  const exports = el('div', {class: 'materials-actions'}, button('Copy CV text', event => busy(event.currentTarget, async () => { await navigator.clipboard.writeText(selected.cv_text || ''); announce(`CV v${selected.version} copied.`); }), 'button-quiet button-small'));
  for (const format of array(selected.formats).filter(item => ['txt', 'docx', 'pdf'].includes(item))) exports.append(el('a', {class: 'button button-quiet button-small', href: `/api/materials/${encodeURIComponent(selected.id)}/${format}`, download: ''}, `Download ${format.toUpperCase()}`));
  const preview = detailSection(`CV v${selected.version} preview`, el('div', {class: 'material-preview cv-text', tabindex: 0, 'aria-label': `CV version ${selected.version} preview`}, selected.cv_text || 'The CV text is available in the export files.'), exports);
  const diff = details(`Changes from ${selected.parent_id ? 'the previous version' : 'the starting CV'}`, array(selected.diff).length ? el('div', {class: 'cv-diff', tabindex: 0, 'aria-label': 'CV version comparison'}, selected.diff.map(part => el('div', {class: `cv-diff-line diff-${['equal', 'insert', 'delete'].includes(part.type) ? part.type : 'equal'}`}, el('span', {class: 'diff-label'}, {insert: 'Added', delete: 'Removed', equal: 'Unchanged'}[part.type] || 'Unchanged'), el(part.type === 'insert' ? 'ins' : part.type === 'delete' ? 'del' : 'span', {}, part.text || '')))) : el('p', {class: 'muted'}, 'This is the first version. Future revisions show additions and removals here.'));
  const reviewSection = detailSection(`Review CV v${selected.version}`);
  reviewSection.append(el('p', {class: 'muted'}, workflow.reviewer_notice || 'Download a review packet for your chosen reviewer, then import its response. The local app validates every proposed change against your evidence.'));
  const currentStatus = review?.status || 'not_started';
  reviewSection.append(el('ol', {class: 'cv-review-steps', 'aria-label': 'Review workflow'}, [['Red team', !!review?.responses?.red], ['Blue team', !!review?.responses?.blue], ['Evidence validation', ['ready_to_revise', 'revised'].includes(currentStatus)], ['Your accepted findings', currentStatus === 'revised']].map(([label, complete]) => el('li', {class: complete ? 'complete' : ''}, `${complete ? '✓ ' : ''}${label}`))));
  if (review) {
    reviewSection.append(el('p', {class: 'review-status'}, `Review round ${review.round} · ${human(currentStatus)} · CV v${selected.version}`));
    const teams = el('div', {class: 'cv-review-teams'});
    for (const team of ['red', 'blue']) {
      const response = review.responses?.[team], ready = team === 'red' || !!review.responses?.red;
      const teamPanel = el('section', {class: 'cv-review-team'}, el('h4', {}, team === 'red' ? 'Red team · challenge the CV' : 'Blue team · independent challenge'), el('p', {class: 'field-help'}, team === 'red' ? 'Use a strong reasoning reviewer of your choice, such as ChatGPT Pro. Find unsupported claims, missed requirements and weak positioning.' : 'Use an independent reviewer, such as Claude. Challenge incorrect criticism, recover overlooked evidence and strengthen truthful positioning.'));
      if (!ready) teamPanel.append(el('p', {class: 'muted'}, 'Import the red-team review first. The blue-team packet includes that critique.'));
      else {
        teamPanel.append(el('a', {class: 'button button-quiet button-small', href: `/api/jobs/${encodeURIComponent(jobId)}/cv/packet?review_id=${encodeURIComponent(review.id)}&team=${team}`, download: ''}, `Download ${team}-team packet`));
        if (response) {
          teamPanel.append(badge('Review imported', ''), el('p', {class: 'field-help'}, `${array(response.findings).length} finding(s) recorded${response.reviewer ? ` · ${response.reviewer}` : ''}. The original review remains in version history.`));
          if (array(response.challenges).length) teamPanel.append(el('h4', {class: 'spaced-top'}, 'Challenges to the red team'), list(response.challenges.map(item => `${item.finding_id}: ${item.reason}${array(item.evidence_ids).length ? ` · Evidence ${item.evidence_ids.join(', ')}` : ''}`), ''), el('p', {class: 'field-help'}, 'Reviewer disagreement is recorded as an opinion. It does not override the evidence controller.'));
        }
        else if (currentStatus !== 'revised') {
          const responseInput = el('textarea', {rows: 6, placeholder: 'Paste the reviewer’s JSON response here…', 'aria-label': `${human(team)}-team JSON response`, spellcheck: 'false'});
          const file = el('input', {type: 'file', accept: '.json,application/json', 'aria-label': `Load ${team}-team response file`});
          const importError = el('div', {class: 'banner banner-error', role: 'alert', hidden: true});
          file.addEventListener('change', async () => {
            importError.hidden = true; const chosen = file.files[0]; if (!chosen) return;
            if (chosen.size > 900000) { showError(importError, new Error('Choose a JSON response smaller than 900 KB.')); return; }
            try { responseInput.value = await chosen.text(); } catch (error) { showError(importError, error); }
          });
          const form = el('form', {class: 'cv-review-import'}, el('label', {class: 'field'}, 'Reviewer response', responseInput), el('label', {class: 'field'}, 'Or load a JSON file', file), el('p', {class: 'field-help'}, 'Use the packet’s response template and preserve its review identity. Candidate facts are validated before any revision.'), importError, el('button', {type: 'submit', class: 'button button-primary button-small'}, `Import ${team}-team review`));
          form.addEventListener('submit', event => {
            event.preventDefault(); importError.hidden = true;
            try { const parsed = JSON.parse(responseInput.value); if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) throw new Error('The review response must be a JSON object.'); update({action: 'submit_review', review_id: review.id, team, response: parsed}, $('button[type=submit]', form)); }
            catch (error) { showError(importError, new Error(`Review not imported: ${error.message}`)); }
          });
          teamPanel.append(form);
        }
      }
      teams.append(teamPanel);
    }
    reviewSection.append(teams);
    if (array(review.findings).length) {
      const accepted = new Set();
      reviewSection.append(el('h4', {class: 'spaced-top'}, 'Evidence validation & your accepted findings'), el('p', {class: 'field-help'}, 'Only validated operations can be accepted for automatic revision. A request to verify a fact, or a reviewer disagreement, does not establish evidence.'));
      const findings = el('div', {class: 'cv-findings'}, review.findings.map(finding => {
        const eligible = finding.validation?.status === 'eligible', actionable = eligible && currentStatus === 'ready_to_revise';
        const checkbox = el('input', {type: 'checkbox', 'aria-label': `Accept finding ${finding.id}`, onchange: event => { if (event.target.checked) accepted.add(finding.id); else accepted.delete(finding.id); }}); checkbox.disabled = !actionable;
        return el('article', {class: 'cv-finding'}, el('label', {class: 'finding-heading'}, checkbox, el('strong', {}, `${human(finding.team)} · ${finding.id}`), badge(human(finding.severity || 'review'), finding.severity === 'critical' || finding.severity === 'high' ? 'red' : 'neutral')), el('p', {}, finding.recommended_action || 'Review the finding.'), el('dl', {class: 'finding-evidence'}, [['CV passage', finding.cv_passage || 'No passage cited'], ['Advert requirement', finding.advert_requirement || 'No requirement cited'], ['Candidate evidence', array(finding.evidence_ids).join(', ') || 'No evidence IDs cited']].map(([label, value]) => el('div', {}, el('dt', {}, label), el('dd', {}, value)))), el('p', {class: eligible ? 'validation-pass' : 'validation-hold'}, `${eligible ? 'Validated operation' : 'Needs verification'}: ${finding.validation?.reason || 'No validation recorded.'}`));
      }));
      reviewSection.append(findings);
      if (currentStatus === 'ready_to_revise') reviewSection.append(button('Create next CV version from accepted findings', event => {
        if (!accepted.size) { announce('Select at least one validated finding to apply. Unverified suggestions cannot be applied automatically.', true); return; }
        update({action: 'revise', review_id: review.id, accepted_ids: [...accepted]}, event.currentTarget);
      }, 'button-primary', {class: 'button button-primary spaced-top'}));
    } else if (currentStatus === 'ready_to_revise') reviewSection.append(el('p', {class: 'muted'}, 'Both reviewers returned no findings. Review the evidence gaps and decide whether another review is useful.'));
    if (array(review.decisions).length) reviewSection.append(details('Recorded revision decisions', list(review.decisions.map(item => `${item.finding_id} · ${human(item.status)} · ${item.reason || ''}`), '')));
  }
  const canReviewAgain = !review || ['revised', 'ready_to_revise'].includes(currentStatus);
  if (canReviewAgain) reviewSection.append(button(review ? 'Review again — start another round' : 'Review again — review this version', event => update({action: 'start_review', material_id: selected.id, explicit: true}, event.currentTarget), 'button-quiet', {class: 'button button-quiet spaced-top'}));
  reviewSection.append(el('p', {class: 'field-help spaced-top'}, `Automatic revision limit: ${workflow.max_automatic_rounds ?? 2} rounds. Review again explicitly requests another round. Genuine evidence gaps remain gaps regardless of wording.`));
  const history = details('All CV versions and review rounds', el('ol', {class: 'history-list'}, versions.map(version => el('li', {}, button(`CV v${version.version} · ${date(version.created_at, true)}`, () => renderCV(workflow, version.id), 'text-link'), el('p', {class: 'muted'}, reviews.filter(item => String(item.material_id) === String(version.id)).length ? reviews.filter(item => String(item.material_id) === String(version.id)).map(item => button(`Review ${item.round}: ${human(item.status)}`, () => renderCV(workflow, version.id, item.id), 'text-link')) : 'No review recorded.')))));
  body.replaceChildren(el('div', {class: 'cv-toolbar'}, el('label', {class: 'field'}, 'Version', selector), el('p', {class: 'muted'}, 'Master CVs remain unchanged. CV generation and review do not mark a job Applied.')), el('div', {class: 'cv-measurements'}, cvMeasurement(selected.analysis?.evidence_match, 'Evidence / job match'), cvMeasurement(selected.analysis?.ats_compatibility, 'Local document compatibility')), el('p', {class: 'field-help'}, 'These are different, transparent heuristic measurements. Neither predicts employer acceptance. Stronger wording cannot create missing experience.'), errorBox, preview, diff, reviewSection, history);
}

function applicationHistory(job) {
  const entries = array(job.history).filter(item => ['application', 'mark_applied', 'interview', 'offer', 'close', 'notes', 'legacy_import'].includes(item.action || item.kind || item.event));
  return details('Application history & provenance', el('p', {class: 'muted'}, `Current record source: ${human(job.application?.source || 'not_recorded')} · Version ${job.application?.version ?? 0} · Last updated ${date(job.application?.updated_at, true)}`), entries.length ? el('ol', {class: 'history-list'}, entries.map(item => {
    const data = item.data || item, before = data.before || {}, after = data.after || data.application || {};
    return el('li', {}, el('strong', {}, `${date(item.created_at || item.at, true)} · ${human(item.action || item.kind || item.event)}`), el('p', {class: 'muted'}, `Recorded by ${human(data.actor || item.actor || after.source || 'not recorded')}`), before.stage || after.stage ? el('p', {}, `${applicationStages[before.stage] || 'Previously unrecorded'} → ${applicationStages[after.stage] || 'Record updated'}`) : null, after.next_action ? el('p', {}, `Next action: ${after.next_action}`) : null);
  })) : el('p', {class: 'muted'}, 'No application-specific changes have been recorded yet.'));
}
function notesSection(job) {
  const app = job.application || {};
  return detailSection('Your application record', el('dl', {class: 'detail-facts'}, [['Stage', applicationStages[applicationStage(job)]], ['Application date', date(app.application_date)], ['Last contact', date(app.last_contact)], ['Next action', app.next_action || 'Not recorded'], ['Follow-up date', date(app.follow_up_date)], ['Recruiter / contact', app.recruiter_contact || 'Not recorded'], ['CV / material reference', app.material_id ? `Material ${app.material_id}` : 'Not recorded'], ['Cover letter reference', app.cover_letter_reference || 'Not recorded']].map(([label, value]) => el('div', {}, el('dt', {}, label), el('dd', {}, value)))), el('p', {class: 'application-notes'}, app.notes || job.notes || 'No private notes recorded.'), button('Edit application record', () => openApplicationTracker(job.id), 'button-primary button-small'), applicationHistory(job));
}
async function openApplicationTracker(id) {
  const dialog = $('#application-dialog'), body = $('#application-dialog-body');
  dialog.dataset.jobId = String(id); applicationDirty = false;
  body.replaceChildren(el('p', {class: 'loading-state'}, 'Loading the current application record…')); openDialog(dialog);
  try { const result = await api(`/api/jobs/${encodeURIComponent(id)}`); if (dialog.dataset.jobId === String(id)) renderApplicationTracker(result.job || result); }
  catch (error) { body.replaceChildren(el('div', {class: 'banner banner-error', role: 'alert'}, error.message), button('Retry', () => openApplicationTracker(id))); }
}
function renderApplicationTracker(job) {
  const app = job.application || {}, form = el('form', {class: 'application-form'}), controls = {};
  $('#application-dialog-title').textContent = `${job.title || 'Application'} · ${job.company || 'Employer'}`;
  const errorBox = el('div', {class: 'banner banner-error', role: 'alert', hidden: true});
  const conflictBox = el('div', {class: 'application-conflict', hidden: true});
  function input(name, label, type = 'text', options = {}) {
    const value = app[name] ?? (name === 'notes' ? job.notes : name === 'follow_up_date' ? job.follow_up_date : '') ?? '';
    const control = name === 'stage' ? el('select', {name}, Object.entries(applicationStages).map(([key, text]) => el('option', {value: key}, text))) : name === 'notes' ? el('textarea', {name, rows: 5}) : el('input', {name, type, ...options});
    control.value = type === 'date' ? String(value).slice(0, 10) : value;
    if (name === 'stage') control.value = applicationStage(job);
    controls[name] = control; return el('label', {class: 'field'}, label, control);
  }
  const stage = input('stage', 'Application stage');
  const confirmation = el('input', {type: 'checkbox', name: 'confirmed_applied'});
  const confirmationLabel = el('label', {class: 'checkbox-field applied-confirmation'}, confirmation, el('span', {}, el('strong', {}, 'I have submitted this application.'), el('small', {}, 'This records your confirmation; it does not submit anything.')));
  function syncConfirmation() { const required = controls.stage.value === 'applied' && applicationStage(job) !== 'applied'; confirmationLabel.hidden = !required; confirmation.required = required; if (!required) confirmation.checked = false; }
  controls.stage.addEventListener('change', syncConfirmation); syncConfirmation();
  const materialLabel = input('material_id', 'CV / material used', 'number', {min: 1, step: 1, placeholder: 'Material ID, if used'});
  if (array(job.materials).length) materialLabel.append(el('small', {class: 'field-help'}, job.materials.map(material => `Version ${material.version}: ID ${material.id}`).join(' · ')));
  form.append(el('p', {class: 'muted'}, 'Save the stage and practical next step. Bookmarks, draft preparation and opening the employer page are separate actions.'), el('div', {class: 'form-grid'}, stage, input('application_date', 'Application date', 'date'), input('last_contact', 'Last contact', 'date'), input('follow_up_date', 'Follow-up date', 'date')), confirmationLabel, input('next_action', 'Next action', 'text', {placeholder: 'For example: check the graduate eligibility dates with the recruiter'}), input('recruiter_contact', 'Recruiter / contact', 'text', {placeholder: 'Name, email or contact details you have verified'}), el('div', {class: 'form-grid'}, materialLabel, input('cover_letter_reference', 'Cover letter reference')), input('notes', 'Private application notes'), el('p', {class: 'field-help'}, 'Follow-up dates appear on Today when due. Recording a date does not schedule a reminder.'), errorBox, conflictBox);
  const submit = el('button', {type: 'submit', class: 'button button-primary'}, 'Save application record');
  form.append(el('div', {class: 'form-footer'}, el('span', {class: 'field-help'}, `Source: ${human(app.source || 'not_recorded')} · Record version ${app.version ?? 0}`), submit));
  form.addEventListener('input', () => { applicationDirty = true; });
  form.addEventListener('submit', async event => {
    event.preventDefault(); errorBox.hidden = true; conflictBox.hidden = true; submit.disabled = true; submit.textContent = 'Saving record…';
    const application = Object.fromEntries(Object.entries(controls).map(([key, control]) => [key, control.value.trim()]));
    application.material_id = application.material_id ? Number(application.material_id) : null;
    for (const key of ['application_date', 'last_contact', 'follow_up_date']) if (!application[key]) application[key] = null;
    try {
      await api(`/api/jobs/${encodeURIComponent(job.id)}/action`, {action: 'application', application, expected_version: app.version ?? 0, ...(confirmation.checked ? {confirmed_applied: true} : {})});
      applicationDirty = false; closeDialog($('#application-dialog')); announce('Application record saved. Your stage and next action are recorded.');
      await refreshState(); if ($('#job-dialog').open && String(state.selectedJob) === String(job.id)) await openJob(job.id);
    } catch (error) {
      showError(errorBox, error);
      if (error.status === 409) { conflictBox.hidden = false; conflictBox.replaceChildren(el('p', {}, 'Another update was saved after this form opened. Your entered values remain here. Copy any notes you want to keep, then load the latest record before making your changes again.'), button('Load latest record', () => openApplicationTracker(job.id), 'button-quiet button-small')); }
    } finally { submit.disabled = false; submit.textContent = 'Save application record'; }
  });
  $('#application-dialog-body').replaceChildren(form, applicationHistory(job));
}
async function act(id, action, control, extra = {}) {
  return busy(control, async () => {
    await api(`/api/jobs/${encodeURIComponent(id)}/action`, {action, ...extra});
    announce({save: 'Opportunity saved.', prepare: 'Application draft prepared. Review the evidence and unresolved answers.', skip: 'Opportunity skipped. Feedback is saved without changing your preferences.', mark_applied: 'Application recorded as submitted.', notes: 'Notes and follow-up date saved.', mark_reviewed: 'Application pack marked reviewed and ready. It has not been submitted.', feedback: 'Feedback saved. Application status is unchanged.', reverify: 'Source verification completed. Check the recorded result.'}[action] || `Opportunity updated: ${human(action)}.`);
    await refreshState();
    if (action === 'prepare' || ($('#job-dialog').open && String(state.selectedJob) === String(id))) await openJob(id, action === 'prepare' ? 'materials' : undefined);
  });
}
function askSkip(id) { state.skipJob = id; $('#feedback-form').reset(); openDialog($('#feedback-dialog')); }
function runProgress(run) {
  const counters = run.counters || run.stats || run.result?.counters || run;
  const found = counters.found ?? counters.roles_found ?? counters.jobs_found ?? counters.imported ?? 0;
  const refreshed = counters.duplicates ?? counters.duplicates_removed ?? 0;
  const checked = counters.checked ?? counters.eligibility_checked ?? counters.evaluated ?? 0;
  const shortlisted = counters.shortlisted ?? counters.selected ?? 0;
  const fresh = run.new_unique ?? counters.new_unique;
  const boards = run.boards_attempted ?? counters.boards_attempted;
  const requests = run.requests ?? counters.requests;
  return `${found} roles found${fresh === undefined ? '' : ` · ${fresh} new unique`} · ${refreshed} existing records refreshed · ${checked} eligibility checked${boards === undefined ? '' : ` · ${boards} boards attempted`}${requests === undefined ? '' : ` · ${requests} source requests`}${run.message ? ` — ${run.message}` : ''}`;
}
async function startSearch(mode, control) {
  await busy(control, async () => {
    const scope = $('#search-scope').value, continuation = mode === 'normal' ? continuableSearch(scope) : null;
    await api(continuation ? `/api/runs/${encodeURIComponent(continuation.id)}/resume` : '/api/search', continuation ? {} : {mode, scope});
    announce(continuation ? 'Continuing with the remaining sources. Your collected jobs are preserved.' : 'Finding jobs. New opportunities will appear here as they arrive.');
    if (state.page !== 'all') navigate('all');
    await refreshState();
  });
  renderDiscoveryStatus();
}
async function cancelRun(id, control) { await busy(control, async () => { await api(`/api/runs/${encodeURIComponent(id)}/cancel`, {}); announce('Cancellation requested. Partial results are preserved.'); await refreshState(); }); }
function renderRuns() {
  if (!state.data) return;
  const runs = array(state.data.runs);
  $('#run-history').replaceChildren(...(runs.length ? runs.map(run => {
    const active = ['pending', 'queued', 'running', 'cancelling'].includes(run.status);
    const row = el('article', {class: 'run-row'}, el('div', {class: 'run-head'}, el('h4', {}, `${human(run.mode || 'search')} ${run.scope || ''} search · ${date(run.started_at || run.created_at, true)}`), badge(human(run.status || 'unknown'), ['failed', 'error'].includes(run.status) ? 'red' : active ? '' : 'neutral')), el('p', {}, runProgress(run)));
    if (run.phase_counts) row.append(el('p', {class: 'muted'}, Object.entries(run.phase_counts).map(([phase, count]) => `${human(phase)}: ${count}`).join(' · ')));
    if (run.stop_reason) row.append(el('p', {class: 'muted'}, `Stopped: ${human(run.stop_reason)}`));
    if (run.error) row.append(el('div', {class: 'banner banner-error'}, typeof run.error === 'string' ? run.error : stringify(run.error)));
    const actions = el('div', {class: 'run-actions'});
    if (active) actions.append(button('Cancel search', event => cancelRun(run.id, event.currentTarget), 'button-quiet button-small'));
    else if (run.checkpoint && !run.resumed_by && !['completed', 'complete', 'succeeded'].includes(run.status)) actions.append(button('Resume from checkpoint', event => busy(event.currentTarget, async () => { await api(`/api/runs/${encodeURIComponent(run.id)}/resume`, {}); announce('Search resumed from its checkpoint.'); await refreshState(); }), 'button-quiet button-small'));
    row.append(actions, el('details', {class: 'run-diagnostics'}, el('summary', {}, 'Diagnostics & recorded usage'), el('pre', {class: 'diagnostics-pre'}, JSON.stringify({status: run.status, counters: run.counters || run.stats || {found: run.found, duplicates: run.duplicates, checked: run.checked, shortlisted: run.shortlisted}, usage: run.usage || {search_spend_usd: run.usage_usd, review_spend_usd: run.review_spend_usd}, warnings: run.warnings, checkpoint: run.checkpoint, events: run.events || run.logs || run.diagnostics}, null, 2)))); return row;
  }) : [el('p', {class: 'muted'}, 'No searches have run yet. Configure a public source or a supported provider, then start a search. Manual import is ready without either.')]));
}
let diagnosticsRequest = 0;
function coverageTable(rows, firstColumn, columns = [['indexed', 'Indexed'], ['universe_visible', 'Visible'], ['recommended', 'Recommended'], ['strong', 'Strong'], ['plausible', 'Plausible'], ['stretch', 'Stretch']]) {
  const entries = Object.entries(rows || {});
  if (!entries.length) return el('p', {class: 'muted'}, 'No records available for this breakdown.');
  return el('div', {class: 'coverage-table-wrap', tabindex: 0, 'aria-label': `${firstColumn} coverage table`}, el('table', {class: 'coverage-table'}, el('thead', {}, el('tr', {}, el('th', {scope: 'col'}, firstColumn), columns.map(([, label]) => el('th', {scope: 'col'}, label)))), el('tbody', {}, entries.map(([key, row]) => el('tr', {}, el('th', {scope: 'row'}, firstColumn === 'Location' ? countryNames[key] || human(key) : key), columns.map(([field]) => el('td', {}, row?.[field] === null || row?.[field] === undefined ? 'Not recorded' : typeof row[field] === 'number' ? row[field].toLocaleString('en-GB') : String(row[field]))))))));
}
async function renderDiagnostics() {
  const target = $('#discovery-diagnostics'), request = ++diagnosticsRequest;
  if (!target.children.length) target.replaceChildren(el('p', {class: 'muted'}, 'Loading indexed jobs and source coverage…'));
  try {
    const data = await api('/api/diagnostics'); if (request !== diagnosticsRequest) return;
    const totals = data.totals || {}, discovery = data.discovery || {};
    const summary = el('div', {class: 'overview-grid'}, [[totals.stored, 'Stored records'], [totals.indexed, 'Indexed vacancies'], [totals.universe_visible, 'Visible in All Jobs'], [totals.recommended, 'Recommended']].map(([value, label]) => el('div', {class: 'overview-stat'}, el('span', {class: 'stat-label'}, label), el('strong', {class: 'stat-number'}, number(value) === null ? '—' : Number(value).toLocaleString('en-GB')))));
    const breakdowns = [['Location', data.by_location], ['Source', data.by_source], ['Source query', data.by_query], ['Role family', data.by_role_family], ['Recommendation class', data.by_recommendation_class]].map(([label, rows]) => details(`${label} breakdown`, coverageTable(rows, label)));
    breakdowns[0].open = true;
    const providers = details('Source coverage, pagination & provider limits', el('p', {}, `Web search: ${discovery.web_search_enabled ? 'enabled' : 'not enabled'}${discovery.web_provider ? ` · ${human(discovery.web_provider)}` : ''}. ${discovery.source_count ?? '—'} configured sources · ${discovery.registry_count ?? '—'} employer boards.`), el('dl', {class: 'detail-facts'}, Object.entries(discovery.providers || {}).map(([name, value]) => el('div', {}, el('dt', {}, human(name)), el('dd', {}, Array.isArray(value) ? value.join(', ') : typeof value === 'object' ? stringify(value) : String(value))))), details('Configured run budgets', el('pre', {class: 'diagnostics-pre'}, JSON.stringify(discovery.limits || {}, null, 2))));
    for (const [scope, queries] of Object.entries(discovery.planned_queries || {})) providers.append(details(`${human(scope)} discovery queries (${array(queries).length})`, list(array(queries).map(item => typeof item === 'string' ? item : item.query || stringify(item)), 'No planned queries recorded.')));
    const runs = details('Discovered vs stored: recorded search runs', el('p', {class: 'field-help'}, 'Rows seen are source observations and may repeat across runs. New unique jobs are new canonical records. Previously discarded rows cannot be reconstructed from the database alone.'));
    for (const run of array(data.run_audit)) {
      const card = details(`Run ${run.id} · ${human(run.scope)} · ${human(run.status)}`, coverageTable({[String(run.id)]: run}, 'Run', [['rows_seen', 'Rows seen'], ['jobs', 'Jobs emitted'], ['new_unique', 'New unique'], ['refreshed', 'Refreshed'], ['duplicates', 'Duplicates'], ['malformed', 'Malformed']]), el('p', {class: 'field-help'}, `Earlier filters: ${run.out_of_scope ?? 'not recorded'} out of scope · ${run.outside_professional_scope ?? 'not recorded'} outside professional scope · ${run.unknown_location ?? 'not recorded'} location unknown · ${run.unlisted ?? 'not recorded'} unlisted.`), run.counter_semantics ? el('p', {class: 'field-help'}, typeof run.counter_semantics === 'string' ? run.counter_semantics : stringify(run.counter_semantics)) : null, details('Pagination, query coverage & stopping limits', el('pre', {class: 'diagnostics-pre'}, JSON.stringify({queries: run.queries, source_counts: run.source_counts, query_counts: run.query_counts, location_counts: run.location_counts, pagination: run.pagination, limits: run.limits, limitations: run.limitations, pending_tasks: run.pending_tasks}, null, 2))));
      runs.append(card);
    }
    if (!array(data.run_audit).length) runs.append(el('p', {class: 'muted'}, 'No discovery runs have been recorded yet.'));
    target.replaceChildren(summary, el('p', {class: 'field-help'}, 'Indexed and recommended totals describe different things. Hidden and closed records remain stored. Location counts can overlap when an advert offers several work locations.'), list(array(data.notes), 'No additional coverage notes.'), ...breakdowns, providers, runs);
  } catch (error) { if (request === diagnosticsRequest) target.replaceChildren(el('div', {class: 'banner banner-error', role: 'alert'}, `Discovery diagnostics could not load: ${error.message}`), button('Retry diagnostics', renderDiagnostics)); }
}

function renderProviders() {
  if (!state.data) return;
  const providers = array(state.data.providers);
  $('#provider-status').replaceChildren(...(providers.length ? providers.map(provider => {
    const ready = provider.ready === true || provider.status === 'ready';
    return el('div', {class: 'provider-row'}, el('div', {}, el('h4', {}, provider.label || provider.name || human(provider.provider || provider.id || 'Provider')), el('p', {}, provider.message || provider.reason || provider.detail || (ready ? 'Configured route is available.' : 'This route is not ready.')), el('p', {}, `Billing: ${human(provider.billing_mode || provider.billing || 'not reported')}${provider.model ? ` · Model: ${provider.model}` : ''}`), provider.live_verified === false ? el('p', {}, 'Configuration readiness only; no live integration verification recorded.') : null), badge(ready ? 'Ready' : human(provider.status || 'not ready'), ready ? '' : 'neutral'));
  }) : [el('div', {class: 'banner banner-info'}, 'No legacy application-pack provider is configured. Create & review CV uses the model connections above.')]));
}

// Settings edit copies of the current state and preserve all unrelated keys.
function field(label, value, onChange, options = {}) {
  const control = options.multiline ? el('textarea', {rows: options.rows || 3, value: value ?? ''}) : options.choices ? el('select', {}, options.choices.map(choice => el('option', {value: Array.isArray(choice) ? choice[0] : choice}, Array.isArray(choice) ? choice[1] : human(choice)))) : el('input', {type: options.type || 'text', value: value ?? '', ...(options.type === 'number' ? {step: options.step ?? 1, ...(options.min !== undefined ? {min: options.min} : {}), ...(options.max !== undefined ? {max: options.max} : {})} : {})});
  if (options.choices) control.value = value ?? '';
  if (options.placeholder) control.placeholder = options.placeholder;
  control.addEventListener(options.choices ? 'change' : 'input', () => onChange(options.type === 'number' ? control.value === '' ? null : Number(control.value) : control.value));
  return el('label', {class: 'field'}, label, control, options.help ? el('small', {}, options.help) : null);
}
function check(label, value, onChange, help) {
  return el('label', {class: 'checkbox-field'}, el('input', {type: 'checkbox', checked: value, onchange: event => onChange(event.target.checked)}), el('span', {}, el('strong', {}, label), help ? el('small', {}, help) : null));
}
function fieldset(label, ...children) { return el('fieldset', {}, el('legend', {}, label), children); }
function details(label, ...children) { return el('details', {class: 'details-box'}, el('summary', {}, label), children); }
function listField(label, values, onChange, help) { return field(label, array(values).join('\n'), value => onChange(value.split(/\n/).map(item => item.trim()).filter(Boolean)), {multiline: true, help: help || 'One entry per line.'}); }
function dirtyProfile() { profileDirty = true; $('#profile-save-status').textContent = 'Unsaved changes'; }
function dirtySettings() { settingsDirty = true; $('#settings-save-status').textContent = 'Unsaved changes'; }
function setProfile(key, value) { profileDraft[key] = value; dirtyProfile(); }
function triStateEditor(label, values, change, nameHelp) {
  const container = fieldset(label), rows = el('div');
  const records = Object.entries(values || {}).map(([name, value]) => ({name, value}));
  function save() { const entries = records.filter(record => record.name.trim()).map(record => [record.name.trim(), record.value]); change(Object.fromEntries(entries)); }
  function render() {
    rows.replaceChildren(...records.map((record, index) => {
      const name = el('input', {value: record.name, placeholder: nameHelp, 'aria-label': `${label}: ${nameHelp}`, oninput: event => { record.name = event.target.value; save(); }});
      const select = el('select', {'aria-label': `${label} status`}, el('option', {value: 'unknown'}, 'Unknown / not confirmed'), el('option', {value: 'yes'}, 'Yes — confirmed'), el('option', {value: 'no'}, 'No — confirmed'));
      select.value = record.value === true ? 'yes' : record.value === false ? 'no' : 'unknown'; select.addEventListener('change', () => { record.value = select.value === 'yes' ? true : select.value === 'no' ? false : null; save(); });
      return el('div', {class: 'tri-row'}, name, select, button('×', () => { records.splice(index, 1); save(); render(); }, 'icon-button', {'aria-label': `Remove ${record.name || 'entry'}`}));
    }));
  }
  render(); container.append(el('p', {class: 'field-help'}, 'An empty list means unknown. Only record a definite yes or no when you have confirmed it.'), rows, button('＋ Add entry', () => { records.push({name: '', value: null}); render(); $('input:last-of-type', rows)?.focus(); }, 'button-quiet button-small')); return container;
}
function recordEditor(label, records, schema, onChange, makeNew) {
  const container = details(label), rows = el('div');
  function render() { rows.replaceChildren(...records.map((record, index) => el('div', {class: 'record-row'}, el('div', {class: 'record-heading'}, el('strong', {}, `${label.replace(/s$/, '')} ${index + 1}`), button('Remove', () => { records.splice(index, 1); onChange(records); render(); }, 'button-quiet button-small')), schema.map(([key, title, options]) => options?.list ? listField(title, record[key], value => { record[key] = value; onChange(records); }, options.help) : options?.boolean ? check(title, record[key], value => { record[key] = value; onChange(records); }) : field(title, record[key], value => { record[key] = value; onChange(records); }, options || {}))))); }
  render(); container.append(rows, button('＋ Add entry', () => { records.push(makeNew()); onChange(records); render(); }, 'button-quiet button-small')); return container;
}
function renderProfile() {
  if (profileDirty) return;
  profileDraft = structuredClone(state.data.profile || {});
  const profile = profileDraft;
  const basic = el('div', {class: 'form-grid'}, field('Name', profile.name, value => setProfile('name', value)), field('Home location', profile.location, value => setProfile('location', value)), field('Availability', profile.availability, value => setProfile('availability', value)), field('Driving eligibility', profile.drives === true ? 'yes' : profile.drives === false ? 'no' : 'unknown', value => setProfile('drives', value === 'yes' ? true : value === 'no' ? false : null), {choices: [['unknown', 'Not confirmed'], ['yes', 'I drive'], ['no', 'I do not drive']]}));
  const skills = el('div', {class: 'form-grid'}, listField('Skills', profile.skills, value => setProfile('skills', value), 'One per line. Proficiency and supporting detail belong in your evidence.'), listField('Domains & research', profile.domains, value => setProfile('domains', value)));
  const eligibilityFields = details('Work authorisation, languages & citizenship', el('div', {class: 'banner banner-info'}, 'Your location, name and preferred destinations never establish citizenship, work authorisation or language ability.'), triStateEditor('Work authorisation', profile.work_authorisation, value => setProfile('work_authorisation', value), 'Country code, e.g. GB or GR'), triStateEditor('Languages', profile.languages, value => setProfile('languages', value), 'Language, e.g. English'), listField('Confirmed citizenships', profile.citizenships, value => setProfile('citizenships', value), 'One country code per line. Leave blank if not recorded.'));
  const qualifications = recordEditor('Qualifications', array(profile.qualifications), [['name', 'Award'], ['institution', 'Institution'], ['classification', 'Classification'], ['evidence_id', 'Supporting evidence ID'], ['completed', 'Completed qualification', {boolean: true}]], records => setProfile('qualifications', records), () => ({name: '', institution: '', classification: '', evidence_id: '', completed: false}));
  const evidence = recordEditor('Evidence records', array(profile.evidence), [['id', 'Evidence ID'], ['text', 'Supported fact', {multiline: true, rows: 3}], ['source', 'Source or document reference'], ['status', 'Evidence status', {choices: [...new Set(['user_provided', 'verified', 'unresolved', ...array(profile.evidence).map(item => item.status).filter(Boolean)])]}]], records => setProfile('evidence', records), () => ({id: `bank:evidence-${Date.now()}`, text: '', source: '', status: 'unresolved'}));
  const contact = details('Contact details', ...[['email', 'Email'], ['phone', 'Phone'], ['linkedin_url', 'Professional profile URL']].map(([key, label]) => field(label, profile.contact?.[key], value => setProfile('contact', {...profileDraft.contact, [key]: value}))));
  const refs = ['direct_evidence_ids', 'Supporting evidence IDs', {list: true, help: 'One exact evidence ID per line, from the evidence records below.'}];
  const employment = recordEditor('Employment', array(profile.employment), [['title', 'Role'], ['employer', 'Employer'], ['start', 'Start date'], ['end', 'End date or Present'], refs], records => setProfile('employment', records), () => ({record_id: `employment-${Date.now()}`, title: '', employer: '', start: '', end: '', direct_evidence_ids: []}));
  const projects = ['projects', 'research'].map(key => recordEditor(human(key), array(profile[key]), [['name', 'Name'], ['period', 'Dates'], refs], records => setProfile(key, records), () => ({record_id: `${key}-${Date.now()}`, name: '', period: '', direct_evidence_ids: []})));
  const guidance = el('p', {class: 'banner banner-info'}, 'Add your experience in your own words. Only facts you approve can appear in an application; uploading a CV alone does not verify them. Your personal details stay in this local workspace.');
  $('#profile-fields').replaceChildren(guidance, basic, contact, skills, evidence, details('Experience & qualifications', employment, ...projects, qualifications), details('Eligibility & languages', eligibilityFields));
}
function renderPolicyFields() {
  const settings = settingsDraft;
  const update = (object, key, value) => { object[key] = value; dirtySettings(); };
  settings.lanes ||= {}; settings.locations ||= {}; settings.london ||= {}; settings.thresholds ||= {}; settings.queue ||= {}; settings.exceptional ||= {};
  const descriptions = {mediterranean: 'Relocation and country-compatible remote roles.', overseas_quant: 'Broad access to quantitative and research work. Scores order opportunities.', london: 'Professional technical, analytical and client-facing roles lead the search.', exceptional: 'Bounded stretches with specific evidence of unusual upside.', cashflow: 'Optional office, operations and immediate-income work.', overseas_quant_worldwide: 'Expand overseas quant beyond your Mediterranean countries.'};
  const professionalMode = (settings.strategy?.mode || 'professional_london_first') === 'professional_london_first';
  const primaryLanes = professionalMode ? ['london', 'mediterranean'] : Object.keys(laneNames);
  const lanes = fieldset(professionalMode ? 'Your active search locations' : 'Your search lanes', el('div', {class: 'toggle-grid'}, primaryLanes.map(key => check(laneNames[key], settings.lanes[key], value => update(settings.lanes, key, value), descriptions[key]))));
  const legacyLanes = fieldset('Earlier search strategies', el('div', {class: 'toggle-grid'}, Object.keys(laneNames).filter(key => !primaryLanes.includes(key)).map(key => check(laneNames[key], settings.lanes[key], value => update(settings.lanes, key, value), descriptions[key]))));
  const locations = details('Countries, cities & overseas salary preferences', el('p', {}, 'Cities guide location preference; excluded cities are explicit exclusions. Overseas salary preferences start unset and use the market’s original currency.'));
  Object.entries(settings.locations).forEach(([code, location]) => {
    const grid = el('div', {class: 'form-grid'}, listField('Preferred cities', location.cities, value => update(location, 'cities', value)), listField('Excluded cities', location.excluded_cities, value => update(location, 'excluded_cities', value)), field('Location priority / 100', location.priority, value => update(location, 'priority', value), {type: 'number', min: 0, max: 100}), field('Minimum annual base preference', location.salary_min, value => update(location, 'salary_min', value), {type: 'number', min: 0, help: 'Leave blank for no market salary minimum.'}), field('Original currency', location.currency, value => update(location, 'currency', value), {help: 'ISO currency code, e.g. EUR or ILS.'}));
    locations.append(el('div', {class: 'country-card'}, check(countryNames[code] || code, location.enabled, value => update(location, 'enabled', value)), grid));
  });
  const london = details('London salary & attendance preferences', el('div', {class: 'form-grid three'}, [['salary_remote_one_day', 'Remote / at most 1 office day', 'Annual gross base minimum, GBP.'], ['salary_two_days', '2 office days / week', 'Annual gross base minimum, GBP.'], ['salary_three_plus_days', '3+ office days / week', 'Annual gross base minimum, GBP.']].map(([key, label, help]) => field(label, settings.london[key], value => update(settings.london, key, value), {type: 'number', min: 0, step: 1000, help}))), el('p', {class: 'field-help'}, 'These are your recorded preferences. Unknown salary or attendance remains a question to clarify; it is not evidence of poor candidacy.'));
  const thresholds = details('Legacy scoring preferences', el('p', {}, 'These earlier lane thresholds remain editable for compatibility. The professional strategy uses the candidacy weights and shortlist controls above.'));
  Object.entries(settings.thresholds).forEach(([lane, limits]) => thresholds.append(fieldset(laneNames[lane] || human(lane), el('div', {class: 'form-grid'}, ['fit', 'priority'].map(key => field(`Minimum ${key} / 100`, limits[key], value => update(limits, key, value), {type: 'number', min: 0, max: 100}))))));
  thresholds.append(fieldset('London roles without disclosed salary', el('div', {class: 'form-grid'}, ['unknown_salary_min_fit', 'unknown_salary_min_priority'].map(key => field(key.endsWith('fit') ? 'Minimum fit / 100' : 'Minimum priority / 100', settings.london[key], value => update(settings.london, key, value), {type: 'number', min: 0, max: 100})))), fieldset('Optional Top picks caps', el('div', {class: 'form-grid three'}, Object.entries(settings.queue).map(([key, value]) => field({recommended: 'Recommended opportunities', stretch: 'Stretch opportunities', london: 'London across both groups', per_company: 'Roles per employer', unknown_salary_london: 'London with unknown salary'}[key] || human(key), value, next => update(settings.queue, key, next), {type: 'number', min: 0, max: 100})))));
  const exceptional = details('Exceptional upside', field('Verified annual base trigger, GBP', settings.exceptional.base_gbp, value => update(settings.exceptional, 'base_gbp', value), {type: 'number', min: 0, step: 1000, help: 'A high range maximum, OTE or speculative equity alone does not establish exceptional pay.'}));
  if (Object.keys(settings.exceptional.markets || {}).length) exceptional.append(...Object.entries(settings.exceptional.markets).map(([market, value]) => field(`${market} market trigger`, value, next => update(settings.exceptional.markets, market, next), {type: 'number', min: 0})));
  const weights = details('Legacy fit, value & priority weights', el('p', {}, 'Fit, value and priority are separate. Each group must total 1.00. Missing soft facts receive neutral values; evidence confidence is assessed separately.'));
  Object.entries(settings.weights || {}).forEach(([group, values]) => weights.append(fieldset(human(group), el('div', {class: 'form-grid three'}, Object.entries(values).map(([key, value]) => field(human(key), value, next => update(values, key, next), {type: 'number', min: 0, max: 1, step: .01}))))));
  settings.strategy ||= {mode: 'professional_london_first', shortlist_size: 10, stretch_size: 2, per_company: 3, weights: {core_skills: .45, responsibilities: .25, seniority: .15, domain: .10, location: .05}};
  const strategy = settings.strategy;
  const professional = fieldset('Professional London first', el('p', {class: 'field-help'}, 'London professional roles lead. Suitable Mediterranean matches remain visible, with genuine stretches kept separate. Scores describe evidence match, never a probability of acceptance.'), el('div', {class: 'form-grid three'}, [['shortlist_size', 'London shortlist size', 10], ['stretch_size', 'Additional stretches', 2], ['per_company', 'Shortlist roles per company', 3]].map(([key, label, fallback]) => field(label, strategy[key] ?? fallback, value => update(strategy, key, value), {type: 'number', min: key === 'stretch_size' ? 0 : 1, max: 50}))));
  settings.cv_review ||= {max_rounds: 2};
  const cvReviewSettings = fieldset('CV review rounds', field('Automatic revision round limit', settings.cv_review.max_rounds ?? 2, value => update(settings.cv_review, 'max_rounds', value), {type: 'number', min: 1, max: 5, help: 'Initially 2. Review again is an explicit request for another round. Review packets work with your chosen reviewers; no paid API is enabled here.'}));
  const professionalWeights = details('Professional candidacy weights', el('p', {}, 'Each component contributes to the professional match score. Weights should total 1.00. Requirement and qualification gaps remain visible regardless of the score.'), el('div', {class: 'form-grid three'}, Object.entries(strategy.weights || {}).map(([key, value]) => field(human(key), value, next => update(strategy.weights, key, next), {type: 'number', min: 0, max: 1, step: .01}))));
  const legacySettings = details('Legacy strategy settings', el('p', {class: 'field-help'}, 'Earlier strategy controls are preserved for compatibility. The professional dashboard uses the London and Mediterranean preferences and candidacy assessment above.'), legacyLanes, thresholds, exceptional, weights);
  $('#policy-fields').replaceChildren(professional, professionalWeights, cvReviewSettings, lanes, locations, london, ...(professionalMode ? [legacySettings] : [thresholds, exceptional, weights]));
}
function renderProviderFields() {
  const settings = settingsDraft, update = (object, key, value) => { object[key] = value; dirtySettings(); };
  settings.providers ||= {}; settings.search ||= {}; settings.search.sources ||= []; settings.search.web ||= {provider: 'none', enabled: false, budget_usd: 0, cost_per_query_usd: null, queries: [], country: 'gb'};
  const provider = settings.providers;
  const currentChoices = [...new Set(['none', 'openai', 'anthropic', provider.active].filter(Boolean))];
  const billingChoices = [...new Set(['disabled', 'paid_api', provider.billing_mode].filter(Boolean))];
  const setup = el('div', {class: 'form-grid'}, field('Active AI provider', provider.active || 'none', value => update(provider, 'active', value), {choices: currentChoices}), field('Model identifier', provider.model, value => update(provider, 'model', value), {help: 'Use a currently supported model for the configured route.'}), field('Billing route', provider.billing_mode || 'disabled', value => update(provider, 'billing_mode', value), {choices: billingChoices}), field('Authorised maximum spend per run, USD', provider.budget_usd, value => update(provider, 'budget_usd', value), {type: 'number', min: 0, step: .1, help: 'Zero disables paid execution. Chat subscriptions do not establish API credit.'}));
  const pricing = details('Provider pricing & token limits', el('p', {}, 'Enter the current prices for your selected model. No rate is assumed. Provider keys are configured on the local server, never entered here.'), el('div', {class: 'form-grid'}, [['input_cost_per_million', 'Input cost per million tokens, USD', null], ['output_cost_per_million', 'Output cost per million tokens, USD', null], ['max_input_tokens', 'Maximum input tokens per review', 60000], ['max_output_tokens', 'Maximum output tokens per review', 1800], ['timeout_seconds', 'Provider timeout, seconds', 30]].map(([key, label, fallback]) => field(label, provider[key] ?? fallback, value => update(provider, key, value), {type: 'number', min: 0, step: key.includes('cost') ? .01 : 1}))));
  const web = settings.search.web;
  const webSearch = fieldset('Web-enabled discovery', check('Enable configured web search', web.enabled, value => update(web, 'enabled', value), 'Live search needs a supported provider, server-side credentials and an explicit budget.'), el('div', {class: 'form-grid'}, field('Search provider', web.provider || 'none', value => update(web, 'provider', value), {choices: ['none', 'brave', 'adzuna']}), field('Authorised search spend per run, USD', web.budget_usd, value => update(web, 'budget_usd', value), {type: 'number', min: 0, step: .1}), field('Verified cost per search query, USD', web.cost_per_query_usd, value => update(web, 'cost_per_query_usd', value), {type: 'number', min: 0, step: .001, help: 'Leave unknown until you confirm your provider’s billing. Use 0 only for an authorised free tier.'}), field('Adzuna search country', web.country || 'gb', value => update(web, 'country', value), {help: 'Provider market code, e.g. gb. This is not your work authorisation.'})), listField('Additional search queries', web.queries, value => update(web, 'queries', value), 'One complete query per line. Normal and deep searches remain bounded by their execution limits.'));
  const sourceHelp = el('p', {class: 'field-help'}, 'Configure supported public ATS boards or careers URLs. Credentials stay in the server environment, outside this form.');
  const sourceRecords = settings.search.sources.map(source => typeof source === 'string' ? {type: 'careers', url: source, company: '', board: '', enabled: true} : {...source, enabled: source.enabled !== false});
  const sources = recordEditor('Public discovery sources', sourceRecords, [['type', 'Source type', {choices: [...new Set(['greenhouse', 'lever', 'careers', ...sourceRecords.map(item => item.type).filter(Boolean)])]}], ['company', 'Company name'], ['board', 'Board identifier'], ['url', 'Public careers URL', {type: 'url'}], ['enabled', 'Enabled', {boolean: true}]], records => update(settings.search, 'sources', records), () => ({type: 'greenhouse', company: '', board: '', url: '', enabled: true}));
  sources.open = true;
  const searchSettings = details('Search coverage & execution limits', el('p', {}, 'Bootstrap uses the coverage objectives below. Objectives and request limits never guarantee a number of opportunities.'), listField('Role families and search terms', settings.search.role_families, value => update(settings.search, 'role_families', value)));
  for (const mode of ['normal', 'deep', 'bootstrap']) {
    settings.search[mode] ||= {};
    searchSettings.append(fieldset(`${human(mode)} search limits`, el('div', {class: 'form-grid three'}, Object.entries(settings.search[mode]).map(([key, value]) => field({max_pages: 'Maximum pages', max_jobs: 'Maximum roles investigated', max_queries: 'Maximum search queries', max_turns: 'Maximum agent turns', concurrency: 'Concurrent requests', timeout_seconds: 'Run timeout, seconds', max_retries: 'Retries per request'}[key] || human(key), value, next => update(settings.search[mode], key, next), {type: 'number', min: key === 'max_retries' ? 0 : 1})))));
  }
  const coverage = details('Coverage objectives & phase budgets', el('p', {}, 'Objectives guide requests and employer coverage; they do not promise a number of vacancies. London and overseas runs have separate scopes.'), field('Default discovery scope', settings.search.scope || 'london', value => update(settings.search, 'scope', value), {choices: ['overseas', 'london']}));
  function coverageControls(object, prefix = '') {
    for (const [key, value] of Object.entries(object || {})) {
      if (value && typeof value === 'object' && !Array.isArray(value)) coverage.append(fieldset(human(prefix + key), ...coverageControlsNested(value)));
      else if (typeof value === 'number' || value === null) coverage.append(field(human(prefix + key), value, next => update(object, key, next), {type: 'number', min: 0}));
      else if (typeof value === 'boolean') coverage.append(check(human(prefix + key), value, next => update(object, key, next)));
    }
  }
  function coverageControlsNested(object) {
    return Object.entries(object).flatMap(([key, value]) => value && typeof value === 'object' && !Array.isArray(value) ? [fieldset(human(key), ...coverageControlsNested(value))] : typeof value === 'boolean' ? [check(human(key), value, next => update(object, key, next))] : typeof value === 'number' || value === null ? [field(human(key), value, next => update(object, key, next), {type: 'number', min: 0})] : []);
  }
  coverageControls(settings.search.coverage || {});
  for (const [scope, limits] of Object.entries(settings.search.scope_budgets || {})) coverage.append(fieldset(`${human(scope)} scope budget overrides`, ...coverageControlsNested(limits)));
  if (!Object.keys(settings.search.coverage || {}).length) coverage.append(el('p', {class: 'muted'}, 'No phase budgets were returned by the server. Configure discovery coverage before a larger run.'));
  const boards = array(state.data.boards);
  const registry = details(`Employer board registry (${boards.length})`, el('p', {}, 'Persisted employer boards are discovery sources, not vacancies. Registry entries retain their own provenance and verification.'), boards.length ? el('div', {class: 'registry-list'}, boards.map(board => el('article', {class: 'registry-row'}, el('strong', {}, board.name || board.company || 'Employer board'), sourceLink(board.url, 'Careers source ↗'), el('p', {class: 'muted'}, `Verified: ${date(board.verified_at)} · ${array(board.countries).map(country => countryNames[country] || country).join(', ') || 'Countries not recorded'}`), el('p', {class: 'muted'}, `Provenance: ${typeof board.provenance === 'string' ? board.provenance : stringify(board.provenance || 'not recorded')}`)))) : el('p', {class: 'muted'}, 'No employer boards are persisted yet. A configured discovery run can expand the registry.'));
  $('#provider-fields').replaceChildren(details('Advanced: legacy application-pack provider', setup, pricing), webSearch, sourceHelp, sources, searchSettings, coverage, registry, el('div', {class: 'banner banner-info'}, settings.schedules_enabled ? 'A schedules flag is present in your configuration; scheduled execution must be verified in the server configuration. A flag alone does not establish a running scheduler.' : 'Schedules are off. Searches run only when you start them, while this local app is running.'));
}
function renderSettings() {
  if (!state.data) return;
  renderProfile();
  if (!settingsDirty) { settingsDraft = structuredClone(state.data.settings || {}); renderPolicyFields(); renderProviderFields(); }
  renderProviders(); renderRuns(); renderDiagnostics(); if (!modelConnectionsDirty) renderModelConnections();
}
function openImport(url) {
  const form = $('#import-form');
  if (url) { form.reset(); importPreview = null; form.elements.namedItem('url').value = url; $('#import-preview-status').textContent = ''; $('#import-preview-warnings').replaceChildren(); $('#import-error').hidden = true; }
  openDialog($('#import-dialog'));
  if (url) fetchImportPreview();
}
async function fetchImportPreview() {
  const form = $('#import-form'), url = form.elements.namedItem('url').value.trim(), control = $('#fetch-import-preview');
  $('#import-error').hidden = true;
  if (!safeURL(url)) { showError($('#import-error'), 'Enter a valid public http or https vacancy URL, or complete the manual fields below.'); return; }
  control.disabled = true; control.textContent = 'Fetching…'; importPreview = null;
  $('#import-preview-status').textContent = 'Retrieving source details. No job has been added yet.'; $('#import-preview-warnings').replaceChildren();
  try {
    const result = await api('/api/import/preview', {url});
    if (form.elements.namedItem('url').value.trim() !== url) { $('#import-preview-status').textContent = 'The URL changed during retrieval. Preview the new URL before adding it.'; return; }
    importPreview = {...result, requested_url: url};
    form.elements.namedItem('bookmark_after_import').checked = result.duplicate ? Boolean(result.existing_bookmarked) : true;
    const job = result.job || {};
    for (const name of ['title', 'company', 'location', 'country', 'description', 'salary_min', 'salary_max', 'salary_currency', 'salary_period', 'salary_type', 'work_pattern', 'office_days', 'sponsorship']) {
      const field = form.elements.namedItem(name), value = job[name] ?? '';
      if (field.tagName === 'SELECT' && value && ![...field.options].some(option => option.value === String(value))) field.append(el('option', {value}, human(value)));
      field.value = value || (field.tagName === 'SELECT' ? 'unknown' : '');
      if (value === 0) field.value = '0';
    }
    const warningItems = [...array(result.warnings)];
    if (result.duplicate) warningItems.unshift('This URL matches an existing job. Its bookmark, application status and original evidence will be preserved.');
    if (!job.title || !job.company) warningItems.push('Complete the advertised job title and company manually. Leave unverified information unknown.');
    $('#import-preview-warnings').replaceChildren(...(warningItems.length ? [el('div', {class: 'banner banner-warning'}, list(warningItems, ''))] : []));
    $('#import-preview-status').textContent = result.duplicate ? 'Existing job found. Review the details before updating its source information.' : 'Preview ready. Review and correct the details before adding the job.';
    if (result.duplicate && result.existing_id) $('#import-preview-warnings').append(button('View existing job', () => openJob(result.existing_id), 'button-quiet button-small'));
  } catch (error) {
    $('#import-preview-status').textContent = 'The source could not be retrieved. Manual entry is available below.';
    showError($('#import-error'), error);
  } finally { control.disabled = false; control.textContent = 'Fetch & preview'; }
}
async function saveReviewedImport(event) {
  event.preventDefault(); const form = event.currentTarget, control = $('button[type=submit]', form), error = $('#import-error'); error.hidden = true;
  const values = new FormData(form), data = Object.fromEntries(values);
  for (const key of Object.keys(data)) data[key] = String(data[key]).trim();
  const bookmark = values.has('bookmark_after_import'); delete data.bookmark_after_import;
  if (!data.title || !data.company) { showError(error, 'Enter the advertised job title and company before adding this job.'); return; }
  if (data.url && !safeURL(data.url)) { showError(error, 'Use a valid http or https job URL.'); return; }
  for (const key of ['salary_min', 'salary_max', 'office_days']) data[key] = data[key] === '' ? null : Number(data[key]);
  if (data.salary_min !== null && data.salary_max !== null && data.salary_min > data.salary_max) { showError(error, 'Salary maximum must be at least the minimum. Correct the range or leave it unknown.'); return; }
  data.country = data.country.toUpperCase(); data.salary_currency = data.salary_currency.toUpperCase(); data.retrieve = false;
  if (importPreview?.preview_token && importPreview.requested_url === data.url) data.preview_token = importPreview.preview_token;
  control.disabled = true; control.textContent = 'Saving reviewed job…';
  try {
    const result = await api('/api/import', data), job = result.job || result;
    let bookmarkError = '';
    if (bookmark && !job.bookmarked) { try { await api(`/api/jobs/${encodeURIComponent(job.id)}/action`, {action: 'bookmark', bookmarked: true}); } catch (problem) { bookmarkError = problem.message; } }
    closeDialog($('#import-dialog')); form.reset(); importPreview = null; $('#import-preview-status').textContent = ''; $('#import-preview-warnings').replaceChildren();
    announce(bookmarkError ? `Job added, but saving the bookmark failed: ${bookmarkError}. Use Save on the job to retry.` : result.duplicate ? 'Existing job updated. Its application history is preserved.' : 'Reviewed job added. Application status remains Not started.', !!bookmarkError);
    await refreshState(); await openJob(job.id);
  } catch (problem) { showError(error, problem); }
  finally { control.disabled = false; control.textContent = 'Add reviewed job'; }
}
function bindEvents() {
  ['#import-button', '#pipeline-import-button'].forEach(selector => $(selector).addEventListener('click', () => openImport()));
  $('#bulk-preparation-tools').addEventListener('toggle', () => $('#inventory-list').classList.toggle('batch-selection', $('#bulk-preparation-tools').open));
  $('#search-scope').addEventListener('change', () => { inventoryState.region = $('#search-scope').value; resetInventoryFilters(); renderDiscoveryStatus(); });
  $('#back-to-jobs-button').addEventListener('click', backToJobs);
  $('#refresh-diagnostics-button').addEventListener('click', event => busy(event.currentTarget, renderDiagnostics));
  $$('[data-location]').forEach(control => control.addEventListener('click', () => { inventoryState.region = 'all'; inventoryState.location = control.dataset.location; inventoryState.page = 1; $('#inventory-country').value = ''; $('#inventory-work-pattern').value = ''; renderPipeline(); }));
  $('#add-company-button').addEventListener('click', () => openDialog($('#company-dialog')));
  $$('[data-close-dialog]').forEach(control => control.addEventListener('click', () => closeDialog(control.closest('dialog'))));
  $$('dialog').forEach(dialog => { dialog.addEventListener('click', event => { if (event.target === dialog) { const box = dialog.getBoundingClientRect(); if (event.clientX < box.left || event.clientX > box.right || event.clientY < box.top || event.clientY > box.bottom) closeDialog(dialog); } }); });
  window.addEventListener('hashchange', () => { const page = normalPage(location.hash.slice(1)); if (['recommended', 'all', 'needs_checking', 'saved', 'applications', 'settings', 'your-cv', 'cv-workflow'].includes(page)) changePage(page, true); });
  $('#fetch-import-preview').addEventListener('click', fetchImportPreview);
  $('#import-form').elements.namedItem('url').addEventListener('input', () => { importPreview = null; $('#import-preview-status').textContent = 'URL changed. Fetch a new preview or complete the manual details.'; });
  $('#application-dialog').addEventListener('close', () => { applicationDirty = false; });
  $$('[data-region]').forEach(control => control.addEventListener('click', () => { inventoryState.location = ''; openInventory(control.dataset.region, {view: inventoryState.view, reset: false}); }));
  $('#inventory-search').addEventListener('input', () => { clearTimeout(inventoryState.searchTimer); inventoryState.searchTimer = setTimeout(() => { inventoryState.page = 1; renderPipeline(); }, 240); });
  ['#inventory-status', '#inventory-country', '#inventory-family', '#inventory-fit', '#inventory-eligibility', '#inventory-work-authorisation', '#inventory-verification', '#inventory-salary', '#inventory-work-pattern', '#inventory-sponsorship', '#inventory-relocation', '#inventory-application-stage', '#inventory-stretches', '#inventory-excluded', '#inventory-seniority', '#inventory-source', '#inventory-saved', '#inventory-hidden', '#inventory-date-field', '#inventory-date-from', '#inventory-date-to'].forEach(selector => $(selector).addEventListener('change', () => { inventoryState.page = 1; renderPipeline(); }));
  $('#inventory-density').addEventListener('change', () => $('#inventory-list').classList.toggle('compact-density', $('#inventory-density').value === 'compact'));
  $('#clear-inventory-filters').addEventListener('click', () => resetInventoryFilters());
  $('#inspect-excluded-button').addEventListener('click', () => { $('#inventory-hidden').value = 'true'; $('#inventory-verification').value = ''; inventoryState.page = 1; renderPipeline(); });
  $('#select-inventory-page').addEventListener('change', event => {
    array(inventoryState.response?.jobs).filter(job => job.inventory?.accessible && !['applied', 'interview', 'offer', 'closed', 'dismissed'].includes(job.status)).forEach(job => { if (event.target.checked) inventoryState.selected.set(String(job.id), job); else inventoryState.selected.delete(String(job.id)); });
    renderPipeline();
  });
  $('#clear-selection-button').addEventListener('click', () => { inventoryState.selected.clear(); renderPipeline(); });
  $('#prepare-selected-button').addEventListener('click', event => previewPreparation({job_ids: [...inventoryState.selected.keys()].map(id => Number(id))}, event.currentTarget));
  $$('[data-prepare-next]').forEach(control => control.addEventListener('click', () => previewPreparation({filters: inventoryFilters(), count: Number(control.dataset.prepareNext)}, control)));
  $('#confirm-preparation-button').addEventListener('click', event => confirmPreparation(event.currentTarget));
  $('#search-button').addEventListener('click', event => startSearch('normal', event.currentTarget)); $('#deep-search-button').addEventListener('click', event => startSearch('deep', event.currentTarget)); $('#bootstrap-search-button').addEventListener('click', event => startSearch('bootstrap', event.currentTarget));
  $('#import-form').addEventListener('submit', saveReviewedImport);
  $('#company-form').addEventListener('submit', async event => {
    event.preventDefault(); const form = event.currentTarget, control = $('button[type=submit]', form), error = $('#company-error'); error.hidden = true;
    const data = Object.fromEntries(new FormData(form));
    if (data.url && !safeURL(data.url)) { showError(error, 'Use a valid http or https company URL.'); return; }
    control.disabled = true;
    try { await api('/api/companies', data); await refreshState(); closeDialog($('#company-dialog')); form.reset(); announce('Company saved as a speculative target.'); } catch (problem) { showError(error, problem); } finally { control.disabled = false; }
  });
  $('#feedback-form').addEventListener('submit', event => {
    event.preventDefault(); const control = $('button[type=submit]', event.currentTarget), feedback = new FormData(event.currentTarget).get('feedback');
    if (feedback === 'Already applied') { closeDialog($('#feedback-dialog')); openApplicationTracker(state.skipJob); return; }
    busy(control, async () => { await api(`/api/jobs/${encodeURIComponent(state.skipJob)}/action`, {action: 'skip', feedback}); closeDialog($('#feedback-dialog')); if (String(state.selectedJob) === String(state.skipJob) && $('#job-dialog').open) closeDialog($('#job-dialog')); await refreshState(); announce(feedback === 'Already applied' ? 'Application recorded as already submitted.' : 'Opportunity skipped. Your core preferences are unchanged.'); });
  });
  $('#profile-form').addEventListener('submit', event => {
    event.preventDefault(); busy($('button[type=submit]', event.currentTarget), async () => { await api('/api/profile', {profile: profileDraft}); profileDirty = false; await refreshState({renderForms: true}); $('#profile-save-status').textContent = 'Profile version saved'; announce('Profile saved. New drafts use the updated evidence.'); });
  });
  $('#settings-form').addEventListener('submit', event => {
    event.preventDefault(); busy($('button[type=submit]', event.currentTarget), async () => { await api('/api/settings', {settings: settingsDraft}); settingsDirty = false; await refreshState({renderForms: true}); $('#settings-save-status').textContent = 'Settings saved'; announce('Search preferences saved.'); });
  });
  document.addEventListener('keydown', event => {
    if (event.ctrlKey || event.metaKey || event.altKey || event.target.closest('input, textarea, select, [contenteditable=true]') || $$('dialog[open]').length) return;
    if (['1', '2', '3', '4', '5'].includes(event.key)) { event.preventDefault(); navigate({'1': 'all', '2': 'your-cv', '3': 'saved', '4': 'applications', '5': 'settings'}[event.key]); }
    if (['a', 'i'].includes(event.key.toLowerCase())) { event.preventDefault(); openImport(); }
    if (event.key === '/') { event.preventDefault(); navigate('pipeline'); setTimeout(() => $('#inventory-search').focus(), 0); }
  });
  window.addEventListener('beforeunload', event => { if (profileDirty || settingsDirty || modelConnectionsDirty || applicationDirty) { event.preventDefault(); event.returnValue = ''; } });
}
async function init() {
  bindEvents();
  const page = normalPage(location.hash.slice(1)); changePage(['recommended', 'all', 'needs_checking', 'saved', 'applications', 'settings', 'your-cv', 'cv-workflow'].includes(page) ? page : 'all');
  try { await refreshState({renderForms: true}); } catch (error) {
    $('#connection-label').textContent = 'Local app unavailable'; $('#connection-dot').className = 'connection-dot error';
    showError($('#global-error'), error);
    $('#inventory-list').replaceChildren(empty('The workspace could not connect.', 'Check that the local CareerOps app is running, then retry. Your saved opportunities remain in the local database.', button('Reconnect', event => busy(event.currentTarget, () => refreshState({renderForms: true})), 'button-primary')));
  }
}
init();
