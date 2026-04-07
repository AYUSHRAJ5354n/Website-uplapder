import requests
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
    HTTPServer(("0.0.0.0", port), Handler).serve_forever()

# ===== HELPERS =====
def extract_episode(title):
    match = re.search(r'episode\s*(\d+)', title.lower())
    return int(match.group(1)) if match else None

def extract_series(title):
    return re.sub(r'episode.*', '', title, flags=re.IGNORECASE).strip().lower()

def format_caption(title):
    ep = extract_episode(title)
    if not ep:
        return None

    clean = re.sub(r'episode.*', '', title, flags=re.IGNORECASE).strip()

    return f"""🔥{clean}

🟡 Episode : {ep}
🟡 Subtitle : Eng sub ✅
🟡 Quality  : 1080p ✨✅

@Donghua_Xin
"""

# ===== 🔥 API SCRAPER =====
def scrape():
    url = "https://animexin.dev/wp-json/wp/v2/posts?per_page=10"

    res = requests.get(url)
    posts = res.json()

    data = []

    for p in posts:
        title = p["title"]["rendered"]
        link = p["link"]

        ep = extract_episode(title)
        if not ep:
            continue

        image = None
        try:
            image = p["yoast_head_json"]["og_image"][0]["url"]
        except:
            pass

        data.append({
            "title": title,
            "link": link,
            "image": image,
            "episode": ep,
            "series": extract_series(title)
        })

    return data

# ===== SEND =====
async def send_post(app, post, chat_id):
    caption = format_caption(post["title"])
    if not caption:
        return

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔥 Watch Now", url=post["link"])]
    ])

    try:
        await app.bot.send_photo(
            chat_id=chat_id,
            photo=post["image"],
            caption=caption,
            reply_markup=keyboard
        )
    except:
        await app.bot.send_message(chat_id=chat_id, text=caption)

# ===== AUTO =====
async def auto_update(app):
    await asyncio.sleep(10)

    while True:
        posts = scrape()

        for post in posts:
            if collection.find_one({"link": post["link"]}):
                continue

            await send_post(app, post, CHAT_ID)
            collection.insert_one(post)

            await asyncio.sleep(2)

        await asyncio.sleep(300)

# ===== SEARCH SYSTEM =====

def get_series_pages(query):
    slug = query.lower().replace(" ", "-")

    urls = []
    for i in range(1, 7):
        if i == 1:
            url = f"https://animexin.dev/{slug}/"
        else:
            url = f"https://animexin.dev/{slug}-season-{i}/"

        try:
            r = requests.get(url)
            if r.status_code == 200:
                urls.append((f"Season {i}", url))
        except:
            pass

    return urls

def get_eps(url):
    r = requests.get(url)
    soup = r.text

    eps = re.findall(r'https://animexin.dev/[^"]+episode-[0-9]+[^"]*/', soup)

    unique = list(set(eps))

    data = []
    for link in unique:
        title = link.split("/")[-2].replace("-", " ").title()
        ep = extract_episode(title)

        if not ep:
            continue

        data.append({
            "title": title,
            "link": link
        })

    return sorted(data, key=lambda x: extract_episode(x["title"]))

# ===== SEARCH CMD =====
async def search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return

    query = " ".join(context.args)
    pages = get_series_pages(query)

    if not pages:
        return await update.message.reply_text("❌ Not found")

    if len(pages) > 1:
        buttons = []
        for name, url in pages:
            doc = collection.insert_one({"temp": True, "url": url})
            buttons.append([InlineKeyboardButton(name, callback_data=str(doc.inserted_id))])

        await update.message.reply_text("📺 Seasons", reply_markup=InlineKeyboardMarkup(buttons))
    else:
        eps = get_eps(pages[0][1])

        buttons = []
        for e in eps[:50]:
            doc = collection.insert_one({"temp": True, **e})
            ep = extract_episode(e["title"])
            buttons.append([InlineKeyboardButton(f"Ep {ep}", callback_data=str(doc.inserted_id))])

        await update.message.reply_text("📺 Episodes", reply_markup=InlineKeyboardMarkup(buttons))

# ===== CALLBACK =====
async def buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = collection.find_one({"_id": ObjectId(query.data)})

    if not data:
        return await query.message.reply_text("❌ Expired")

    if "url" in data:
        eps = get_eps(data["url"])

        buttons = []
        for e in eps[:50]:
            doc = collection.insert_one({"temp": True, **e})
            ep = extract_episode(e["title"])
            buttons.append([InlineKeyboardButton(f"Ep {ep}", callback_data=str(doc.inserted_id))])

        return await query.message.reply_text("📺 Episodes", reply_markup=InlineKeyboardMarkup(buttons))

    buttons = [[
        InlineKeyboardButton("📩 DM", callback_data=f"dm_{data['_id']}"),
        InlineKeyboardButton("📢 Channel", callback_data=f"ch_{data['_id']}")
    ]]

    await query.message.reply_text("Send where?", reply_markup=InlineKeyboardMarkup(buttons))

async def send_target(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    mode, post_id = query.data.split("_")
    post = collection.find_one({"_id": ObjectId(post_id)})

    if not post:
        return

    chat = query.from_user.id if mode == "dm" else CHAT_ID

    await send_post(context.application, post, chat)

# ===== COMMANDS =====
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return
    await update.message.reply_text("🔥 Bot Ready")

async def update_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    posts = scrape()
    count = 0

    for post in posts:
        if collection.find_one({"link": post["link"]}):
            continue

        await send_post(context.application, post, CHAT_ID)
        collection.insert_one(post)
        count += 1

    await update.message.reply_text(f"🚀 Uploaded {count} new episodes")

async def reupload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    posts = scrape()

    for post in posts:
        await send_post(context.application, post, CHAT_ID)
        await asyncio.sleep(2)

    await update.message.reply_text("🔁 Reuploaded homepage episodes")

async def clean(update: Update, context: ContextTypes.DEFAULT_TYPE):
    deleted = collection.delete_many({}).deleted_count
    await update.message.reply_text(f"💀 Deleted: {deleted}")

# ===== MAIN =====
def main():
    threading.Thread(target=run_server, daemon=True).start()

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("update", update_cmd))
    app.add_handler(CommandHandler("reupload", reupload))
    app.add_handler(CommandHandler("clean", clean))
    app.add_handler(CommandHandler("search", search))

    app.add_handler(CallbackQueryHandler(send_target, pattern="^(dm_|ch_)"))
    app.add_handler(CallbackQueryHandler(buttons))

    async def startup(app):
        asyncio.create_task(auto_update(app))

    app.post_init = startup

    print("🔥 Bot Running...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
