# Ikan Cupang v2

High-Performance Async Networking & Stream Processing Console.

Ikan Cupang v2 adalah framework pemrosesan stream dan validasi socket koneksi berkecepatan tinggi. Dirancang untuk throughput maksimal dengan arsitektur non-blocking `asyncio` raw TLS streams, in-memory caching, streaming queue asinkron, dan real-time monitoring console.

**Repo:** https://github.com/bibnk/ikan-cupang-v2  
**Dokumentasi Deploy:** [TUTORIAL.md](./TUTORIAL.md)

---

## ⚡ Fitur Utama

| Fitur | Keterangan |
|-------|------------|
| **Raw Async Socket Engine** | Menggunakan direct `asyncio.open_connection` + TLS/SSL context murni. Menembus limit thread OS dan meminimalkan resource CPU/RAM. |
| **High-Throughput Concurrency** | Mendukung hingga 1.000 worker async paralel simultan dengan in-memory address caching. |
| **Batch Disk Writer Queue** | Penulisan data hasil di-buffer dalam memory queue dan di-flush per batch (interval 0.5s) untuk mencegah disk I/O lock contention. |
| **Zero-Roundtrip Data Traversal** | Pemrosesan dan pengurutan data dilakukan langsung di memory tanpa handshake jaringan berulang. |
| **Realtime Watcher & Live Stream** | Server-Sent Events (SSE) untuk streaming status visual secara real-time ke web console. |
| **Dual Mode (CLI & Web Console)** | Dapat dioperasikan langsung via terminal untuk kecepatan penuh atau via antarmuka web modern. |

---

## 🚀 Cara Menjalankan

### 1. Lokal (Windows)

Jalankan shortcut:
```
START_IKAN_CUPANG_V2.bat
```
Atau melalui terminal:
```bash
cd "C:\Users\Administrator\ikan cupang v2"
py -3.14 app.py
```
- Akses Browser: `http://localhost:5000/`
- Kode Akses Default: `sbb`

### 2. Fast CLI Mode

Untuk memproses file data langsung dari terminal dengan performa maksimal:

```bash
python fast_checker.py data.txt -c 500 -t 3.0 -o hasil_output
```

Parameter:
- `-c, --concurrency`: Jumlah worker paralel (default: `500`, rekomendasi: `200`–`1000`).
- `-t, --timeout`: Batas waktu timeout socket per koneksi dalam detik (default: `3.0`).
- `-s, --senders`: Filter parameter target (opsional, gunakan `*` untuk memproses seluruh data).
- `-o, --output`: Direktori penyimpanan output file.

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

## 📁 Struktur Direktori

```
ikan-cupang-v2/
├── fast_checker.py          # Core engine async raw TLS socket
├── ikan_cupang_v2.py        # CLI runner wrapper
├── app.py                   # Web application & real-time SSE API
├── static/                  # Asset UI & Realtime JavaScript
├── templates/               # UI Templates (dashboard, console, admin)
├── START_IKAN_CUPANG_V2.bat # One-click runner untuk Windows
├── setup.sh                 # VPS auto-installer
└── TUTORIAL.md              # Dokumentasi lengkap
```

---

## 🔧 Manajemen Service VPS

```bash
# Cek status service
sudo systemctl status cupang-service

# Restart service
sudo systemctl restart cupang-service

# Log real-time
sudo journalctl -u cupang-service -f
```
