/*
 * Чат-виджет единого окна: Лоцман, Боцман и Штурман за одним окном.
 * Подключение: <script src="https://<сервер>/static/widget.js" data-api="https://<сервер>" defer></script>
 * Тексты ассистентов (приветствие, подсказки) приходят с сервера — из prompts/ и config/assistants.yaml.
 */
(function () {
  "use strict";
  var script = document.currentScript;
  // без data-api адрес API — там же, откуда загружен виджет; работает и в подразделе сайта (example.ru/yachtsales)
  var API = ((script && script.dataset.api) || new URL(script.src).href.replace(/\/static\/widget\.js(\?.*)?$/, ""))
    .replace(/\/$/, "");
  var STORAGE_KEY = "ycw-session";

  var ICON_CHAT = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12a8 8 0 0 1-11.6 7.1L4 20l1-4.6A8 8 0 1 1 21 12z"/></svg>';
  var ICON_CLOSE = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><path d="M6 6l12 12M18 6L6 18"/></svg>';
  var ICON_RESET = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 12a9 9 0 1 0 3-6.7L3 8"/><path d="M3 3v5h5"/></svg>';
  var ICON_SEND = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M12 19V5M5 12l7-7 7 7"/></svg>';

  function sessionId() {
    var id = null;
    try { id = localStorage.getItem(STORAGE_KEY); } catch (e) { /* приватный режим */ }
    if (!id || !/^[A-Za-z0-9_-]{8,64}$/.test(id)) {
      id = (crypto.randomUUID ? crypto.randomUUID() : String(Date.now()) + Math.random().toString(16).slice(2))
        .replace(/[^A-Za-z0-9_-]/g, "");
      try { localStorage.setItem(STORAGE_KEY, id); } catch (e) { /* без сохранения — новая сессия при перезагрузке */ }
    }
    return id;
  }

  function el(tag, cls, html) {
    var node = document.createElement(tag);
    if (cls) node.className = cls;
    if (html != null) node.innerHTML = html;
    return node;
  }

  function escapeHtml(s) {
    return s.replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  // Минимальная разметка ответов модели: абзацы, списки, **жирный**. Сначала экранируем — XSS не пройдет.
  function format(text) {
    var blocks = escapeHtml(text).split(/\n{2,}/);
    return blocks.map(function (block) {
      var lines = block.split("\n");
      if (lines.every(function (l) { return /^\s*([-•*]|\d+[.)])\s+/.test(l); })) {
        var ordered = /^\s*\d/.test(lines[0]);
        var items = lines.map(function (l) { return "<li>" + l.replace(/^\s*([-•*]|\d+[.)])\s+/, "") + "</li>"; });
        return (ordered ? "<ol>" : "<ul>") + items.join("") + (ordered ? "</ol>" : "</ul>");
      }
      return "<p>" + lines.join("<br>") + "</p>";
    }).join("").replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
  }

  function time() {
    return new Date().toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" });
  }

  function initial(name) { return (name || "?").trim().charAt(0).toUpperCase(); }

  var link = document.createElement("link");
  link.rel = "stylesheet";
  link.href = API + "/static/widget.css";
  document.head.appendChild(link);

  var root = el("div", "ycw-root");
  root.innerHTML =
    '<button class="ycw-launcher" type="button" aria-label="Открыть чат">' + ICON_CHAT + "</button>" +
    '<section class="ycw-panel" role="dialog" aria-label="Чат">' +
    '  <header class="ycw-header">' +
    '    <div class="ycw-avatar"></div>' +
    '    <div class="ycw-who"><div class="ycw-name"></div><div class="ycw-title"></div></div>' +
    '    <button class="ycw-icon-btn ycw-reset" type="button" title="Начать заново" aria-label="Начать заново">' + ICON_RESET + "</button>" +
    '    <button class="ycw-icon-btn ycw-close" type="button" title="Закрыть" aria-label="Закрыть">' + ICON_CLOSE + "</button>" +
    "  </header>" +
    '  <div class="ycw-log" aria-live="polite"></div>' +
    '  <form class="ycw-form">' +
    '    <textarea class="ycw-input" rows="1" placeholder="Введите сообщение" maxlength="2000"></textarea>' +
    '    <button class="ycw-send" type="submit" aria-label="Отправить" disabled>' + ICON_SEND + "</button>" +
    "  </form>" +
    "</section>";
  document.body.appendChild(root);

  var log = root.querySelector(".ycw-log");
  var input = root.querySelector(".ycw-input");
  var send = root.querySelector(".ycw-send");
  var session = sessionId();
  var current = null;   // текущий ассистент {id, name, title}
  var busy = false;
  var started = false;

  function setAssistant(a) {
    current = a;
    var avatar = root.querySelector(".ycw-header .ycw-avatar");
    avatar.textContent = initial(a.name);
    avatar.dataset.assistant = a.id;
    root.querySelector(".ycw-name").textContent = a.name;
    root.querySelector(".ycw-title").textContent = a.title;
  }

  function scroll() { log.scrollTop = log.scrollHeight; }

  function addBot(text, a) {
    var row = el("div", "ycw-row ycw-bot");
    var avatar = el("div", "ycw-avatar ycw-sm");
    avatar.textContent = initial(a.name);
    avatar.dataset.assistant = a.id;
    var bubble = el("div", "ycw-bubble");
    bubble.appendChild(el("div", "ycw-from")).textContent = a.name;
    bubble.appendChild(el("div", "", format(text)));
    bubble.appendChild(el("div", "ycw-time")).textContent = time();
    row.appendChild(avatar);
    row.appendChild(bubble);
    log.appendChild(row);
    scroll();
  }

  function addUser(text) {
    var row = el("div", "ycw-row ycw-user");
    var bubble = el("div", "ycw-bubble", format(text));
    bubble.appendChild(el("div", "ycw-time")).textContent = time();
    row.appendChild(bubble);
    log.appendChild(row);
    scroll();
  }

  function addNote(text) {
    log.appendChild(el("div", "ycw-note")).textContent = text;
    scroll();
  }

  function addChips(list) {
    if (!list || !list.length) return;
    var box = el("div", "ycw-chips");
    list.forEach(function (label) {
      var chip = el("button", "ycw-chip");
      chip.type = "button";
      chip.textContent = label;
      chip.addEventListener("click", function () { box.remove(); ask(label); });
      box.appendChild(chip);
    });
    log.appendChild(box);
    scroll();
  }

  function typing(on) {
    var node = log.querySelector(".ycw-typing-row");
    if (!on) { if (node) node.remove(); return; }
    if (node) return;
    var row = el("div", "ycw-row ycw-bot ycw-typing-row");
    var avatar = el("div", "ycw-avatar ycw-sm");
    avatar.textContent = initial(current && current.name);
    if (current) avatar.dataset.assistant = current.id;
    row.appendChild(avatar);
    row.appendChild(el("div", "ycw-bubble", '<span class="ycw-typing"><span></span><span></span><span></span></span>'));
    log.appendChild(row);
    scroll();
  }

  function post(path, body) {
    return fetch(API + path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body)
    }).then(function (r) {
      if (r.status === 429) {
        // лимит сообщений: сервер присылает текст для посетителя
        return r.json().then(function (d) { var e = new Error("limit"); e.text = d.text; throw e; });
      }
      if (!r.ok) throw new Error("HTTP " + r.status);
      return r.json();
    });
  }

  function show(answer) {
    if (answer.switched_from) {
      addNote("Разговор продолжит " + answer.assistant.name + " — " + answer.assistant.title.toLowerCase());
    }
    setAssistant(answer.assistant);
    if (answer.text) addBot(answer.text, answer.assistant);
    addChips(answer.starters);
  }

  function start() {
    log.innerHTML = "";
    busy = true;
    typing(true);
    post("/api/start", { session: session })
      .then(function (answer) { typing(false); show(answer); })
      .catch(function (e) { typing(false); addNote(e.text || "Чат временно недоступен. Попробуйте позже."); })
      .then(function () { busy = false; update(); });
  }

  function ask(text) {
    if (busy || !text.trim()) return;
    var chips = log.querySelector(".ycw-chips");
    if (chips) chips.remove();
    addUser(text);
    busy = true;
    update();
    typing(true);
    post("/api/message", { session: session, text: text })
      .then(function (answer) { typing(false); show(answer); })
      .catch(function (e) { typing(false); addNote(e.text || "Сообщение не отправлено — проверьте соединение и попробуйте еще раз."); })
      .then(function () { busy = false; update(); input.focus(); });
  }

  function update() {
    send.disabled = busy || !input.value.trim();
    input.style.height = "auto";
    input.style.height = Math.min(input.scrollHeight, 120) + "px";
  }

  root.querySelector(".ycw-launcher").addEventListener("click", function () {
    root.classList.add("ycw-open");
    if (!started) { started = true; start(); }
    input.focus();
  });
  root.querySelector(".ycw-close").addEventListener("click", function () { root.classList.remove("ycw-open"); });
  root.querySelector(".ycw-reset").addEventListener("click", function () { if (!busy) start(); });
  root.querySelector(".ycw-form").addEventListener("submit", function (e) {
    e.preventDefault();
    var text = input.value;
    input.value = "";
    ask(text);
  });
  input.addEventListener("input", update);
  input.addEventListener("keydown", function (e) {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); root.querySelector(".ycw-form").requestSubmit(); }
  });

  if (script && script.dataset.open === "true") root.querySelector(".ycw-launcher").click();
})();
