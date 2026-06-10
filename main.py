import asyncio, io, random
import chess, chess.svg, cairosvg
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

TOKEN = "ВАШ_ТОКЕН_СЮДА"

games = {}  # game_id -> game dict

def new_game_id():
    while True:
        gid = str(random.randint(1000, 9999))
        if gid not in games:
            return gid

def board_to_png(board, selected=None, hints=None, last_move=None) -> bytes:
    fill = {}
    if last_move:
        fill[last_move.from_square] = "#cdd16f"
        fill[last_move.to_square]   = "#cdd16f"
    if selected is not None:
        fill[selected] = "#7fc97f"
    if hints:
        for sq in hints:
            fill[sq] = "#4a9eff" if board.piece_at(sq) is None else "#e05252"
    svg = chess.svg.board(board, fill=fill, size=400,
                          lastmove=last_move,
                          check=board.king(board.turn) if board.is_check() else None)
    return cairosvg.svg2png(bytestring=svg.encode())

def legal_targets(board, sq):
    return [m.to_square for m in board.legal_moves if m.from_square == sq]

PIECE_SYMBOLS = {
    (chess.KING,   True):  "♔", (chess.QUEEN,  True):  "♕",
    (chess.ROOK,   True):  "♖", (chess.BISHOP, True):  "♗",
    (chess.KNIGHT, True):  "♘", (chess.PAWN,   True):  "♙",
    (chess.KING,   False): "♚", (chess.QUEEN,  False): "♛",
    (chess.ROOK,   False): "♜", (chess.BISHOP, False): "♝",
    (chess.KNIGHT, False): "♞", (chess.PAWN,   False): "♟",
}

def board_keyboard(gid):
    g = games[gid]
    board    = g["board"]
    selected = g.get("selected")
    hints    = g.get("hints", [])
    rows = []
    for rank in range(7, -1, -1):
        row = []
        for file in range(8):
            sq = chess.square(file, rank)
            p  = board.piece_at(sq)
            if p:
                sym = PIECE_SYMBOLS[(p.piece_type, p.color)]
            else:
                sym = "·"
            if sq == selected:
                sym = f"[{sym}]"
            elif sq in hints:
                sym = f"({sym})" if p else "○"
            row.append(InlineKeyboardButton(sym, callback_data=f"sq_{gid}_{sq}"))
        rows.append(row)
    rows.append([
        InlineKeyboardButton("🏳 Сдаться", callback_data=f"resign_{gid}"),
        InlineKeyboardButton("🤝 Ничья",   callback_data=f"draw_{gid}"),
    ])
    return InlineKeyboardMarkup(rows)

async def push_board(ctx, chat_id, gid, caption_extra=""):
    g = games[gid]
    board = g["board"]
    lm = board.peek() if board.move_stack else None
    png = board_to_png(board, g.get("selected"), g.get("hints", []), lm)
    turn = "Белые ♙" if board.turn == chess.WHITE else "Чёрные ♟"
    cap  = f"Игра #{gid}  |  {g['white_name']} vs {g['black_name'] or '???'}\nХодят: {turn}"
    if board.is_check(): cap += "  ⚠️ ШАХ"
    if caption_extra:    cap += f"\n{caption_extra}"
    await ctx.bot.send_photo(chat_id, io.BytesIO(png), caption=cap,
                             reply_markup=board_keyboard(gid))

def game_over(board):
    if board.is_checkmate():
        w = "Белые" if board.turn == chess.BLACK else "Чёрные"
        return True, f"Мат! Победили {w} 🏆"
    if board.is_stalemate():         return True, "Пат — ничья 🤝"
    if board.is_insufficient_material(): return True, "Нет материала — ничья 🤝"
    if board.is_seventyfive_moves(): return True, "75 ходов — ничья 🤝"
    return False, ""

async def finish(ctx, gid, result_text):
    g = games.pop(gid, None)
    if not g: return
    lm  = g["board"].peek() if g["board"].move_stack else None
    png = board_to_png(g["board"], last_move=lm)
    for pid in [g["white_id"], g["black_id"]]:
        if pid and pid > 0:
            await ctx.bot.send_photo(pid, io.BytesIO(png), caption=f"🏁 {result_text}")

# ── команды ──────────────────────────────────────────────────────────────────

async def cmd_start(u: Update, ctx):
    await u.message.reply_html(
        "♟ <b>Шахматы</b>\n\n"
        "/newgame — создать партию (ты белые)\n"
        "/join &lt;ID&gt; — присоединиться\n"
        "/ai — играть против бота\n"
        "/mygame — показать доску\n"
    )

async def cmd_newgame(u: Update, ctx):
    user = u.effective_user
    gid  = new_game_id()
    games[gid] = dict(board=chess.Board(),
                      white_id=user.id, white_name=user.first_name,
                      black_id=None, black_name=None, ai=False,
                      selected=None, hints=[])
    ctx.user_data["game_id"] = gid
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton(f"🎮 Войти в игру #{gid}", callback_data=f"join_{gid}")
    ]])
    await u.message.reply_html(
        f"✅ Партия <b>#{gid}</b> создана! Ты белые ♙\n"
        f"Отправь другу: <code>/join {gid}</code>", reply_markup=kb)

async def cmd_join(u: Update, ctx):
    if not ctx.args:
        await u.message.reply_text("Использование: /join 1234")
        return
    await do_join(u, ctx, u.effective_user, ctx.args[0])

async def cmd_ai(u: Update, ctx):
    user = u.effective_user
    gid  = new_game_id()
    games[gid] = dict(board=chess.Board(),
                      white_id=user.id, white_name=user.first_name,
                      black_id=-1, black_name="🤖 AI", ai=True,
                      selected=None, hints=[])
    ctx.user_data["game_id"] = gid
    await u.message.reply_text("🤖 Играешь против AI! Ты белые ♙")
    await push_board(ctx, user.id, gid)

async def cmd_mygame(u: Update, ctx):
    gid = ctx.user_data.get("game_id")
    if not gid or gid not in games:
        await u.message.reply_text("Нет активной партии. /newgame или /ai")
        return
    await push_board(ctx, u.effective_user.id, gid)

async def do_join(source, ctx, user, gid):
    async def say(t): await source.message.reply_text(t)
    if gid not in games:      return await say(f"❌ Партия #{gid} не найдена.")
    g = games[gid]
    if g["white_id"] == user.id: return await say("Ты уже в этой партии (белые).")
    if g["black_id"] not in (None, user.id): return await say("❌ Партия уже полная.")
    g["black_id"]   = user.id
    g["black_name"] = user.first_name
    ctx.user_data["game_id"] = gid
    await say(f"✅ Ты в партии #{gid}! Играешь чёрными ♟")
    await ctx.bot.send_message(g["white_id"], f"👥 {user.first_name} вступил — игра началась!")
    await push_board(ctx, g["white_id"], gid)
    await push_board(ctx, user.id, gid)

# ── обработчик кнопок ────────────────────────────────────────────────────────

async def on_button(u: Update, ctx):
    q    = u.callback_query
    await q.answer()
    data = q.data
    user = q.from_user

    if data.startswith("join_"):
        await do_join(q, ctx, user, data[5:])
        return

    if data.startswith("resign_"):
        gid = data[7:]
        if gid in games:
            await finish(ctx, gid, f"🏳 {user.first_name} сдался.")
        return

    if data.startswith("draw_"):
        gid = data[5:]
        if gid in games:
            await finish(ctx, gid, "🤝 Ничья по соглашению.")
        return

    if data.startswith("sq_"):
        _, gid, sq_str = data.split("_")
        sq = int(sq_str)
        if gid not in games:
            await q.message.reply_text("Партия не найдена.")
            return
        g = games[gid]
        board = g["board"]

        # Проверяем очерёдность хода
        if board.turn == chess.WHITE and user.id != g["white_id"]:
            await q.answer("Сейчас ходят белые!", show_alert=True); return
        if board.turn == chess.BLACK and user.id != g["black_id"]:
            await q.answer("Сейчас ходят чёрные!", show_alert=True); return

        selected = g.get("selected")
        hints    = g.get("hints", [])

        if selected is None:
            p = board.piece_at(sq)
            if p and p.color == board.turn:
                g["selected"] = sq
                g["hints"]    = legal_targets(board, sq)
                await push_board(ctx, user.id, gid, "Выбери куда ходить 👆")
            else:
                await q.answer("Выбери свою фигуру!", show_alert=False)
            return

        # Уже выбрана фигура
        if sq == selected:                          # снять выбор
            g["selected"] = None; g["hints"] = []
            await push_board(ctx, user.id, gid)
            return

        if sq in hints:                             # делаем ход
            promo = None
            p = board.piece_at(selected)
            if p and p.piece_type == chess.PAWN and chess.square_rank(sq) in (0, 7):
                promo = chess.QUEEN
            move = chess.Move(selected, sq, promotion=promo)
            g["selected"] = None; g["hints"] = []
            board.push(move)

            ended, txt = game_over(board)
            if ended:
                await finish(ctx, gid, txt); return

            opp = g["black_id"] if user.id == g["white_id"] else g["white_id"]
            await push_board(ctx, user.id, gid, "✅ Ход сделан")
            if opp and opp > 0:
                await push_board(ctx, opp, gid, "👆 Твой ход!")

            if g["ai"] and board.turn == chess.BLACK:
                await asyncio.sleep(1)
                await ai_move(ctx, gid)
            return

        # Кликнули на другую свою фигуру — переключить
        p = board.piece_at(sq)
        if p and p.color == board.turn:
            g["selected"] = sq
            g["hints"]    = legal_targets(board, sq)
            await push_board(ctx, user.id, gid, "Выбери куда ходить 👆")
        else:
            g["selected"] = None; g["hints"] = []
            await push_board(ctx, user.id, gid)

async def ai_move(ctx, gid):
    g = games.get(gid)
    if not g: return
    board = g["board"]
    moves = list(board.legal_moves)
    if not moves: return
    captures = [m for m in moves if board.is_capture(m)]
    checks   = [m for m in moves if board.gives_check(m)]
    move = random.choice(captures or checks or moves)
    board.push(move)
    ended, txt = game_over(board)
    if ended:
        await finish(ctx, gid, txt); return
    await push_board(ctx, g["white_id"], gid, "🤖 AI сходил. Твой ход!")

# ── запуск ───────────────────────────────────────────────────────────────────

def main():
    app = Application.builder().token(TOKEN).build()
    for cmd, fn in [("start", cmd_start), ("newgame", cmd_newgame),
                    ("join",  cmd_join),   ("ai",      cmd_ai),
                    ("mygame",cmd_mygame)]:
        app.add_handler(CommandHandler(cmd, fn))
    app.add_handler(CallbackQueryHandler(on_button))
    app.run_polling()

if __name__ == "__main__":
    main()
