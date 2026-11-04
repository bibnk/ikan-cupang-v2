// get_email.js — Get Email Frontend (ultra-fast + Realtime Watcher)

(function () {
    "use strict";

    var APP_BASE = (window.APP_BASE || "");
    var DEFAULT_SENDERS = "@booking.com\nrewards@booking.com\nemail.campaign@sg.booking.com\n@account.agoda.com\n@agoda.com";

    const form = document.getElementById("get-email-form");
    const accountInput = document.getElementById("ge-account");
    const sendersInput = document.getElementById("ge-senders");
    const keywordsInput = document.getElementById("ge-keywords");
    const daysInput = document.getElementById("ge-days");
    const limitSelect = document.getElementById("ge-limit");
    const submitBtn = document.getElementById("ge-submit");
    const watchBtn = document.getElementById("ge-watch-btn");
    const watchStatus = document.getElementById("ge-watch-status");

    const loadingDiv = document.getElementById("ge-loading");
    const errorDiv = document.getElementById("ge-error");
    const resultsSection = document.getElementById("ge-results");
    const emailList = document.getElementById("ge-email-list");
    const countBadge = document.getElementById("ge-count");
    const emptyDiv = document.getElementById("ge-empty");

    // Modal elements
    const modal = document.getElementById("ge-modal");
    const modalClose = document.getElementById("ge-modal-close");
    const modalSubject = modal.querySelector(".ge-modal-subject");
    const modalInfo = modal.querySelector(".ge-modal-info");
    const modalBody = modal.querySelector(".ge-modal-body");

    // State
    let currentEmail = "";
    let currentPassword = "";
    let emailsData = [];
    let seenUids = new Set();
    let isWatching = false;
    let watchInterval = null;
    let isPolling = false;

    function playChime() {
        try {
            var ctx = new (window.AudioContext || window.webkitAudioContext)();
            var osc = ctx.createOscillator();
            var gain = ctx.createGain();
            osc.connect(gain);
            gain.connect(ctx.destination);
            osc.type = "sine";
            osc.frequency.setValueAtTime(880, ctx.currentTime);
            osc.frequency.exponentialRampToValueAtTime(1320, ctx.currentTime + 0.15);
            gain.gain.setValueAtTime(0.12, ctx.currentTime);
            gain.gain.exponentialRampToValueAtTime(0.01, ctx.currentTime + 0.3);
            osc.start();
            osc.stop(ctx.currentTime + 0.3);
        } catch (e) {}
    }

    // ---- Modal controls ----
    function openModal(idx) {
        var em = emailsData[idx];
        if (!em) return;

        modalSubject.textContent = em.subject || "(Tanpa subjek)";
        modalInfo.innerHTML = "📧 " + escapeHtml(em.from) + " &nbsp;•&nbsp; 📅 " + escapeHtml(em.date);

        var html = em.body_html || "";

        if (em.links && em.links.length > 0) {
            html += '<div class="ge-links-section">' +
                '<div class="ge-links-header">🔗 Link Ditemukan (' + em.links.length + ')</div>' +
                '<div class="ge-links-list">';
            em.links.forEach(function (link, i) {
                var label = link.label || "";
                var url = link.url || "";
                if (label && label !== url) {
                    html += '<div class="ge-link-item">' +
                        '<span class="ge-link-num">' + (i + 1) + '.</span>' +
                        '<div class="ge-link-detail">' +
                        '<span class="ge-link-label">' + escapeHtml(label) + '</span>' +
                        '<a href="' + escapeHtml(url) + '" target="_blank" rel="noopener noreferrer" class="ge-link-url">' + escapeHtml(url) + '</a>' +
                        '</div></div>';
                } else {
                    html += '<div class="ge-link-item">' +
                        '<span class="ge-link-num">' + (i + 1) + '.</span>' +
                        '<a href="' + escapeHtml(url) + '" target="_blank" rel="noopener noreferrer" class="ge-link-url">' + escapeHtml(url) + '</a>' +
                        '</div>';
                }
            });
            html += '</div></div>';
        }

        modalBody.innerHTML = html;
        modal.classList.remove("hidden");
        document.body.style.overflow = "hidden";
    }

    function closeModal() {
        modal.classList.add("hidden");
        document.body.style.overflow = "";
    }

    modalClose.addEventListener("click", closeModal);
    modal.querySelector(".ge-modal-backdrop").addEventListener("click", closeModal);
    document.addEventListener("keydown", function (e) {
        if (e.key === "Escape" && !modal.classList.contains("hidden")) {
            closeModal();
        }
    });

    function renderEmailList(emails, totalFound, isWatcherUpdate) {
        if (!emails || emails.length === 0) {
            if (!isWatcherUpdate) {
                emptyDiv.classList.remove("hidden");
                resultsSection.classList.add("hidden");
            }
            return;
        }

        emptyDiv.classList.add("hidden");
        resultsSection.classList.remove("hidden");
        emailsData = emails;
        countBadge.textContent = totalFound && totalFound > emails.length
            ? (emails.length + " dari " + totalFound + " total")
            : emails.length;

        var newlyArrived = [];
        emails.forEach(function (em) {
            var key = em.uid || (em.subject + ":" + em.date);
            if (!seenUids.has(key)) {
                if (seenUids.size > 0 && isWatcherUpdate) {
                    newlyArrived.push(key);
                }
                seenUids.add(key);
            }
        });

        if (newlyArrived.length > 0) {
            playChime();
        }

        emailList.innerHTML = "";
        emails.forEach(function (em, idx) {
            var key = em.uid || (em.subject + ":" + em.date);
            var isNew = newlyArrived.indexOf(key) !== -1;

            var card = document.createElement("div");
            card.className = "ge-email-card" + (isNew ? " ge-card-new" : "");
            card.setAttribute("data-uid", em.uid || "");

            var newBadge = isNew
                ? '<span style="background:#10b981; color:#fff; font-size:11px; padding:2px 8px; border-radius:12px; font-weight:700; margin-left:8px;">✨ BARU</span>'
                : '';

            card.innerHTML =
                '<div class="ge-email-header">' +
                '<div class="ge-email-num">#' + (idx + 1) + '</div>' +
                '<div class="ge-email-meta">' +
                '<div class="ge-email-subject">' + escapeHtml(em.subject || "(Tanpa subjek)") + newBadge + '</div>' +
                '<div class="ge-email-info">' +
                '<span class="ge-email-from">📧 ' + escapeHtml(em.from) + '</span>' +
                '<span class="ge-email-date">📅 ' + escapeHtml(em.date) + '</span>' +
                '</div>' +
                '</div>' +
                '<div class="ge-btn-group">' +
                '<button class="ge-toggle-btn" data-idx="' + idx + '">' +
                '<span class="ge-toggle-label">👁 Tampilkan</span>' +
                '</button>' +
                '<button class="ge-delete-btn" data-uid="' + escapeHtml(em.uid || '') + '">' +
                '🗑️ Hapus' +
                '</button>' +
                '</div>' +
                '</div>';
            emailList.appendChild(card);
        });

        // Attach view handlers
        emailList.querySelectorAll(".ge-toggle-btn").forEach(function (btn) {
            btn.addEventListener("click", function () {
                var idx = parseInt(this.getAttribute("data-idx"), 10);
                openModal(idx);
            });
        });

        // Attach delete handlers
        emailList.querySelectorAll(".ge-delete-btn").forEach(function (btn) {
            btn.addEventListener("click", function () {
                var uid = this.getAttribute("data-uid");
                handleDelete(uid, this);
            });
        });
    }

    async function executeFetch(isWatcherUpdate) {
        if (isPolling) return;
        isPolling = true;

        const accountStr = accountInput.value.trim();
        let senders = sendersInput.value.trim();
        let keywords = keywordsInput.value.trim();
        const days = parseInt(daysInput.value, 10) || 365;
        const limitVal = parseInt(limitSelect ? limitSelect.value : 20, 10) || 20;

        if (!accountStr) {
            isPolling = false;
            stopWatcher();
            return alert("Masukkan akun email!");
        }

        const colonIdx = accountStr.indexOf(":");
        if (colonIdx === -1) {
            isPolling = false;
            stopWatcher();
            return alert("Format akun: email:password");
        }
        const emailAddr = accountStr.substring(0, colonIdx).trim();
        const password = accountStr.substring(colonIdx + 1).trim();
        if (!emailAddr || !password) {
            isPolling = false;
            stopWatcher();
            return alert("Email dan password wajib diisi!");
        }

        currentEmail = emailAddr;
        currentPassword = password;

        if (!isWatcherUpdate) {
            submitBtn.disabled = true;
            submitBtn.querySelector(".btn-text").textContent = "Mengambil email...";
            loadingDiv.classList.remove("hidden");
            errorDiv.classList.add("hidden");
            resultsSection.classList.add("hidden");
            emptyDiv.classList.add("hidden");
            emailList.innerHTML = "";
            emailsData = [];
            seenUids.clear();
        }

        try {
            const res = await fetch(APP_BASE + "/api/get-email", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    email: emailAddr,
                    password: password,
                    senders: senders,
                    keywords: keywords,
                    search_days: days,
                    limit: limitVal,
                }),
            });

            const data = await res.json();
            if (!isWatcherUpdate) {
                loadingDiv.classList.add("hidden");
                submitBtn.disabled = false;
                submitBtn.querySelector(".btn-text").textContent = "Ambil Email";
            }

            if (!data.success) {
                if (!isWatcherUpdate) {
                    errorDiv.textContent = "❌ " + (data.error || "Terjadi kesalahan");
                    errorDiv.classList.remove("hidden");
                } else if (watchStatus) {
                    watchStatus.textContent = "⚠️ Error: " + (data.error || "Gagal cek");
                }
                isPolling = false;
                return;
            }

            renderEmailList(data.emails, data.total_found, isWatcherUpdate);

            if (isWatching && watchStatus) {
                var now = new Date();
                var timeStr = now.toTimeString().split(" ")[0];
                watchStatus.textContent = "🟢 Realtime aktif • Update: " + timeStr + " (Auto 2.5s)";
            }
        } catch (err) {
            if (!isWatcherUpdate) {
                loadingDiv.classList.add("hidden");
                submitBtn.disabled = false;
                submitBtn.querySelector(".btn-text").textContent = "Ambil Email";
                errorDiv.textContent = "❌ Gagal menghubungi server: " + err.message;
                errorDiv.classList.remove("hidden");
            }
        } finally {
            isPolling = false;
        }
    }

    function startWatcher() {
        isWatching = true;
        if (watchBtn) {
            watchBtn.style.background = "#10b981";
            watchBtn.style.color = "#ffffff";
            watchBtn.querySelector(".watch-text").textContent = "Matikan Watcher";
        }
        if (watchStatus) {
            watchStatus.style.display = "inline";
            watchStatus.textContent = "🟢 Menghubungkan realtime...";
        }
        executeFetch(true);
        watchInterval = setInterval(function () {
            executeFetch(true);
        }, 2500);
    }

    function stopWatcher() {
        isWatching = false;
        if (watchInterval) {
            clearInterval(watchInterval);
            watchInterval = null;
        }
        if (watchBtn) {
            watchBtn.style.background = "rgba(16,185,129,0.08)";
            watchBtn.style.color = "#10b981";
            watchBtn.querySelector(".watch-text").textContent = "Realtime Watcher (Auto Cek)";
        }
        if (watchStatus) {
            watchStatus.style.display = "none";
        }
    }

    // ---- Event Listeners ----
    form.addEventListener("submit", function (e) {
        e.preventDefault();
        executeFetch(false);
    });

    if (watchBtn) {
        watchBtn.addEventListener("click", function () {
            if (isWatching) {
                stopWatcher();
            } else {
                startWatcher();
            }
        });
    }

    window.addEventListener("beforeunload", function () {
        stopWatcher();
    });

    async function handleDelete(uid, btnEl) {
        if (!uid) return alert("UID email tidak ditemukan.");
        if (!confirm("⚠️ Email ini akan dihapus PERMANEN (termasuk dari Trash). Lanjutkan?")) return;

        var card = btnEl.closest(".ge-email-card");
        btnEl.disabled = true;
        btnEl.textContent = "Menghapus...";

        try {
            var res = await fetch(APP_BASE + "/api/delete-email", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    email: currentEmail,
                    password: currentPassword,
                    uid: uid,
                }),
            });

            var data = await res.json();

            if (data.success) {
                card.classList.add("ge-email-deleted");
                card.innerHTML =
                    '<div class="ge-deleted-msg">' +
                    '<span>✅ Email berhasil dihapus permanen</span>' +
                    '</div>';
                var remaining = document.querySelectorAll(".ge-email-card:not(.ge-email-deleted)").length;
                countBadge.textContent = remaining;
            } else {
                alert("❌ Gagal menghapus: " + (data.error || "Unknown error"));
                btnEl.disabled = false;
                btnEl.textContent = "🗑️ Hapus";
            }
        } catch (err) {
            alert("❌ Error: " + err.message);
            btnEl.disabled = false;
            btnEl.textContent = "🗑️ Hapus";
        }
    }

    function escapeHtml(str) {
        if (!str) return "";
        var div = document.createElement("div");
        div.textContent = str;
        return div.innerHTML;
    }
})();
