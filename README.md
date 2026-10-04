# Clubapp VPN

[English](#english) · [فارسی](#فارسی)

A Windows desktop client (Python / Tkinter) for the [Aether](https://github.com/CluvexStudio/Aether) censorship-circumvention core.
It runs Aether (2.1+) as a local SOCKS5 proxy or as a full system VPN, and can chain **Psiphon** or **Tor** through the WARP tunnel.

- Telegram: <https://t.me/Clubapp8>
- Android version: <https://github.com/Mrmmd2004/ClubappVpnAndroid>
- Author: [@Mrmmd2004](https://github.com/Mrmmd2004)

<img width="819" height="758" alt="Screenshot 2026-10-04" src="https://github.com/user-attachments/assets/26373faa-b0fd-46d3-b1c3-e43a9eb7fcf4" />

---

## English

### Features

- Home screen with three engines: **Aether**, **Psiphon**, **Tor** — click one to see its options
- Aether transports: MASQUE / HTTP-3, MASQUE / HTTP-2, WireGuard, WARP-in-WARP, MASQUE-in-MASQUE
- Psiphon: auto / CDN-fronting only / direct, exit-country picker, custom CDN edges and config; runs inside the WARP tunnel, dialled through it, or standalone
- Tor: bridge handling (auto / at once / never), onionoo relays, your own bridge lines or bridge file
- Full **VPN mode** (`tun2socks` + Wintun driver) or system-proxy-only mode
- In-app download / update of the Aether core from its GitHub releases
- Scan mode, obfuscation ("noize") profile, IP version, exit-location filter, DNS, log level
- Automatic reconnect with a fresh gateway scan after a real tunnel drop
- Windows installer (Inno Setup) and portable build

### What's new in 2.8.8

- **Psiphon datastore fix.** Psiphon now gets its own data folder (`psiphon-data`) via `--psiphon-dir`. If it reports `tryDatastoreOpenDB ... timeout`, the app kills leftover helper processes and retries with a fresh data folder (up to 3 times).
- **No more orphaned helpers.** Stopping the core now kills the whole process tree (`taskkill /T /F`) instead of only `aether.exe`, so `psiphon-tunnel-core.exe` can no longer stay alive and keep the datastore locked.
- **Smarter reconnect.** After a dropped tunnel the app reconnects with `--no-quick-reconnect`, so it does not reuse the gateway that was just throttled.
- **Cleaner Psiphon status.** The connection test waits for `psiphon is ready` instead of firing too early and showing a false "SOCKS5 connect failed".
- GitHub link at the end of Settings.

### Quick start

1. Download the release zip and extract it (do not run it from inside the archive).
2. Start `clubapp_vpn.exe` (or `python clubapp_vpn.py`).
3. If the core is missing: **Settings → Download / update core**.
4. Pick an engine on the Home page and press connect.

For full VPN mode you also need `tun2socks-windows-amd64.exe` and `wintun.dll` (see below) and administrator rights.

### Running from source

```bash
pip install pillow   # optional: crisp navigation icons
python clubapp_vpn.py
```

Place next to the script (Settings can fetch or point to them):

| File | Needed for | Where to get it |
|---|---|---|
| `aether.exe` + `pt/` | everything | Settings → *Download / update core* ([Aether releases](https://github.com/CluvexStudio/Aether)) |
| `tun2socks-windows-amd64.exe` | VPN mode only | [tun2socks releases](https://github.com/xjasonlyu/tun2socks/releases) |
| `wintun.dll` | VPN mode only | [wintun.net](https://www.wintun.net/) (match your CPU architecture) |

### Building

```bash
pyinstaller clubapp_vpn.spec        # clubapp_vpn.exe
python build_portable.py            # portable build
python build_installer.py           # writes ClubappVPN.iss and compiles it if Inno Setup is installed
```

### Troubleshooting

**Psiphon: `tryDatastoreOpenDB failed ... timeout`**
Psiphon cannot open its database, usually because a leftover `psiphon-tunnel-core.exe` from a previous run still holds the lock. Version 2.8.8 handles this automatically. By hand:

```
taskkill /F /T /IM psiphon-tunnel-core.exe
taskkill /F /T /IM aether.exe
```

or run the helper (from CMD, ideally as administrator):

```
python fix_aether.py            # kill leftovers, free proxy ports
python fix_aether.py --clean    # ...and also reset the Psiphon datastore
```

If it still times out with a brand-new data folder, an antivirus / Defender is probably blocking `psiphon-tunnel-core.exe` — add the `pt` folder and `%APPDATA%\ClubappVPN` to the exclusions.

**Aether disconnects after a few minutes**
Often the selected gateway is being throttled. Turn **Quick reconnect** off, try the **MASQUE / HTTP-2** transport (TCP/443) instead of WireGuard (UDP), or a stronger noize profile (`gfw` / `aggressive`).

**`Connection test failed` right after Psiphon starts**
Normal for a few seconds — Psiphon opens its port before its tunnel is built. 2.8.8 waits for `psiphon is ready` before testing.

### Repository contents

| Path | What it is |
|---|---|
| `clubapp_vpn.py` | The application (Tkinter UI + process management) |
| `clubapp_vpn.spec` | PyInstaller spec |
| `build_portable.py` / `build_portable.bat` | Portable build |
| `build_installer.py` | Generates the Inno Setup script and installer (`ClubappVPN.iss` is regenerated, not committed) |
| `fix_aether.py` | CMD helper that kills leftover Aether / Psiphon processes |
| `pt/` | *(not in git)* `psiphon-tunnel-core.exe`, `lyrebird.exe` — come with the Aether download |
| `run-aether.bat` | Run the Aether core standalone |
| `all_helps.txt` | Reference dump of `aether --help` |
| `icon.ico` | App icon |

Large third-party binaries (`aether.exe`, `tun2socks`, `wintun.dll`) are intentionally kept out of git — the app downloads them or you fetch them once from their upstream pages. See `.gitignore`.

### Source code & links

| Project | Role | Source / download |
|---|---|---|
| Clubapp VPN (this app) | Windows GUI | <https://github.com/Mrmmd2004> |
| Clubapp VPN for Android | Android version | <https://github.com/Mrmmd2004/ClubappVpnAndroid> |
| Aether | circumvention core (WARP / MASQUE / WireGuard) | <https://github.com/CluvexStudio/Aether> |
| Psiphon tunnel core | Psiphon engine | <https://github.com/Psiphon-Labs/psiphon-tunnel-core> |
| Tor | Tor engine | <https://gitlab.torproject.org/tpo/core/tor> · <https://www.torproject.org> |
| lyrebird | Tor pluggable transports (bridges) | <https://gitlab.torproject.org/tpo/anti-censorship/pluggable-transports/lyrebird> |
| tun2socks | system-wide VPN mode | <https://github.com/xjasonlyu/tun2socks> |
| Wintun | virtual network adapter driver | <https://www.wintun.net/> · <https://git.zx2c4.com/wintun> |
| PyInstaller | builds the `.exe` | <https://github.com/pyinstaller/pyinstaller> |
| Inno Setup | builds the installer | <https://jrsoftware.org/isinfo.php> |
| Pillow | optional icon rendering | <https://github.com/python-pillow/Pillow> |
| Telegram channel | news & support | <https://t.me/Clubapp8> |

### Credits & license

Powered by [Aether](https://github.com/CluvexStudio/Aether) by CluvexStudio (AGPL-3.0), [Psiphon](https://psiphon.ca/), Tor / lyrebird, [tun2socks](https://github.com/xjasonlyu/tun2socks) and [Wintun](https://www.wintun.net/).
Because Aether is AGPL-3.0, check its terms before redistributing a bundle that includes it. Add your own license file for this project's code (e.g. MIT) — none is declared yet.

---

## فارسی

یک کلاینت دسکتاپ ویندوزی (پایتون / Tkinter) برای هسته‌ی دور زدن فیلترینگ [Aether](https://github.com/CluvexStudio/Aether).
این برنامه Aether (نسخه‌ی ۲.۱ به بعد) را به‌صورت پراکسی SOCKS5 محلی یا VPN کامل سیستمی اجرا می‌کند و می‌تواند **Psiphon** یا **Tor** را داخل تونل WARP اجرا کند.

### امکانات

- صفحه‌ی اصلی با سه موتور: **Aether**، **Psiphon** و **Tor** — با کلیک روی هر کدام گزینه‌هایش نمایش داده می‌شود
- ترنسپورت‌های Aether: ‏MASQUE / HTTP-3، ‏MASQUE / HTTP-2، ‏WireGuard، ‏WARP-in-WARP و MASQUE-in-MASQUE
- Psiphon: خودکار / فقط CDN / مستقیم، انتخاب کشور خروجی، CDN و کانفیگ دلخواه؛ داخل تونل WARP، از طریق آن، یا مستقل
- Tor: مدیریت بریج (خودکار / فوری / هرگز)، ریلی‌های onionoo، بریج یا فایل بریج دلخواه
- **حالت VPN کامل** (با `tun2socks` و درایور Wintun) یا فقط پراکسی سیستمی
- دانلود و به‌روزرسانی هسته‌ی Aether از داخل برنامه
- تنظیم حالت اسکن، پروفایل مبهم‌سازی (noize)، نسخه‌ی IP، فیلتر محل خروجی، DNS و سطح لاگ
- اتصال مجدد خودکار با اسکن تازه‌ی گیت‌وی بعد از قطع واقعی تونل
- نصب‌کننده‌ی ویندوز (Inno Setup) و نسخه‌ی پرتابل

### تغییرات نسخه‌ی ۲.۸.۸

- **رفع خطای دیتابیس Psiphon.** حالا Psiphon پوشه‌ی دیتای مخصوص خودش (`psiphon-data`) را با `--psiphon-dir` می‌گیرد. اگر خطای `tryDatastoreOpenDB ... timeout` بدهد، برنامه پروسه‌های باقی‌مانده را می‌کُشد و با یک پوشه‌ی دیتای جدید دوباره امتحان می‌کند (تا ۳ بار).
- **دیگر پروسه‌ی یتیم نمی‌ماند.** هنگام توقف، کل درخت پروسه (`taskkill /T /F`) کشته می‌شود، نه فقط `aether.exe`؛ پس `psiphon-tunnel-core.exe` نمی‌ماند تا دیتابیس را قفل نگه دارد.
- **اتصال مجدد هوشمندتر.** بعد از قطع تونل با `--no-quick-reconnect` وصل می‌شود تا همان گیت‌وی محدود‌شده دوباره استفاده نشود.
- **وضعیت Psiphon تمیزتر.** تست اتصال تا پیام `psiphon is ready` صبر می‌کند و دیگر خطای الکی «SOCKS5 connect failed» نمی‌دهد.
- لینک گیت‌هاب در انتهای صفحه‌ی تنظیمات.

### شروع سریع

1. زیپ را دانلود و استخراج کنید (از داخل خود آرشیو اجرا نکنید).
2. فایل `clubapp_vpn.exe` را اجرا کنید (یا `python clubapp_vpn.py`).
3. اگر هسته نصب نیست: **Settings ← Download / update core**.
4. در صفحه‌ی اصلی یک موتور را انتخاب و وصل شوید.

برای حالت VPN کامل به `tun2socks-windows-amd64.exe` و `wintun.dll` و دسترسی ادمین هم نیاز دارید.

### اجرا از روی سورس

```bash
pip install pillow   # اختیاری، برای آیکون‌های شفاف‌تر
python clubapp_vpn.py
```

| فایل | برای چه | از کجا |
|---|---|---|
| `aether.exe` و پوشه‌ی `pt/` | همه‌چیز | Settings ← *Download / update core* |
| `tun2socks-windows-amd64.exe` | فقط حالت VPN | [ریلیزهای tun2socks](https://github.com/xjasonlyu/tun2socks/releases) |
| `wintun.dll` | فقط حالت VPN | [wintun.net](https://www.wintun.net/) (متناسب با معماری CPU) |

### ساخت

```bash
pyinstaller clubapp_vpn.spec        # ساخت clubapp_vpn.exe
python build_portable.py            # نسخه‌ی پرتابل
python build_installer.py           # ساخت ClubappVPN.iss و کامپایل با Inno Setup (در صورت نصب بودن)
```

### رفع مشکل

**Psiphon: خطای `tryDatastoreOpenDB failed ... timeout`**
Psiphon نمی‌تواند دیتابیسش را باز کند؛ معمولاً چون یک `psiphon-tunnel-core.exe` باقی‌مانده از اجرای قبلی هنوز قفل را نگه داشته است. نسخه‌ی ۲.۸.۸ این را خودکار حل می‌کند. دستی:

```
taskkill /F /T /IM psiphon-tunnel-core.exe
taskkill /F /T /IM aether.exe
```

یا اسکریپت کمکی (در CMD، ترجیحاً با دسترسی ادمین):

```
python fix_aether.py            # پروسه‌های باقی‌مانده را می‌کشد و پورت‌ها را آزاد می‌کند
python fix_aether.py --clean    # و دیتابیس Psiphon را هم ریست می‌کند
```

اگر با پوشه‌ی دیتای کاملاً تازه هم باز timeout داد، احتمالاً آنتی‌ویروس / Defender جلوی `psiphon-tunnel-core.exe` را گرفته؛ پوشه‌ی `pt` و `%APPDATA%\ClubappVPN` را به exclusions اضافه کنید.

**Aether بعد از چند دقیقه قطع می‌شود**
معمولاً گیت‌وی انتخاب‌شده throttle می‌شود. **Quick reconnect** را خاموش کنید، ترنسپورت **MASQUE / HTTP-2** (روی TCP/443) را به‌جای WireGuard (UDP) امتحان کنید، یا پروفایل noize قوی‌تر (`gfw` / `aggressive`).

**`Connection test failed` درست بعد از شروع Psiphon**
چند ثانیه عادی است؛ Psiphon پورتش را قبل از ساخته شدن تونل باز می‌کند. نسخه‌ی ۲.۸.۸ قبل از تست منتظر `psiphon is ready` می‌ماند.

### محتویات ریپازیتوری

| مسیر | توضیح |
|---|---|
| `clubapp_vpn.py` | خود برنامه (رابط Tkinter + مدیریت پروسه‌ها) |
| `clubapp_vpn.spec` | مشخصات PyInstaller |
| `build_portable.py` / `build_portable.bat` | ساخت نسخه‌ی پرتابل |
| `build_installer.py` | تولید اسکریپت و نصب‌کننده‌ی Inno Setup (فایل `ClubappVPN.iss` هر بار ساخته می‌شود و commit نمی‌شود) |
| `fix_aether.py` | اسکریپت کمکی CMD برای کشتن پروسه‌های باقی‌مانده Aether / Psiphon |
| `pt/` | *(در گیت نیست)* فایل‌های `psiphon-tunnel-core.exe` و `lyrebird.exe` — همراه دانلود Aether می‌آیند |
| `run-aether.bat` | اجرای مستقل هسته‌ی Aether |
| `all_helps.txt` | خروجی مرجع `aether --help` |
| `icon.ico` | آیکون برنامه |

باینری‌های حجیم شخص ثالث (`aether.exe`، `tun2socks`، `wintun.dll`) عمداً در گیت نیستند؛ برنامه آن‌ها را دانلود می‌کند یا یک‌بار از صفحه‌ی اصلی خودشان می‌گیرید. فایل `.gitignore` را ببینید.

### سورس و لینک‌ها

| پروژه | نقش | سورس / دانلود |
|---|---|---|
| Clubapp VPN (همین برنامه) | رابط گرافیکی ویندوز | <https://github.com/Mrmmd2004> |
| Clubapp VPN اندروید | نسخه‌ی اندروید | <https://github.com/Mrmmd2004/ClubappVpnAndroid> |
| Aether | هسته‌ی دور زدن فیلترینگ (WARP / MASQUE / WireGuard) | <https://github.com/CluvexStudio/Aether> |
| Psiphon tunnel core | موتور Psiphon | <https://github.com/Psiphon-Labs/psiphon-tunnel-core> |
| Tor | موتور Tor | <https://gitlab.torproject.org/tpo/core/tor> · <https://www.torproject.org> |
| lyrebird | پلاگبل ترنسپورت‌های Tor (بریج) | <https://gitlab.torproject.org/tpo/anti-censorship/pluggable-transports/lyrebird> |
| tun2socks | حالت VPN سیستمی | <https://github.com/xjasonlyu/tun2socks> |
| Wintun | درایور کارت شبکه‌ی مجازی | <https://www.wintun.net/> · <https://git.zx2c4.com/wintun> |
| PyInstaller | ساخت فایل `.exe` | <https://github.com/pyinstaller/pyinstaller> |
| Inno Setup | ساخت نصب‌کننده | <https://jrsoftware.org/isinfo.php> |
| Pillow | رندر اختیاری آیکون‌ها | <https://github.com/python-pillow/Pillow> |
| کانال تلگرام | اخبار و پشتیبانی | <https://t.me/Clubapp8> |

### اعتبار و لایسنس

بر پایه‌ی [Aether](https://github.com/CluvexStudio/Aether) از CluvexStudio (AGPL-3.0)، ‏[Psiphon](https://psiphon.ca/)، ‏Tor / lyrebird، ‏[tun2socks](https://github.com/xjasonlyu/tun2socks) و [Wintun](https://www.wintun.net/).
چون Aether زیر AGPL-3.0 است، پیش از بازتوزیع بسته‌ای که آن را شامل می‌شود شرایطش را بررسی کنید. برای کد خود پروژه یک فایل لایسنس (مثلاً MIT) اضافه کنید — هنوز لایسنسی اعلام نشده است.

---

<div align="center">

Made by [Mrmmd2004](https://github.com/Mrmmd2004) · [Telegram @Clubapp8](https://t.me/Clubapp8)

</div>
