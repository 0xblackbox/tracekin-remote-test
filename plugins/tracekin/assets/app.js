const $ = (id) => document.getElementById(id);
const token = location.hash.slice(1) || sessionStorage.getItem('tracekin-token') || '';
if (location.hash) { sessionStorage.setItem('tracekin-token', token); history.replaceState(null, '', '/'); }
let state, ready = false, busy = false, messageTimer;
function notice(text) { $('message').textContent = text; clearTimeout(messageTimer); messageTimer = setTimeout(() => $('message').textContent = '', 5500); }
async function api(path, data) {
  const r = await fetch(path, {method: data === undefined ? 'GET' : 'POST', headers: {'X-Tracekin-Token': token, 'Content-Type': 'application/json'}, ...(data === undefined ? {} : {body: JSON.stringify(data)})});
  const value = await r.json(); if (!r.ok) throw new Error(value.error || '本地服务暂未响应'); return value;
}
function render(data, initial = false) {
  state = data;
  const c = data.config;
  const projects = Array.isArray(c.projects) ? c.projects : [];
  const scopeReady = projects.length > 0;
  $('mode').textContent = c.demo ? '仅本机演示 · 零外发' : '本机服务 · Codex 插件';
  $('sharing-status').textContent = c.sharing_enabled && scopeReady ? (c.demo ? '全部任务 · 本机演示' : '默认共享 · 已开启') : c.consent_granted && !scopeReady ? '需要确认项目' : c.consent_granted ? '全局共享已暂停' : '等待一次授权';
  $('mode-explainer').textContent = c.demo ? '这一页正在演示客户端授权流程。接收器也在本机，不会向平台发送数据。' : '首次明确同意后，新会话默认共享；输入 tracekin off 只暂停当前会话。';
  $('endpoint-note').textContent = c.demo ? '当前是本机测试接收器。切换到正式模式并配置 HTTPS 服务后，才会产生外部共享。' : '更换范围、地址或令牌后，需要重新进行一次授权。';
  const useExistingPet = Boolean(c.use_existing_pet);
  $('use-existing-pet').checked = useExistingPet;
  $('use-existing-pet').disabled = busy;
  $('custom-pet-settings').hidden = useExistingPet;
  $('existing-pet-note').hidden = !useExistingPet;
  $('sharing').checked = c.sharing_enabled;
  $('sharing').disabled = busy || !c.endpoint;
  $('sharing-control').hidden = !c.demo;
  // Show the action again when an older install has consent but no bound
  // project, or when global sharing was paused. This avoids a dead-end state.
  $('quick-start').hidden = Boolean(c.consent_granted && c.sharing_enabled && scopeReady) || busy;
  $('quick-share').disabled = busy || !c.endpoint;
  $('advanced-scope').open = Boolean(!c.endpoint && !c.demo);
  $('demo-event').hidden = !c.demo;
  $('demo-event').disabled = busy || !c.sharing_enabled;
  $('pending').textContent = data.counts.pending;
  $('sent').textContent = data.counts.sent;
  $('paused').textContent = data.session_controls.paused_sessions;
  $('sent-label').textContent = c.demo ? '本机接收器已确认' : '接收服务已确认';
  $('connection').textContent = !c.sharing_enabled ? (c.consent_granted ? '全局共享已暂停' : '等待一次授权') : data.delivery === 'retry' ? '发送失败，等待重试' : c.demo ? '本机演示运行中' : '默认共享运行中';
  $('delivery-note').textContent = c.demo ? '所有演示事件均带 synthetic 标记' : '回执只代表接收，不代表质量验收';
  $('empty').hidden = Boolean(data.events.length);
  $('events').replaceChildren(...data.events.map(({payload, status}) => {
    const card = document.createElement('article'); card.className = 'event';
    const head = document.createElement('div'); head.className = 'event-head';
    const title = document.createElement('span'); title.textContent = {Stop: '任务结束事件', UserPromptSubmit: '提示词事件', PostToolUse: '工具活动事件'}[payload.event] || '任务事件';
    const label = document.createElement('span'); label.textContent = status === 'sent' ? (c.demo ? '本机已接收' : '接收方已确认') : '待发送';
    head.append(title, label); const pre = document.createElement('pre'); pre.textContent = JSON.stringify(payload, null, 2);
    card.append(head, pre); return card;
  }));
  if (initial) {
    $('pet-name').value = c.pet.name; $('pet-concept').value = c.pet.concept;
    $('projects').value = projects.join('\n') || data.default_project;
    $('endpoint').value = c.endpoint; $('endpoint').readOnly = c.demo;
  }
}
async function act(path, data, message) {
  if (busy) return;
  busy = true; if (state) render(state);
  try { const next = await api(path, data); render(next); if (message) notice(message); return next; }
  catch (e) { notice(e.message); }
  finally { busy = false; if (state) render(state); }
}
$('scope-form').onsubmit = async (e) => {
  e.preventDefault();
  const payload = {projects: $('projects').value.split('\n').map(x => x.trim()).filter(Boolean), endpoint: $('endpoint').value.trim()};
  if ($('clear-token').checked) payload.endpoint_token = '';
  else if ($('endpoint-token').value) payload.endpoint_token = $('endpoint-token').value;
  const next = await act('/api/config', payload, '范围已保存；如状态提示等待授权，请点击允许共享。');
  if (next) { $('projects').value = next.config.projects.join('\n'); $('endpoint').value = next.config.endpoint; $('endpoint-token').value = ''; $('clear-token').checked = false; }
};
$('quick-share').onclick = () => act('/api/config', {projects: [state.default_project], endpoint: state.config.endpoint, consent_granted: true, sharing_enabled: true, share_all: true}, '授权已记录。当前项目的新会话默认共享；敏感会话请先输入 tracekin off。');
$('quick-local').onclick = () => act('/api/config', {consent_granted: false, sharing_enabled: false, share_all: false}, '当前保持本地模式，宠物仍会照常陪伴。');
$('sharing').onchange = () => act('/api/config', {consent_granted: $('sharing').checked, sharing_enabled: $('sharing').checked}, $('sharing').checked ? '演示共享已开启。' : '演示共享已关闭。');
$('use-existing-pet').onchange = () => act('/api/config', {use_existing_pet: $('use-existing-pet').checked}, $('use-existing-pet').checked ? '已切换为沿用当前 Codex 宠物。' : '已切换为自定义宠物创建流程。');
$('pet-form').onsubmit = async (e) => {
  e.preventDefault();
  const next = await act('/api/config', {pet: {name: $('pet-name').value.trim(), concept: $('pet-concept').value.trim()}}, '描述已保存；原生宠物外观将在 Codex 创建后生效。');
  if (next) {
    $('pet-prompt').value = `请用 hatch-pet 创建一个 Codex 原生自定义宠物，名字叫「${next.config.pet.name}」。外观描述：${next.config.pet.concept}。请完成当前版本要求的动画、验证和本地安装。只创建宠物；保持 Tracekin 数据共享设置不变。`;
    $('prompt-wrap').hidden = false;
  }
};
$('copy-prompt').onclick = async () => { try { await navigator.clipboard.writeText($('pet-prompt').value); notice('提示词已复制。'); } catch { $('pet-prompt').focus(); $('pet-prompt').select(); notice('请复制已选中的提示词。'); } };
$('demo-event').onclick = () => act('/api/demo-event', {}, '两条合成事件已通过真实 Hook 脚本进入队列。');
$('clear').onclick = () => { if (confirm('撤回全局共享授权并清除本地贡献记录？这不会删除接收方已经收到的数据，也不会影响原生宠物。')) act('/api/clear', {}, '授权已撤回，本地贡献记录已清除。'); };
async function refresh() { if (busy) return; try { render(await api('/api/state'), !ready); ready = true; } catch (e) { notice(e.message); } }
refresh(); setInterval(refresh, 2500);
