# PROJECT_CONTEXT — دليل حي للبوت

> **الغرض:** أي ذكاء اصطناعي أو مطوّر يشتغل على المشروع **يقرأ هذا الملف أولًا** قبل أي تعديل.
> **قاعدة ذهبية:** بعد كل إضافة أو حذف أو تغيير سلوك مهم → حدّث هذا الملف في نفس الـ commit أو مباشرة بعده.

**آخر تحديث:** 2026-07-30
**الفرع النشط للتشغيل:** `claude/telegram-bot-learning-app-1uhdf0`
**المستودع:** `dawood7919/learning-programming-app` (قد يكون Private)

---

## 1) ما هو المشروع؟

بوت تيليجرام (Python + `python-telegram-bot` v21) يحمّل فيديوهات من روابط عبر **yt-dlp**، يرفعها للمستخدم، ويدعم:

| المجال | الوصف |
|--------|--------|
| تحميل فيديو/صوت | رابط → فحص → اختيار جودة → تنزيل → رفع |
| بلايليست | أول N فيديو |
| تقسيم الملفات الكبيرة | لو تجاوز حد الرفع |
| `/secret` | بحث **wow.xxx فقط** (صاحب البوت) |
| Inline | `@bot كلمة` → نتائج wow.xxx |
| تورينت | بحث المصادر الرسمية Ubuntu وDebian وFedora + رفع ملف `.torrent` |
| أدوات | scan، pdf، stats، logs، killall، speedtest، cookies |

**مهم:** تم **إزالة كل مميزات الـ AI** (`/ai`، chat mode، ai_tools، Groq). لا تستخدمها ولا تعيدها إلا بطلب صريح من المالك.

---

## 2) هيكل الملفات

```
learning-programming-app/
├── main.py                 # نقطة التشغيل، handlers، webhook/polling
├── start.sh                # يشغّل Local Bot API اختياريًا ثم python main.py
├── Dockerfile              # Alpine + ffmpeg + aria2 + chromium + bot-api binary
├── requirements.txt
├── .env.example
├── PROJECT_CONTEXT.md      # ← هذا الملف (حدّثه دائمًا)
└── bot/
    ├── config.py           # pydantic-settings من البيئة
    ├── downloader.py       # yt-dlp + aria2c
    ├── uploader.py         # رفع تيليجرام + تقسيم
    ├── jobs.py             # run_download_job + StatusReporter
    ├── manager.py          # طابور التحميلات / إلغاء
    ├── splitter.py         # تقسيم الملفات الكبيرة
    ├── torrentdl.py        # aria2c BitTorrent
    ├── pagescan.py         # استخراج روابط من صفحة
    ├── pdfconvert.py       # HTML/URL → PDF عبر Chromium
    ├── netspeed.py         # speedtest
    ├── validators.py       # استخراج/التحقق من URL
    ├── utils.py            # تنسيق، ديسك، إشعار المالك
    └── handlers/
        ├── commands.py     # /start /help /stats
        ├── messages.py     # رسائل نصية = روابط فيديو
        ├── callbacks.py    # أزرار dl/pl/cancel + توجيه secret*
        ├── secret.py       # /secret + inline + callbacks (wow.xxx فقط)
        ├── torrent.py      # /torrent + inline t/ubuntu + .torrent
        ├── scan.py         # /scan
        ├── pdf.py          # /pdf
        ├── cookies.py      # /setcookies
        └── tools.py        # /killall /logs /speedtest
```

**ملفات AI محذوفة (لا تعد إنشاؤها بدون طلب):**
- `bot/handlers/ai_cmd.py`
- `bot/ai_client.py`
- `bot/ai_tools.py`

---

## 3) الأوامر والأدوات (للمستخدم النهائي)

### عامة (أي مستخدم)
| أمر / فعل | الاستخدام | الملف |
|-----------|-----------|--------|
| رسالة برابط `http(s)` | فحص الفيديو وعرض أزرار الجودة | `handlers/messages.py` |
| `/start` | ترحيب وملخص | `handlers/commands.py` |
| `/help` | شرح الاستخدام | `handlers/commands.py` |
| `/stats` | CPU/RAM/Disk/طابور | `handlers/commands.py` |
| `/scan <url>` | استخراج روابط فيديو من صفحة | `handlers/scan.py` |
| `/pdf <url>` | تحويل صفحة لـ PDF | `handlers/pdf.py` |
| `/torrent <كلمة>` | بحث مصادر Ubuntu/Debian/Fedora الرسمية | `handlers/torrent.py` |
| رفع ملف `.torrent` | تنزيل محتوى التورينت | `handlers/torrent.py` |
| `/speedtest` | قياس سرعة السيرفر | `handlers/tools.py` |
| `/logs` | آخر اللوج | `handlers/tools.py` |
| `/killall` | إلغاء كل التحميلات ومسح temp | `handlers/tools.py` |
| أزرار `dl:` / `pl:` / `cancel:` | بدء/إلغاء تحميل | `handlers/callbacks.py` |

### صاحب البوت فقط (`TELEGRAM_OWNER_ID`)
| أمر / فعل | الاستخدام | الملف |
|-----------|-----------|--------|
| `/secret search <كلمات>` أو `/secret s <كلمات>` | بحث على **wow.xxx** + صفحات + أزرار تحميل | `handlers/secret.py` |
| Inline: `@BotName <كلمات>` | نفس البحث داخل تيليجرام | `secret_inline_query` في `secret.py` |
| Inline: `@BotName t ubuntu` أو `ubuntu` أو `debian` أو `fedora` | بحث تورنت في المصادر الرسمية المفعّلة | `main.py` يوجّه لـ torrent |
| `/setcookies` ثم ملف/نص cookies | كوكيز yt-dlp | `handlers/cookies.py` |

### أزرار `/secret`
| `callback_data` | المعنى |
|-----------------|--------|
| `secretdl:<key>:<index>` | تحميل فيديو واحد من القائمة |
| `secretpage:<key>:<offset>` | صفحة تالية/سابقة |
| `secretrun:<key>:<offset>` | تحميل صفحة النتائج الحالية |
| `secretrunall:<key>` | تحميل كل النتائج (إن وُجد) |
| `secretdrop:<key>` | إلغاء القائمة |
| `secretdl_i:<key>` | تحميل من نتيجة Inline |

التوجيه: أي `callback` يبدأ بـ `secret` → `handle_secret_callback` عبر `callbacks.py`.

---

## 4) مسار التحميل (Pipeline)

```
رابط أو نتيجة secret
    → YTDLPDownloader.extract_info / download  (downloader.py)
    → DownloadManager طابور + cancel          (manager.py)
    → لو الحجم > upload_limit → splitter.py
    → UploadManager رفع لتيليجرام              (uploader.py)
    → StatusReporter يحدّث رسالة الحالة        (jobs.py)
```

- **حد الرفع:** ~50MB على Cloud API، حتى ~2GB لو Local Bot API شغال (`TELEGRAM_API_ID` + `TELEGRAM_API_HASH` عبر `start.sh`).
- **aria2c:** تنزيل متوازي لـ yt-dlp وللتورينت.

---

## 5) `/secret` و wow.xxx (تفاصيل تقنية)

- المصدر الوحيد: `https://www.wow.xxx/` (من `Config.secret_base_url`).
- رابط البحث: `/search/{slug}/` حيث المسافات → شرطات (`big ass` → `big-ass`).
- يحاول أيضًا `/relevance/` وصفحات 2.
- استخراج: regex على روابط `/videos/...` + عنوان + thumbnail.
- إعدادات:
  - `SECRET_PAGE_SIZE` (افتراضي 15)
  - `SECRET_MAX_RESULTS` (افتراضي 60)
- Inline: أي نص ≥ حرفين = بحث (بعد بوابة المالك). بادئة `s ` أو `search ` اختيارية.
- **BotFather:** لازم Inline Mode = ON وإلا الـ inline لا يعمل.

---

## 6) متغيرات البيئة

### مطلوب
```env
TELEGRAM_BOT_TOKEN=...
```

### مهم للـ secret والكوكيز
```env
TELEGRAM_OWNER_ID=1096429310   # آيدي تيليجرام الرقمي للمالك
```

### اختياري شائع
```env
# Webhook (Render/Railway) — لو فاضي → polling
WEBHOOK_URL=
PORT=8080

# Local Bot API (رفع 2GB)
TELEGRAM_API_ID=
TELEGRAM_API_HASH=
# أو TELEGRAM_BASE_URL=http://127.0.0.1:8081

# Cookies yt-dlp
YTDLP_COOKIES_FILE=
YTDLP_COOKIES_CONTENT=

# حدود
MAX_CONCURRENT_DOWNLOADS=20
MAX_PLAYLIST_ITEMS=10
MIN_FREE_DISK_MB=500
DOWNLOAD_TIMEOUT_SECONDS=1800

# secret
SECRET_BASE_URL=https://www.wow.xxx/
SECRET_PAGE_SIZE=15
SECRET_MAX_RESULTS=60

# المصادر القانونية الافتراضية للتورنت؛ يمكن تفعيل/تعطيل كل مصدر من /settings
TORRENT_SOURCES=ubuntu,debian,fedora
TORRENT_MAX_RESULTS=30
```

**لا يوجد بعد الآن:** `AI_API_KEY`, `AI_BASE_URL`, `AI_MODEL`, `AI_CHAT_MODE`, `SECRET_SITES`, `SECRET_PARALLEL_SITES`.

---

## 7) التشغيل على VPS (Docker)

```bash
cd ~/learning-programming-app
git fetch origin
git checkout claude/telegram-bot-learning-app-1uhdf0
git reset --hard origin/claude/telegram-bot-learning-app-1uhdf0

# مستودع خاص: استخدم توكن في clone/fetch ثم امسحه من remote
# git remote set-url origin https://TOKEN@github.com/dawood7919/learning-programming-app.git

docker rm -f video-bot 2>/dev/null || true
docker build -t video-bot .
docker run -d --name video-bot --restart unless-stopped \
  --env-file .env \
  -p 8080:8080 \
  -v video-bot-tmp:/tmp \
  video-bot

docker logs --tail 50 video-bot
```

`CMD` الصورة = `./start.sh` → optional local bot-api → `python main.py`.

---

## 8) قرارات تصميم مهمة (لا تكسرها بدون سبب)

1. **`/secret` للمالك فقط** — لا تفتحه للعامة.
2. **بحث secret = wow.xxx فقط** — المالك طلب إزالة المواقع المتعددة والـ AI.
3. **مصادر التورنت مقيّدة** — البحث يقتصر على Ubuntu وDebian وFedora الرسمية، ويمكن إدارة المصادر المفعّلة من `/settings`؛ ما يزال قبول ملف `.torrent` المباشر مدعومًا.
4. **Inline الافتراضي = secret**؛ تورينت يحتاج `t ` أو `torrent ` أو بداية استعلام بـ `ubuntu` أو `debian` أو `fedora`.
5. **Thumbnails في inline** قد يرفضها تيليجرام → الكود يعيد المحاولة بدون صور.
6. لا تخزّن أسرار (توكنات) في الكود أو في هذا الملف.

---

## 9) سجل التغييرات (Changelog مختصر)

| تاريخ | ماذا حصل |
|-------|----------|
| 2026-08-13 | إضافة قبول شروط الاستخدام ومحرك تورنت مدمج لمصادر Ubuntu وDebian وFedora الرسمية، مع إدارة المصادر من `/settings` |
| 2026-07-30 | إزالة كاملة للـ AI والملفات المرتبطة |
| 2026-07-30 | `/secret` يقتصر على wow.xxx + pagination + inline |
| 2026-07-30 | إصلاح توجيه callbacks لكل `secret*` |
| 2026-07-30 | Inline: أي نص بحث (مش شرط `s `) |
| قبل ذلك | تجربة multi-site + AI chat agent (أُلغيت) |
| 2026-07-30 | إضافة `/secret` الأصلي + بحث hyphen encoding |

*(أضف صفًا جديدًا هنا مع كل تعديل جوهري.)*

---

## 10) تعليمات لأي AI يعدّل المشروع

1. اقرأ **هذا الملف كاملًا** ثم الملف المستهدف فقط.
2. لا تُعد ميزة AI أو مواقع secret إضافية إلا بطلب المستخدم الصريح.
3. بعد التعديل:
   - حدّث القسم المناسب هنا (أوامر، ملفات، env، changelog).
   - لا تكسر بوابة `TELEGRAM_OWNER_ID` على secret.
4. اختبار يدوي مقترح:
   - `/start` → قبول شروط الاستخدام → `/stats`
   - `/torrent ubuntu` وinline: `t fedora`
   - رابط يوتيوب بسيط
   - `/secret search milf`
   - `@bot milf` (بعد تفعيل Inline في BotFather)
5. الفرع الافتراضي للعمل مع المالك: `claude/telegram-bot-learning-app-1uhdf0`.

---

## 11) مشاكل شائعة

| عرض | سبب محتمل | حل |
|-----|-----------|-----|
| Inline فاضي تمامًا | Inline Mode مقفول في BotFather | `/mybots` → Inline → On |
| Inline فاضي للمالك | `TELEGRAM_OWNER_ID` غلط | ضع آيديك الرقمي |
| `No such container: video-bot` | مش شغال أصلًا | `docker run ...` من غير rm |
| Private repo clone fail | مفيش PAT | توكن بصلاحية `repo` ثم امسحه |
| answer_inline_query error | thumbnails سيئة | الكود يعيد بدون thumbs — حدّث secret.py |
| نفس نتائج قليلة | كاش / صفحة واحدة | SECRET_MAX_RESULTS + صفحات relevance/2 |

---

*نهاية PROJECT_CONTEXT — حدّثني مع كل commit مهم.*
