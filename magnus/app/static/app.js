/* MAGNUS — panel de control (sin dependencias).
 *
 * Recibe el estado del robot por Server-Sent Events (/api/events), lo pinta y
 * envía los botones como comandos (POST /api/command).  Toda la lógica de
 * partida vive en Python: aquí solo se representa y se anima.
 */
(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const FILES = "abcdefgh";
  const GLYPH = { k: "♚", q: "♛", r: "♜", b: "♝", n: "♞", p: "♟" };
  const VALUE = { p: 1, n: 3, b: 3, r: 5, q: 9, k: 0 };
  const SIDE_ES = { white: "blancas", black: "negras" };
  const ARM_MODE_ES = { off: "APAGADO", simulated: "SIMULADO", cyberpi: "CYBERPI" };
  const ARM_STATUS_ES = {
    off: "Sin brazo: mueve tú las piezas de MAGNUS",
    connecting: "Esperando a que la CyberPi se conecte…",
    homing: "Referenciando: buscando los topes y fijando el cero…",
    ready: "Listo",
    busy: "Ejecutando jugada",
    error: "Error",
  };

  const state = {
    snap: null,
    flip: false,
    lastEventId: null,
    lastPlacementKey: null,
    lastGameId: null,
    gameStartedAt: null,
    overShownFor: null,
    connected: false,
    kiosk: new URLSearchParams(location.search).get("kiosk") === "1",
    bootDone: false,
    lastPhrase: null,
  };
  const pieceNodes = new Map();   // id -> {el, square, symbol}
  let pieceSeq = 0;

  // ------------------------------------------------------------------ //
  // Comunicación
  // ------------------------------------------------------------------ //
  async function command(name, params = {}) {
    try {
      const res = await fetch("/api/command", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name, params }),
      });
      const data = await res.json();
      if (!data.ok) toast("error", data.error || "Comando rechazado");
      return data;
    } catch (err) {
      toast("error", "Sin conexión con el robot");
      return { ok: false };
    }
  }

  function connect() {
    const source = new EventSource("/api/events");
    source.onmessage = (ev) => {
      setConnected(true);
      try { render(JSON.parse(ev.data)); } catch (err) { console.error(err); }
    };
    source.onerror = () => setConnected(false);
  }

  function setConnected(on) {
    if (state.connected === on) return;
    state.connected = on;
    $("offline").classList.toggle("show", !on);
    if (on) markBoot("server", "ok");
  }

  // ------------------------------------------------------------------ //
  // Utilidades
  // ------------------------------------------------------------------ //
  function squareXY(sq) {
    const f = FILES.indexOf(sq[0]);
    const r = parseInt(sq[1], 10);
    return state.flip ? [7 - f, r - 1] : [f, 8 - r];
  }
  function isLight(sq) { return (FILES.indexOf(sq[0]) + parseInt(sq[1], 10)) % 2 === 1; }
  function fmt(n) { return String(n).padStart(2, "0"); }
  function el(tag, cls, text) {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  // ------------------------------------------------------------------ //
  // Tablero
  // ------------------------------------------------------------------ //
  const squareRects = new Map();
  function buildBoard() {
    const svg = $("board-svg");
    const ns = "http://www.w3.org/2000/svg";
    svg.innerHTML = "";
    for (const f of FILES) {
      for (let r = 1; r <= 8; r++) {
        const sq = f + r;
        const rect = document.createElementNS(ns, "rect");
        rect.setAttribute("width", "1");
        rect.setAttribute("height", "1");
        rect.dataset.square = sq;
        rect.classList.add("sq", isLight(sq) ? "light" : "dark");
        svg.appendChild(rect);
        squareRects.set(sq, rect);
      }
    }
    layoutSquares();
  }
  function layoutSquares() {
    for (const [sq, rect] of squareRects) {
      const [x, y] = squareXY(sq);
      rect.setAttribute("x", x);
      rect.setAttribute("y", y);
    }
    const files = state.flip ? [...FILES].reverse() : [...FILES];
    const ranks = state.flip ? [1, 2, 3, 4, 5, 6, 7, 8] : [8, 7, 6, 5, 4, 3, 2, 1];
    $("coords-files").innerHTML = files.map((f) => `<span>${f}</span>`).join("");
    $("coords-ranks").innerHTML = ranks.map((r) => `<span>${r}</span>`).join("");
  }

  function placePiece(node, square, animate) {
    const [x, y] = squareXY(square);
    const tx = `translate(${x * 100}%, ${y * 100}%)`;
    node.el.style.setProperty("--tx", tx);
    if (!animate) node.el.style.transition = "none";
    node.el.style.transform = tx;
    if (!animate) {
      void node.el.offsetWidth;          // fuerza el reflow y reactiva la transición
      node.el.style.transition = "";
    } else {
      node.el.classList.add("moving");
      setTimeout(() => node.el.classList.remove("moving"), 600);
    }
    node.square = square;
  }

  function createPiece(square, symbol, animateIn) {
    const div = el("div", "piece " + (symbol === symbol.toUpperCase() ? "white" : "black"));
    div.textContent = GLYPH[symbol.toLowerCase()] || "?";
    if (animateIn) div.classList.add("enter");
    $("pieces").appendChild(div);
    const node = { el: div, square, symbol };
    placePiece(node, square, false);
    pieceNodes.set(++pieceSeq, node);
    return node;
  }

  function removePiece(id, node) {
    node.el.classList.add("leave");
    pieceNodes.delete(id);
    setTimeout(() => node.el.remove(), 400);
  }

  /** Diferencia el placement nuevo contra las piezas en pantalla y anima. */
  function updatePieces(placement, hardReset) {
    if (hardReset) {
      for (const [, node] of pieceNodes) node.el.remove();
      pieceNodes.clear();
    }
    const arrivals = new Map(Object.entries(placement));   // square -> symbol
    const orphans = [];
    for (const [id, node] of pieceNodes) {
      if (arrivals.get(node.square) === node.symbol) {
        arrivals.delete(node.square);
      } else {
        orphans.push([id, node]);
      }
    }
    // Emparejar huérfanas con llegadas del mismo símbolo (la más cercana).
    for (const [id, node] of orphans) {
      let best = null, bestD = Infinity;
      for (const [sq, sym] of arrivals) {
        if (sym !== node.symbol) continue;
        const [x1, y1] = squareXY(node.square), [x2, y2] = squareXY(sq);
        const d = Math.hypot(x1 - x2, y1 - y2);
        if (d < bestD) { bestD = d; best = sq; }
      }
      if (best) {
        arrivals.delete(best);
        placePiece(node, best, true);
      } else {
        removePiece(id, node);
      }
    }
    for (const [sq, sym] of arrivals) createPiece(sq, sym, !hardReset);
  }

  function updateSquares(board) {
    const last = board.last_move ? [board.last_move.slice(0, 2), board.last_move.slice(2, 4)] : [];
    const pending = new Set(board.pending || []);
    for (const [sq, rect] of squareRects) {
      rect.classList.toggle("last", last.includes(sq));
      rect.classList.toggle("check", board.check === sq);
      rect.classList.toggle("pending", pending.has(sq));
    }
    const arrow = $("planned-arrow");
    if (board.planned) {
      const [x1, y1] = squareXY(board.planned.from), [x2, y2] = squareXY(board.planned.to);
      arrow.setAttribute("x1", x1 + 0.5); arrow.setAttribute("y1", y1 + 0.5);
      arrow.setAttribute("x2", x2 + 0.5); arrow.setAttribute("y2", y2 + 0.5);
      arrow.style.display = "";
    } else {
      arrow.style.display = "none";
    }
  }

  function updateEval(ev) {
    let frac = 0.5, label = "0.0", top = false;
    if (ev.mate !== null && ev.mate !== undefined) {
      frac = ev.mate > 0 ? 0.98 : 0.02;
      label = ev.label || "M";
      top = ev.mate < 0;
    } else if (ev.cp !== null && ev.cp !== undefined) {
      frac = 1 / (1 + Math.exp(-ev.cp / 350));
      frac = Math.min(0.97, Math.max(0.03, frac));
      label = (ev.cp / 100).toFixed(1);
      if (ev.cp > 0) label = "+" + label;
      top = ev.cp < 0;
    }
    if (state.flip) { frac = 1 - frac; }
    $("evalbar-white").style.height = (frac * 100).toFixed(1) + "%";
    $("evalbar-white").style.top = state.flip ? "0" : "";
    $("evalbar-white").style.bottom = state.flip ? "" : "0";
    const lab = $("evalbar-label");
    lab.textContent = label;
    lab.classList.toggle("top", state.flip ? !top : top);
  }

  // ------------------------------------------------------------------ //
  // Banner de fase
  // ------------------------------------------------------------------ //
  function setBanner({ kicker, title, detail, cls, html, actions }) {
    const banner = $("banner");
    banner.className = "banner" + (cls ? " " + cls : "");
    $("banner-kicker").textContent = kicker;
    const t = $("banner-title");
    const next = html || title;
    if (t.dataset.v !== next) {
      t.dataset.v = next;
      if (html) t.innerHTML = html; else t.textContent = title;
      t.style.animation = "none"; void t.offsetWidth; t.style.animation = "";
    }
    $("banner-detail").textContent = detail || "";
    const box = $("banner-actions");
    const key = (actions || []).map((a) => a.label + a.cmd).join("|");
    if (box.dataset.key !== key) {
      box.dataset.key = key;
      box.innerHTML = "";
      for (const a of actions || []) {
        const b = el("button", "btn " + (a.cls || "btn-primary"), a.label);
        if (a.pulse) b.classList.add("pulse");
        b.onclick = () => command(a.cmd, a.params || {});
        box.appendChild(b);
      }
    }
  }

  function renderBanner(s) {
    const arm = s.arm || {};
    const board = $("board");
    board.classList.toggle("thinking", s.sub === "robot_thinking");
    if (s.phase === "setup") {
      if (!s.camera.ok) {
        return setBanner({ kicker: "SIN CÁMARA", title: "No hay señal de vídeo", cls: "danger",
          detail: s.camera.error || "Elige otra cámara en Ajustes" });
      }
      if (s.sub === "ready") {
        return setBanner({ kicker: "LISTO PARA JUGAR", title: "Tablero en posición inicial",
          detail: "MAGNUS juega con " + SIDE_ES[s.robot_side],
          actions: [{ label: "▶ Iniciar partida", cmd: "start_game", pulse: true }] });
      }
      if (s.setup.corners < 4) {
        return setBanner({ kicker: "PREPARACIÓN", title: "Buscando el tablero", cls: "alert",
          detail: `Esquinas visibles: ${s.setup.corners}/4 · IDs 40-43` });
      }
      const pct = Math.round(100 * s.setup.pieces_detected / s.setup.pieces_needed);
      const ring = `<svg class="ring" viewBox="0 0 44 44"><circle class="bg" cx="22" cy="22" r="18"/>` +
        `<circle class="fg" cx="22" cy="22" r="18" stroke-dasharray="113" stroke-dashoffset="${113 - 113 * pct / 100}"/></svg>`;
      return setBanner({ kicker: "PREPARACIÓN", html: ring + `<span>Coloca la posición inicial</span>`,
        detail: `${s.setup.pieces_detected} / ${s.setup.pieces_needed} piezas detectadas`,
        actions: [{ label: "Iniciar de todas formas", cmd: "start_game", cls: "btn-ghost" }] });
    }
    if (s.phase === "over") {
      const r = s.result || {};
      return setBanner({ kicker: "FIN DE LA PARTIDA", title: r.text || "Partida terminada",
        cls: r.robot_won ? "robot" : "human",
        actions: [{ label: "Nueva partida", cmd: "start_game" }] });
    }
    const planned = s.board.planned;
    switch (s.sub) {
      case "human_turn": {
        const last = s.history.length ? s.history[s.history.length - 1] : null;
        return setBanner({ kicker: "TU TURNO", title: "Mueve una pieza", cls: "human",
          detail: last && s.history.length % 2 === (s.robot_side === "white" ? 1 : 0)
            ? `MAGNUS jugó ${last}` : (s.history.length ? "" : "Empiezas tú") });
      }
      case "robot_thinking":
        return setBanner({ kicker: "MAGNUS", cls: "robot",
          html: `Pensando<span class="dots"><span>.</span><span>.</span><span>.</span></span>`,
          detail: s.engine.enabled ? `Dificultad ${s.engine.difficulty}` : "Sin engine: mueve tú por MAGNUS" });
      case "arm_moving": {
        const i = arm.step_index, n = (arm.steps || []).length;
        return setBanner({ kicker: "BRAZO EN MOVIMIENTO", cls: "robot",
          title: (arm.steps || [])[i] || "…", detail: `Paso ${Math.max(0, i) + 1} de ${n} · ${planned ? planned.san : ""}`,
          actions: [{ label: "■ PARADA", cmd: "arm_stop", cls: "btn-danger" }] });
      }
      case "robot_ready": {
        const san = planned ? planned.san : "…";
        const path = planned ? `${planned.from} → ${planned.to}` : "";
        if (!arm || arm.mode === "off") {
          return setBanner({ kicker: "MAGNUS JUEGA", title: san, cls: "robot",
            detail: `Mueve la pieza por MAGNUS: ${path}` });
        }
        if (arm.pending) {
          return setBanner({ kicker: "MAGNUS JUEGA", title: san, cls: "robot",
            detail: `Brazo listo · ${path}`,
            actions: [{ label: "▶ Ejecutar con el brazo", cmd: "arm_execute", pulse: true },
                      { label: "■ PARADA", cmd: "arm_stop", cls: "btn-danger" }] });
        }
        if (arm.status === "error" || arm.status === "connecting") {
          return setBanner({ kicker: "MAGNUS JUEGA", title: san, cls: "alert",
            detail: (arm.error || ARM_STATUS_ES[arm.status]) + ` · mueve tú: ${path}` });
        }
        return setBanner({ kicker: "MAGNUS JUEGA", title: san, cls: "robot", detail: "Preparando el brazo…" });
      }
    }
    return setBanner({ kicker: "PARTIDA", title: "…" });
  }

  // ------------------------------------------------------------------ //
  // Paneles
  // ------------------------------------------------------------------ //
  function setPill(name, st, title) {
    const p = document.querySelector(`.pill[data-pill="${name}"]`);
    p.dataset.state = st;
    p.title = title || "";
  }
  function renderPills(s) {
    setPill("camera", s.camera.ok ? "ok" : "bad", s.camera.error || s.camera.label);
    const v = s.vision;
    setPill("vision", v.pose_ok ? "ok" : (v.corners_found ? "warn" : "bad"),
      v.pose_error || `Esquinas ${v.corners_found}/4 · ${v.pieces_confirmed} piezas`);
    const e = s.engine;
    setPill("engine", !e.enabled ? "bad" : e.status === "listo" ? (e.thinking ? "busy" : "ok")
      : e.status === "iniciando" ? "warn" : "bad", e.error || `Stockfish ${e.status} · ${e.difficulty}`);
    const a = s.arm || { mode: "off", status: "off" };
    setPill("arm", a.mode === "off" ? "bad" : a.status === "ready" ? "ok" : a.status === "busy" ? "busy"
      : (a.status === "connecting" || a.status === "homing") ? "warn" : "bad",
      a.error || `${ARM_MODE_ES[a.mode]} · ${a.status}`);
    const vo = s.voice;
    setPill("voice", !vo.available ? "bad" : vo.muted ? "warn" : vo.speaking ? "busy" : "ok",
      vo.backend ? `${vo.backend}${vo.muted ? " (silenciada)" : ""}` : "Sin voz");
    $("btn-mute").classList.toggle("muted", !vo.available || vo.muted);
    $("btn-mute").classList.toggle("active", vo.speaking);
    markBoot("camera", s.camera.ok ? "ok" : "bad");
    markBoot("vision", v.pose_ok ? "ok" : "warn");
    markBoot("engine", e.status === "listo" ? "ok" : e.status === "iniciando" ? "warn" : "bad");
    markBoot("voice", vo.available ? "ok" : "warn");
    markBoot("arm", a.mode === "off" ? "warn" : a.status === "ready" ? "ok" : a.status === "error" ? "bad" : "warn");
  }

  function renderTelemetry(s) {
    const v = s.vision;
    $("camera-label").textContent = s.camera.label;
    $("t-fps").textContent = v.fps ? v.fps.toFixed(1) : "—";
    const c = $("t-corners"); c.textContent = `${v.corners_found}/4` + (v.corners_remembered ? ` (+${v.corners_remembered} mem)` : "");
    c.className = "tele-v " + (v.corners_found === 4 ? "ok" : "bad");
    $("t-pieces").textContent = v.pieces_confirmed;
    $("t-pending").textContent = v.pieces_pending;
    $("t-off").textContent = v.pieces_off_board;
    const arm = $("t-arm"); arm.textContent = v.arm_seen ? "VISTO" : "—"; arm.className = "tele-v " + (v.arm_seen ? "ok" : "");
    $("fen").textContent = s.board.fen || "—";
    $("vision-msg").textContent = v.pose_error || v.layout_warning || s.camera.error || "";
    $("camera-offline").classList.toggle("show", !s.camera.ok);
    $("camera-offline-text").textContent = s.camera.error || "Sin señal de cámara";
  }

  function renderPlayers(s) {
    const human = s.robot_side === "white" ? "black" : "white";
    $("player-human-side").textContent = SIDE_ES[human];
    $("player-robot-side").textContent = SIDE_ES[s.robot_side] + (s.engine.enabled ? ` · ${s.engine.difficulty}` : "");
    const humanTurn = s.in_game && s.phase === "playing" && s.turn === human;
    const robotTurn = s.in_game && s.phase === "playing" && s.turn === s.robot_side;
    $("player-human").classList.toggle("active", humanTurn);
    $("player-robot").classList.toggle("active", robotTurn);
  }

  function renderMoves(s) {
    const box = $("moves");
    const hist = s.history || [];
    $("move-count").textContent = hist.length ? `jugada ${Math.floor(hist.length / 2) + 1} · ${hist.length % 2 ? "negras" : "blancas"}` : "sin jugadas";
    if (box.dataset.key === hist.join(" ") + "|" + s.robot_side) return;
    box.dataset.key = hist.join(" ") + "|" + s.robot_side;
    box.innerHTML = "";
    if (!hist.length) { box.appendChild(el("div", "moves-empty", "Sin jugadas todavía")); return; }
    for (let i = 0; i < hist.length; i += 2) {
      const row = el("div", "mv-row");
      row.appendChild(el("span", "mv-n", `${i / 2 + 1}.`));
      for (const j of [i, i + 1]) {
        const san = hist[j];
        const cell = el("span", "mv", san || "");
        if (san !== undefined) {
          const isRobot = (j % 2 === 0) === (s.robot_side === "white");
          if (isRobot) cell.classList.add("robot");
          if (j === hist.length - 1) cell.classList.add("latest");
        }
        row.appendChild(cell);
      }
      box.appendChild(row);
    }
    box.scrollTop = box.scrollHeight;
  }

  function renderCaptured(s) {
    const byWhite = s.captured.by_white || [], byBlack = s.captured.by_black || [];
    const humanIsWhite = s.robot_side !== "white";
    const human = humanIsWhite ? byWhite : byBlack, robot = humanIsWhite ? byBlack : byWhite;
    const glyphs = (arr) => arr.map((p) => `<span class="${p === p.toUpperCase() ? "w" : "b"}">${GLYPH[p.toLowerCase()]}</span>`).join("");
    const sum = (arr) => arr.reduce((t, p) => t + (VALUE[p.toLowerCase()] || 0), 0);
    $("cap-human").innerHTML = glyphs(human);
    $("cap-robot").innerHTML = glyphs(robot);
    const diff = sum(human) - sum(robot);
    $("cap-human-diff").textContent = diff > 0 ? `+${diff}` : "";
    $("cap-robot-diff").textContent = diff < 0 ? `+${-diff}` : "";
  }

  function renderArm(s) {
    const a = s.arm;
    if (!a) return;
    $("arm-mode-badge").textContent = ARM_MODE_ES[a.mode] || a.mode.toUpperCase();
    const led = $("arm-led");
    led.className = "led " + (a.status === "ready" ? "ok" : a.status === "busy" ? "busy"
      : (a.status === "connecting" || a.status === "homing") ? "warn" : a.status === "error" ? "bad" : "");
    $("arm-status-text").textContent = a.status === "error" ? (a.error || "Error") : ARM_STATUS_ES[a.status] || a.status;
    const planned = s.board.planned || {};
    let steps, mode;                       // mode: "busy" | "result" | "preview"
    if (a.status === "busy") { steps = a.steps; mode = "busy"; }
    else if (a.last_outcome && a.last_uci && a.last_uci === planned.uci) { steps = a.steps; mode = "result"; }
    else { steps = a.preview || []; mode = "preview"; }
    const list = $("arm-steps");
    const key = steps.join("|") + "#" + a.step_index + "#" + mode + "#" + a.last_outcome;
    if (list.dataset.key !== key) {
      list.dataset.key = key;
      list.innerHTML = "";
      steps.forEach((t, i) => {
        const li = el("li", "", t);
        if (mode === "preview") li.classList.add("preview");
        else if (mode === "result") li.classList.add(a.last_outcome === "done" || i < a.step_index ? "done" : (i === a.step_index ? "failed" : ""));
        else if (i < a.step_index) li.classList.add("done");
        else if (i === a.step_index) li.classList.add("current");
        list.appendChild(li);
      });
      const cur = list.querySelector(".current");
      if (cur) cur.scrollIntoView({ block: "nearest" });
    }
    const n = steps.length;
    const done = mode === "busy" ? Math.max(0, a.step_index) : mode === "result" ? (a.last_outcome === "done" ? n : Math.max(0, a.step_index)) : 0;
    $("arm-progress-bar").style.width = n ? (100 * done / n).toFixed(0) + "%" : "0";
    $("btn-arm-execute").hidden = !a.pending;
    $("btn-arm-execute").classList.toggle("pulse", !!a.pending);
    $("btn-arm-stop").hidden = a.mode === "off";
    const home = $("btn-arm-home");
    home.hidden = a.mode === "off";
    home.disabled = !a.can_home;
    home.title = a.can_home ? "Busca los topes del brazo y fija ahí el cero"
      : "Solo con el brazo conectado y parado";
    const p = a.positions;
    $("arm-coverage").textContent = p.complete ? `Tabla calibrada: ${p.calibrated}/${p.total} posiciones`
      : p.exists ? `Tabla incompleta: ${p.calibrated}/${p.total} calibradas` : "Sin tabla de posiciones (positions.json)";
  }

  function renderVoice(s) {
    const v = s.voice;
    $("wave").classList.toggle("on", v.speaking && !v.muted);
    const sub = $("subtitle");
    const phrase = v.last_phrase || (v.available ? "MAGNUS está callado" : "Voz no disponible");
    if (phrase !== state.lastPhrase) {
      state.lastPhrase = phrase;
      sub.textContent = phrase;
      sub.classList.remove("fresh"); void sub.offsetWidth; sub.classList.add("fresh");
    }
    sub.classList.toggle("muted-voice", !v.available || v.muted);
  }

  // ------------------------------------------------------------------ //
  // Ajustes
  // ------------------------------------------------------------------ //
  function buildDifficulty(cat) {
    const grid = $("diff-grid");
    if (grid.dataset.built) return;
    grid.dataset.built = "1";
    for (const d of cat) {
      const card = el("button", "diff-card");
      card.dataset.level = d.name;
      card.innerHTML = `<div class="diff-name">${d.name}</div><div class="diff-elo">${d.elo ? "≈ " + d.elo + " Elo" : "sin límite"}</div>` +
        `<div class="diff-desc">${d.description}</div><div class="diff-bar">${[1, 2, 3, 4, 5, 6].map((i) => `<i class="${i <= d.value ? "on" : ""}"></i>`).join("")}</div>`;
      card.onclick = () => command("set_difficulty", { level: d.name });
      grid.appendChild(card);
    }
  }

  function isEditing(id) { return document.activeElement === $(id); }

  function renderSettings(s) {
    const st = s.settings;
    buildDifficulty(s.difficulties || []);
    document.querySelectorAll(".diff-card").forEach((c) => c.classList.toggle("active", c.dataset.level === st.difficulty));
    $("chip-difficulty-v").textContent = st.difficulty;
    document.querySelectorAll("#seg-side button").forEach((b) => b.classList.toggle("active", b.dataset.side === st.robot_side));
    document.querySelectorAll("#seg-arm button").forEach((b) => b.classList.toggle("active", b.dataset.arm === st.arm_mode));
    const p = s.arm ? s.arm.positions : null;
    const cyber = $("seg-arm-cyberpi");
    cyber.disabled = !(p && p.complete);
    cyber.title = cyber.disabled ? "Requiere positions.json completo" : "Brazo real por TCP";
    $("arm-help").textContent = st.arm_mode === "off" ? "El robot canta la jugada y tú mueves la pieza por él."
      : st.arm_mode === "simulated" ? "Backend falso: muestra la secuencia que ejecutaría el brazo, sin hardware."
      : "Brazo real: el host abre un servidor TCP y la CyberPi se conecta a él.";
    if (!isEditing("in-camera")) $("in-camera").value = st.camera_index;
    if (!isEditing("in-arm-port")) $("in-arm-port").value = st.arm_port;
    if (!isEditing("in-positions")) $("in-positions").value = st.positions_path;
    $("tg-muted").checked = !!st.voice_muted;
    $("tg-announce").checked = !!st.announce_human_moves;
    $("tg-arm-auto").checked = !!st.arm_auto_execute;
    $("tg-arm-home").checked = !!st.arm_auto_home;
    if (!isEditing("in-idle")) { $("in-idle").value = st.idle_prompt_s; $("in-idle-v").textContent = st.idle_prompt_s ? `${st.idle_prompt_s} s` : "off"; }
    $("voice-backend").textContent = s.voice.available ? `Motor de voz: ${s.voice.backend}` : "Voz no disponible en este equipo";
    if (p) {
      $("coverage-fill").style.width = (100 * p.calibrated / p.total).toFixed(0) + "%";
      $("coverage-text").textContent = p.error ? p.error : `${p.calibrated} / ${p.total} posiciones calibradas` + (p.complete ? " · completa ✓" : "");
      const miss = (p.missing || []).concat(p.invalid || []);
      $("coverage-missing").textContent = miss.length && miss.length < p.total ? "Faltan: " + miss.slice(0, 24).join(" ") + (miss.length > 24 ? " …" : "") : (p.exists ? "" : "Genera la plantilla con examples/generate_positions_template.py");
    }
    $("camera-help").textContent = `Mapeo girado ${s.vision.board_turns * 90}°. Si el tablero digital sale rotado, gira el mapeo; si mueves la cámara, reinicia la detección.`;
  }

  // ------------------------------------------------------------------ //
  // Eventos (tostadas), modal, reloj, arranque
  // ------------------------------------------------------------------ //
  function toast(kind, text) {
    const box = $("toasts");
    const t = el("div", "toast " + kind, text);
    box.appendChild(t);
    setTimeout(() => { t.classList.add("out"); setTimeout(() => t.remove(), 400); }, kind === "error" ? 6000 : 3500);
    while (box.children.length > 5) box.firstChild.remove();
  }
  function renderLog(events) {
    const box = $("log");
    const key = events.length ? events[events.length - 1].id + ":" + events.length : "0";
    if (box.dataset.key === key) return;
    box.dataset.key = key;
    box.innerHTML = "";
    for (const e of events) {
      const row = el("div", "log-row " + e.kind);
      const d = new Date(e.time * 1000);
      row.appendChild(el("span", "log-t", `${fmt(d.getHours())}:${fmt(d.getMinutes())}:${fmt(d.getSeconds())}`));
      row.appendChild(el("span", "", e.text));
      box.appendChild(row);
    }
    box.scrollTop = box.scrollHeight;
  }
  function renderEvents(s) {
    const events = s.events || [];
    renderLog(events);
    if (state.lastEventId === null) {
      state.lastEventId = events.length ? Math.max(...events.map((e) => e.id)) : 0;
      return;
    }
    for (const e of events) {
      if (e.id > state.lastEventId) {
        // Solo lo que requiere atención salta como tostada; el resto va al registro.
        if (e.kind === "warn" || e.kind === "error") toast(e.kind, e.text);
        state.lastEventId = e.id;
      }
    }
  }

  function renderModal(s) {
    if (s.phase === "over" && state.overShownFor !== s.game_id) {
      state.overShownFor = s.game_id;
      const r = s.result || {};
      $("over-title").textContent = r.text || "Partida terminada";
      $("over-sub").textContent = r.kind === "checkmate" ? (r.robot_won ? "MAGNUS se lleva la partida." : "¡Enhorabuena, has vencido al robot!") : "Nadie gana esta vez.";
      $("modal-over").classList.add("open");
    }
    if (s.phase !== "over") $("modal-over").classList.remove("open");
  }

  function tickClock() {
    const c = $("clock");
    if (!state.gameStartedAt) { c.textContent = "00:00"; return; }
    const secs = Math.floor((Date.now() - state.gameStartedAt) / 1000);
    c.textContent = `${fmt(Math.floor(secs / 60))}:${fmt(secs % 60)}`;
  }

  function markBoot(name, st) {
    const li = document.querySelector(`.boot-list li[data-boot="${name}"]`);
    if (li) li.className = st;
  }
  function finishBoot() {
    if (state.bootDone) return;
    state.bootDone = true;
    $("boot").classList.add("hide");
  }

  // ------------------------------------------------------------------ //
  // Render principal
  // ------------------------------------------------------------------ //
  function render(s) {
    state.snap = s;
    const flip = !!s.settings.flip_view;
    let hardReset = false;
    if (flip !== state.flip) { state.flip = flip; layoutSquares(); hardReset = true; }
    if (s.game_id !== state.lastGameId) {
      if (state.lastGameId !== null || s.in_game) state.gameStartedAt = s.in_game ? Date.now() : null;
      state.lastGameId = s.game_id;
      hardReset = hardReset || s.in_game;
    }
    if (!s.in_game) state.gameStartedAt = null;
    if (s.in_game && !state.gameStartedAt) state.gameStartedAt = Date.now();

    const key = JSON.stringify(s.board.placement);
    if (key !== state.lastPlacementKey || hardReset) {
      state.lastPlacementKey = key;
      updatePieces(s.board.placement, hardReset);
    }
    updateSquares(s.board);
    updateEval(s.eval);
    renderBanner(s);
    renderPills(s);
    renderTelemetry(s);
    renderPlayers(s);
    renderMoves(s);
    renderCaptured(s);
    renderArm(s);
    renderVoice(s);
    renderSettings(s);
    renderEvents(s);
    renderModal(s);
    $("last-move").textContent = s.board.last_move ? `última: ${s.board.last_move}` : (s.in_game ? "sin jugadas" : "observación");
    $("board-msg").textContent = s.message || "";
    if (!state.bootDone) setTimeout(finishBoot, 1400);
  }

  // ------------------------------------------------------------------ //
  // Interacción
  // ------------------------------------------------------------------ //
  function openDrawer(open) {
    $("drawer").classList.toggle("open", open);
    $("drawer-backdrop").classList.toggle("open", open);
  }
  function toggleFullscreen() {
    if (!document.fullscreenElement) document.documentElement.requestFullscreen?.();
    else document.exitFullscreen?.();
  }

  function wire() {
    $("btn-settings").onclick = () => openDrawer(true);
    $("chip-difficulty").onclick = () => { openDrawer(true); $("set-difficulty").scrollIntoView({ behavior: "smooth" }); };
    $("btn-close-drawer").onclick = () => openDrawer(false);
    $("drawer-backdrop").onclick = () => openDrawer(false);
    $("btn-fullscreen").onclick = toggleFullscreen;
    $("btn-mute").onclick = () => command("set_voice", { muted: !(state.snap && state.snap.settings.voice_muted) });
    $("boot").onclick = finishBoot;

    document.querySelectorAll("#seg-side button").forEach((b) => b.onclick = () => command("set_robot_side", { side: b.dataset.side }));
    document.querySelectorAll("#seg-arm button").forEach((b) => b.onclick = () => command("set_arm", { mode: b.dataset.arm }));
    $("btn-camera-apply").onclick = () => command("set_camera", { index: parseInt($("in-camera").value, 10) || 0 });
    $("btn-reset").onclick = () => command("reset_detection");
    $("btn-rotate").onclick = () => command("rotate_mapping");
    $("btn-flip").onclick = () => command("flip_view");
    $("tg-muted").onchange = (e) => command("set_voice", { muted: e.target.checked });
    $("tg-announce").onchange = (e) => command("set_voice", { announce_human_moves: e.target.checked });
    $("in-idle").oninput = (e) => { $("in-idle-v").textContent = e.target.value > 0 ? `${e.target.value} s` : "off"; };
    $("in-idle").onchange = (e) => command("set_voice", { idle_prompt_s: parseFloat(e.target.value) });
    $("btn-say").onclick = () => { const t = $("in-say").value.trim(); if (t) { command("say", { text: t }); $("in-say").value = ""; } };
    $("in-say").onkeydown = (e) => { if (e.key === "Enter") $("btn-say").click(); };
    $("tg-arm-auto").onchange = (e) => command("set_arm", { auto_execute: e.target.checked });
    $("tg-arm-home").onchange = (e) => command("set_arm", { auto_home: e.target.checked });
    $("btn-arm-apply").onclick = () => command("set_arm", {
      port: parseInt($("in-arm-port").value, 10) || 5555, positions_path: $("in-positions").value.trim() });
    $("btn-arm-execute").onclick = () => command("arm_execute");
    $("btn-arm-stop").onclick = () => command("arm_stop");
    $("btn-arm-home").onclick = () => command("arm_home");
    $("btn-start-2").onclick = () => { command("start_game"); openDrawer(false); };
    $("btn-stop").onclick = () => { command("stop_game"); openDrawer(false); };
    $("btn-new-game").onclick = () => { $("modal-over").classList.remove("open"); command("start_game"); };
    $("btn-over-close").onclick = () => $("modal-over").classList.remove("open");

    document.addEventListener("keydown", (e) => {
      const tag = (e.target.tagName || "").toLowerCase();
      if (tag === "input" || tag === "textarea") { if (e.key === "Escape") e.target.blur(); return; }
      const k = e.key.toLowerCase();
      if (k === "escape") {
        if ($("drawer").classList.contains("open")) openDrawer(false);
        else if ($("modal-over").classList.contains("open")) $("modal-over").classList.remove("open");
        else command("arm_stop");
        return;
      }
      const map = { g: "start_game", o: "stop_game", f: "flip_view", t: "rotate_mapping", r: "reset_detection", e: "arm_execute" };
      if (map[k]) { command(map[k]); return; }
      if (k === "m") $("btn-mute").click();
      else if (k === "s") openDrawer(!$("drawer").classList.contains("open"));
      else if (k === "k") toggleFullscreen();
    });

    if (state.kiosk) {
      document.body.classList.add("kiosk");
      let idleTimer = null;
      const wake = () => { document.body.classList.remove("idle"); clearTimeout(idleTimer); idleTimer = setTimeout(() => document.body.classList.add("idle"), 3000); };
      document.addEventListener("mousemove", wake); wake();
      document.addEventListener("click", () => { if (!document.fullscreenElement) document.documentElement.requestFullscreen?.(); }, { once: true });
    }
    // El stream MJPEG se recupera solo si el servidor se reinicia.
    $("camera").onerror = () => setTimeout(() => { $("camera").src = "/stream/camera.mjpg?" + Date.now(); }, 1500);
    setInterval(tickClock, 500);
  }

  buildBoard();
  wire();
  connect();
})();
