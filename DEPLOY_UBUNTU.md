# تشغيل البوت على خادم Ubuntu بوضع Polling

هذا الدليل يشغّل البوت داخل Docker بوضع **Polling**. في هذا الوضع يتصل البوت بخوادم Telegram اتصالًا صادرًا فقط، لذلك لا يحتاج اسم نطاق أو شهادة TLS أو فتح منفذ وارد. تحفظ وحدات التخزين إعدادات `/settings` وقبول شروط الاستخدام وملفات التنزيل المؤقتة عند إعادة بناء الحاوية أو إعادة تشغيل الخادم.

> **تنبيه أمني:** لا تضع `TELEGRAM_BOT_TOKEN` في GitHub أو في رسالة أو لقطة شاشة. أدخله مباشرةً في ملف `.env` على الخادم، واحفظ الملف بصلاحية المالك فقط.

## المتطلبات

يلزم خادم Ubuntu بمعمارية 64-bit، واتصال إنترنت صادر، ومستخدم يملك صلاحية `sudo`. يوثّق Docker دعم Ubuntu 22.04 و24.04 لتثبيت Docker Engine من مستودعه الرسمي.[1]

| المتطلب | الغرض |
|---|---|
| Docker Engine وDocker Compose Plugin | بناء الحاوية وتشغيلها وإعادة تشغيلها تلقائيًا |
| Git | تنزيل المشروع وتحديثه لاحقًا |
| توكن البوت | تشغيل اتصال Telegram |
| `TELEGRAM_OWNER_ID` | يبقي البوت محصورًا في حساب المالك ويؤمّن الإعدادات |

## 1. تثبيت Docker

إذا كان Docker و`docker compose` مثبتين بالفعل، تحقق من ذلك ثم انتقل إلى القسم التالي:

```bash
docker --version
docker compose version
```

أما للخادم الجديد، فاستخدم مستودع Docker الرسمي. لا تستخدم سكربت التثبيت المختصر على خادم إنتاجي؛ توصي وثائق Docker بمسار مستودع الحزم لأنه أسهل في الصيانة والتحديث.[1]

```bash
sudo apt update
sudo apt install -y ca-certificates curl git
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc

sudo tee /etc/apt/sources.list.d/docker.sources > /dev/null <<EOF
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: $(. /etc/os-release && echo "${UBUNTU_CODENAME:-$VERSION_CODENAME}")
Components: stable
Architectures: $(dpkg --print-architecture)
Signed-By: /etc/apt/keyrings/docker.asc
EOF

sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo systemctl enable --now docker
sudo docker run --rm hello-world
```

تستخدم الأوامر السابقة الحزم الرسمية `docker-ce` و`docker-compose-plugin`، وتحقق Docker من التثبيت عبر صورة `hello-world`.[1] [2]

## 2. تنزيل المشروع

أنشئ مجلدًا ثابتًا للتطبيق ثم نزّل الفرع الذي يحتوي على آخر نسخة من البوت:

```bash
sudo mkdir -p /opt/legal-torrent-bot
sudo chown "$USER":"$USER" /opt/legal-torrent-bot

git clone --branch claude/session-guidance-afegtt --single-branch \
  https://github.com/dawood7919/Price-tracker.git \
  /opt/legal-torrent-bot

cd /opt/legal-torrent-bot
```

إذا كان المستودع خاصًا، استخدم مفتاح نشر SSH محدود الصلاحية أو رمز وصول قصير العمر مع صلاحية القراءة فقط. لا تضع رمز الوصول في رابط محفوظ داخل ملف أو سجل الأوامر.

## 3. إنشاء ملف الإعدادات

انسخ الملف النموذجي ثم افتحه بالمحرر:

```bash
cd /opt/legal-torrent-bot
cp .env.example .env
chmod 600 .env
nano .env
```

ضع القيم التالية، واستبدل القيم بين الأقواس بقيمك الحقيقية. اترك أي إعداد `WEBHOOK_URL` فارغًا أو محذوفًا؛ وضع Polling لا يستخدمه.

```env
TELEGRAM_BOT_TOKEN=ضع_توكن_البوت_هنا
TELEGRAM_OWNER_ID=ضع_رقم_حسابك_الرقمي_هنا

# محرك التورنت الرسمي
TORRENT_SOURCES=ubuntu,debian,fedora
TORRENT_MAX_RESULTS=30

# حدود التشغيل المناسبة لخادم صغير؛ عدّلها بحسب مواردك
MAX_CONCURRENT_DOWNLOADS=3
MIN_FREE_DISK_MB=2048
DOWNLOAD_TIMEOUT_SECONDS=1800
```

لا تضف `TELEGRAM_API_ID` أو `TELEGRAM_API_HASH` إن كنت تريد إعدادًا بسيطًا. عند غيابهما يستخدم البوت واجهة Telegram السحابية ويقسم الملفات تلقائيًا لتناسب حد الرفع. أضفهما فقط إذا كنت تعرف أنك تحتاج Local Bot API وحد رفع أكبر وتملك الموارد اللازمة لتشغيله.

## 4. البناء والتشغيل الدائم

شغّل الحاوية في الخلفية:

```bash
cd /opt/legal-torrent-bot
sudo docker compose up -d --build
```

يستخدم `compose.yaml` سياسة `restart: unless-stopped`؛ لذلك سيعود البوت تلقائيًا بعد إعادة تشغيل Docker أو الخادم، ما لم توقفه أنت صراحةً. لا توجد أي منافذ منشورة في وضع Polling، لذلك لا تحتاج إلى تعديل جدار الحماية من أجل البوت.

## 5. التحقق الأولي

تحقق من أن الحاوية تعمل ثم راقب السجل عند البداية:

```bash
cd /opt/legal-torrent-bot
sudo docker compose ps
sudo docker compose logs --tail=100 -f
```

يجب أن يظهر سجل يتضمن `Polling mode` بدلًا من Webhook. بعد ذلك افتح البوت في Telegram، وأرسل `/start`، واقبل شروط الاستخدام، ثم جرّب `/torrent ubuntu` أو البحث المضمّن بكتابة `t fedora` بعد اسم البوت.

## أوامر الإدارة اليومية

| العملية | الأمر |
|---|---|
| عرض حالة الحاوية | `sudo docker compose ps` |
| متابعة السجل | `sudo docker compose logs -f --tail=100` |
| إعادة تشغيل البوت | `sudo docker compose restart` |
| إيقاف البوت مؤقتًا | `sudo docker compose stop` |
| تشغيله بعد الإيقاف | `sudo docker compose start` |
| تحديث الصورة بعد تعديل الإعدادات | `sudo docker compose up -d --build` |
| حذف الحاوية مع الإبقاء على الحالة | `sudo docker compose down` |

> لا تستخدم `sudo docker compose down -v` إلا إذا أردت حذف حالة البوت المحفوظة، بما فيها قبول الشروط واختيارات `/settings`.

## تحديث المشروع لاحقًا

نفّذ الأوامر التالية من مجلد المشروع. يتوقف الأمر إذا كانت هناك تغييرات محلية بدلًا من استبدالها بصمت:

```bash
cd /opt/legal-torrent-bot
git pull --ff-only origin claude/session-guidance-afegtt
sudo docker compose up -d --build --remove-orphans
sudo docker compose logs --tail=100
```

## استكشاف المشكلات

| العرض | الإجراء المقترح |
|---|---|
| الحاوية تتوقف فورًا | شغّل `sudo docker compose logs --tail=200` وتحقق أولًا من صحة `TELEGRAM_BOT_TOKEN`. |
| البوت لا يرد | تأكد من أن الحاوية `running` عبر `sudo docker compose ps`، ثم تحقق من عدم وجود نسخة ثانية من البوت تستخدم التوكن نفسه. |
| رسالة أن البوت مقفول | ضع رقمك الرقمي الصحيح في `TELEGRAM_OWNER_ID` ثم نفّذ `sudo docker compose up -d --build`. |
| امتلاء المساحة | راقب `df -h` وخفّض `MAX_CONCURRENT_DOWNLOADS` أو ارفع `MIN_FREE_DISK_MB`. |
| اختيارات `/settings` تختفي | لا تستخدم `down -v`، وتحقق أن وحدة `bot_state` موجودة عبر `sudo docker volume ls`. |

## المراجع

[1] [Docker Engine: Install on Ubuntu](https://docs.docker.com/engine/install/ubuntu/)

[2] [Docker Compose Plugin: Install on Linux](https://docs.docker.com/compose/install/linux/)
