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
  const currentAuthorized = Boolean(data.current_project_authorized);
  $('mode').textContent = c.demo ? '仅本机演示 · 零外发' : '本机服务 · Codex 插件';
  $('sharing-status').textContent = c.sharing_enabled && currentAuthorized ? (c.demo ? '本机演示 · 默认开启' : '默认共享 · 已开启') : c.consent_decision === 'denied' ? '全局关闭 · 仅本地' : '正在绑定当前项目';
  $('mode-explainer').textContent = c.demo ? '这是本机演示接收器，不会访问外部网络。正式版安装后自动使用 Tracekin Cloud；敏感会话可单独暂停。' : 'Tracekin 安装后自动绑定当前项目和固定 Tracekin Cloud 地址；敏感会话第一条消息输入 tracekin off。';
  $('current-project').textContent = data.default_project || '当前会话未提供项目目录';
  const useExistingPet = Boolean(c.use_existing_pet);
  $('use-existing-pet').checked = useExistingPet;
  $('use-existing-pet').disabled = busy;
  $('custom-pet-settings').hidden = useExistingPet;
  $('existing-pet-note').hidden = !useExistingPet;
  $('demo-event').hidden = !c.demo;
  $('demo-event').disabled = busy || !c.sharing_enabled;
  $('pending').textContent = data.counts.pending;
  $('sent').textContent = data.counts.sent;
  $('paused').textContent = data.session_controls.paused_sessions;
  $('sent-label').textContent = c.demo ? '本机接收器已确认' : '接收服务已确认';
  $('connection').textContent = !c.sharing_enabled ? (c.consent_decision === 'denied' ? '全局关闭 · 本地模式' : '正在绑定项目') : data.delivery === 'retry' ? '发送失败，等待重试' : c.demo ? '本机演示运行中' : '默认共享运行中';
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
  }
}
async function act(path, data, message) {
  if (busy) return;
  busy = true; if (state) render(state);
  try { const next = await api(path, data); render(next); if (message) notice(message); return next; }
  catch (e) { notice(e.message); }
  finally { busy = false; if (state) render(state); }
}
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
$('clear').onclick = () => { if (confirm('清除本地贡献记录？默认同步会继续保持开启；敏感会话请输入 tracekin off。这不会删除接收方已经收到的数据，也不会影响原生宠物。')) act('/api/clear', {}, '本地贡献记录已清除，默认同步仍保持开启。'); };
async function refresh() { if (busy) return; try { render(await api('/api/state'), !ready); ready = true; } catch (e) { notice(e.message); } }
refresh(); setInterval(refresh, 2500);
