import requests
from bs4 import BeautifulSoup
import asyncio
import re
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes
from pymongo import MongoClient
from bson import ObjectId

# ===== CONFIG =====
BOT_TOKEN = os.getenv("BOT_TOKEN")
OWNER_ID = int(os.getenv("OWNER_ID"))
CHAT_ID = int(os.getenv("CHAT_ID"))
MONGO_URL = os.getenv("MONGO_URL")

# ===== DB =====
client = MongoClient(MONGO_URL)
db = client["donghua_bot"]
collection = db["posts"]

# ===== HEALTH SERVER =====
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")

def run_server():
    port = int(os.getenv("PORT", 8000))
    server = HTTPServer(("0.0.0.0", port), Handler)
    server.serve_forever()

# ===== FORMAT =====
def format_caption(title):
    ep = re.search(r'episode\s*(\d+)', title.lower())
    ep = ep.group(1) if ep else "?"

    clean = re.sub(r'episode.*', '', title, flags=re.IGNORECASE).strip()

    return f"""🔥{clean}

🟡 Episode : {ep}
🟡 Subtitle : Eng sub ✅
🟡 Quality  : 1080p ✨✅

@Donghua_Xin
"""

# ===== IMAGE SCRAPER =====
def get_image(url):
    try:
        res = requests.get(url, headers={"User-Agent": "Mozilla/5.0"})
        soup = BeautifulSoup(res.text, "html.parser")
        img = soup.find("img")
        return img["src"] if img else None
    except:
        return None

# ===== SEND =====
async def send_post(app, post, chat_id):
    if not post.get("image"):
        post["image"] = get_image(post["link"])

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔥 Watch Now", url=post["link"])]
    ])

    caption = format_caption(post["title"])

    try:
        await app.bot.send_photo(
            chat_id=chat_id,
            photo=post["image"],
            caption=caption,
            reply_markup=keyboard
        )
    except:
        await app.bot.send_message(chat_id=chat_id, text=caption)

# ===== SCRAPE HOME =====
def scrape():
    res = requests.get("https://animexin.dev/", headers={"User-Agent": "Mozilla/5.0"})
    soup = BeautifulSoup(res.text, "html.parser")

    posts = soup.find_all("article")
    data = []

    for p in posts:
        try:
            title = p.find(["h2","h3"]).text.strip()

            if not re.search(r'episode\s*\d+', title.lower()):
                continue

            link = p.find("a")["href"]

            img = p.find("img")
            image = img.get("src") if img else None

            data.append({
                "title": title,
                "link": link,
                "image": image
            })

        except:
            continue

    return data

# ===== AUTO UPDATE =====
async def auto_update(app):
    await asyncio.sleep(10)

    while True:
        posts = scrape()

        for post in posts:
            if not collection.find_one({"link": post["link"]}):
                await send_post(app, post, CHAT_ID)
                collection.insert_one(post)
                await asyncio.sleep(5)

        await asyncio.sleep(300)

# ===== GET EPISODES =====
def get_eps(url):
    res = requests.get(url, headers={"User-Agent": "Mozilla/5.0"})
    soup = BeautifulSoup(res.text, "html.parser")

    links = soup.find_all("a", href=True)
    eps = []

    for a in links:
        href = a["href"]

        if "episode" not in href:
            continue

        title = a.text.strip()

        if not re.search(r'episode\s*\d+', title.lower()):
            continue

        eps.append({"title": title, "link": href})

    unique = {e["link"]: e for e in eps}.values()

    def get_ep(x):
        m = re.search(r'episode\s*(\d+)', x["title"].lower())
        return int(m.group(1)) if m else 0

    return sorted(unique, key=get_ep)

# ===== SEARCH =====
async def search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return

    query = " ".join(context.args)
    slug = query.lower().replace(" ", "-")

    seasons = []

    for i in range(1, 7):
        if i == 1:
            url = f"https://animexin.dev/{slug}/"
        else:
            url = f"https://animexin.dev/{slug}-season-{i}/"

        try:
            res = requests.get(url)
            if res.status_code == 200:
                seasons.append((f"Season {i}", url))
        except:
            continue

    if not seasons:
        await update.message.reply_text("❌ Not found")
        return

    # multiple seasons
    if len(seasons) > 1:
        buttons = []
        for name, url in seasons:
            doc = collection.insert_one({
                "season": True,
                "url": url,
                "temp": True
            })

            buttons.append([
                InlineKeyboardButton(name, callback_data=str(doc.inserted_id))
            ])

        await update.message.reply_text(
            f"📺 {query} Seasons",
            reply_markup=InlineKeyboardMarkup(buttons)
        )

    else:
        eps = get_eps(seasons[0][1])

        buttons = []
        for ep in eps[:50]:
            doc = collection.insert_one({
                "title": ep["title"],
                "link": ep["link"],
                "temp": True
            })

            num = re.search(r'\d+', ep["title"]).group()

            buttons.append([
                InlineKeyboardButton(f"Ep {num}", callback_data=str(doc.inserted_id))
            ])

        await update.message.reply_text(
            f"📺 {query} Episodes",
            reply_markup=InlineKeyboardMarkup(buttons)
        )

# ===== CALLBACK HANDLERS =====
async def send_target(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    mode, post_id = query.data.split("_")

    post = collection.find_one({"_id": ObjectId(post_id)})

    if not post:
        await query.message.reply_text("❌ Expired")
        return

    if mode == "dm":
        await send_post(context.application, post, query.from_user.id)
    else:
        await send_post(context.application, post, CHAT_ID)

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = collection.find_one({"_id": ObjectId(query.data)})

    if not data:
        await query.message.reply_text("❌ Expired")
        return

    if data.get("season"):
        eps = get_eps(data["url"])

        buttons = []
        for ep in eps[:50]:
            doc = collection.insert_one({
                "title": ep["title"],
                "link": ep["link"],
                "temp": True
            })

            num = re.search(r'\d+', ep["title"]).group()

            buttons.append([
                InlineKeyboardButton(f"Ep {num}", callback_data=str(doc.inserted_id))
            ])

        await query.message.reply_text(
            "📺 Episodes",
            reply_markup=InlineKeyboardMarkup(buttons)
        )
        return

    # ask where to send
    buttons = [[
        InlineKeyboardButton("📩 DM", callback_data=f"dm_{data['_id']}"),
        InlineKeyboardButton("📢 Channel", callback_data=f"ch_{data['_id']}")
    ]]

    await query.message.reply_text(
        "📤 Where do you want to send?",
        reply_markup=InlineKeyboardMarkup(buttons)
    )

# ===== COMMANDS =====
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("❌ Private bot")
        return
    await update.message.reply_text("🔥 Donghua Bot Ready")

async def update_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    posts = scrape()
    for post in posts:
        if not collection.find_one({"link": post["link"]}):
            await send_post(context.application, post, CHAT_ID)
            collection.insert_one(post)

    await update.message.reply_text("✅ Updated")

async def reupload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    posts = list(collection.find().sort("_id", -1).limit(10))
    for post in posts:
        await send_post(context.application, post, CHAT_ID)
    await update.message.reply_text("🔁 Reuploaded")

async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    total = collection.count_documents({})
    await update.message.reply_text(f"📊 Total posts: {total}")

# ===== MAIN =====
def main():
    threading.Thread(target=run_server, daemon=True).start()

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("search", search))
    app.add_handler(CommandHandler("update", update_cmd))
    app.add_handler(CommandHandler("reupload", reupload))
    app.add_handler(CommandHandler("stats", stats))

    app.add_handler(CallbackQueryHandler(send_target, pattern="^(dm_|ch_)"))
    app.add_handler(CallbackQueryHandler(button_handler))

    async def startup(app):
        asyncio.create_task(auto_update(app))

    app.post_init = startup

    print("🔥 Bot Running...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
