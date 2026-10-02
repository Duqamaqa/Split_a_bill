#!/usr/bin/env python3
"""
Post Automation driver - runs as n8n Execute Command node.
Modes:
  daily          - analyze metrics, generate draft+image, send to group with buttons
  callback <json>- handle Telegram button press (approve/tweak/total) or photo/command
State: /home/pi/projects/post-automation/state/
"""
import json, os, sys, time, subprocess, urllib.request, urllib.parse

BASE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(BASE, "state")
PHOTOS = os.path.join(BASE, "photos")
os.makedirs(STATE, exist_ok=True)
os.makedirs(PHOTOS, exist_ok=True)

# secrets
for line in open(os.path.expanduser("~/.hermes/secrets/meta-post-automation.env")):
    line = line.strip()
    if line and not line.startswith("#"):
        k, _, v = line.partition("=")
        os.environ.setdefault(k, v.strip('"'))
for line in open(os.path.expanduser("~/.hermes/secrets/postauto-bot.env")):
    line = line.strip()
    if line and not line.startswith("#"):
        k, _, v = line.partition("=")
        os.environ.setdefault(k, v.strip('"'))
for line in open(os.path.expanduser("~/.hermes/secrets/openrouter-postauto.env")):
    line = line.strip()
    if line and not line.startswith("#"):
        k, _, v = line.partition("=")
        os.environ.setdefault(k, v.strip('"'))

BOT = os.environ["POSTAUTO_BOT_TOKEN"]
GROUP = os.environ["POSTAUTO_GROUP_CHAT_ID"]
PAGE_ID = os.environ["META_PAGE_ID"]
IG_ID = os.environ["META_IG_USER_ID"]
PTOKEN = os.environ["META_PAGE_TOKEN"]
OR_KEY = os.environ["OPENROUTER_API_KEY_POSTAUTO"]
MODEL = "z-ai/glm-5.3-flash"
API = "https://graph.facebook.com/v21.0"

def tg(method, **kw):
    data = urllib.parse.urlencode(kw).encode()
    req = urllib.request.Request(f"https://api.telegram.org/bot{BOT}/{method}", data=data)
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)

def send_draft(caption, image_urls, state_extra=None):
    if isinstance(image_urls, str): image_urls = [image_urls]
    kb = {"inline_keyboard": [[
        {"text": "✅ פרסום", "callback_data": "approve"},
        {"text": "✏️ שינוי", "callback_data": "tweak"},
        {"text": "🔄 אחר לגמרי", "callback_data": "total"},
    ]]}
    st = json.load(open(f"{STATE}/pending.json")) if os.path.exists(f"{STATE}/pending.json") else {}
    st.update({"caption": caption, "image_urls": image_urls, "ts": time.time()})
    if state_extra: st.update(state_extra)
    def _multipart_send(files, single):
        """Upload photo file(s) directly to Telegram as multipart attachment. files: list of local paths."""
        import uuid
        b = uuid.uuid4().hex
        parts = []
        def field(name, val):
            parts.append(f"--{b}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{val}\r\n".encode())
        field("chat_id", GROUP)
        if single:
            field("caption", caption[:1024]); field("reply_markup", json.dumps(kb))
            method, key = "sendPhoto", "photo"
        else:
            media = [{"type": "photo", "media": f"attach://f{i}", "caption": caption[:1024] if i == 0 else ""}
                     for i in range(len(files))]
            field("media", json.dumps(media))
            method, key = "sendMediaGroup", None
        for i, t in enumerate(files):
            name = key or f"f{i}"
            parts.append(f"--{b}\r\nContent-Disposition: form-data; name=\"{name}\"; filename=\"p{i}.jpg\"\r\nContent-Type: image/jpeg\r\n\r\n".encode())
            with open(t, "rb") as f: parts.append(f.read())
            parts.append(b"\r\n")
        parts.append(f"--{b}--\r\n".encode())
        req = urllib.request.Request(f"https://api.telegram.org/bot{BOT}/{method}", data=b"".join(parts),
            headers={"Content-Type": f"multipart/form-data; boundary={b}"})
        with urllib.request.urlopen(req, timeout=120) as rr:
            r = json.load(rr)
        if not r.get("ok"): raise RuntimeError(f"{method} upload failed: {r}")
        if single:
            return r, r["result"]["message_id"], None
        r2 = tg("sendMessage", chat_id=GROUP, text="⬆️ טיוטה חדשה — בחרו:", reply_markup=json.dumps(kb))
        return r, r["result"][0]["message_id"], r2["result"]["message_id"]

    def _local(u): return u[5:] if u.startswith("file:") else None

    def _try_send(urls):
        if all(_local(u) for u in urls):
            return _multipart_send([_local(u) for u in urls], single=(len(urls) == 1))
        if len(urls) == 1:
            r = tg("sendPhoto", chat_id=GROUP, photo=urls[0], caption=caption[:1024], reply_markup=json.dumps(kb))
            return r, r["result"]["message_id"], None
        media = [{"type": "photo", "media": u, "caption": caption[:1024] if i == 0 else ""}
                 for i, u in enumerate(urls[:10])]
        r = tg("sendMediaGroup", chat_id=GROUP, media=json.dumps(media))
        r2 = tg("sendMessage", chat_id=GROUP, text="⬆️ טיוטה חדשה — בחרו:", reply_markup=json.dumps(kb))
        return r, r["result"][0]["message_id"], r2["result"]["message_id"]
    try:
        r, msg_id, btn_id = _try_send(image_urls)
        st["msg_id"] = msg_id
        if btn_id: st["btn_msg_id"] = btn_id
    except Exception:
        # URL fetch by Telegram failed - download and send as multipart upload
        try:
            import tempfile
            tmps = []
            for u in image_urls[:10]:
                tmp = tempfile.mktemp(suffix=".jpg")
                subprocess.run(["curl", "-sL", "-o", tmp, u], timeout=60)
                if os.path.exists(tmp) and os.path.getsize(tmp) > 5000:
                    tmps.append(tmp)
            if not tmps: raise RuntimeError("could not download image files for upload")
            if len(tmps) == 1:
                import uuid
                b = uuid.uuid4().hex
                body = f"--{b}\r\nContent-Disposition: form-data; name=\"chat_id\"\r\n\r\n{GROUP}\r\n"
                body += f"--{b}\r\nContent-Disposition: form-data; name=\"caption\"\r\n\r\n{caption[:1024]}\r\n"
                body += f"--{b}\r\nContent-Disposition: form-data; name=\"reply_markup\"\r\n\r\n{json.dumps(kb)}\r\n"
                body += f"--{b}\r\nContent-Disposition: form-data; name=\"photo\"; filename=\"photo.jpg\"\r\nContent-Type: image/jpeg\r\n\r\n".encode()
                with open(tmps[0], "rb") as f: raw = f.read()
                body += raw + f"\r\n--{b}--\r\n".encode()
                req = urllib.request.Request(f"https://api.telegram.org/bot{BOT}/sendPhoto", data=body,
                    headers={"Content-Type": f"multipart/form-data; boundary={b}"})
                with urllib.request.urlopen(req, timeout=90) as rr:
                    r = json.load(rr)
                if not r.get("ok"): raise RuntimeError(f"sendPhoto upload failed: {r}")
            else:
                media = [{"type": "photo", "media": f"attach://f{i}", "caption": caption[:1024] if i == 0 else ""}
                         for i in range(len(tmps))]
                import uuid
                b = uuid.uuid4().hex
                parts = []
                def field(name, val):
                    parts.append(f"--{b}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{val}\r\n".encode())
                field("chat_id", GROUP); field("media", json.dumps(media))
                for i, t in enumerate(tmps):
                    parts.append(f"--{b}\r\nContent-Disposition: form-data; name=\"f{i}\"; filename=\"p{i}.jpg\"\r\nContent-Type: image/jpeg\r\n\r\n".encode())
                    with open(t, "rb") as f: parts.append(f.read())
                    parts.append(b"\r\n")
                parts.append(f"--{b}--\r\n".encode())
                req = urllib.request.Request(f"https://api.telegram.org/bot{BOT}/sendMediaGroup", data=b"".join(parts),
                    headers={"Content-Type": f"multipart/form-data; boundary={b}"})
                with urllib.request.urlopen(req, timeout=120) as rr:
                    r = json.load(rr)
                if not r.get("ok"): raise RuntimeError(f"sendMediaGroup upload failed: {r}")
                r2 = tg("sendMessage", chat_id=GROUP, text="⬆️ טיוטה חדשה — בחרו:", reply_markup=json.dumps(kb))
            for t in tmps: os.remove(t)
            msg_id = r["result"]["message_id"] if len(tmps) == 1 else r["result"][0]["message_id"]
            btn_id = None if len(tmps) == 1 else r2["result"]["message_id"]
            st["msg_id"] = msg_id
            if btn_id: st["btn_msg_id"] = btn_id
        except Exception as e2:
            tg("sendMessage", chat_id=GROUP, text=f"⚠️ יצירת הטיוטה נכשלה ({e2}). נסו /run שוב.")
            raise
    json.dump(st, open(f"{STATE}/pending.json", "w"), ensure_ascii=False)
    return r

def llm(messages, max_tokens=700):
    body = json.dumps({"model": MODEL, "messages": messages, "max_tokens": max_tokens}).encode()
    req = urllib.request.Request("https://openrouter.ai/api/v1/chat/completions", data=body,
        headers={"Authorization": f"Bearer {OR_KEY}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=90) as r:
        msg = json.load(r)["choices"][0]["message"]
    out = msg.get("content") or ""
    if not out.strip():
        # reasoning models put text in the reasoning field or hit token cap; retry with more tokens
        out = llm(messages, max_tokens=max_tokens * 4)
    return out.strip()

def gen_image(prompt, seed=None):
    """GPT image generation (gpt-image-1-mini) via OpenRouter Image API -> saves to photos dir, returns public URL."""
    import re as _re
    cyr = len(_re.findall(r"[\u0400-\u04FF]", prompt))
    if cyr > 10:  # prompt came back mostly in Russian -> translate to English, keep Hebrew quoted text
        heb_quotes = _re.findall(r'"[^"]*[\u0590-\u05FF][^"]*"', prompt)
        stripped = _re.sub(r'"[^"]*[\u0590-\u05FF][^"]*"', '{HEB}', prompt)
        en = llm([
            {"role": "system", "content": "Translate this Russian image-generation prompt to ONE English prompt. Keep every visual element and style. Output only the prompt."},
            {"role": "user", "content": stripped[:800]}], max_tokens=1000)
        for h in heb_quotes:
            en = en.replace("{HEB}", h, 1) if "{HEB}" in en else en + f' {h}'
        prompt = en
    body = json.dumps({"model": "openai/gpt-image-1-mini", "prompt": prompt, "n": 1}).encode()
    req = urllib.request.Request("https://openrouter.ai/api/v1/images/generations", data=body,
        headers={"Authorization": f"Bearer {OR_KEY}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=180) as r:
        d = json.load(r)["data"][0]
    b64 = d.get("b64_json")
    path = os.path.join(PHOTOS, f"gen_{int(time.time())}.jpg")
    if b64:
        import base64
        raw = base64.b64decode(b64)
        # convert to jpg under 5MB (Telegram + IG friendly)
        try:
            from PIL import Image
            import io
            im = Image.open(__import__("io").BytesIO(raw)).convert("RGB")
            im.save(path, "JPEG", quality=88)
        except Exception:
            path = os.path.join(PHOTOS, f"gen_{int(time.time())}.png")
            open(path, "wb").write(raw)
        pub = upload_public(path)
        if pub: return pub
        print(f"[warn] catbox upload failed; using local file for draft: {path}")
        return f"file:{path}"
    raise RuntimeError("no b64_json in image response")

def pick_photo():
    """Prefer real photos uploaded by the group. Ignore generated/Pollinations files - they must never be reused."""
    files = [f for f in os.listdir(PHOTOS)
             if f.lower().endswith((".jpg", ".jpeg", ".png", ".webp"))
             and not f.startswith(("gen_", "starter_"))]
    return os.path.join(PHOTOS, sorted(files)[int(time.time()) % len(files)]) if files else None

def upload_public(path):
    """Host a local photo to get a public URL (needed by IG container)."""
    import subprocess
    for attempt in range(4):
        out = subprocess.run(["curl", "-s", "-F", f"fileToUpload=@{path}", "-F", "reqtype=fileupload",
                              "https://catbox.moe/user/api.php"], capture_output=True, text=True, timeout=60).stdout.strip()
        if out.startswith("http"):
            # verify catbox actually serves the file (it has been returning 0-byte responses)
            v = subprocess.run(["curl", "-sL", "-o", "/dev/null", "-w", "%{size_download}", out],
                               capture_output=True, text=True, timeout=60).stdout.strip()
            if v.isdigit() and int(v) > 5000: return out
        time.sleep(20 * (attempt + 1))  # catbox rate-limits rapid repeated uploads
    return None

def daily():
    # 1. metrics (deterministic)
    out = subprocess_run([sys.executable, os.path.join(BASE, "analyze.py")])
    posts = json.load(open("/tmp/posts_metrics.json"))
    top = sorted(posts, key=lambda p: p["views"] + 3*p["likes"] + 2*p["comments"], reverse=True)[:5]
    lines = "\n\n".join(f"[{p['platform']}] views={p['views']} likes={p['likes']} comments={p['comments']}\n{(p['caption'] or '')[:250]}" for p in top)
    # 2. technique brief (LLM call 1)
    technique = llm([
        {"role": "system", "content": "אתה מנתח פוסטים של 'Atelier vert' - עסק לעיצוב נוף ומרפסות בישראל. מהפוסטים המוצלחים למטה, סכם בעברית (עד 100 מילים) מה עבד: סוג נושא, סגנון פתיחה, מבנה, CTA. החזר רק את הסיכום."},
        {"role": "user", "content": lines}])
    # 3. caption (LLM call 2) - higher tokens so reasoning model doesn't truncate the Hebrew text
    ideas = open(os.path.join(BASE, "ideas.md")).read() if os.path.exists(os.path.join(BASE, "ideas.md")) else ""
    ideas_block = f"\n\nרעיונות שהמשתמשים הציעו - בחר את הכי מתאים וכתוב עליו את הפוסט (התמונות יווצרו בהתאמה לרעיון הזה):\n{ideas}" if ideas else ""
    # the driving idea = newest idea line (the one /run should follow)
    used_idea = ""
    if ideas.strip():
        all_lines = ideas.strip().splitlines()
        start = None
        for i, l in enumerate(all_lines):
            if l.strip().startswith("- "):
                start = i  # keep going - we want the LAST entry
        if start is not None:
            entry_lines = [all_lines[start].split("):", 1)[-1].strip()]
            for l in all_lines[start + 1:]:
                if l.strip().startswith("- "):
                    break
                if l.strip():
                    entry_lines.append(l.strip())
            used_idea = " ".join(entry_lines)
    caption = llm([
        {"role": "system", "content": "כותב פוסטים לאינסטגרם/פייסבוק עבור 'Atelier vert' (עיצוב נוף ומרפסות, ישראל). החזר רק את טקסט הפוסט בעברית, עם אימוג'י ו-CTA, 3-5 משפטים מלאים (200-400 תווים). אל תקטע את הטקסט - סיים את המשפט האחרון עד הסוף. כשיש רעיון מהמשתמש - הפוסט חייב לתמוך ברעיון ובמבנה שלו (אם הרעיון מציג שתי תמונות - הטקסט צריך להסביר את שתיהן ולחבר ביניהן, ולהפנות את הקורא לתמונות)."},
        {"role": "user", "content": f"טכניקות שעבדו:\n{technique}\n\nפוסטים מוצלחים:\n{lines[:800]}{ideas_block}"}], max_tokens=2000)
    caption = caption.strip()
    # guard: if it still looks truncated (no ending punctuation), regenerate once with more room
    if caption and caption[-1] not in ".!?…\n\"'”םן":
        caption = llm([
            {"role": "system", "content": "כותב פוסטים ל-'Atelier vert'. החזר פוסט עברי מלא ומסתיים, 3-5 משפטים."},
            {"role": "user", "content": f"הטיוטה הקודמת נקטעה באמצע: {caption}\n\nכתוב פוסט מלא חדש על אותו נושא, שמסתיים בכיף."}], max_tokens=2000)
    # 4. images: if the caption came from a user idea, images MUST be generated to fit that idea.
    #    Folder photos are only used when the draft is not idea-driven, and each is used once (moved to used/ after publish).
    n_imgs = llm([
        {"role": "system", "content": "החזר רק מספר (1, 2 או 3): כמה תמונות לפוסט. אם הרעיון או הפוסט מזכירים שתי תמונות / לפני-אחרי / שני חלקים - ענה 2. החזר ספרה בלבד."},
        {"role": "user", "content": f"רעיון: {used_idea[:400]}\n\nפוסט: {caption[:400]}"}], max_tokens=1000).strip()
    n_imgs = min(max(int(n_imgs[0]) if n_imgs and n_imgs[0].isdigit() else 1, 1), 3)
    idea_driven = bool(ideas.strip())
    exact = is_exact_idea(used_idea) if used_idea else False
    if exact:
        spec = structure_exact_idea(used_idea)
        caption = spec.get("caption") or caption
        # HARD RULE: on-image text must be Hebrew. If the parser returned Cyrillic/other, translate via LLM.
        import re as _re2
        for im in spec.get("images", []):
            t = (im.get("text_on_image") or "").strip()
            if t and _re2.search(r"[\u0400-\u04FF]", t):
                t = llm([
                    {"role": "system", "content": "Translate this short image caption to natural HEBREW. Output only the translation."},
                    {"role": "user", "content": t}], max_tokens=300).strip()
            # strip emoji that the overlay font can't draw (they show as boxes)
            t = _re2.sub(r"[\U0001F000-\U0001FAFF\u2600-\u27BF\uFE0F]", "", t).strip()
            t = _re2.sub(r"\([^)]*\)", "", t).strip()  # drop leaked annotations like (top)
            t = _re2.sub(r"\s{2,}", " ", t)
            im["text_on_image"] = t
        # caption: if the brief's caption is not Hebrew, translate it too (audience is Israeli)
        cap = (spec.get("caption") or "").strip()
        if cap and _re2.search(r"[\u0400-\u04FF]", cap):
            cap = llm([
                {"role": "system", "content": "Translate this social post caption to natural HEBREW. Keep line breaks, emojis and the brand name. Output only the translation."},
                {"role": "user", "content": cap}], max_tokens=800).strip()
            spec["caption"] = cap
        caption = spec["caption"]  # use the (possibly translated) caption
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=3) as pool:
            urls = list(pool.map(lambda im: gen_image(im["prompt"]), spec["images"]))
        # overlay any on-image text that the generator should not be trusted with (Hebrew, code-rendered)
        fixed = []
        for i, im in enumerate(spec["images"]):
            u = urls[i] if i < len(urls) else urls[-1]
            if im.get("text_on_image"):
                tmp = f"/tmp/ex_{i}_{int(time.time())}.jpg"
                subprocess.run(["curl", "-s", "-o", tmp, u], timeout=60)
                if os.path.exists(tmp) and os.path.getsize(tmp) > 5000:
                    overlaid = overlay_text(tmp, im["text_on_image"], position="bottom" if i else "top")
                    pu = upload_public(overlaid)
                    if pu: u = pu
                try: os.remove(tmp)
                except Exception: pass
            fixed.append(u)
        urls = fixed
        send_draft(caption, urls, {"technique": technique, "top": lines[:800], "used_idea": used_idea, "exact": True})
        if used_idea and os.path.exists(IDEAS_FILE):
            _consume_idea(used_idea)
        return
    urls = []
    real = []
    if not idea_driven:
        real = [f for f in os.listdir(PHOTOS)
                if f.lower().endswith((".jpg", ".jpeg", ".png", ".webp"))
                and not f.startswith(("gen_", "starter_"))]
        if real:
            local = os.path.join(PHOTOS, sorted(real)[0])
            u = upload_public(local)
            if u:
                urls.append(u)
                open(os.path.join(STATE, "last_used_photo.txt"), "w").write(local)
    if used_idea:
        # split the idea into per-image prompts, honoring each image's OWN style instructions
        prompts = llm([
            {"role": "system", "content": 'You convert a user\'s post idea into exactly %d image-generation prompts (JSON array of strings, output ONLY the JSON). For EACH image: if the idea specifies a style for it (child\'s crayon drawing, photorealistic, cartoon...), that style MUST be followed exactly. If no style is specified, use photorealistic professional photography. Keep every element the user listed (animals, objects, jokes). IMPORTANT: do NOT include any written text, words, letters, captions or writing inside the image - the prompt must explicitly say "no text, no letters, no writing in the image" (text is added programmatically later). NEVER mention any language for in-image text. Output only the JSON array.' % n_imgs},
            {"role": "user", "content": f"Idea: {used_idea[:800]}"}], max_tokens=1500)
        try:
            arr = json.loads(prompts[prompts.index("["):prompts.rindex("]")+1])
        except Exception:
            arr = [used_idea[:300] + ", realistic photo, high quality"]
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=3) as pool:
            urls = list(pool.map(lambda p: gen_image(p), arr[:n_imgs]))
    else:
        ip = llm([{"role": "system", "content": "ONE English image-generation prompt (max 60 words) for a high-end, professionally designed urban balcony/rooftop — like an award-winning landscape-architecture project, NOT a plain balcony with a few potted plants. Describe a deliberate design: layered planting composition (tall architectural plants, mid-level flowering shrubs, trailing groundcover), natural materials (thermo-treated wood decking, corten steel or stone planters, woven screens for privacy), coordinated furniture and warm evening lighting, strong depth and framing. Photorealistic, golden-hour light, wide editorial shot. No text or logos. Output only the prompt."},
                  {"role": "user", "content": f"Post caption: {caption[:300]}"}], max_tokens=1000)
        urls = [gen_image(ip)]
    urls = apply_idea_text_on_images(caption, urls, used_idea)
    send_draft(caption, urls, {"technique": technique, "top": lines[:800], "used_idea": used_idea})
    _consume_idea(used_idea)

def publish(caption, image_urls):
    if isinstance(image_urls, str): image_urls = [image_urls]
    image_urls = [upload_public(u[5:]) if u.startswith("file:") else u for u in image_urls]
    image_urls = [u for u in image_urls if u]
    try:
        tg("sendMessage", chat_id=GROUP, text="📤 מפרסם לאינסטגרם ולפייסבוק...")
    except Exception:
        pass
    results = {}
    # Instagram: children containers -> parent CAROUSEL container -> publish parent
    try:
        cids = []
        for u in image_urls:
            is_video = u.lower().endswith((".mp4", ".mov"))
            params = {"access_token": PTOKEN}
            if is_video:
                params.update({"media_type": "REELS", "video_url": u})
            else:
                params.update({"image_url": u})
            if len(image_urls) > 1:
                params["is_carousel_item"] = "true"
            c = json.load(urllib.request.urlopen(urllib.request.Request(
                f"{API}/{IG_ID}/media", data=urllib.parse.urlencode(params).encode()), timeout=60))
            cid = c["id"]
            if is_video:
                for _ in range(30):
                    s = json.load(urllib.request.urlopen(f"{API}/{cid}?fields=status_code&access_token={PTOKEN}", timeout=30))
                    if s.get("status_code") == "FINISHED": break
                    time.sleep(3)
            cids.append(cid)
        def _wait(cid):
            for _ in range(30):
                s = json.load(urllib.request.urlopen(f"{API}/{cid}?fields=status_code&access_token={PTOKEN}", timeout=30))
                if s.get("status_code") == "FINISHED": return
                time.sleep(3)
        if len(cids) == 1:
            pub_id = cids[0]
            _wait(pub_id)
        else:
            parent = json.load(urllib.request.urlopen(urllib.request.Request(f"{API}/{IG_ID}/media",
                data=urllib.parse.urlencode({"media_type": "CAROUSEL", "children": ",".join(cids),
                                             "caption": caption, "access_token": PTOKEN}).encode()), timeout=60))
            pub_id = parent["id"]
            _wait(pub_id)
        pub_params = {"creation_id": pub_id, "access_token": PTOKEN}
        if len(cids) == 1:
            pub_params["caption"] = caption
        p = json.load(urllib.request.urlopen(urllib.request.Request(f"{API}/{IG_ID}/media_publish",
            data=urllib.parse.urlencode(pub_params).encode()), timeout=60))
        results["instagram"] = p.get("id")
    except Exception as e:
        results["instagram"] = f"ERROR: {e}"
    # Facebook: multi-photo post (attached_media for carousel-style)
    try:
        if len(image_urls) == 1:
            body = {"url": image_urls[0], "caption": caption, "access_token": PTOKEN}
            p = json.load(urllib.request.urlopen(urllib.request.Request(f"{API}/{PAGE_ID}/photos",
                data=urllib.parse.urlencode(body).encode()), timeout=60))
            results["facebook"] = p.get("post_id") or p.get("id")
        else:
            body = {"access_token": PTOKEN, "message": caption}
            fbid = json.load(urllib.request.urlopen(urllib.request.Request(f"{API}/{PAGE_ID}/photos",
                data=urllib.parse.urlencode({"published": "false", "url": image_urls[0], "access_token": PTOKEN}).encode()), timeout=60))["id"]
            body["attached_media[0]"] = json.dumps({"media_fbid": fbid})
            for i, u in enumerate(image_urls[1:9], start=1):
                fbx = json.load(urllib.request.urlopen(urllib.request.Request(f"{API}/{PAGE_ID}/photos",
                    data=urllib.parse.urlencode({"published": "false", "url": u, "access_token": PTOKEN}).encode()), timeout=60))["id"]
                body[f"attached_media[{i}]"] = json.dumps({"media_fbid": fbx})
            p = json.load(urllib.request.urlopen(urllib.request.Request(f"{API}/{PAGE_ID}/feed",
                data=urllib.parse.urlencode(body).encode()), timeout=60))
            results["facebook"] = p.get("post_id") or p.get("id")
    except Exception as e:
        results["facebook"] = f"ERROR: {e}"
    # archive any folder photo that was just published so it is never used again
    used_dir = os.path.join(PHOTOS, "used")
    os.makedirs(used_dir, exist_ok=True)
    last_used_path = os.path.join(STATE, "last_used_photo.txt")
    if os.path.exists(last_used_path):
        f = open(last_used_path).read().strip()
        if f and os.path.exists(os.path.join(PHOTOS, f)):
            os.rename(os.path.join(PHOTOS, f), os.path.join(used_dir, f))
        os.remove(last_used_path)
    links = {}
    try:
        if isinstance(results.get("instagram"), str) and not results["instagram"].startswith("ERROR"):
            ig_info = json.load(urllib.request.urlopen(
                f"{API}/{results['instagram']}?fields=permalink&access_token={PTOKEN}", timeout=30))
            links["Instagram"] = ig_info.get("permalink", "")
    except Exception:
        pass
    try:
        if isinstance(results.get("facebook"), str) and not results["facebook"].startswith("ERROR"):
            fb_info = json.load(urllib.request.urlopen(
                f"{API}/{results['facebook']}?fields=permalink_url&access_token={PTOKEN}", timeout=30))
            if fb_info.get("permalink_url"):
                links["Facebook"] = fb_info["permalink_url"]
            else:
                links["Facebook"] = f"https://www.facebook.com/{results['facebook']}"
    except Exception:
        links["Facebook"] = f"https://www.facebook.com/{results['facebook']}"
    lines = ["🚀 פורסם!"]
    if links.get("Instagram"):
        lines.append(f"📸 Instagram: {links['Instagram']}")
    elif "instagram" in results:
        lines.append(f"📸 Instagram: {results['instagram']}")
    if links.get("Facebook"):
        lines.append(f"👍 Facebook: {links['Facebook']}")
    elif "facebook" in results:
        lines.append(f"👍 Facebook: {results['facebook']}")
    tg("sendMessage", chat_id=GROUP, text="\n".join(lines), disable_web_page_preview=False)
    json.dump({"results": results, "links": links}, open(f"{STATE}/last_publish.json", "w"))
    os.remove(f"{STATE}/pending.json")

def subprocess_run(cmd):
    import subprocess
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=120,
                       env={**os.environ, "OUT": "/tmp/posts_metrics.json"})
    if r.returncode != 0:
        raise RuntimeError(r.stderr[-500:])
    return r.stdout

IDEAS_FILE = os.path.join(BASE, "ideas.md")
EXACT_MARKERS = ("ПРОМПТ ДЛЯ ГЕНЕРАЦИИ", "PROMPT FOR GENERATION", "ТЕКСТ ПОД ПОСТОМ",
                 "CAPTION:", "ТЕКСТ НА КАРТИНКЕ", "Изображение 1", "IMAGE 1", "Картинка 1")

def is_exact_idea(idea_text):
    return any(m in idea_text for m in EXACT_MARKERS)

def structure_exact_idea(idea_text):
    """Parse a precise brief into images + caption WITHOUT creative rewriting.
    Returns dict: {caption, images: [{prompt, text_on_image}]}"""
    raw = llm([
        {"role": "system", "content":
         "You are a STRICT parser, not a writer. The user gives a precise image-generation brief. "
         "Convert it to JSON (output ONLY JSON): "
         '{"caption": "<the exact post text the user specified, translated to Hebrew if it is not Hebrew; if none specified, empty string>", '
         '"images": [{"prompt": "<the image description translated to English, keeping EVERY directive: style, composition, elements, lighting. Do not add, remove, soften or beautify anything. If the user says a child\'s crayon drawing, it IS a child\'s crayon drawing. If they say not CGI, add: real photograph. EXCLUDE any instruction to draw text/words/letters inside the image - do not mention text at all in the prompt>", '
         '"text_on_image": "<ONLY the phrase itself that the user wants on the image, in HEBREW (translate if needed), with no positioning words, no parentheses, no annotations - positioning is handled separately - or empty string>"}]}. '
         "Number of images = exactly what the user specified. Do not invent extra images or text."},
        {"role": "user", "content": idea_text[:3000]}], max_tokens=2500)
    d = json.loads(raw[raw.index("{"):raw.rindex("}")+1])
    return d


def add_idea(text, author=None):
    who = author or "?"
    norm = " ".join(text.strip().split())
    if os.path.exists(IDEAS_FILE):
        for l in open(IDEAS_FILE):
            if f"({who}):" in l and norm in " ".join(l.split("):", 1)[-1].split()):
                return -1  # duplicate from same author - skip
    ts = time.strftime("%Y-%m-%d %H:%M")
    with open(IDEAS_FILE, "a") as f:
        f.write(f"- {ts} ({who}): {text.strip()}\n")
    return len([l for l in open(IDEAS_FILE) if l.startswith("- ")])


FONT_PATH = "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf"

def overlay_text(image_path, text, position="bottom"):
    """Draw Hebrew/any text on the image with a contrast band. Returns path."""
    from PIL import Image, ImageDraw, ImageFont
    im = Image.open(image_path).convert("RGB")
    W, H = im.size
    fs = max(28, W // 16)
    font = ImageFont.truetype(FONT_PATH, fs)
    d = ImageDraw.Draw(im)
    # wrap text
    words = text.split()
    lines, cur = [], ""
    for w in words:
        t = (cur + " " + w).strip()
        if d.textlength(t, font=font) <= W * 0.9:
            cur = t
        else:
            if cur: lines.append(cur)
            cur = w
    if cur: lines.append(cur)
    lh = int(fs * 1.35)
    band_h = lh * len(lines) + int(fs * 0.8)
    y0 = H - band_h if position == "bottom" else 0
    band = Image.new("RGB", (W, band_h), (0, 0, 0))
    im.paste(band, (0, y0))
    d = ImageDraw.Draw(im)
    y = y0 + int(fs * 0.4)
    for line in lines:
        wpx = d.textlength(line, font=font)
        d.text(((W - wpx) / 2, y), line, font=font, fill=(255, 255, 255))
        y += lh
    out = image_path.replace(".jpg", "_txt.jpg")
    im.save(out, "JPEG", quality=88)
    return out

def apply_idea_text_on_images(caption, urls, used_idea=""):
    """If the idea asks for text written ON the images, overlay it."""
    if not (used_idea and urls):
        return urls
    plan = llm([
        {"role": "system", "content": "Does the user's idea ask for text to be written ON the images themselves (not in the caption)? If yes, output ONLY a JSON array with one short HEBREW text string per image (max 6 words each) - translate whatever language the idea is written in to Hebrew; the business audience is Israeli. If no, output ONLY []."},
        {"role": "user", "content": f"Idea: {used_idea[:500]}"}], max_tokens=500)
    try:
        arr = json.loads(plan[plan.index("["):plan.rindex("]")+1])
    except Exception:
        return urls
    if not isinstance(arr, list) or not arr:
        return urls
    out = []
    import subprocess as sp
    for i, u in enumerate(urls):
        local = f"/tmp/ovl_{i}.jpg"
        sp.run(["curl", "-s", "-o", local, u], timeout=60)
        if os.path.exists(local) and os.path.getsize(local) > 10000 and i < len(arr):
            newp = overlay_text(local, str(arr[i]))
            pub = upload_public(newp)
            out.append(pub or u)
            os.remove(local)
        else:
            out.append(u)
    return out

def _consume_idea(used_idea):
    """Remove the LAST idea entry (the one daily() always picks). Position-based, not string-based."""
    if not os.path.exists(IDEAS_FILE):
        return
    all_lines = open(IDEAS_FILE).readlines()
    last_start = None
    for i, l in enumerate(all_lines):
        if l.strip().startswith("- "):
            last_start = i  # last entry header
    if last_start is None:
        return
    remaining = [l for i, l in enumerate(all_lines) if i < last_start]
    open(IDEAS_FILE, "w").write("".join(remaining))

def handle_callback(payload):
    data = payload.get("callback_query") or {}
    if data:
        action = data.get("data")
        cb_id = data["id"]
        try:
            tg("answerCallbackQuery", callback_query_id=cb_id)
        except Exception:
            pass  # stale/expired callback id - continue with the action anyway
        st = json.load(open(f"{STATE}/pending.json")) if os.path.exists(f"{STATE}/pending.json") else None
        if not st:
            tg("sendMessage", chat_id=GROUP, text="אין טיוטה פעילה."); return
        if action == "approve":
            tg("editMessageReplyMarkup", chat_id=GROUP, message_id=st["msg_id"], reply_markup="")
            publish(st["caption"], st.get("image_urls") or st.get("image_url"))
        elif action in ("tweak", "total"):
            st["mode"] = action
            json.dump(st, open(f"{STATE}/pending.json", "w"), ensure_ascii=False)
            if action == "tweak":
                tg("sendMessage", chat_id=GROUP, text="מה לשנות? כתבו את השינוי בהודעה 👇")
            else:
                revise(st, "total")
    else:
        # plain message: either feedback text (tweak pending) or a photo for the folder or /run
        m = payload.get("message") or {}
        chat = m.get("chat", {})
        if str(chat.get("id")) != str(GROUP): return
        if "photo" in m:
            fid = m["photo"][-1]["file_id"]
            fi = tg("getFile", file_id=fid)
            path = fi["result"]["file_path"]
            urllib.request.urlretrieve(f"https://api.telegram.org/file/bot{BOT}/{path}",
                                       os.path.join(PHOTOS, f"{int(time.time())}.jpg"))
            tg("sendMessage", chat_id=GROUP, text=f"📸 נשמר לתיקיית התמונות (סה״כ {len([f for f in os.listdir(PHOTOS)])})")
        elif m.get("text", "").strip() == "/run":
            daily()
        elif m.get("text", "").strip() == "/clearideas":
            open(IDEAS_FILE, "w").write("")
            tg("sendMessage", chat_id=GROUP, text="🧹 רשימת הרעיונות נוקתה")
        elif m.get("text", "").strip() == "/ideas":
            n = len([l for l in open(IDEAS_FILE)] ) if os.path.exists(IDEAS_FILE) else 0
            body = open(IDEAS_FILE).read()[-3000:] if os.path.exists(IDEAS_FILE) else "ריק עדיין"
            tg("sendMessage", chat_id=GROUP, text=f"💡 רעיונות ({n}):\n{body}")
        elif m.get("text"):
            st = json.load(open(f"{STATE}/pending.json")) if os.path.exists(f"{STATE}/pending.json") else None
            if st and st.get("mode") == "tweak":
                tg("sendMessage", chat_id=GROUP, text="✏️ עובד על השינוי...")
                revise(st, "tweak", m["text"])
            elif m["text"].strip().startswith("/"):
                tg("sendMessage", chat_id=GROUP, text="🤖 פקודות: /run טיוטה חדשה · /ideas הצגת רעיונות · או פשוט שלחו טקסט = רעיון")
            else:
                n = add_idea(m["text"], m.get("from", {}).get("first_name"))
                tg("sendMessage", chat_id=GROUP, text=f"💡 נשמר כרעיון לפוסט (סה״כ {n})")

def revise(st, mode, feedback=None):
    sysmsg = "כותב פוסטים ל-'Atelier vert' (עיצוב נוף ומרפסות, ישראל, עברית). החזר רק את טקסט הפוסט, עד 400 תווים."
    user = f"טכניקות:\n{st.get('technique','')}\n\nטיוטה קודמת:\n{st['caption']}"
    if mode == "tweak":
        user += f"\n\nשינוי מבוקש: {feedback}. החל אותו ושמור על השאר."
    else:
        user += "\n\nהטיוטה נדחתה. כתוב זווית שונה לגמרי - נושא ופתיחה אחרים."
    caption = llm([{"role": "system", "content": sysmsg}, {"role": "user", "content": user}], max_tokens=2000)
    if mode == "total":
        urls = []
        for i in range(len(st.get("image_urls", [])) or 1):
            ip = llm([{"role": "system", "content": "ONE English image prompt (max 50 words) for a high-end, professionally designed urban balcony/rooftop — award-winning landscape-architecture look, NOT a plain balcony with a few pots. Mention deliberate design elements: layered planting (architectural plants, flowering shrubs, trailing greenery), quality materials (wood decking, corten/stone planters, privacy screen), furniture and warm lighting. Photorealistic, editorial quality, completely different composition than before. No text or logos. Output only the prompt."},
                      {"role": "user", "content": caption[:400]}], max_tokens=1000)
            urls.append(gen_image(ip))
        urls = urls
    else:
        urls = st.get("image_urls") or st.get("image_url")
    for mid in ([st["msg_id"]] + ([st.get("btn_msg_id")] if st.get("btn_msg_id") else [])):
        try: tg("deleteMessage", chat_id=GROUP, message_id=mid)
        except Exception: pass
    send_draft(caption, urls, {"mode": None, "technique": st.get("technique", "")})

if __name__ == "__main__":
    mode = sys.argv[1]
    if mode == "daily":
        daily()
    elif mode == "callback":
        handle_callback(json.loads(sys.argv[2]))
