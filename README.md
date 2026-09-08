# autoverif-dana

Bot auto-approve verifikasi login/pembayaran DANA di device vsphone.

Flow yang diotomasi (paket `id.dana`, bahasa English):

1. Layar **Login Verification** (`Request to login to CapCut using DANA`) -> tap **VERIFY**
2. Bottom-sheet **Beware of the scam methods!** -> tap **CONTINUE** (link teks kecil, bukan tombol biru CANCEL)
3. Layar **Login Request Approved** -> tap **GOT IT**

`REJECT` dan `CANCEL` tidak akan pernah disentuh.

## Status

- [x] Tahap 1: client API + SSH tunnel (paramiko) + recon uiautomator
- [x] Tahap 2: `watch.py` state machine (4 layar, uji end-to-end OK)
- [x] Tahap 3: heartbeat + auto-reconnect tunnel + anti-crash + run.bat
- [x] Tahap 4: jalur BLIND (layar verify ber-animasi tak bisa di-dump) + menu CLI + format log aurigostore
- [ ] Tahap 5: paralel multi-device

Catatan teknis: layar "Login Verification" punya `LogoProgressView` yang beranimasi
terus -> `uiautomator dump` **tidak akan pernah** dapat idle state di layar itu.
Solusinya jalur BLIND: kalau `dumpsys window` bilang kita di `PushVerifyActivity`
tapi UI tak terbaca, bot tap titik **(528,1204)** = tombol "lanjut" (VERIFY /
CONTINUE / GOT IT ada di titik itu di ketiga layar; REJECT & CANCEL tidak).

## Menjalankan

```powershell
python src\watch.py
```

Muncul header + **MENU UTAMA**:

```
1  Cek koneksi / config        tes tunnel + info device (Android, DANA terpasang?)
2  Jalankan bot (auto-verify)  watch loop, jalan terus sampai Ctrl+C
0  Keluar
```

Ctrl+C saat bot jalan -> balik ke menu. Ctrl+C di menu -> keluar.

Tanpa menu (buat script / auto-restart):

```powershell
python src\watch.py --no-menu     # langsung jalan
python src\watch.py --dry-run      # deteksi saja, tidak nge-tap
python src\watch.py --once         # berhenti setelah 1 alur
```

Format log: `[HH:MM:SS][LEVEL][device] pesan` (gaya `D:\BOT\CONSOL LOG\LOG_FORMAT.md`).
File: `logs\watch_YYYYMMDD.log` (tanpa kode warna). Dump tiap layar: `logs\screens\`.
Tombol `REJECT` / `CANCEL` tidak akan pernah disentuh (hard-blacklist di kode).

### Ditinggal jalan lama

- `watch.py` (tanpa `--once`) jalan terus: tiap verifikasi baru di-handle otomatis.
- Heartbeat tiap 60 dtk di log = bukti bot hidup.
- Kalau tunnel putus / ADB link expired -> `watch.py` reconnect sendiri (backoff).
- `run.bat` = jalankan `watch.py --no-menu` + restart otomatis kalau prosesnya mati total.
- **QuickEdit PowerShell** (bukan terminal VS Code): klik di dalam terminal membekukan
  proses. Matikan: klik-kanan bar judul > Properties > hilangkan centang *QuickEdit Mode*.
- **Mode API** (disarankan utk unattended): kosongkan `adb_panel` (`connect_key: null`),
  isi `api.access_key` / `api.secret_key` + `pad_code`. Bot minta tunnel 7 hari sendiri
  dan saat reconnect ambil kredensial baru otomatis - tidak perlu nyalin dari panel lagi.

## Prasyarat

- Python 3.11+  (`pip install -r requirements.txt`)
- **platform-tools terbaru**. `adb` bawaan (`C:\adb\adb.exe`) versi 1.0.32 terlalu tua
  (`exec-out` belum ada, protokol bisa bentrok). Download
  <https://developer.android.com/tools/releases/platform-tools>, unzip, timpa `C:\adb\`.
  Cek: `adb version` harus >= 1.0.41.
- OpenSSH client (`ssh` di PATH) -- sudah ada di Windows 11.

## Setup

```powershell
cd d:\BOT\VSPHONE\autoverif-dana
pip install -r requirements.txt
copy config.example.json config.json
```

Edit `config.json`:

**Opsi A - cepat, untuk recon (tanpa API):**
Di client vsphone buka device -> nyalakan **Turn on ADB** -> salin 3 nilai ke `adb_panel`:
- `connect_command`  = isi kotak **Connect command**
- `connect_key`      = isi kotak **Connect Key**
- `adb_address`      = isi **ADB Address** tanpa `adb connect ` (mis. `localhost:64669`)

**Opsi B - via API (untuk operasional / paralel):**
Kosongkan `adb_panel` (set `connect_key` ke `null`), isi `api.access_key` / `api.secret_key`
(dari panel Developer -> API) dan `pad_code`. Tool akan panggil `openOnlineAdb` + `adb` sendiri.

## Jalankan recon

Buka **layar verifikasi DANA** di device (pay CapCut sampai muncul popup Login Verification),
lalu:

```powershell
python src\recon.py            # sekali
python src\recon.py --watch    # tiap 2 dtk, buat nangkap ke-3 layar berurutan
```

Hasil di `recon\<timestamp>\`. Yang penting **`info.txt`** -> baca bagian `VERDICT`:

- **OK** -> teks tombol kebaca di node. Jalur utama layak, lanjut tahap 2.
- **UI tree kebaca tapi teks tombol tidak ada** -> kirim `ui_dump.xml` untuk dianalisa
  (mungkin tombolnya pakai `resource-id` tanpa `text`, atau di dalam WebView).
- **uiautomator tidak menghasilkan node** -> layar diblok, perlu fallback
  (accessibility-service app di dalam instance / OCR / koordinat statis).

Cek juga `screen.png`: kalau hitam = `FLAG_SECURE` aktif untuk screenshot
(OCR mati, tapi uiautomator sering masih jalan).

## Struktur

```
config.json             kredensial + parameter (gitignored)
src/watch.py             entry point: header + menu CLI + state machine + supervisor
src/recon.py             perkakas recon UI (dev)
src/vsphone/logger.py    format log gaya aurigostore (header, menu, log)
src/vsphone/session.py   load_config + build/open tunnel (panel atau API)
src/vsphone/signer.py    signing V2 OpenAPI
src/vsphone/api.py       client: openOnlineAdb, adb, simulateTouch, inputText, ...
src/vsphone/tunnel.py    SSH tunnel (paramiko) + adb connect + helper .adb()
src/vsphone/uidump.py    uiautomator dump + parser node + pencarian
run.bat                  jalankan --no-menu + auto-restart
logs/, recon/            output (gitignored)
```
