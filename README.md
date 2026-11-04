# Ikan Cupang v2 — Ultra-Fast Async IMAP Engine & Mail Console

Ikan Cupang v2 adalah rewrite engine IMAP checker generasi kedua. Dirancang untuk throughput tinggi (mampu memvalidasi 6.000 akun dalam ~4-10 detik) dengan arsitektur non-blocking `asyncio` raw TLS socket, DNS caching in-memory, zero-roundtrip email UID sorting, dan realtime mail watcher.

**Repo:** https://github.com/bibnk/ikan-cupang-v2  
**Dokumentasi Deploy VPS:** [TUTORIAL.md](./TUTORIAL.md)

---

## ⚡ Apa yang Baru di v2?

| Fitur / Peningkatan | Keterangan |
|---------------------|------------|
| **Raw Async Socket Engine (`fast_checker.py`)** | Menggantikan thread-per-connection `imaplib` dengan multiplexed `asyncio.open_connection` + SSL context langsung. Menembus batas GIL & thread OS. |
| **Throughput 6.000 Acc / ~4-10s** | Concurrency hingga 1.000 worker async simultan dengan DNS lookup caching in-memory. |
| **Pure Empass Checker (`*`)** | Ketik `*` di input **Target Sender**. Seluruh login yang valid otomatis masuk kategori **LIVE** tanpa perlu filter pengirim email. |
| **Instant Header Fetching** | Hanya menarik `BODY.PEEK[HEADER.FIELDS (SUBJECT FROM DATE)]` (~200 bytes per email) bukan full `(RFC822)` (~50 KB), menghemat bandwidth hingga 99%. |
| **Zero-Roundtrip UID Sorting** | Sorting pesan inbox dilakukan langsung in-memory berdasar integer UID IMAP (RFC 3501). Menghilangkan ratusan network roundtrip `INTERNALDATE` yang membuat v1 lambat. |
| **⚡ Realtime Watcher (Get Email)** | Auto-polling inbox tiap 2.5 detik tanpa reload halaman. Email baru langsung muncul di posisi teratas disertai notifikasi audio chime & badge `✨ BARU`. |
| **Wildcard Subdomain Matching** | Target sender `@booking.com` otomatis menangkap `noreply-iam@booking.com`, `email.campaign@sg.booking.com`, dan subdomain dinamis lainnya. |
| **Batch Disk Writer Queue** | Penulisan hasil (`live.txt`, `die.txt`, `no_email.txt`) di-buffer dalam memory queue dan di-flush per batch (interval 0.5s) untuk mencegah disk I/O lock contention. |
| **CLI & Web Runner Ganda** | Dapat dijalankan via CLI murni (`python fast_checker.py`) atau Web Console interaktif (`app.py`). |

---

## 🚀 Cara Menjalankan

### 1. Lokal (Windows)

Klik ganda file:
```
START_IKAN_CUPANG_V2.bat
```
Atau via terminal:
```bash
cd "C:\Users\Administrator\ikan cupang v2"
py -3.14 app.py
```
- Akses Browser: `http://localhost:5000/`
- Kode Akses: `sbb`

### 2. Fast CLI Mode (Tanpa Web)

Untuk check file combo `email:password` langsung dari terminal dengan performa maksimal:

```bash
python fast_checker.py combo.txt -c 500 -t 3.0 -o hasil_cek
```

Parameter:
- `-c, --concurrency`: Jumlah worker paralel (default: `500`, rekomendasi: `200`–`1000`).
- `-t, --timeout`: Timeout koneksi IMAP per akun dalam detik (default: `3.0`).
- `-s, --senders`: Target sender dipisah koma (misal: `@booking.com,@agoda.com` atau `*` untuk pure empass).
- `-o, --output`: Folder penyimpanan file hasil (`live.txt`, `no_email.txt`, `die.txt`).

---

## 🌐 Quick Deploy di VPS (Ubuntu 24.04 LTS)

```bash
ssh root@IP_VPS_KAMU
apt update && apt install -y git python3 python3-pip python3-venv nginx
git clone https://github.com/bibnk/ikan-cupang-v2.git /root/ikan-cupang-v2
cd /root/ikan-cupang-v2
chmod +x setup.sh
./setup.sh
```

Akses web di VPS:
- `http://IP_VPS_KAMU/` → Halaman publik samaran ("Hello World").
- `http://IP_VPS_KAMU/sbb/` → Dashboard utama Ikan Cupang v2 (Kode: `sbb`).

---

## 📁 Struktur File v2

```
ikan-cupang-v2/
├── fast_checker.py          # Core engine async raw TLS socket
├── ikan_cupang_v2.py        # CLI runner wrapper
├── app.py                   # Flask web application & SSE API
├── imap_engine.py           # Jembatan web controller ke async engine
├── get_email_engine.py      # Engine get email & delete email
├── imap_config.json         # Database konfigurasi 34.000+ domain IMAP
├── START_IKAN_CUPANG_V2.bat # One-click runner untuk Windows
├── static/                  # CSS & Frontend JavaScript (SSE + Watcher)
├── templates/               # UI Templates (index, get_email, admin, login)
├── setup.sh                 # VPS setup installer
└── TUTORIAL.md              # Panduan lengkap operasional & sinkronisasi
```

---

## 🔧 Manajemen Service VPS

```bash
# Cek status
sudo systemctl status imap-checker

# Restart
sudo systemctl restart imap-checker

# Cek log aplikasi
sudo journalctl -u imap-checker -f
```
