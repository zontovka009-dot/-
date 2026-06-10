import asyncio, json, random, os
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
import chess
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.ext import Application, CommandHandler, ContextTypes
import uvicorn

TOKEN   = "ВАШ_ТОКЕН_СЮДА"
# BotHost даёт домен вида bot1234.bothost.tech — вставь свой
WEBAPP_URL = "https://ВАШ_ДОМЕН_СЮДА"

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

@app.get("/")
async def index():
    return FileResponse("index.html")

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

async def run_bot():
    bot_app = Application.builder().token(TOKEN).build()
    for cmd, fn in [("start", cmd_start), ("newgame", cmd_newgame),
                    ("join", cmd_join), ("ai", cmd_ai)]:
        bot_app.add_handler(CommandHandler(cmd, fn))
    await bot_app.initialize()
    await bot_app.start()
    await bot_app.updater.start_polling()

async def main():
    await run_bot()
    config = uvicorn.Config(app, host="0.0.0.0", port=8000, log_level="warning")
    server = uvicorn.Server(config)
    await server.serve()

if __name__ == "__main__":
    asyncio.run(main())
