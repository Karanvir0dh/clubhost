const $ = selector => document.querySelector(selector);
let token = '', workspace = null, selectedTemplate = null, selectedApp = null;
let locked = true, refreshPromise = null, editorVersion = null, generation = 0, historyOpen = false;
const escapeHtml = value => String(value).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const badge = status => `<span class="badge ${escapeHtml(status)}">${escapeHtml(status)}</span>`;
document.querySelectorAll('dialog').forEach(dialog => {
  const alert = document.createElement('p'); alert.className = 'dialog-notice';
  alert.setAttribute('role','alert'); alert.hidden = true; dialog.prepend(alert);
});
function notice(message) {
  for (const element of document.querySelectorAll('#notice,.dialog-notice')) {
    element.textContent = message; element.hidden = !message;
  }
}
async function api(path, body) {
  const result = await fetch(path, {headers:{Authorization:`Bearer ${token}`, ...(body ? {'Content-Type':'application/json'} : {})}, ...(body ? {method:'POST',body:JSON.stringify(body)} : {})});
  if (!result.ok) {
    const error = await result.json().catch(() => ({}));
    throw new Error(typeof error.detail === 'string' ? error.detail : Array.isArray(error.detail) ? error.detail.map(e => e.msg).join('; ') : 'Please try again.');
  }
  return result.headers.get('content-type')?.includes('application/json') ? result.json() : result.text();
}
async function refresh(force = false) {
  if (locked) return;
  if (refreshPromise) {
    await refreshPromise;
    if (!force || locked) return;
  }
  const currentGeneration = generation;
  refreshPromise = (async () => {
    const data = await api('/api/workspace');
    if (locked || generation !== currentGeneration) return;
    workspace = data; render();
  })();
  try { await refreshPromise; } finally { refreshPromise = null; }
}
const currentApp = () => workspace?.apps.find(app => app.id === selectedApp);
function render() {
  const {apps,mode,club,templates,quota} = workspace;
  $('#club-name').textContent = club.name;
  $('#app-count').textContent = apps.length; $('#list-count').textContent = apps.length;
  $('#quota-label').textContent = `of ${quota} available`;
  $('#ready-count').textContent = apps.filter(a => a.releases[0].status === 'ready').length;
  $('#failed-count').textContent = apps.filter(a => a.releases[0].status === 'failed').length;
  $('#mode-name').textContent = mode === 'simulator' ? 'LOCAL SIMULATOR' : 'KUBERNETES';
  $('#mode-description').textContent = mode === 'simulator' ? 'Cluster rollouts are simulated. Published pages and event registrations work through this portal.' : 'Rollouts run in your cluster. Event registration is managed by this portal.';
  $('#apps').innerHTML = apps.length ? apps.map(a => `<div class="app-row"><div class="app-icon">↗</div><div><h3>${escapeHtml(a.name)}</h3><small>${escapeHtml(templates.find(t => t.id === a.template).name)} · ${a.published ? 'Public page enabled' : 'Private'} · r${a.releases[0].revision}</small></div>${badge(a.releases[0].status)}<span class="subtle">${escapeHtml(a.releases[0].version)}</span><button class="secondary" data-app="${a.id}">Manage →</button></div>`).join('') : '<div class="empty"><strong>A little space for your next big idea.</strong>Choose a template below to deploy your first application.</div>';
  $('#template-list').innerHTML = templates.map(t => `<article class="template-card"><div class="template-icon">${t.icon}</div><h3>${escapeHtml(t.name)}</h3><p>${escapeHtml(t.description)}</p><div class="template-bottom"><span>${t.id === 'event-page' ? 'RSVP · WAITLIST · CHECK-IN' : 'EDITABLE CONTENT · PUBLIC PAGE'}</span><button class="secondary" data-template="${t.id}" ${apps.length >= quota ? 'disabled' : ''}>Use template ↗</button></div></article>`).join('');
  if ($('#detail-dialog').open && selectedApp) renderDetail();
}
function renderDetail() {
  const a = currentApp(); if (!a) return;
  const disabled = a.releases[0].status === 'deploying' ? 'disabled' : '';
  const active = a.releases.find(r => r.revision === a.active_revision);
  const dirty = !active || JSON.stringify(active.content) !== JSON.stringify(a.draft);
  $('#detail-content').innerHTML = `<h2>${escapeHtml(a.draft.title)}</h2><p>${escapeHtml(a.name)} · Last successful revision: ${a.active_revision ? 'r'+a.active_revision : 'none yet'}</p>
<div class="content-summary"><strong>${dirty ? 'Draft has unpublished changes' : 'Draft matches the live content'}</strong><p>Deploy a saved draft to update the page. Restoring a revision restores its content and keeps your current draft.</p></div>
<div class="actions"><button class="primary" data-edit="true">Edit content</button><button class="secondary" data-preview="draft">Preview draft</button><button class="secondary" data-release="${active?.version || 'v1'}" ${disabled}>Deploy saved draft</button><button class="secondary" data-release="${active?.version === 'v2' ? 'v1' : 'v2'}" ${disabled}>Deploy with ${active?.version === 'v2' ? 'green' : 'violet'} theme</button></div>
<div class="publication"><strong>Visitor access: ${a.published ? 'enabled' : 'private'}</strong><p>${a.published ? `<a href="${escapeHtml(a.public_path)}" target="_blank" rel="noopener">Open public page ↗</a>` : 'Enable the public page when the content is ready to share.'}</p><button class="secondary" data-visibility="${!a.published}" ${!a.active_revision ? 'disabled' : ''}>${a.published ? 'Disable public page' : 'Enable public page'}</button>${a.template === 'event-page' ? ' <button class="secondary" data-attendees="true">Manage attendees</button>' : ''}</div>
${workspace.mode === 'kubernetes' ? `<p>Open the cluster copy:</p><code>kubectl --context YOUR_CONTEXT -n club-${escapeHtml(workspace.club.id)} port-forward service/app-${a.id} 8081:80</code><p>Open http://localhost:8081. Public-page visibility controls the portal copy, not cluster network access.</p>` : ''}
<details class="release-details"><summary>Release history and troubleshooting</summary><div class="actions">${workspace.mode === 'simulator' ? `<button class="secondary" data-fail="true" ${disabled}>Simulate failed release</button>` : ''}<button class="secondary" data-preview="live" ${!active ? 'disabled' : ''}>Preview live content</button></div>${a.releases.map(r => `<div class="release"><div class="release-head"><strong>r${r.revision} · ${escapeHtml(r.version)} · ${escapeHtml(r.action)}</strong>${badge(r.status)}</div><p>${escapeHtml(r.content.title)}<br>${escapeHtml(r.message)}</p><small>${new Date(r.created*1000).toLocaleString()}</small>${r.status === 'ready' ? `<button class="secondary" data-rollback="${r.revision}" ${disabled}>Restore this revision</button>` : ''}</div>`).join('')}</details>`;
  $('.release-details').open = historyOpen;
  $('.release-details').addEventListener('toggle', event => { historyOpen = event.target.open; });
}
function openEditor() {
  const a = currentApp(); editorVersion = a.draft_version;
  const form = $('#editor-form'); form.reset();
  for (const [key,value] of Object.entries(a.draft)) {
    const field = form.elements.namedItem(key); if (!field) continue;
    if (field.type === 'checkbox') field.checked = value;
    else if (key === 'event_date' && value) {
      const date = new Date(value);
      field.value = new Date(date.getTime()-date.getTimezoneOffset()*60000).toISOString().slice(0,16);
    } else field.value = value;
  }
  $('#event-fields').hidden = a.template !== 'event-page';
  notice(''); $('#editor-dialog').showModal();
}
async function loadAttendees() {
  const rows = await api(`/api/apps/${selectedApp}/attendees`);
  $('#attendees-title').textContent = `${rows.filter(r => r.status === 'confirmed').length} confirmed · ${rows.filter(r => r.status === 'waitlisted').length} waiting`;
  $('#attendees-list').innerHTML = rows.length ? rows.map(r => `<article class="attendee"><div><strong>${escapeHtml(r.name)}</strong><p>${escapeHtml(r.email)}</p><small>${escapeHtml(r.status)}${r.checked_in ? ' · Checked in' : ''}</small></div><div class="actions">${r.status === 'confirmed' ? `<button class="secondary" data-attendance="${r.checked_in ? 'undo-check-in' : 'check-in'}" data-person="${r.id}">${r.checked_in ? 'Undo check-in' : 'Check in'}</button>` : ''}${r.status !== 'cancelled' ? `<button class="quiet" data-attendance="cancel" data-person="${r.id}">Cancel place</button>` : ''}</div></article>`).join('') : '<p class="empty">No registrations yet. Publish your event and share its public page.</p>';
}
$('#login-form').addEventListener('submit', async event => {
  event.preventDefault(); event.submitter.disabled = true;
  token = $('#token').value.trim(); locked = false; generation++;
  try { await refresh(true); $('#login').hidden = true; $('#dashboard').hidden = false; $('#logout').hidden = false; $('#token').value = ''; notice(''); }
  catch (error) { locked = true; token = ''; notice(error.message); }
  finally { event.submitter.disabled = false; }
});
$('#logout').addEventListener('click', () => {
  locked = true; generation++; token = ''; workspace = null; selectedApp = null;
  document.querySelectorAll('dialog').forEach(d => d.close());
  $('#editor-form').reset(); $('#attendees-list').textContent = ''; $('#preview-frame').srcdoc = '';
  $('#detail-content').textContent = ''; $('#apps').textContent = ''; $('#template-list').textContent = '';
  $('#club-name').textContent = 'Student clubs'; $('#login').hidden = false; $('#dashboard').hidden = true; $('#logout').hidden = true; notice('');
});
document.addEventListener('click', async event => {
  const button = event.target.closest('button');
  if (!button || button.disabled || (button.form && button.type === 'submit')) return;
  if (button.classList.contains('close-dialog')) { button.closest('dialog').close(); notice(''); return; }
  if (button.dataset.template) {
    selectedTemplate = button.dataset.template; $('#create-title').textContent = workspace.templates.find(t => t.id === selectedTemplate).name;
    $('#create-form').reset(); notice(''); $('#create-dialog').showModal(); return;
  }
  if (button.dataset.app) { selectedApp = button.dataset.app; historyOpen = false; renderDetail(); notice(''); $('#detail-dialog').showModal(); return; }
  if (!selectedApp) return;
  button.disabled = true;
  try {
    notice('');
    if (button.dataset.edit) { openEditor(); return; }
    if (button.dataset.release || button.dataset.fail) await api(`/api/apps/${selectedApp}/releases`, {version:button.dataset.release || 'v2',simulate_failure:!!button.dataset.fail});
    else if (button.dataset.rollback) await api(`/api/apps/${selectedApp}/rollback`, {revision:Number(button.dataset.rollback)});
    else if (button.dataset.visibility) await api(`/api/apps/${selectedApp}/visibility`, {published:button.dataset.visibility === 'true'});
    else if (button.dataset.preview) {
      $('#preview-frame').srcdoc = await api(`/api/apps/${selectedApp}/preview?draft=${button.dataset.preview === 'draft'}`);
      $('#preview-dialog').showModal(); return;
    } else if (button.dataset.attendees || button.dataset.reloadAttendees) {
      await loadAttendees(); if (!$('#attendees-dialog').open) $('#attendees-dialog').showModal(); return;
    } else if (button.dataset.attendance) {
      if (button.dataset.attendance === 'cancel' && !confirm('Cancel this registration? The next waitlisted person may receive this place.')) return;
      await api(`/api/apps/${selectedApp}/attendees/${button.dataset.person}`, {action:button.dataset.attendance}); await loadAttendees(); return;
    } else if (button.dataset.export) {
      const csv = await api(`/api/apps/${selectedApp}/attendees.csv`);
      const url = URL.createObjectURL(new Blob([csv], {type:'text/csv;charset=utf-8'}));
      const link = document.createElement('a'); link.href = url; link.download = `${currentApp().name}-attendees.csv`; link.click();
      setTimeout(() => URL.revokeObjectURL(url),1000); return;
    }
    await refresh(true);
  } catch (error) { notice(error.message); }
  finally { button.disabled = false; }
});
$('#create-form').addEventListener('submit', async event => {
  event.preventDefault(); const button = event.submitter; button.disabled = true;
  try {
    const app = await api('/api/apps', {name:$('#app-name').value,template:selectedTemplate,version:'v1'});
    $('#create-dialog').close(); selectedApp = app.id; await refresh(true); renderDetail(); $('#detail-dialog').showModal(); notice('');
  } catch (error) { notice(error.message); }
  finally { button.disabled = false; }
});
$('#editor-form').addEventListener('submit', async event => {
  event.preventDefault(); const button = event.submitter; button.disabled = true;
  try {
    const content = Object.fromEntries(new FormData(event.target));
    content.capacity = Number(content.capacity); content.registration_open = event.target.elements.registration_open.checked;
    content.event_date = content.event_date ? new Date(content.event_date).toISOString() : '';
    await api(`/api/apps/${selectedApp}/content`, {content,expected_version:editorVersion});
    $('#editor-dialog').close(); await refresh(true); notice('Draft saved. Deploy the saved draft to update your page.');
  } catch (error) { notice(error.message); }
  finally { button.disabled = false; }
});
setInterval(() => { if (!locked) refresh().catch(error => notice(error.message)); },2500);
