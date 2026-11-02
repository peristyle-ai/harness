// harness — group chat client (added 2026-10-23).
//
// One page: /g/<group_id>. It shows the shared group transcript, and
// lets the user post a message. An agent only answers when it is
// @mentioned; several agents can be mentioned in one message, and they
// take their turns in mention order.
//
// The server streams NDJSON (same shape as the 1:1 entity chat), so a
// long agent turn doesn't hit Cloudflare's 125s origin timeout.

(function () {
  const script = document.currentScript;
  const groupId = (script && script.dataset.groupId) || null;
  const $ = (id) => document.getElementById(id);
  const escapeHtml = (s) => String(s).replace(/[&<>"']/g, c => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  }[c]));

  function renderMarkdown(text) {
    if (text == null) return '';
    const raw = String(text);
    try {
      if (typeof marked !== 'undefined' && typeof marked.parse === 'function') {
        const html = marked.parse(raw, {gfm: true, breaks: true, mangle: false, headerIds: false});
        if (typeof DOMPurify !== 'undefined' && typeof DOMPurify.sanitize === 'function') {
          return DOMPurify.sanitize(html, {ADD_ATTR: ['target'], USE_PROFILES: {html: true}});
        }
        return html;
      }
    } catch (e) {
      console.warn('renderMarkdown failed:', e);
    }
    return escapeHtml(raw);
  }

  const PALETTE = ['#4f9aff', '#5fc77e', '#e89c4f', '#c45fd0', '#4fd0c0', '#e85a85'];
  function colorFor(name) {
    let h = 0;
    for (let i = 0; i < name.length; i++) h = (h * 31 + name.charCodeAt(i)) >>> 0;
    return PALETTE[h % PALETTE.length];
  }
  function agentIconHTML(name, size) {
    const c = colorFor(name);
    const sz = size ? `width:${size}px;height:${size}px;font-size:${Math.round(size * 0.4)}px;` : '';
    const letter = (name[0] || '?').toUpperCase();
    return `<div class="entity-icon" style="--icon-bg:${c};background:${c};${sz}">${letter}</div>`;
  }
  function fmtTime(iso) {
    if (!iso) return '';
    const d = new Date(iso);
    if (isNaN(d.getTime())) return '';
    return d.toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'});
  }

  async function api(path, opts) {
    opts = opts || {};
    opts.credentials = 'same-origin';
    const isFormData = typeof FormData !== 'undefined' && opts.body instanceof FormData;
    if (opts.body && typeof opts.body !== 'string' && !isFormData) {
      opts.headers = Object.assign({'Content-Type': 'application/json'}, opts.headers || {});
      opts.body = JSON.stringify(opts.body);
    }
    const r = await fetch(path, opts);
    if (r.status === 401) throw new Error('unauthorized');
    if (!r.ok) {
      let detail = '';
      try { detail = (await r.json()).error || ''; } catch (e) {}
      throw new Error(detail || ('HTTP ' + r.status));
    }
    return r;
  }

  let toastTimer = null;
  function showToast(msg, isError) {
    let t = $('harnessToast');
    if (!t) {
      t = document.createElement('div');
      t.id = 'harnessToast';
      t.className = 'toast';
      document.body.appendChild(t);
    }
    t.textContent = msg;
    t.className = 'toast' + (isError ? ' error' : '');
    t.style.display = '';
    if (toastTimer) clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { t.style.display = 'none'; }, 4000);
  }

  // --- auth --------------------------------------------------------------
  let currentUser = null;

  function showLogin() {
    const lp = $('loginPane'); if (lp) lp.style.display = '';
    const cp = $('chatPane'); if (cp) cp.style.display = 'none';
    const mw = $('menuWrap'); if (mw) mw.style.display = 'none';
  }
  function showAuth(user) {
    const lp = $('loginPane'); if (lp) lp.style.display = 'none';
    const cp = $('chatPane'); if (cp) cp.style.display = 'flex';
    const mw = $('menuWrap'); if (mw) mw.style.display = '';
    const mh = $('menuHeader'); if (mh) mh.textContent = user.email;
  }
  async function checkAuth() {
    try {
      const r = await fetch('/api/v1/auth/me', {credentials: 'same-origin'});
      if (r.ok) {
        const data = await r.json();
        if (data.authenticated) {
          currentUser = data.user;
          showAuth(data.user);
          await loadGroup();
          return data.user;
        }
      }
    } catch (e) {}
    showLogin();
    return null;
  }

  const loginForm = $('loginForm');
  if (loginForm) {
    loginForm.addEventListener('submit', async (e) => {
      e.preventDefault();
      const email = $('email').value.trim();
      const password = $('password').value;
      if (!email || !password) return;
      const loginBtn = $('loginBtn');
      const loginError = $('loginError');
      loginBtn.disabled = true;
      loginError.style.display = 'none';
      try {
        const r = await fetch('/api/v1/auth/login', {
          method: 'POST', credentials: 'same-origin',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({email, password}),
        });
        if (r.ok) {
          const data = await r.json();
          currentUser = data.user;
          showAuth(data.user);
          await loadGroup();
        } else {
          const data = await r.json().catch(() => ({}));
          loginError.textContent = data.error || 'Login failed';
          loginError.style.display = 'block';
        }
      } catch (err) {
        loginError.textContent = 'Network error: ' + err;
        loginError.style.display = 'block';
      } finally {
        loginBtn.disabled = false;
      }
    });
    const passwordInput = $('password');
    const passwordToggle = $('passwordToggle');
    if (passwordInput && passwordToggle) {
      const toggle = (ev) => {
        if (ev) { ev.preventDefault(); ev.stopPropagation(); }
        const visible = passwordInput.type === 'password';
        passwordInput.type = visible ? 'text' : 'password';
        passwordToggle.classList.toggle('is-visible', visible);
        passwordToggle.setAttribute('aria-pressed', String(visible));
        passwordToggle.setAttribute('aria-label', visible ? 'Hide password' : 'Show password');
      };
      passwordToggle.addEventListener('pointerdown', toggle);
      passwordToggle.addEventListener('click', toggle);
    }
  }

  // --- burger menu -------------------------------------------------------
  function openMenu() {
    const pop = $('menuPop'); if (pop) pop.style.display = '';
    const btn = $('menuBtn'); if (btn) btn.setAttribute('aria-expanded', 'true');
  }
  function closeMenu() {
    const pop = $('menuPop'); if (pop) pop.style.display = 'none';
    const btn = $('menuBtn'); if (btn) btn.setAttribute('aria-expanded', 'false');
  }
  const menuBtn = $('menuBtn');
  if (menuBtn) {
    menuBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      const pop = $('menuPop');
      if (!pop) return;
      if (pop.style.display === 'none') openMenu(); else closeMenu();
    });
    document.addEventListener('click', (e) => {
      const wrap = $('menuWrap');
      if (wrap && !wrap.contains(e.target)) closeMenu();
    });
    document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeMenu(); });
  }
  const menuLogout = $('menuLogout');
  if (menuLogout) {
    menuLogout.addEventListener('click', async () => {
      closeMenu();
      await fetch('/api/v1/auth/logout', {method: 'POST', credentials: 'same-origin'});
      location.href = '/';
    });
  }

  // --- group + transcript -------------------------------------------------
  let group = null;

  // agent key -> the name it displays (the group payload carries it).
  // The KEY is what the roster stores and what the API path takes; the
  // LABEL is what the agent calls itself.
  let displayNames = {};

  function labelFor(agent) {
    return displayNames[agent] || agent;
  }

  async function loadGroup() {
    if (!groupId) return;
    try {
      const r = await api('/api/v1/groups/' + encodeURIComponent(groupId));
      const data = await r.json();
      group = data.group || null;
      if (group) {
        const icon = $('groupIcon');
        if (icon) {
          const c = colorFor(group.name);
          icon.style.setProperty('--icon-bg', c);
          icon.style.background = c;
          icon.textContent = (group.name[0] || '?').toUpperCase();
        }
        displayNames = group.display_names || {};
        renderMentionChips(group.agents || []);
      }
    } catch (e) {
      console.error('loadGroup failed', e);
    }
    await loadMessages();
  }

  function renderMentionChips(agents) {
    const bar = $('mentionBar');
    const chips = $('mentionChips');
    if (!bar || !chips) return;
    chips.innerHTML = '';
    if (!agents || !agents.length) { bar.style.display = 'none'; return; }
    for (const a of agents) {
      const b = document.createElement('button');
      b.type = 'button';
      b.className = 'mention-chip';
      b.dataset.agent = a;
      b.innerHTML = agentIconHTML(a, 18) + '<span>@' + escapeHtml(labelFor(a)) + '</span>';
      b.addEventListener('click', () => insertMention(a));
      chips.appendChild(b);
    }
    bar.style.display = '';
  }

  function insertMention(agent) {
    const input = $('input');
    if (!input) return;
    const start = input.selectionStart || input.value.length;
    const end = input.selectionEnd || input.value.length;
    const before = input.value.slice(0, start);
    const after = input.value.slice(end);
    const needsSpace = before && !/\s$/.test(before);
    const token = (needsSpace ? ' ' : '') + '@' + agent + ' ';
    input.value = before + token + after;
    const pos = before.length + token.length;
    input.focus();
    try { input.setSelectionRange(pos, pos); } catch (e) {}
    input.dispatchEvent(new Event('input'));
  }

  async function loadMessages() {
    if (!groupId) return;
    const messagesEl = $('messages');
    try {
      const r = await api('/api/v1/groups/' + encodeURIComponent(groupId) + '/messages?limit=100');
      const data = await r.json();
      const list = data.messages || [];
      const empty = $('empty');
      if (empty) empty.style.display = list.length ? 'none' : '';
      Array.from(messagesEl.querySelectorAll('.gmsg, .gsys')).forEach(n => n.remove());
      for (const m of list) renderMessage(m);
      scrollToBottom();
    } catch (e) {
      console.error('loadMessages failed', e);
    }
  }

  function scrollToBottom() {
    const messagesEl = $('messages');
    if (!messagesEl) return;
    requestAnimationFrame(() => { messagesEl.scrollTop = messagesEl.scrollHeight; });
  }

  function renderMessage(m) {
    const messagesEl = $('messages');
    if (!messagesEl) return;
    const empty = $('empty');
    if (empty) empty.style.display = 'none';
    if (m.author_type === 'system') {
      messagesEl.appendChild(systemLine(m.content, m.created_at));
      return;
    }
    if (m.author_type === 'agent') {
      messagesEl.appendChild(agentBubble(m.author_name, m.content, m.created_at));
      return;
    }
    messagesEl.appendChild(userBubble(m.author_name, m.content, m.created_at));
  }

  function systemLine(text, iso) {
    const div = document.createElement('div');
    div.className = 'gsys';
    div.innerHTML = '<span>' + escapeHtml(text || '') + '</span>' +
                    (iso ? ' <span class="gsys-time">' + escapeHtml(fmtTime(iso)) + '</span>' : '');
    return div;
  }

  function userBubble(name, text, iso) {
    const div = document.createElement('div');
    div.className = 'gmsg gmsg-user';
    div.innerHTML =
      '<div class="gmsg-meta">' + escapeHtml(name || 'you') +
      (iso ? ' · ' + escapeHtml(fmtTime(iso)) : '') + '</div>' +
      '<div class="gmsg-body">' + escapeHtml(text || '') + '</div>';
    return div;
  }

  function agentBubble(agent, text, iso) {
    const div = document.createElement('div');
    div.className = 'gmsg gmsg-agent';
    div.innerHTML =
      '<div class="gmsg-meta">' + agentIconHTML(agent || '?', 18) +
      '<span class="gmsg-name">' + escapeHtml(agent ? labelFor(agent) : 'agent') + '</span>' +
      (iso ? ' · ' + escapeHtml(fmtTime(iso)) : '') + '</div>' +
      '<div class="gmsg-body"><div class="gmsg-answer">' +
      renderMarkdown(text || '') + '</div></div>';
    return div;
  }

  // --- live agent bubbles --------------------------------------------------
  const liveBubbles = {};   // agent name -> bubble element

  function liveBubbleFor(agent) {
    if (liveBubbles[agent]) return liveBubbles[agent];
    const messagesEl = $('messages');
    const div = document.createElement('div');
    div.className = 'gmsg gmsg-agent live';
    div.innerHTML =
      '<div class="gmsg-meta">' + agentIconHTML(agent || '?', 18) +
      '<span class="gmsg-name">' + escapeHtml(agent || 'agent') + '</span></div>' +
      // The body carries TWO parts: the live status line (the model's
      // latest thought, at the top) and the answer. They are separate
      // elements, so a thought never overwrites the answer and the
      // answer never wipes the thought.
      '<div class="gmsg-body">' +
      '<div class="gmsg-status"></div>' +
      '<div class="gmsg-answer">…</div></div>' +
      '<details class="trace"><summary class="trace-summary">working...</summary>' +
      '<ol class="trace-list"></ol></details>' +
      // The thought stream, exactly as the 1:1 chat has it: a
      // collapsible section of the model's per-iteration narration.
      // It is LIVE ONLY (not a turn) and it starts CLOSED.
      '<details class="thoughts"><summary class="thought-summary">thought stream</summary>' +
      '<div class="thought-lines"></div></details>';
    messagesEl.appendChild(div);
    liveBubbles[agent] = div;
    scrollToBottom();
    return div;
  }

  function finalizeBubble(agent, text, resp) {
    const div = liveBubbles[agent];
    if (!div) return;
    const answer = div.querySelector('.gmsg-answer');
    if (answer) answer.innerHTML = renderMarkdown(text || '(no response)');
    // The status line was the "what is it doing RIGHT NOW" line. The
    // answer has arrived, so it goes.
    const status = div.querySelector('.gmsg-status');
    if (status) status.remove();
    // The turn is over: the trace collapses into a static summary, the
    // same shape the 1:1 chat uses. Both sections stay CLOSED -- the
    // operator asked for the thought stream and the tool-call summary
    // to be collapsed by default.
    const list = div.querySelector('.trace-list');
    const traceSummary = div.querySelector('.trace-summary');
    const traceEl = div.querySelector('.trace');
    if (list && traceSummary) {
      const tools = list.querySelectorAll('li.trace-item:not(.nudge)').length;
      const iters = (resp && resp.tool_iterations) || 0;
      const lat = (resp && resp.latency_ms) ? (resp.latency_ms / 1000).toFixed(1) : '';
      traceSummary.textContent = iters + ' iter · ' + tools + ' tool call' +
        (tools === 1 ? '' : 's') + (lat ? ' · ' + lat + 's' : '');
    }
    if (traceEl) traceEl.removeAttribute('open');
    const thoughtsEl = div.querySelector('.thoughts');
    if (thoughtsEl) thoughtsEl.removeAttribute('open');
    div.classList.remove('live');
    // The entry is KEPT (not deleted) until the persisted transcript
    // row arrives -- that row replaces this bubble (the server sends
    // both the `final` event and the transcript row for the same
    // reply, and only one of them should end up on screen).
    scrollToBottom();
  }

  // --- sending -------------------------------------------------------------
  let inFlight = false;
  const form = $('form');
  const input = $('input');
  const sendBtn = $('send');

  function autoGrow() {
    if (!input) return;
    input.style.height = 'auto';
    input.style.height = Math.min(input.scrollHeight, 160) + 'px';
  }
  if (input) {
    input.addEventListener('input', autoGrow);
    input.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        if (form) form.requestSubmit();
      }
    });
  }

  if (form && input && sendBtn) {
    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      if (inFlight) return;
      const text = input.value.trim();
      if (!text) return;
      input.value = '';
      autoGrow();
      // Optimistic bubble; replaced when the server confirms it.
      const optimistic = userBubble((currentUser && currentUser.display_name) || 'you', text, new Date().toISOString());
      const messagesEl = $('messages');
      const empty = $('empty');
      if (empty) empty.style.display = 'none';
      messagesEl.appendChild(optimistic);
      scrollToBottom();
      inFlight = true;
      sendBtn.disabled = true;
      const ti = $('typingIndicator'); if (ti) ti.classList.add('is-active');
      const sendT0 = Date.now();
      try {
        await sendStreaming(text, optimistic, sendT0);
      } catch (err) {
        const msg = err.message || 'Network error';
        if (msg === 'unauthorized') showLogin();
        showToast('Send failed: ' + msg, true);
        messagesEl.appendChild(systemLine('⚠️ ' + msg));
      } finally {
        const ti = $('typingIndicator'); if (ti) ti.classList.remove('is-active');
        inFlight = false;
        sendBtn.disabled = false;
        input.focus();
      }
    });
  }

  async function sendStreaming(text, optimisticBubble, sendT0) {
    const r = await fetch('/api/v1/groups/' + encodeURIComponent(groupId) + '/chat', {
      method: 'POST',
      credentials: 'same-origin',
      headers: {
        'Content-Type': 'application/json',
        'Accept': 'application/x-ndjson',
      },
      body: JSON.stringify({message: text, attachments: []}),
    });
    if (r.status === 401) throw new Error('unauthorized');
    if (!r.ok) {
      let detail = '';
      try { detail = (await r.json()).error || ''; } catch (e) {}
      throw new Error(detail || ('HTTP ' + r.status));
    }
    const reader = r.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    while (true) {
      const {done, value} = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, {stream: true});
      let nl;
      while ((nl = buffer.indexOf('\n')) >= 0) {
        const line = buffer.slice(0, nl);
        buffer = buffer.slice(nl + 1);
        if (!line.trim() || line.startsWith('#')) continue;
        let ev;
        try { ev = JSON.parse(line); } catch (e) { continue; }
        handleEvent(ev, optimisticBubble, sendT0);
      }
    }
  }

  function handleEvent(ev, optimisticBubble, sendT0) {
    const messagesEl = $('messages');
    const agent = ev.agent || '';
    switch (ev.type) {
      case 'group_message': {
        const m = ev.message || {};
        if (m.author_type === 'user') {
          // Replace the optimistic bubble with the server's copy.
          if (optimisticBubble && optimisticBubble.parentNode) {
            const replacement = userBubble(m.author_name, m.content, m.created_at || new Date().toISOString());
            optimisticBubble.parentNode.replaceChild(replacement, optimisticBubble);
          }
        } else if (m.author_type === 'agent') {
          // The agent's reply arrives twice: once as the `final` event
          // (which closes the live bubble) and once as this persisted
          // transcript row. The persisted row is the canonical one, so
          // it REPLACES the live bubble instead of being appended next
          // to it.
          const live = liveBubbles[m.author_name];
          const bubble = agentBubble(m.author_name, m.content, m.created_at);
          if (live && live.parentNode) {
            // The trace and the thought stream exist only in the live
            // DOM, so they are CARRIED OVER to the persisted bubble.
            // Replacing the bubble wholesale threw the tool-call
            // summary away at the end of every turn.
            ['trace', 'thoughts'].forEach(function (cls) {
              const src = live.querySelector(':scope > .' + cls);
              if (src) bubble.appendChild(src);
            });
            live.parentNode.replaceChild(bubble, live);
            delete liveBubbles[m.author_name];
          } else {
            messagesEl.appendChild(bubble);
          }
          scrollToBottom();
        }
        break;
      }
      case 'agent_start': {
        liveBubbleFor(agent);
        break;
      }
      case 'iter_start': {
        // The marker goes into the THOUGHT section, not into the body:
        // the body is the answer (operator: "i don't want the thinking
        // ... (iter 10) to appear" in the answer slot).
        const div = liveBubbleFor(agent);
        const lines = div.querySelector('.thought-lines');
        if (lines) {
          const marker = document.createElement('div');
          marker.className = 'thought-line marker';
          marker.textContent = 'thinking… (iter ' +
            (ev.iter === undefined ? '?' : ev.iter) + ')';
          lines.appendChild(marker);
        }
        break;
      }
      case 'delta': {
        const div = liveBubbleFor(agent);
        const answer = div.querySelector('.gmsg-answer');
        if (answer) answer.textContent = (answer.dataset.raw || '') + (ev.text || '');
        const raw = (answer && answer.dataset.raw ? answer.dataset.raw : '') + (ev.text || '');
        if (answer) answer.dataset.raw = raw;
        break;
      }
      case 'iter_text': {
        const div = liveBubbleFor(agent);
        const answer = div.querySelector('.gmsg-answer');
        if (answer) { answer.dataset.raw = ev.text || ''; answer.textContent = ev.text || ''; }
        break;
      }
      case 'thought': {
        // The model's one-line thought for this iteration. Same event
        // the 1:1 chat renders; the group client used to drop it.
        const div = liveBubbleFor(agent);
        // The LIVE STATUS LINE: the model's latest thought, at the top
        // of the bubble. The operator asked for it (2026-11-01): the
        // thought stream is collapsed by default, so the one line that
        // says what the agent is doing RIGHT NOW has to be visible
        // without opening anything.
        const status = div.querySelector('.gmsg-status');
        if (status) status.textContent = ev.text || '';
        const lines = div.querySelector('.thought-lines');
        const summary = div.querySelector('.thought-summary');
        if (lines) {
          const line = document.createElement('div');
          line.className = 'thought-line';
          line.textContent = ev.text || '';
          lines.appendChild(line);
        }
        if (summary) {
          const n = lines ? lines.querySelectorAll('.thought-line:not(.marker)').length : 0;
          summary.textContent = 'thought stream · ' + n;
        }
        scrollToBottom();
        break;
      }
      case 'tool_start': {
        const div = liveBubbleFor(agent);
        const list = div.querySelector('.trace-list');
        const summary = div.querySelector('.trace-summary');
        if (list) {
          const li = document.createElement('li');
          const argsStr = ev.args ? JSON.stringify(ev.args) : '';
          li.textContent = '🔧 ' + ev.name + '(' + (argsStr.length > 80 ? argsStr.slice(0, 80) + '…' : argsStr) + ')';
          li.className = 'trace-item pending';
          // The tool name and its args ride along on the element, so
          // tool_end can finish THIS line instead of appending a
          // second one.
          li.dataset.tool = ev.name || '';
          li.dataset.args = argsStr;
          list.appendChild(li);
        }
        if (summary) {
          const n = list ? list.querySelectorAll('li.trace-item').length : 0;
          summary.textContent = n + ' tool call' + (n === 1 ? '' : 's') + '…';
        }
        scrollToBottom();
        break;
      }
      case 'tool_end': {
        const div = liveBubbleFor(agent);
        const list = div.querySelector('.trace-list');
        const summary = div.querySelector('.trace-summary');
        if (list) {
          // ONE line per tool call: finish the pending line that
          // tool_start wrote, instead of appending a second one (the
          // 1:1 client does exactly this). Appending made every tool
          // call appear TWICE in the group chat.
          const pendings = list.querySelectorAll(
            'li.trace-item.pending[data-tool="' + CSS.escape(ev.name || '') + '"]');
          const pending = pendings.length ? pendings[pendings.length - 1] : null;
          const args = (pending && pending.dataset.args) || '';
          const shortArgs = args.length > 80 ? args.slice(0, 80) + '…' : args;
          if (pending) {
            pending.classList.remove('pending');
            pending.classList.add(ev.ok ? 'ok' : 'err');
            pending.textContent = (ev.ok ? '✓ ' : '✗ ') + ev.name +
              '(' + shortArgs + ')';
            if (!ev.ok && ev.result_preview) pending.title = ev.result_preview;
          } else {
            const li = document.createElement('li');
            li.textContent = (ev.ok ? '✓ ' : '✗ ') + ev.name;
            li.className = 'trace-item ' + (ev.ok ? 'ok' : 'err');
            list.appendChild(li);
          }
        }
        if (summary) {
          const n = list ? list.querySelectorAll('li.trace-item:not(.nudge)').length : 0;
          summary.textContent = n + ' tool call' + (n === 1 ? '' : 's') +
            ' · ' + ((Date.now() - sendT0) / 1000).toFixed(1) + 's';
        }
        scrollToBottom();
        break;
      }
      case 'nudge': {
        // The model narrated a tool call instead of making one. The
        // 1:1 chat shows it in the trace; the group chat used to drop
        // it, so the operator saw nothing at all.
        const div = liveBubbleFor(agent);
        const list = div.querySelector('.trace-list');
        if (list) {
          const li = document.createElement('li');
          li.textContent = '⚠️ model narrated a tool call — nudging for retry';
          li.className = 'trace-item nudge';
          list.appendChild(li);
        }
        scrollToBottom();
        break;
      }
      case 'notice': {
        messagesEl.appendChild(systemLine(ev.message || ''));
        scrollToBottom();
        break;
      }
      case 'final': {
        const resp = ev.response || {};
        finalizeBubble(agent, resp.assistant || '', resp);
        break;
      }
      case 'agent_done': {
        // The turn is over. Drop the live-bubble handle so the next
        // message from this agent starts a fresh bubble.
        delete liveBubbles[agent];
        break;
      }
      case 'error': {
        const div = liveBubbles[agent];
        if (div) {
          const answer = div.querySelector('.gmsg-answer');
          const status = div.querySelector('.gmsg-status');
          if (status) status.remove();
          if (answer) answer.innerHTML = '<span class="gmsg-error">⚠️ ' + escapeHtml(ev.message || 'error') + '</span>';
          div.classList.remove('live');
          delete liveBubbles[agent];
        } else {
          messagesEl.appendChild(systemLine('⚠️ ' + (ev.message || 'error')));
        }
        scrollToBottom();
        break;
      }
      default:
        break;
    }
  }

  // --- boot ---------------------------------------------------------------
  checkAuth();
})();
