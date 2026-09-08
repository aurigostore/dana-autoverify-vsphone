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
- [x] Tahap 5: multi-device (maks 4) - 1 thread per device, log ditandai `[W1]..[W4]`

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

### Multi-device (maks 4)

`config.json` -> array `devices`, 1 entry per device vsphone:

```json
"devices": [
  { "pad_code": "ACP...QUQ8", "adb_panel": { "connect_command": "...", "connect_key": "...", "adb_address": "localhost:64669" } },
  { "pad_code": "ACP...XXXX", "adb_panel": { "connect_command": "...", "connect_key": "...", "adb_address": "localhost:51xxx" } }
]
```

- Tiap device jalan di thread sendiri; log ditandai `[W1]`..`[W4]` (nomor ikut **posisi** di list).
- `adb_address` (port) tiap device beda -> ambil dari panel "Turn on ADB" masing-masing.
- Device yang `adb_panel`-nya dikosongkan -> pakai mode API (`api` + `pad_code` device itu).
- 1 device saja: boleh tetap pakai bentuk lama (`pad_code` + `adb_panel` di root).
- Recon 1 device tertentu: `python src\recon.py --device 2`.
- Semua device 720x1280 (menu 1 kasih peringatan kalau beda - koordinat BLIND ikut resolusi itu).

**Pilih sebagian device** (isi 4 entry, jalankan 2) - 3 cara:

- **Lewat menu** (paling gampang): pilih `3  Pilih device aktif` -> muncul daftar
  semua device + statusnya -> ketik `all`, atau `1,3` (nomor W#), atau `enabled`
  (balik ke flag config), atau Enter untuk batal. Menu 1 & 2 lalu pakai pilihan itu.
- **Flag config**: `"enabled": false` pada entry yang tidak dipakai -> di-skip saat start.
- **Flag CLI** `--devices` (menimpa `enabled`), nomor W# atau pad_code:
  ```powershell
  python src\watch.py --devices 1,2
  python src\watch.py --devices ACP61358RMOYQUQ8,ACP...DEV3
  ```

Header & menu selalu tampilkan `N/total aktif (W1+W2)`.

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

Edit `config.json` -> isi `devices[]` (1 entry per device vsphone, maks 4).

**Per device, cara cepat (tanpa API):** di client vsphone buka device itu ->
nyalakan **Turn on ADB** -> salin 3 nilai ke `adb_panel`:
- `connect_command`  = kotak **Connect command**
- `connect_key`      = kotak **Connect Key**
- `adb_address`      = **ADB Address** tanpa `adb connect ` (mis. `localhost:64669`)

**Per device, mode API:** hapus/`null`-kan `adb_panel` device itu, cukup isi `pad_code`.
Isi juga `api.access_key` / `api.secret_key` (panel Developer -> API) sekali di root.
Bot minta tunnel sendiri (dan perbarui saat reconnect).

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
