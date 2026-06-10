import asyncio, json, random, os
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
import chess
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.ext import Application, CommandHandler, ContextTypes
import uvicorn

TOKEN   = "8878624804:AAFeeJHAigv5J1nB4nFmH8YsySFsoaqUUIw"
# BotHost даёт домен вида bot1234.bothost.tech — вставь свой
WEBAPP_URL = "https://chesssa.bothost.tech"

# ── Хранилище игр ─────────────────────────────────────────────────────────────

games = {}   # game_id -> dict
sockets = {} # game_id -> {white: ws, black: ws}

def new_id():
    while True:
        gid = str(random.randint(1000, 9999))
        if gid not in games:
            return gid

def game_state(gid):
    """Сериализует состояние игры для отправки клиенту."""
    g = games[gid]
    b = g["board"]
    pieces = {}
    for sq in chess.SQUARES:
        p = b.piece_at(sq)
        if p:
            pieces[sq] = {"type": p.piece_type, "color": int(p.color)}
    return {
        "type":       "state",
        "game_id":    gid,
        "pieces":     pieces,
        "turn":       int(b.turn),
        "white_name": g["white_name"],
        "black_name": g.get("black_name") or "Ожидание...",
        "in_check":   b.is_check(),
        "legal_moves": [[m.from_square, m.to_square] for m in b.legal_moves],
        "status":     g.get("status", "playing"),
        "result":     g.get("result", ""),
        "last_move":  [b.peek().from_square, b.peek().to_square] if b.move_stack else None,
        "ai":         g.get("ai", False),
    }

def check_end(board):
    if board.is_checkmate():
        w = "Белые" if board.turn == chess.BLACK else "Чёрные"
        return True, f"Мат! Победили {w} 🏆"
    if board.is_stalemate():              return True, "Пат — ничья 🤝"
    if board.is_insufficient_material(): return True, "Нет материала — ничья 🤝"
    if board.is_seventyfive_moves():      return True, "75 ходов — ничья 🤝"
    return False, ""

async def broadcast(gid, data):
    """Отправляет сообщение обоим игрокам."""
    msg = json.dumps(data, ensure_ascii=False)
    for ws in (sockets.get(gid) or {}).values():
        try:
            await ws.send_text(msg)
        except Exception:
            pass

async def ai_move_task(gid):
    await asyncio.sleep(0.8)
    g = games.get(gid)
    if not g or not g.get("ai"): return
    board = g["board"]
    if board.turn != chess.BLACK: return
    moves = list(board.legal_moves)
    if not moves: return
    captures = [m for m in moves if board.is_capture(m)]
    checks   = [m for m in moves if board.gives_check(m)]
    board.push(random.choice(captures or checks or moves))
    ended, result = check_end(board)
    if ended:
        g["status"] = "ended"
        g["result"] = result
    await broadcast(gid, game_state(gid))

# ── FastAPI ───────────────────────────────────────────────────────────────────

app = FastAPI()

HTML_PAGE = '<!DOCTYPE html>\n<html lang="ru">\n<head>\n<meta charset="UTF-8">\n<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">\n<title>Шахматы</title>\n<script src="https://telegram.org/js/telegram-web-app.js"></script>\n<style>\n  * { margin:0; padding:0; box-sizing:border-box; -webkit-tap-highlight-color:transparent; }\n  body {\n    background: #1a1a2e;\n    color: #fff;\n    font-family: -apple-system, sans-serif;\n    display: flex;\n    flex-direction: column;\n    align-items: center;\n    min-height: 100vh;\n    padding: 8px;\n    gap: 8px;\n  }\n  #status-bar {\n    width: 100%;\n    max-width: 420px;\n    background: #2a2a3e;\n    border-radius: 12px;\n    padding: 10px 14px;\n    display: flex;\n    justify-content: space-between;\n    align-items: center;\n    font-size: 13px;\n  }\n  .player { display: flex; align-items: center; gap: 6px; }\n  .player-name { font-weight: 600; max-width: 110px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }\n  .dot { width: 9px; height: 9px; border-radius: 50%; flex-shrink: 0; }\n  .dot.white { background: #fff; border: 1px solid #aaa; }\n  .dot.black { background: #333; border: 1px solid #777; }\n  .dot.active { box-shadow: 0 0 0 2px #4ade80; }\n  #turn-label {\n    font-size: 11px;\n    background: #3b82f6;\n    padding: 3px 10px;\n    border-radius: 20px;\n    font-weight: 700;\n    white-space: nowrap;\n  }\n  #turn-label.check { background: #ef4444; }\n  #canvas-wrap {\n    width: min(98vw, 420px);\n    height: min(98vw, 420px);\n  }\n  #board {\n    display: block;\n    width: 100%;\n    height: 100%;\n    border-radius: 6px;\n    touch-action: none;\n  }\n  #btns {\n    display: flex;\n    gap: 8px;\n    width: 100%;\n    max-width: 420px;\n  }\n  button {\n    flex: 1;\n    padding: 11px;\n    border: none;\n    border-radius: 10px;\n    font-size: 14px;\n    font-weight: 600;\n    cursor: pointer;\n    background: #2a2a3e;\n    color: #fff;\n  }\n  button:active { opacity: 0.7; }\n  #btn-resign { background: #dc2626; }\n  #debug {\n    width: 100%;\n    max-width: 420px;\n    background: #111;\n    color: #4ade80;\n    font-size: 11px;\n    font-family: monospace;\n    padding: 6px 10px;\n    border-radius: 8px;\n    min-height: 36px;\n    word-break: break-all;\n  }\n  #overlay {\n    display: none;\n    position: fixed; inset: 0;\n    background: rgba(0,0,0,0.8);\n    z-index: 99;\n    justify-content: center;\n    align-items: center;\n  }\n  #overlay.show { display: flex; }\n  #overlay-box {\n    background: #1e1e2e;\n    border-radius: 20px;\n    padding: 32px 28px;\n    text-align: center;\n    max-width: 280px;\n  }\n  #overlay-box h2 { font-size: 22px; margin-bottom: 8px; }\n  #overlay-box p { color: #94a3b8; font-size: 14px; }\n</style>\n</head>\n<body>\n\n<div id="status-bar">\n  <div class="player">\n    <div class="dot white" id="dot-white"></div>\n    <span class="player-name" id="name-white">—</span>\n  </div>\n  <span id="turn-label">⏳</span>\n  <div class="player">\n    <div class="dot black" id="dot-black"></div>\n    <span class="player-name" id="name-black">—</span>\n  </div>\n</div>\n\n<div id="canvas-wrap">\n  <canvas id="board"></canvas>\n</div>\n\n<div id="btns">\n  <button id="btn-resign">🏳 Сдаться</button>\n</div>\n\n<div id="debug">Подключение...</div>\n\n<div id="overlay">\n  <div id="overlay-box">\n    <h2 id="overlay-title">—</h2>\n    <p id="overlay-sub">Игра завершена</p>\n  </div>\n</div>\n\n<script>\n// ── Telegram WebApp ───────────────────────────────────────────────────────────\nconst tg = window.Telegram && window.Telegram.WebApp;\nif (tg) { tg.expand(); tg.enableClosingConfirmation(); }\n\n// ── Параметры ─────────────────────────────────────────────────────────────────\nconst params   = new URLSearchParams(location.search);\nconst GAME_ID  = params.get("game");\nconst MY_COLOR = params.get("color") || "white";  // "white" | "black"\nconst MY_CINT  = MY_COLOR === "white" ? 1 : 0;\nconst FLIPPED  = MY_COLOR === "black";\n\nfunction dbg(msg) {\n  document.getElementById("debug").textContent = msg;\n  console.log("[chess]", msg);\n}\n\n// ── Canvas setup ──────────────────────────────────────────────────────────────\nconst canvas = document.getElementById("board");\nconst ctx2   = canvas.getContext("2d");\n\nfunction resizeCanvas() {\n  const wrap = document.getElementById("canvas-wrap");\n  const sz   = wrap.offsetWidth;\n  canvas.width  = sz;\n  canvas.height = sz;\n  render();\n}\nwindow.addEventListener("resize", resizeCanvas);\n\n// ── Состояние игры ────────────────────────────────────────────────────────────\nlet state = {\n  pieces: {},\n  turn: 1,\n  legal_moves: [],\n  in_check: false,\n  last_move: null,\n  white_name: "—",\n  black_name: "—",\n  status: "waiting",\n  result: ""\n};\nlet selected    = null;\nlet hintSquares = [];\nlet gameOver    = false;\n\n// ── Рендер ────────────────────────────────────────────────────────────────────\nconst C_LIGHT    = "#f0d9b5";\nconst C_DARK     = "#b58863";\nconst C_SEL      = "#7fc97f";\nconst C_LASTMOVE = "#cdd16f";\nconst C_CHECK    = "#e05050";\n\nconst GLYPHS = {\n  "1_6":"♔","1_5":"♕","1_4":"♖","1_3":"♗","1_2":"♘","1_1":"♙",\n  "0_6":"♚","0_5":"♛","0_4":"♜","0_3":"♝","0_2":"♞","0_1":"♟",\n};\n\nfunction sqToXY(sq) {\n  const file = sq % 8;\n  const rank = Math.floor(sq / 8);\n  const df   = FLIPPED ? (7 - file) : file;\n  const dr   = FLIPPED ? rank : (7 - rank);\n  const cell = canvas.width / 8;\n  return { x: df * cell, y: dr * cell, cell };\n}\n\nfunction xyToSq(px, py) {\n  const cell = canvas.width / 8;\n  const file = FLIPPED ? (7 - Math.floor(px / cell)) : Math.floor(px / cell);\n  const rank = FLIPPED ? Math.floor(py / cell) : (7 - Math.floor(py / cell));\n  if (file < 0 || file > 7 || rank < 0 || rank > 7) return -1;\n  return rank * 8 + file;\n}\n\nfunction render() {\n  const sz   = canvas.width;\n  const cell = sz / 8;\n\n  ctx2.clearRect(0, 0, sz, sz);\n\n  // Клетки\n  for (let rank = 0; rank < 8; rank++) {\n    for (let file = 0; file < 8; file++) {\n      const sq = rank * 8 + file;\n      const {x, y} = sqToXY(sq);\n      const light = (file + rank) % 2 === 0;\n\n      let bg = light ? C_LIGHT : C_DARK;\n      if (state.last_move && (state.last_move[0] === sq || state.last_move[1] === sq))\n        bg = C_LASTMOVE;\n      const p = state.pieces[sq];\n      if (state.in_check && p && p.type === 6 && p.color === state.turn)\n        bg = C_CHECK;\n      if (sq === selected)\n        bg = C_SEL;\n\n      ctx2.fillStyle = bg;\n      ctx2.fillRect(x, y, cell, cell);\n\n      // Подсветка ходов\n      if (hintSquares.includes(sq)) {\n        if (state.pieces[sq]) {\n          ctx2.fillStyle = "rgba(220,80,80,0.5)";\n          ctx2.fillRect(x, y, cell, cell);\n        } else {\n          ctx2.fillStyle = "rgba(70,160,255,0.6)";\n          ctx2.beginPath();\n          ctx2.arc(x + cell/2, y + cell/2, cell * 0.2, 0, Math.PI*2);\n          ctx2.fill();\n        }\n      }\n    }\n  }\n\n  // Координаты\n  const labelSz = Math.max(10, cell * 0.2);\n  ctx2.font = `bold ${labelSz}px sans-serif`;\n  const FILES = FLIPPED ? "hgfedcba" : "abcdefgh";\n  const RANKS = FLIPPED ? "12345678" : "87654321";\n  for (let i = 0; i < 8; i++) {\n    const cell_ = canvas.width / 8;\n    const light_f = (i % 2 === 0);\n    ctx2.fillStyle = light_f ? C_DARK : C_LIGHT;\n    ctx2.fillText(FILES[i], i*cell_ + cell_*0.05, sz - cell_*0.05);\n    ctx2.fillStyle = (i % 2 === 0) ? C_DARK : C_LIGHT;\n    ctx2.fillText(RANKS[i], cell_*0.04, i*cell_ + cell_*0.22);\n  }\n\n  // Фигуры\n  ctx2.textAlign    = "center";\n  ctx2.textBaseline = "middle";\n\n  for (const [sqStr, p] of Object.entries(state.pieces)) {\n    const sq = parseInt(sqStr);\n    const {x, y, cell: c} = sqToXY(sq);\n    const glyph = GLYPHS[`${p.color}_${p.type}`];\n    if (!glyph) continue;\n\n    ctx2.font = `${c * 0.75}px serif`;\n\n    // Тень\n    ctx2.fillStyle = p.color === 1 ? "rgba(0,0,0,0.45)" : "rgba(255,255,255,0.25)";\n    ctx2.fillText(glyph, x + c/2 + 1, y + c/2 + 2);\n    // Фигура\n    ctx2.fillStyle = p.color === 1 ? "#ffffff" : "#111111";\n    ctx2.fillText(glyph, x + c/2, y + c/2);\n  }\n}\n\n// ── Обновление UI ─────────────────────────────────────────────────────────────\nfunction applyState(s) {\n  state = s;\n  document.getElementById("name-white").textContent = s.white_name;\n  document.getElementById("name-black").textContent = s.black_name;\n\n  const tl = document.getElementById("turn-label");\n  if (s.status === "ended") {\n    tl.textContent = "🏁 Конец";\n    tl.className = "";\n  } else {\n    tl.textContent = s.turn === 1 ? "Белые ♙" : "Чёрные ♟";\n    tl.className   = s.in_check ? "check" : "";\n  }\n\n  const dw = document.getElementById("dot-white");\n  const db = document.getElementById("dot-black");\n  dw.className = "dot white" + (s.turn===1 && s.status!=="ended" ? " active" : "");\n  db.className = "dot black" + (s.turn===0 && s.status!=="ended" ? " active" : "");\n\n  selected    = null;\n  hintSquares = [];\n  render();\n\n  const pieceCount = Object.keys(s.pieces).length;\n  dbg(`Статус: ${s.status} | Ход: ${s.turn===1?"белые":"чёрные"} | Фигур: ${pieceCount} | Игра: #${GAME_ID}`);\n\n  if (s.status === "ended" && !gameOver) {\n    gameOver = true;\n    setTimeout(() => {\n      document.getElementById("overlay-title").textContent = s.result || "Игра завершена";\n      document.getElementById("overlay").classList.add("show");\n    }, 600);\n  }\n}\n\n// ── Клики ─────────────────────────────────────────────────────────────────────\ncanvas.addEventListener("click", e => {\n  if (gameOver) return;\n  if (state.turn !== MY_CINT) { dbg("Не твой ход!"); return; }\n\n  const rect = canvas.getBoundingClientRect();\n  const scaleX = canvas.width  / rect.width;\n  const scaleY = canvas.height / rect.height;\n  const px = (e.clientX - rect.left) * scaleX;\n  const py = (e.clientY - rect.top)  * scaleY;\n  const sq = xyToSq(px, py);\n  if (sq < 0) return;\n\n  if (selected === null) {\n    const p = state.pieces[sq];\n    if (p && p.color === MY_CINT) {\n      selected    = sq;\n      hintSquares = state.legal_moves.filter(m => m[0] === sq).map(m => m[1]);\n      render();\n      dbg(`Выбрана фигура на ${sq}, ходов: ${hintSquares.length}`);\n    }\n  } else {\n    if (sq === selected) {\n      selected = null; hintSquares = []; render(); return;\n    }\n    if (hintSquares.includes(sq)) {\n      ws.send(JSON.stringify({ type: "move", from: selected, to: sq }));\n      selected = null; hintSquares = []; render();\n      dbg("Ход отправлен...");\n    } else {\n      const p = state.pieces[sq];\n      if (p && p.color === MY_CINT) {\n        selected    = sq;\n        hintSquares = state.legal_moves.filter(m => m[0] === sq).map(m => m[1]);\n        render();\n      } else {\n        selected = null; hintSquares = []; render();\n      }\n    }\n  }\n});\n\ndocument.getElementById("btn-resign").onclick = () => {\n  if (gameOver) return;\n  if (tg) {\n    tg.showConfirm("Сдаться?", ok => { if (ok) ws.send(JSON.stringify({type:"resign"})); });\n  } else {\n    if (confirm("Сдаться?")) ws.send(JSON.stringify({type:"resign"}));\n  }\n};\n\n// ── WebSocket ─────────────────────────────────────────────────────────────────\nlet ws, reconnTimer;\n\nfunction connect() {\n  const proto = location.protocol === "https:" ? "wss:" : "ws:";\n  const url   = `${proto}//${location.host}/ws/${GAME_ID}/${MY_COLOR}`;\n  dbg("WS: " + url);\n  ws = new WebSocket(url);\n\n  ws.onopen = () => { dbg("✅ Подключено! Ожидаем данные..."); };\n\n  ws.onmessage = e => {\n    try {\n      const data = JSON.parse(e.data);\n      dbg("← " + data.type + (data.type==="state" ? ` (${Object.keys(data.pieces||{}).length} фигур)` : ""));\n      if (data.type === "state")   { applyState(data); }\n      else if (data.type === "joined")  { dbg("👥 " + data.msg); }\n      else if (data.type === "error")   { dbg("❌ " + data.msg); }\n      else if (data.type === "illegal") { dbg("⚠️ Недопустимый ход"); selected=null; hintSquares=[]; render(); }\n    } catch(err) {\n      dbg("Ошибка парсинга: " + err);\n    }\n  };\n\n  ws.onclose = ev => {\n    dbg(`WS закрыт (${ev.code}). Реконнект...`);\n    if (!gameOver) reconnTimer = setTimeout(connect, 2500);\n  };\n\n  ws.onerror = err => { dbg("WS ошибка"); ws.close(); };\n}\n\n// ── Старт ─────────────────────────────────────────────────────────────────────\nif (!GAME_ID) {\n  document.body.innerHTML = `<div style="padding:40px;text-align:center;color:#f87171">\n    ❌ Не указан ID игры.<br><br>Используй /newgame в боте.</div>`;\n} else {\n  // Сначала рисуем пустую доску пока не пришли данные\n  setTimeout(() => {\n    resizeCanvas();\n    // Рисуем пустую доску для проверки\n    const sz = canvas.width;\n    const cell = sz / 8;\n    for (let r = 0; r < 8; r++) {\n      for (let f = 0; f < 8; f++) {\n        ctx2.fillStyle = (f+r)%2===0 ? C_LIGHT : C_DARK;\n        ctx2.fillRect(f*cell, r*cell, cell, cell);\n      }\n    }\n    dbg("Доска нарисована. Подключаемся к WS...");\n    connect();\n  }, 100);\n}\n</script>\n</body>\n</html>\n'

@app.get("/")
async def index():
    return HTMLResponse(HTML_PAGE)

@app.websocket("/ws/{game_id}/{color}")
async def ws_endpoint(websocket: WebSocket, game_id: str, color: str):
    await websocket.accept()

    if game_id not in games:
        await websocket.send_text(json.dumps({"type": "error", "msg": "Игра не найдена"}))
        await websocket.close()
        return

    # Регистрируем сокет
    if game_id not in sockets:
        sockets[game_id] = {}
    sockets[game_id][color] = websocket

    g = games[game_id]

    # Если чёрный подключился — уведомляем
    if color == "black" and g.get("black_name"):
        await broadcast(game_id, {
            "type": "joined",
            "msg": f"👥 {g['black_name']} присоединился!"
        })

    # Шлём текущее состояние
    await websocket.send_text(json.dumps(game_state(game_id), ensure_ascii=False))

    try:
        while True:
            raw = await websocket.receive_text()
            data = json.loads(raw)

            if data["type"] == "move":
                board = g["board"]
                fr = data["from"]
                to = data["to"]

                # Проверка очерёдности
                if board.turn == chess.WHITE and color != "white": continue
                if board.turn == chess.BLACK and color != "black": continue

                # Превращение пешки
                promo = None
                p = board.piece_at(fr)
                if p and p.piece_type == chess.PAWN and chess.square_rank(to) in (0, 7):
                    promo = chess.QUEEN

                move = chess.Move(fr, to, promotion=promo)
                if move not in board.legal_moves:
                    await websocket.send_text(json.dumps({"type": "illegal"}))
                    continue

                board.push(move)
                ended, result = check_end(board)
                if ended:
                    g["status"] = "ended"
                    g["result"] = result

                state = game_state(game_id)
                await broadcast(game_id, state)

                # AI ход
                if g.get("ai") and not ended and board.turn == chess.BLACK:
                    asyncio.create_task(ai_move_task(game_id))

            elif data["type"] == "resign":
                g["status"] = "ended"
                g["result"] = f"🏳 Сдался {'белые' if color=='white' else 'чёрные'}"
                await broadcast(game_id, game_state(game_id))

    except WebSocketDisconnect:
        sockets.get(game_id, {}).pop(color, None)

# ── Telegram бот ──────────────────────────────────────────────────────────────

async def cmd_start(u: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await u.message.reply_html(
        "♟ <b>Шахматы</b>\n\n"
        "/newgame — создать партию (ты белые)\n"
        "/join &lt;ID&gt; — присоединиться к другу\n"
        "/ai — играть против бота\n"
    )

async def cmd_newgame(u: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = u.effective_user
    gid  = new_id()
    games[gid] = dict(
        board=chess.Board(),
        white_id=user.id, white_name=user.first_name,
        black_id=None, black_name=None, ai=False,
        status="waiting"
    )
    url = f"{WEBAPP_URL}?game={gid}&color=white"
    kb  = InlineKeyboardMarkup([[
        InlineKeyboardButton("♟ Открыть доску", web_app=WebAppInfo(url=url))
    ],[
        InlineKeyboardButton(f"🔗 Пригласить (ID: {gid})", callback_data=f"noop")
    ]])
    await u.message.reply_html(
        f"✅ Партия <b>#{gid}</b> создана!\n"
        f"Отправь другу: <code>/join {gid}</code>\n"
        f"Или поделись ссылкой: <code>{WEBAPP_URL}?game={gid}&color=black</code>",
        reply_markup=kb
    )

async def cmd_join(u: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = u.effective_user
    if not ctx.args:
        await u.message.reply_text("Использование: /join 1234"); return
    gid = ctx.args[0]
    if gid not in games:
        await u.message.reply_text(f"❌ Партия #{gid} не найдена."); return
    g = games[gid]
    if g["white_id"] == user.id:
        await u.message.reply_text("Ты уже в этой партии (белые)."); return
    if g["black_id"] not in (None, user.id):
        await u.message.reply_text("❌ Партия уже полная."); return
    g["black_id"]   = user.id
    g["black_name"] = user.first_name
    g["status"]     = "playing"
    url = f"{WEBAPP_URL}?game={gid}&color=black"
    kb  = InlineKeyboardMarkup([[
        InlineKeyboardButton("♟ Открыть доску", web_app=WebAppInfo(url=url))
    ]])
    await u.message.reply_html(
        f"✅ Ты в партии <b>#{gid}</b>! Играешь чёрными ♟",
        reply_markup=kb
    )
    # Уведомляем белых
    url_w = f"{WEBAPP_URL}?game={gid}&color=white"
    kb_w  = InlineKeyboardMarkup([[
        InlineKeyboardButton("♟ Открыть доску", web_app=WebAppInfo(url=url_w))
    ]])
    await ctx.bot.send_message(
        g["white_id"],
        f"👥 {user.first_name} присоединился — игра началась!",
        reply_markup=kb_w
    )

async def cmd_ai(u: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = u.effective_user
    gid  = new_id()
    games[gid] = dict(
        board=chess.Board(),
        white_id=user.id, white_name=user.first_name,
        black_id=-1, black_name="🤖 AI", ai=True,
        status="playing"
    )
    url = f"{WEBAPP_URL}?game={gid}&color=white"
    kb  = InlineKeyboardMarkup([[
        InlineKeyboardButton("♟ Открыть доску", web_app=WebAppInfo(url=url))
    ]])
    await u.message.reply_html(
        "🤖 Играешь против AI! Ты белые ♙",
        reply_markup=kb
    )

# ── Запуск ────────────────────────────────────────────────────────────────────


async def main():
    # Запускаем бота и веб-сервер параллельно
    bot_app = Application.builder().token(TOKEN).build()
    for cmd, fn in [("start", cmd_start), ("newgame", cmd_newgame),
                    ("join", cmd_join), ("ai", cmd_ai)]:
        bot_app.add_handler(CommandHandler(cmd, fn))
    await bot_app.initialize()
    await bot_app.start()
    await bot_app.updater.start_polling(drop_pending_updates=True)

    # Uvicorn на порту 8080 (BotHost)
    config = uvicorn.Config(app, host="0.0.0.0", port=8080, log_level="info")
    server = uvicorn.Server(config)
    await server.serve()

    await bot_app.updater.stop()
    await bot_app.stop()

if __name__ == "__main__":
    asyncio.run(main())
