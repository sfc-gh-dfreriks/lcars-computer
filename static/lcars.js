// LCARS console: WebSocket to server.py, push-to-talk / hands-free capture, chime + voice queue.
const $ = (id) => document.getElementById(id);
const logEl = $('log'), opsEl = $('ops'), planEl = $('plan');
let SOUNDS = {}, ws, muted = false, handsFree = false, question = null, lastWorking = 0, current = null;

// ---------------------------------------------------------------- audio out (one queue, in order)
const queue = []; let playing = false;
function play(url) { if (!muted) { queue.push(url); pump(); } }
function chime(name) { play(SOUNDS[name] || `sounds/${name}.wav`); }
function pump() {
  if (playing || !queue.length) return;
  playing = true; $('pulse').classList.add('speaking');
  const a = new Audio(queue.shift());
  const next = () => { playing = false; $('pulse').classList.remove('speaking'); pump(); };
  a.onended = next; a.onerror = next; a.play().catch(next);
}
const speaking = () => playing || queue.length > 0;

// ---------------------------------------------------------------- log
function esc(s) { return s.replace(/[&<>]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c])); }
function md(s) {  // tiny formatter: code fences, inline code, bold; strip the VOICE: line
  s = s.replace(/^\s*VOICE:.*$/gm, '').trim();
  return esc(s).replace(/```[\w-]*\n?([\s\S]*?)```/g, '<pre>$1</pre>').replace(/`([^`]+)`/g, '<code>$1</code>')
               .replace(/\*\*([^*]+)\*\*/g, '<b>$1</b>');
}
function entry(cls, who, html) {
  const d = document.createElement('div'); d.className = `entry ${cls}`;
  d.innerHTML = `<div class="who">${who}</div><div class="body">${html}</div>`;
  logEl.appendChild(d); logEl.scrollTop = logEl.scrollHeight; return d;
}
function setStatus(text, cls = '') { const s = $('status'); s.textContent = text; s.className = cls; }

// ---------------------------------------------------------------- server events
function connect() {
  ws = new WebSocket(`ws://${location.host}/ws`); ws.binaryType = 'arraybuffer';
  ws.onopen = () => setStatus('INITIALIZING', 'working');
  ws.onclose = () => { setStatus('OFFLINE'); setTimeout(connect, 2000); };
  ws.onmessage = (e) => {
    const m = JSON.parse(e.data);
    switch (m.type) {
      case 'sounds': SOUNDS = m.urls; break;
      case 'status': entry('system', 'SYSTEM', esc(m.text)); break;
      case 'session':
        $('sid').textContent = m.sessionId.slice(0, 8).toUpperCase(); $('model').textContent = (m.model || '').toUpperCase();
        setStatus('STANDBY'); opsEl.innerHTML = ''; planEl.innerHTML = ''; break;
      case 'heard': entry('you', 'YOU', esc(m.text)); break;
      case 'ack': chime('ack'); break;
      case 'working': setStatus('WORKING', 'working'); current = entry('computer', 'COMPUTER', '<span class="spoken">WORKING…</span>'); current.raw = ''; break;
      case 'text':
        if (!current) { current = entry('computer', 'COMPUTER', ''); current.raw = ''; }
        current.raw += m.text; current.querySelector('.body').innerHTML = md(current.raw); logEl.scrollTop = logEl.scrollHeight; break;
      case 'thought': break;
      case 'tool': tool(m); break;
      case 'plan':
        planEl.innerHTML = m.entries.map(p => `<li class="${p.status}"><span class="light"></span><span class="t">${esc(p.content || '')}</span></li>`).join(''); break;
      case 'auto_approved': entry('system', 'AUTO-APPROVED', esc(m.title || 'request')); break;
      case 'question': ask(m); break;
      case 'answered': entry('you', 'YOU · SELECTION', esc(m.label)); hideAsk(); break;
      case 'done': setStatus('STANDBY'); current = null; break;
      case 'say':
        if (m.chime) chime(m.chime);
        play(m.url);
        (current || entry('computer', 'COMPUTER', '')).insertAdjacentHTML('beforeend', `<div class="spoken">“${esc(m.text)}”</div>`);
        break;
      case 'error': setStatus('ERROR'); entry('system', 'ERROR', esc(m.text)); current = null; break;
    }
  };
}

function tool(m) {
  let li = document.getElementById('t-' + m.id);
  if (!li) {
    li = document.createElement('li'); li.id = 't-' + m.id;
    li.innerHTML = `<span class="light"></span><span class="t"></span>`; opsEl.prepend(li);
  }
  if (m.title) li.querySelector('.t').textContent = m.title;
  li.className = m.status || 'pending';
  if ((m.status === 'pending') && Date.now() - lastWorking > 6000) { lastWorking = Date.now(); chime('working'); }
}

function ask(m) {
  question = m;
  $('askQ').textContent = m.question + (m.total > 1 ? `  (${m.index + 1} OF ${m.total})` : '');
  const colors = ['orange', 'peach', 'lilac', 'blue', 'sky'];
  $('askOpts').innerHTML = '';
  m.options.forEach((o, i) => {
    const b = document.createElement('button'); b.className = `pill ${o.free ? 'red' : colors[i % colors.length]}`;
    b.innerHTML = `${o.free ? 'OTHER' : (i + 1) + ' · ' + esc(o.label.toUpperCase())}<small>${esc(o.desc || '')}</small>`;
    b.onclick = () => o.free ? ($('askFree').classList.remove('hidden'), $('askFreeText').focus())
                             : ws.send(JSON.stringify({ type: 'answer', rid: m.rid, optionId: o.id, label: o.label }));
    $('askOpts').appendChild(b);
  });
  $('ask').classList.remove('hidden'); setStatus('INPUT REQUIRED', 'listening');
}
function hideAsk() { question = null; $('ask').classList.add('hidden'); $('askFree').classList.add('hidden'); setStatus('WORKING', 'working'); }
$('askFree').onsubmit = (e) => {
  e.preventDefault(); const t = $('askFreeText').value.trim(); if (!t || !question) return;
  const free = question.options.find(o => o.free);
  ws.send(JSON.stringify({ type: 'answer', rid: question.rid, optionId: free.id, free: t })); $('askFreeText').value = '';
};

// ---------------------------------------------------------------- microphone
let ctx, stream, proc, chunks = [], recording = false, mode = 'ptt';
async function mic() {
  if (ctx) return;
  stream = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true } });
  ctx = new AudioContext({ sampleRate: 16000 });
  const src = ctx.createMediaStreamSource(stream); proc = ctx.createScriptProcessor(2048, 1, 1);
  src.connect(proc); proc.connect(ctx.destination);
  proc.onaudioprocess = (ev) => {
    const d = ev.inputBuffer.getChannelData(0);
    if (recording) chunks.push(new Float32Array(d));
    if (handsFree && mode === 'vad') vad(d);
  };
}
function wav(chs) {  // 16 kHz mono 16-bit PCM WAV
  const n = chs.reduce((a, c) => a + c.length, 0), b = new ArrayBuffer(44 + n * 2), v = new DataView(b);
  const w = (p, s) => [...s].forEach((c, i) => v.setUint8(p + i, c.charCodeAt(0)));
  w(0, 'RIFF'); v.setUint32(4, 36 + n * 2, true); w(8, 'WAVEfmt '); v.setUint32(16, 16, true); v.setUint16(20, 1, true);
  v.setUint16(22, 1, true); v.setUint32(24, 16000, true); v.setUint32(28, 32000, true); v.setUint16(32, 2, true);
  v.setUint16(34, 16, true); w(36, 'data'); v.setUint32(40, n * 2, true);
  const pcm = new Int16Array(b, 44); let o = 0;
  for (const c of chs) for (const x of c) pcm[o++] = Math.max(-1, Math.min(1, x)) * 0x7fff;
  return new Uint8Array(b);
}
function send(chs, handsFreeFlag) {  // 1 flag byte (0 = push-to-talk, 1 = hands-free) + WAV
  const body = wav(chs), msg = new Uint8Array(body.length + 1);
  msg[0] = handsFreeFlag ? 1 : 0; msg.set(body, 1); ws.send(msg.buffer);
}

// push-to-talk
async function startTalk() { if (recording) return; await mic(); await ctx.resume(); mode = 'ptt'; chunks = []; recording = true; chime('listen'); $('talk').classList.add('rec'); setStatus('LISTENING', 'listening'); }
function stopTalk() {
  if (!recording || mode !== 'ptt') return; recording = false; $('talk').classList.remove('rec');
  const secs = chunks.reduce((a, c) => a + c.length, 0) / 16000;
  if (secs > 0.4) { send(chunks, false); setStatus('PROCESSING', 'working'); } else setStatus('STANDBY');
  if (handsFree) mode = 'vad';
}
$('talk').addEventListener('pointerdown', startTalk); $('talk').addEventListener('pointerup', stopTalk); $('talk').addEventListener('pointerleave', stopTalk);
document.addEventListener('keydown', (e) => { if (e.code === 'Space' && e.target.tagName !== 'INPUT' && !e.repeat) { e.preventDefault(); startTalk(); } });
document.addEventListener('keyup', (e) => { if (e.code === 'Space' && e.target.tagName !== 'INPUT') { e.preventDefault(); stopTalk(); } });

// hands-free: energy-based voice activity detection; the server only acts on phrases that start
// with "Computer" (or answer a pending question). The mic is ignored while the computer speaks.
let voiced = 0, silent = 0, seg = [];
function vad(d) {
  if (speaking()) { seg = []; voiced = 0; return; }
  let e = 0; for (const x of d) e += x * x; e = Math.sqrt(e / d.length);
  const loud = e > 0.02;
  if (loud) { voiced++; silent = 0; } else silent++;
  if (voiced) seg.push(new Float32Array(d));
  const segSecs = seg.length * 2048 / 16000;
  if (voiced && (silent > 9 || segSecs > 20)) {          // ~1.1 s of silence ends the phrase
    if (voiced > 4 && segSecs > 0.6) send(seg, !question);
    seg = []; voiced = 0; silent = 0;
  }
}
$('hands').onclick = async () => {
  handsFree = !handsFree; $('handsState').textContent = handsFree ? 'ON · SAY "COMPUTER"' : 'OFF'; $('hands').classList.toggle('on', handsFree);
  if (handsFree) { await mic(); await ctx.resume(); mode = 'vad'; chime('listen'); } else mode = 'ptt';
};

// ---------------------------------------------------------------- other controls
$('typed').onsubmit = (e) => { e.preventDefault(); const t = $('q').value.trim(); if (!t) return; ws.send(JSON.stringify({ type: 'text', text: t })); $('q').value = ''; };
$('cancel').onclick = () => ws.send(JSON.stringify({ type: 'cancel' }));
$('reset').onclick = () => ws.send(JSON.stringify({ type: 'reset' }));
$('mute').onclick = () => { muted = !muted; $('muteState').textContent = muted ? 'MUTED' : 'ON'; if (muted) queue.length = 0; };

// decorative LCARS number block and the stardate, shown as today's date in YYYYMM.DD form
function stardate() { const n = new Date(), p = (x) => String(x).padStart(2, '0');
  return `${n.getFullYear()}${p(n.getMonth() + 1)}.${p(n.getDate())}`; }
setInterval(() => { $('stardate').textContent = stardate(); }, 1000); $('stardate').textContent = stardate();
$('numbers').innerHTML = Array.from({ length: 24 }, () => `${Math.floor(Math.random() * 90 + 10)}-${Math.floor(Math.random() * 9000 + 1000)}`).join('<br>');
connect();
