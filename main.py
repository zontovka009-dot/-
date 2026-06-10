import asyncio, io, random
import chess
from PIL import Image, ImageDraw, ImageFont
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

TOKEN = "8878624804:AAFeeJHAigv5J1nB4nFmH8YsySFsoaqUUIw"

games = {}

def new_game_id():
    while True:
        gid = str(random.randint(1000, 9999))
        if gid not in games:
            return gid

# ── Рендер доски через PIL ────────────────────────────────────────────────────

PIECE_UNICODE = {
    (chess.KING,   True):  "♔", (chess.QUEEN,  True):  "♕",
    (chess.ROOK,   True):  "♖", (chess.BISHOP, True):  "♗",
    (chess.KNIGHT, True):  "♘", (chess.PAWN,   True):  "♙",
    (chess.KING,   False): "♚", (chess.QUEEN,  False): "♛",
    (chess.ROOK,   False): "♜", (chess.BISHOP, False): "♝",
    (chess.KNIGHT, False): "♞", (chess.PAWN,   False): "♟",
}

# ASCII fallback если юникод-шрифт недоступен
PIECE_ASCII = {
    (chess.KING,   True):  "K", (chess.QUEEN,  True):  "Q",
    (chess.ROOK,   True):  "R", (chess.BISHOP, True):  "B",
    (chess.KNIGHT, True):  "N", (chess.PAWN,   True):  "P",
    (chess.KING,   False): "k", (chess.QUEEN,  False): "q",
    (chess.ROOK,   False): "r", (chess.BISHOP, False): "b",
    (chess.KNIGHT, False): "n", (chess.PAWN,   False): "p",
}

COLOR_LIGHT    = (240, 217, 181)
COLOR_DARK     = (181, 136, 99)
COLOR_SELECTED = (130, 210, 130)
COLOR_HINT     = (100, 170, 255)
COLOR_CAPTURE  = (220, 90,  90)
COLOR_LASTMOVE = (205, 209, 111)
COLOR_CHECK    = (220, 50,  50)
COLOR_WHITE_P  = (255, 255, 255)
COLOR_BLACK_P  = (20,  20,  20)
COLOR_LABEL    = (100, 80,  60)

CELL = 60
MARGIN = 24
SIZE = CELL * 8 + MARGIN * 2

def board_to_png(board, selected=None, hints=None, last_move=None) -> bytes:
    img  = Image.new("RGB", (SIZE, SIZE), (40, 30, 20))
    draw = ImageDraw.Draw(img)

    hints = hints or []
    lm_squares = []
    if last_move:
        lm_squares = [last_move.from_square, last_move.to_square]

    check_sq = None
    if board.is_check():
        check_sq = board.king(board.turn)

    # Пробуем загрузить шрифт
    try:
        font  = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 36)
        small = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 14)
    except:
        font  = ImageFont.load_default()
        small = font

    for rank in range(7, -1, -1):
        for file in range(8):
            sq = chess.square(file, rank)
            x  = MARGIN + file * CELL
            y  = MARGIN + (7 - rank) * CELL

            # Цвет клетки
            base = COLOR_LIGHT if (file + rank) % 2 == 1 else COLOR_DARK
            if sq == check_sq:          color = COLOR_CHECK
            elif sq == selected:         color = COLOR_SELECTED
            elif sq in lm_squares:       color = COLOR_LASTMOVE
            elif sq in hints:
                color = COLOR_CAPTURE if board.piece_at(sq) else COLOR_HINT
            else:
                color = base

            draw.rectangle([x, y, x+CELL-1, y+CELL-1], fill=color)

            # Точка-подсказка (если пустая клетка и в hints)
            if sq in hints and not board.piece_at(sq):
                cx, cy = x + CELL//2, y + CELL//2
                r = 8
                draw.ellipse([cx-r, cy-r, cx+r, cy+r], fill=(60, 120, 200))

            # Фигура
            p = board.piece_at(sq)
            if p:
                sym  = PIECE_UNICODE.get((p.piece_type, p.color), "?")
                fc   = COLOR_WHITE_P if p.color == chess.WHITE else COLOR_BLACK_P
                # Тень
                draw.text((x + CELL//2 + 1, y + CELL//2 + 2), sym,
                          fill=(0,0,0,120), font=font, anchor="mm")
                draw.text((x + CELL//2, y + CELL//2), sym,
                          fill=fc, font=font, anchor="mm")

    # Координаты по краям
    files_label = "abcdefgh"
    for i in range(8):
        # снизу
        draw.text((MARGIN + i*CELL + CELL//2, SIZE - MARGIN//2),
                  files_label[i], fill=COLOR_LABEL, font=small, anchor="mm")
        # слева
        draw.text((MARGIN//2, MARGIN + (7-i)*CELL + CELL//2),
                  str(i+1), fill=COLOR_LABEL, font=small, anchor="mm")

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf.read()

# ── Клавиатура ────────────────────────────────────────────────────────────────

PIECE_KB = {
    (chess.KING,   True):  "♔", (chess.QUEEN,  True):  "♕",
    (chess.ROOK,   True):  "♖", (chess.BISHOP, True):  "♗",
    (chess.KNIGHT, True):  "♘", (chess.PAWN,   True):  "♙",
    (chess.KING,   False): "♚", (chess.QUEEN,  False): "♛",
    (chess.ROOK,   False): "♜", (chess.BISHOP, False): "♝",
    (chess.KNIGHT, False): "♞", (chess.PAWN,   False): "♟",
}

def board_keyboard(gid):
    g     = games[gid]
    board = g["board"]
    sel   = g.get("selected")
    hints = g.get("hints", [])
    rows  = []
    for rank in range(7, -1, -1):
        row = []
        for file in range(8):
            sq = chess.square(file, rank)
            p  = board.piece_at(sq)
            sym = PIECE_KB.get((p.piece_type, p.color), "?") if p else "·"
            if sq == sel:        sym = f"[{sym}]"
            elif sq in hints:    sym = f"({sym})" if p else "○"
            row.append(InlineKeyboardButton(sym, callback_data=f"sq_{gid}_{sq}"))
        rows.append(row)
    rows.append([
        InlineKeyboardButton("🏳 Сдаться", callback_data=f"resign_{gid}"),
        InlineKeyboardButton("🤝 Ничья",   callback_data=f"draw_{gid}"),
    ])
    return InlineKeyboardMarkup(rows)

async def push_board(ctx, chat_id, gid, extra=""):
    g     = games[gid]
    board = g["board"]
    lm    = board.peek() if board.move_stack else None
    png   = board_to_png(board, g.get("selected"), g.get("hints", []), lm)
    turn  = "Белые ♙" if board.turn == chess.WHITE else "Чёрные ♟"
    cap   = f"Игра #{gid}  |  {g['white_name']} vs {g['black_name'] or '???'}\nХодят: {turn}"
    if board.is_check(): cap += "  ⚠️ ШАХ"
    if extra:            cap += f"\n{extra}"
    await ctx.bot.send_photo(chat_id, io.BytesIO(png), caption=cap,
                             reply_markup=board_keyboard(gid))

def game_over(board):
    if board.is_checkmate():
        w = "Белые" if board.turn == chess.BLACK else "Чёрные"
        return True, f"Мат! Победили {w} 🏆"
    if board.is_stalemate():              return True, "Пат — ничья 🤝"
    if board.is_insufficient_material(): return True, "Нет материала — ничья 🤝"
    if board.is_seventyfive_moves():      return True, "75 ходов — ничья 🤝"
    return False, ""

async def finish(ctx, gid, txt):
    g = games.pop(gid, None)
    if not g: return
    lm  = g["board"].peek() if g["board"].move_stack else None
    png = board_to_png(g["board"], last_move=lm)
    for pid in [g["white_id"], g["black_id"]]:
        if pid and pid > 0:
            await ctx.bot.send_photo(pid, io.BytesIO(png), caption=f"🏁 {txt}")

def legal_targets(board, sq):
    return [m.to_square for m in board.legal_moves if m.from_square == sq]

# ── Команды ───────────────────────────────────────────────────────────────────

async def cmd_start(u: Update, ctx):
    await u.message.reply_html(
        "♟ <b>Шахматы</b>\n\n"
        "/newgame — создать партию (ты белые)\n"
        "/join &lt;ID&gt; — присоединиться к партии друга\n"
        "/ai — играть против бота\n"
        "/mygame — показать текущую доску\n"
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
        await u.message.reply_text("Использование: /join 1234"); return
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
        await u.message.reply_text("Нет активной партии. /newgame или /ai"); return
    await push_board(ctx, u.effective_user.id, gid)

async def do_join(src, ctx, user, gid):
    async def say(t): await src.message.reply_text(t)
    if gid not in games:              return await say(f"❌ Партия #{gid} не найдена.")
    g = games[gid]
    if g["white_id"] == user.id:      return await say("Ты уже в этой партии (белые).")
    if g["black_id"] not in (None, user.id): return await say("❌ Партия уже полная.")
    g["black_id"]   = user.id
    g["black_name"] = user.first_name
    ctx.user_data["game_id"] = gid
    await say(f"✅ Ты в партии #{gid}! Играешь чёрными ♟")
    await ctx.bot.send_message(g["white_id"], f"👥 {user.first_name} вступил — игра началась!")
    await push_board(ctx, g["white_id"], gid)
    await push_board(ctx, user.id, gid)

# ── Кнопки ────────────────────────────────────────────────────────────────────

async def on_button(u: Update, ctx):
    q    = u.callback_query
    await q.answer()
    data = q.data
    user = q.from_user

    if data.startswith("join_"):
        await do_join(q, ctx, user, data[5:]); return

    if data.startswith("resign_"):
        gid = data[7:]
        if gid in games:
            await finish(ctx, gid, f"🏳 {user.first_name} сдался."); return

    if data.startswith("draw_"):
        gid = data[5:]
        if gid in games:
            await finish(ctx, gid, "🤝 Ничья по соглашению."); return

    if not data.startswith("sq_"): return
    _, gid, sq_str = data.split("_")
    sq = int(sq_str)

    if gid not in games:
        await q.message.reply_text("Партия не найдена."); return

    g     = games[gid]
    board = g["board"]

    if board.turn == chess.WHITE and user.id != g["white_id"]:
        await q.answer("Сейчас ходят белые!", show_alert=True); return
    if board.turn == chess.BLACK and user.id != g["black_id"]:
        await q.answer("Сейчас ходят чёрные!", show_alert=True); return

    sel   = g.get("selected")
    hints = g.get("hints", [])

    if sel is None:
        p = board.piece_at(sq)
        if p and p.color == board.turn:
            g["selected"] = sq
            g["hints"]    = legal_targets(board, sq)
            await push_board(ctx, user.id, gid, "Выбери куда ходить 👆")
        else:
            await q.answer("Выбери свою фигуру!")
        return

    if sq == sel:
        g["selected"] = None; g["hints"] = []
        await push_board(ctx, user.id, gid); return

    if sq in hints:
        promo = None
        p = board.piece_at(sel)
        if p and p.piece_type == chess.PAWN and chess.square_rank(sq) in (0, 7):
            promo = chess.QUEEN
        board.push(chess.Move(sel, sq, promotion=promo))
        g["selected"] = None; g["hints"] = []

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

    # Кликнули на другую свою фигуру
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
    board.push(random.choice(captures or checks or moves))
    ended, txt = game_over(board)
    if ended:
        await finish(ctx, gid, txt); return
    await push_board(ctx, g["white_id"], gid, "🤖 AI сходил. Твой ход!")

# ── Запуск ────────────────────────────────────────────────────────────────────

def main():
    app = Application.builder().token(TOKEN).build()
    for cmd, fn in [("start",   cmd_start),
                    ("newgame", cmd_newgame),
                    ("join",    cmd_join),
                    ("ai",      cmd_ai),
                    ("mygame",  cmd_mygame)]:
        app.add_handler(CommandHandler(cmd, fn))
    app.add_handler(CallbackQueryHandler(on_button))
    app.run_polling()

if __name__ == "__main__":
    main()
