import os
import json
import time
import asyncio
import re
import requests
from datetime import datetime
from telethon import TelegramClient
from telethon.tl.types import MessageMediaPhoto, MessageMediaDocument

# === ENV ===
API_ID = int(os.getenv("TELEGRAM_API_ID"))
API_HASH = os.getenv("TELEGRAM_API_HASH")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
IG_USER_ID = os.getenv("IG_USER_ID")
IG_GRAPH_TOKEN = os.getenv("IG_GRAPH_TOKEN")
IMGBB_API_KEY = os.getenv("IMGBB_API_KEY")

SESSION_FILE = "telegram_session"
RESULT_FILE = "results.json"

# === Load all previously posted texts from results.json ===
def load_posted_texts_from_results():
    try:
        with open(RESULT_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return set(entry["original_text"].strip() for entry in data if entry.get("original_text"))
    except:
        return set()

# === Append new entries to results.json (no overwrite) ===
def log_result(new_entries):
    try:
        with open(RESULT_FILE, "r", encoding="utf-8") as f:
            existing_entries = json.load(f)
    except:
        existing_entries = []

    combined = existing_entries + new_entries

    with open(RESULT_FILE, "w", encoding="utf-8") as f:
        json.dump(combined, f, ensure_ascii=False, indent=2)

# === Translate ===
def translate_to_malay(text):
    cleaned = re.sub(r'@\w+', '', text, flags=re.IGNORECASE)
    cleaned = re.sub(r'https?://\S+', '', cleaned)
    cleaned = re.sub(r'\[.*?\]\(.*?\)', '', cleaned)
    cleaned = re.sub(r'\n+', '\n', cleaned).strip()

    prompt = f"""
Translate the following post into Malay.
Do not include any usernames, mentions, links, or Telegram source references.
If the original post starts with 'JUST IN:' or '**JUST IN:**', please translate it as 'TERKINI:'.
Write it as a casual, friendly FB caption in one paragraph — no heading, no explanation.
Do not use slang or shouting. Keep it natural, chill, and neutral.

'{cleaned}'
"""
    try:
        res = requests.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}",
            headers={"Content-Type": "application/json"},
            json={"contents": [{"parts": [{"text": prompt}]}]}
        )
        return res.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
    except Exception as e:
        print(f"[Gemini Error] {e}")
        return "Translation failed"

# === Instagram Posting ===
def upload_image_to_imgbb(image_path):
    try:
        with open(image_path, 'rb') as f:
            res = requests.post(
                "https://api.imgbb.com/1/upload",
                params={"key": IMGBB_API_KEY},
                files={"image": f}
            )
            return res.json()["data"]["url"]
    except Exception as e:
        print(f"[Upload to imgbb Error] {e}")
        return None

def upload_to_ig_container(image_url, caption):
    try:
        r = requests.post(
            f"https://graph.facebook.com/v19.0/{IG_USER_ID}/media",
            data={
                "image_url": image_url,
                "caption": caption,
                "access_token": IG_GRAPH_TOKEN
            }
        )
        r.raise_for_status()
        return r.json().get("id")
    except Exception as e:
        print(f"[IG Container Error] {e}")
        return None

def publish_ig_container(container_id):
    try:
        r = requests.post(
            f"https://graph.facebook.com/v19.0/{IG_USER_ID}/media_publish",
            data={
                "creation_id": container_id,
                "access_token": IG_GRAPH_TOKEN
            }
        )
        r.raise_for_status()
        print("[IG] Post success.")
        return True
    except Exception as e:
        print(f"[IG Publish Error] {e}")
        return False

# === MAIN ===
async def main():
    client = TelegramClient(SESSION_FILE, API_ID, API_HASH)
    await client.start()

    posted_texts = load_posted_texts_from_results()
    media_group_ids_done = set()
    results = []

    async for msg in client.iter_messages("WatcherGuru", limit=20):
        original_text = (msg.text or "").strip()

        if not original_text or len(original_text.split()) < 3:
            continue

        if original_text in posted_texts:
            print(f"[SKIP] Already posted content: {original_text[:60]}...")
            continue

        if hasattr(msg, "media_group_id") and msg.media_group_id in media_group_ids_done:
            continue

        translated = translate_to_malay(original_text)
        if translated == "Translation failed":
            continue

        image_paths = []
        video_path = None
        success = False

        if hasattr(msg, "media_group_id") and msg.media_group_id:
            group_msgs = []
            async for grouped in client.iter_messages("WatcherGuru", min_id=msg.id - 15, max_id=msg.id + 15):
                if (
                    hasattr(grouped, "media_group_id") and
                    grouped.media_group_id == msg.media_group_id
                ):
                    group_msgs.append(grouped)
            media_group_ids_done.add(msg.media_group_id)

            for media_msg in reversed(group_msgs):
                if isinstance(media_msg.media, MessageMediaPhoto):
                    path = f"temp_{media_msg.id}.jpg"
                    await client.download_media(media_msg.media, file=path)
                    image_paths.append(path)
        elif isinstance(msg.media, MessageMediaPhoto):
            path = f"temp_{msg.id}.jpg"
            await client.download_media(msg.media, file=path)
            image_paths.append(path)

        # === Instagram Posting Only ===
        if image_paths:
            ig_url = upload_image_to_imgbb(image_paths[0])
            if ig_url:
                container_id = upload_to_ig_container(ig_url, translated)
                if container_id:
                    success = publish_ig_container(container_id)
        else:
            print("[SKIP] No image found — skipping (Instagram requires media).")
            success = False

        if success:
            results.append({
                "telegram_id": msg.id,
                "original_text": original_text,
                "translated_caption": translated,
                "ig_status": "Posted",
                "date_posted": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            })

        for path in image_paths:
            if os.path.exists(path):
                os.remove(path)

        time.sleep(1)

    log_result(results)
    await client.disconnect()

if __name__ == "__main__":
    asyncio.run(main())
