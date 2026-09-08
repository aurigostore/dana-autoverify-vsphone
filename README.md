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
- [ ] Tahap 4: paralel multi-device

Hasil recon: `uiautomator` jalan penuh, TIDAK ada FLAG_SECURE, semua layar native
dengan resource-id stabil di Activity `id.dana/.pushverify...PushVerifyActivity`.

## Menjalankan bot (watch.py)

```powershell
# 1. WAJIB: sinkron ulang adb_panel di config.json tiap kali toggle ADB di client
#    vsphone di-off/on (connect_command, connect_key, adb_address, port berubah).

# 2. Uji deteksi TANPA nge-tap. Picu login CapCut, amati log.
python src\watch.py --dry-run

# 3. Kalau log dry-run sudah benar (VERIFY/CONTINUE/GOT IT terdeteksi di
#    koordinat yang pas), jalankan mode aktif:
python src\watch.py
#    atau berhenti otomatis setelah 1 alur:
python src\watch.py --once
```

Log: `logs\watch_YYYYMMDD.log`. Struktur tiap layar baru: `logs\screens\`.
Tombol `REJECT` / `CANCEL` tidak akan pernah disentuh (hard-blacklist di kode).

### Ditinggal jalan lama

- `watch.py` (tanpa `--once`) jalan terus: tiap verifikasi baru di-handle otomatis.
- Heartbeat tiap 60 dtk di log = bukti bot hidup.
- Kalau tunnel putus / ADB link expired -> `watch.py` reconnect sendiri (backoff).
- `run.bat` = restart `watch.py` otomatis kalau prosesnya sampai mati total.
- **QuickEdit PowerShell**: klik di dalam terminal membekukan proses. Matikan:
  klik-kanan bar judul > Properties > hilangkan centang *QuickEdit Mode*.
  Atau pakai `run.bat`, atau minimize saja jangan diklik.
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
config.json            kredensial + parameter (gitignored)
src/vsphone/signer.py   signing V2 OpenAPI
src/vsphone/api.py      client: openOnlineAdb, adb, simulateTouch, inputText, ...
src/vsphone/tunnel.py   SSH tunnel + adb connect + helper .adb()
src/vsphone/uidump.py   uiautomator dump + parser node + pencarian
src/recon.py            skrip recon tahap 1
recon/                  output recon (gitignored)
```
