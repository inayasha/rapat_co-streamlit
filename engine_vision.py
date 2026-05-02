import os
import io
import base64
import datetime
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from google.cloud.firestore_v1.base_query import FieldFilter

import streamlit as st
import streamlit.components.v1 as components
import google.generativeai as genai
from firebase_admin import firestore

from database import (
    db, get_user, get_active_keys, get_system_config,
    increment_api_usage, invalidate_user_cache
)
from config import PROMPT_VISION_OCR


# ==========================================
# 1. TARIF VISION PER GAMBAR
# ==========================================

def _harga_reguler(idx_gambar: int) -> int:
    """
    Tarif per gambar untuk user Regular & AIO (Rupiah).
    idx_gambar berbasis 0 (gambar pertama = 0).
    Gambar ke-1 : Rp 10.000
    Gambar ke-2 : Rp  7.500
    Gambar ke-3+: Rp  5.000 flat
    """
    if idx_gambar == 0: return 10_000
    if idx_gambar == 1: return  7_500
    return 5_000


def _menit_b2b(idx_gambar: int) -> int:
    """
    Tarif per gambar untuk user B2B (menit tangki).
    Gambar ke-1 : 15 menit
    Gambar ke-2 : 20 menit
    Gambar ke-3+: 25 menit flat
    """
    if idx_gambar == 0: return 15
    if idx_gambar == 1: return 20
    return 25


def _hitung_total_reguler(jumlah: int) -> int:
    """Total Rupiah untuk jumlah gambar tertentu."""
    return sum(_harga_reguler(i) for i in range(jumlah))


def _hitung_total_b2b(jumlah: int) -> int:
    """Total menit untuk jumlah gambar tertentu."""
    return sum(_menit_b2b(i) for i in range(jumlah))


def _fmt_rp(angka: int) -> str:
    return f"Rp {angka:,}".replace(',', '.')


# ==========================================
# 2. VISION KEY RESOLVER (2-tier fallback)
# ==========================================

def get_active_vision_keys():
    """
    Tier 1 : Gemini keys yang di-checklist is_vision=True (counter shared).
    Tier 2 : Provider dedicated 'Gemini Vision' (backup key khusus).
    Returns list of valid key dicts, atau [] jika semua habis/kosong.
    """
    wib_tz    = datetime.timezone(datetime.timedelta(hours=7))
    today_str = datetime.datetime.now(wib_tz).strftime("%Y-%m-%d")

    keys_ref = (
        db.collection('api_keys')
        .where(filter=FieldFilter("provider", "==", "Gemini"))
        .where(filter=FieldFilter("is_active", "==", True))
        .stream()
    )
    valid_keys = []
    for doc in keys_ref:
        data = doc.to_dict()
        if not data.get('is_vision', False):
            continue
        doc_id = doc.id
        if data.get('last_reset_date', '') != today_str:
            db.collection('api_keys').document(doc_id).update({
                "used": 0, "last_reset_date": today_str
            })
            data['used'] = 0
            data['last_reset_date'] = today_str
        data['id'] = doc_id
        if data['used'] < data['limit']:
            valid_keys.append(data)

    if valid_keys:
        return valid_keys

    return get_active_keys("Gemini Vision")


# ==========================================
# 3. EMAIL NOTIFIKASI ADMIN (80% threshold)
# ==========================================

def _kirim_notifikasi_vision_email(key_name: str, used: int, limit: int):
    """
    Kirim email ke semua user role=admin saat key Vision mencapai >=80% limit.
    Anti-spam: hanya satu kali per hari. Silent-fail.
    """
    try:
        wib_tz    = datetime.timezone(datetime.timedelta(hours=7))
        today_str = datetime.datetime.now(wib_tz).strftime("%Y-%m-%d")

        config_ref  = db.collection('settings').document('system_config')
        config_snap = config_ref.get()
        if config_snap.exists:
            if config_snap.to_dict().get('last_vision_alert_date', '') == today_str:
                return

        admin_docs   = db.collection('users').where('role', '==', 'admin').stream()
        admin_emails = [doc.id for doc in admin_docs]
        if not admin_emails:
            return

        pct     = int((used / limit) * 100)
        now_wib = datetime.datetime.now(wib_tz).strftime("%d/%m/%Y %H:%M WIB")

        smtp_server  = st.secrets["email"]["smtp_server"]
        smtp_port    = int(st.secrets["email"]["smtp_port"])
        sender_email = st.secrets["email"]["sender_email"]
        sender_pass  = st.secrets["email"]["sender_password"]

        subject = f"[TOM'STT AI] ⚠️ Vision API '{key_name}' mencapai {pct}% limit harian"
        body = f"""\
Notifikasi otomatis dari sistem TOM'STT AI — Vision Mode.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
API Key Vision  : {key_name}
Penggunaan      : {used} / {limit} panggilan ({pct}%)
Waktu Deteksi   : {now_wib}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Sisa kuota tinggal {limit - used} panggilan. Segera tambahkan API key baru
atau aktifkan key Vision cadangan di Panel Super Admin Developer.

Panel Admin: https://rapat.co (formerly tom-stt.com)

─────────────────────────────────
Email ini hanya dikirim sekali per hari (reset tengah malam WIB).
TOM'STT AI | admin@tom-stt.com
"""
        for recipient in admin_emails:
            msg = MIMEMultipart()
            msg['From']    = sender_email
            msg['To']      = recipient
            msg['Subject'] = subject
            msg.attach(MIMEText(body, 'plain', 'utf-8'))

            with smtplib.SMTP(smtp_server, smtp_port) as server:
                server.starttls()
                server.login(sender_email, sender_pass)
                server.send_message(msg)

        config_ref.set({'last_vision_alert_date': today_str}, merge=True)

    except Exception as e:
        print(f"[Vision Alert] Gagal kirim email: {e}")


# ==========================================
# 4. HELPER: EKSTRAK 1 GAMBAR VIA GEMINI
# ==========================================

def _ekstrak_satu_gambar(kd: dict, image_bytes: bytes, mime_type: str) -> tuple:
    """
    Panggil Gemini Vision untuk satu gambar.
    Returns (result_text, error_str) — result_text None jika gagal.
    """
    try:
        sys_cfg       = get_system_config()
        vision_weight = int(sys_cfg.get("vision_api_weight", 5))

        genai.configure(api_key=kd["key"])
        _vis_model = kd.get("model", "gemini-2.5-flash")
        model = genai.GenerativeModel(_vis_model)
        # Nonaktifkan thinking mode hanya untuk model yang support thinking_config
        # (gemini-2.5-pro / gemini-2.5-flash, bukan lite).
        _THINKING_CAPABLE = ("gemini-2.5-pro", "gemini-2.5-flash")
        _vis_cfg = None
        if any(p in _vis_model for p in _THINKING_CAPABLE) and "lite" not in _vis_model:
            _vis_cfg = {"thinking_config": {"thinking_budget": 0}}

        image_part = {
            "inline_data": {
                "mime_type": mime_type,
                "data": base64.b64encode(image_bytes).decode('utf-8')
            }
        }

        try:
            response = model.generate_content([PROMPT_VISION_OCR, image_part], generation_config=_vis_cfg)
        except Exception as _cfg_err:
            # Fallback: jika model tidak support thinking_config, retry tanpa config
            if _vis_cfg and "Unknown field" in str(_cfg_err):
                response = model.generate_content([PROMPT_VISION_OCR, image_part], generation_config=None)
            else:
                raise
        result   = response.text

        increment_api_usage(kd["id"], kd["used"], count=vision_weight)
        kd["used"] += vision_weight

        return result, ""

    except Exception as e:
        err_str = str(e)
        # Auto-exhaust key jika 429
        if "429" in err_str or "ResourceExhausted" in err_str or "quota" in err_str.lower():
            try:
                db.collection('api_keys').document(kd["id"]).update({"used": kd["limit"]})
                kd["used"] = kd["limit"]
            except Exception:
                pass
        return None, err_str


# ==========================================
# 5. MAIN VISION PROCESSING FUNCTION
# ==========================================

def proses_vision_gambar(image_files: list, source_names: list):
    """
    Pipeline utama Vision Mode. Mendukung multi-gambar.

    Billing per gambar (tanpa free trial):
      Regular/AIO : gambar ke-1 Rp 10.000 | ke-2 Rp 7.500 | ke-3+ Rp 5.000 flat
      B2B         : gambar ke-1 15 mnt     | ke-2 20 mnt    | ke-3+ 25 mnt flat
      Admin       : gratis penuh

    Urutan eksekusi:
      1. Hitung total biaya berdasarkan jumlah gambar
      2. Cek affordability SEBELUM panggil API (hemat quota jika tidak mampu)
      3. Proses setiap gambar via Gemini Vision
      4. Potong biaya hanya untuk gambar yang BERHASIL
      5. Simpan ke session_state & Firebase
      6. Tampilkan hasil + tombol lanjut ke Tab AI
    """
    st.markdown("---")

    stt_css = st.empty()
    stt_css.markdown("""
    <style>
    @keyframes pulse-text { 0%,100%{opacity:1} 50%{opacity:0.4} }
    [data-testid="stStatusWidget"] {
        top:20px!important; left:auto!important; right:20px!important;
        width:auto!important; height:auto!important;
        background-color:transparent!important; backdrop-filter:none!important;
        flex-direction:row!important; align-items:center!important;
    }
    [data-testid="stStatusWidget"]::before {
        content:""!important; width:20px!important; height:20px!important;
        border:3px solid rgba(231,76,60,0.3)!important;
        border-top-color:#e74c3c!important; border-radius:50%!important;
        margin-bottom:0!important; margin-right:10px!important;
        background-color:transparent!important; box-shadow:none!important;
        animation:custom-spin 1s linear infinite!important;
    }
    [data-testid="stStatusWidget"]::after {
        content:"Memproses Gambar..."!important; font-size:13px!important;
        color:#FFFFFF!important; background-color:rgba(231,76,60,0.9)!important;
        padding:5px 12px!important; border-radius:20px!important;
        animation:pulse-text 1.5s ease-in-out infinite!important;
    }
    @keyframes custom-spin{0%{transform:rotate(0deg)}100%{transform:rotate(360deg)}}
    </style>
    """, unsafe_allow_html=True)

    jumlah_gambar = len(image_files)
    status_box    = st.empty()
    progress_bar  = st.progress(0)
    preview_box   = st.empty()

    mime_map = {
        '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
        '.png': 'image/png',  '.webp': 'image/webp'
    }

    try:
        # ── STEP 1: AMBIL KEY VISION ──────────────────────────────────
        active_keys = get_active_vision_keys()
        if not active_keys:
            status_box.empty()
            st.error("❌ Server Vision AI sedang sibuk atau tidak tersedia. Silahkan coba lagi nanti.")
            return None

        # ── STEP 2: CEK AFFORDABILITY SEBELUM PANGGIL API ────────────
        # Menghitung total biaya untuk SEMUA gambar yang diminta.
        # Jika tidak cukup, batal lebih awal — hemat quota API.
        status_box.info("🔍 Memvalidasi akun dan biaya...")
        progress_bar.progress(5)

        is_admin = (st.session_state.user_role == "admin")

        u_info = None
        vid    = None
        if not is_admin:
            u_info = get_user(st.session_state.current_user)
            if not u_info:
                st.error("Data akun tidak ditemukan.")
                return None
            vid = u_info.get("active_corporate_voucher")

        # Hitung ekspektasi total biaya
        total_rp_expected  = _hitung_total_reguler(jumlah_gambar)   # untuk Regular/AIO
        total_mnt_expected = _hitung_total_b2b(jumlah_gambar)        # untuk B2B

        if not is_admin:
            if vid:
                # --- CEK TANGKI B2B ---
                v_ref  = db.collection('vouchers').document(vid)
                v_snap = v_ref.get()
                if not v_snap.exists:
                    st.error("❌ Data lisensi instansi tidak ditemukan.")
                    return None
                v_data      = v_snap.to_dict()
                sisa_tangki = v_data.get("shared_quota_minutes", 0) - v_data.get("used_quota_minutes", 0)
                if sisa_tangki < total_mnt_expected:
                    st.error(
                        f"❌ **WAKTU INSTANSI TIDAK CUKUP:**\n\n"
                        f"{jumlah_gambar} gambar membutuhkan **{total_mnt_expected} menit** "
                        f"dari tangki, sedangkan sisa tangki Anda **{sisa_tangki} menit**.\n\n"
                        f"💡 Rincian: " +
                        " + ".join([f"gambar {i+1} ({_menit_b2b(i)} mnt)" for i in range(jumlah_gambar)]) +
                        f" = {total_mnt_expected} menit total."
                    )
                    return None
            else:
                # --- CEK SALDO REGULER/AIO ---
                saldo_user = u_info.get("saldo", 0)
                if saldo_user < total_rp_expected:
                    st.error(
                        f"❌ **SALDO TIDAK CUKUP:**\n\n"
                        f"{jumlah_gambar} gambar membutuhkan **{_fmt_rp(total_rp_expected)}**, "
                        f"sedangkan saldo Anda **{_fmt_rp(saldo_user)}**.\n\n"
                        f"💡 Rincian: " +
                        " + ".join([f"gambar {i+1} ({_fmt_rp(_harga_reguler(i))})" for i in range(jumlah_gambar)]) +
                        f" = {_fmt_rp(total_rp_expected)} total."
                    )
                    st.warning("Silahkan Top-Up Saldo Utama di menu samping.")
                    return None

        # ── STEP 3: PROSES SETIAP GAMBAR VIA GEMINI VISION ───────────
        all_results = []   # list of (idx_asli, src_name, result_text or None)

        for idx, (img_file, src_name) in enumerate(zip(image_files, source_names)):
            # Cek threshold 80% sebelum setiap panggilan
            for kd in active_keys:
                if kd['used'] >= kd['limit'] * 0.8:
                    try:
                        _kirim_notifikasi_vision_email(kd['name'], kd['used'], kd['limit'])
                    except Exception:
                        pass

            label_idx = f"Gambar {idx + 1} dari {jumlah_gambar}" if jumlah_gambar > 1 else "Gambar"
            status_box.info(f"📷 AI sedang membaca **{label_idx}**: *{src_name}*...")

            img_bytes = img_file.read() if hasattr(img_file, 'read') else img_file.getvalue()
            ext       = os.path.splitext(src_name)[1].lower()
            mime_type = mime_map.get(ext, 'image/jpeg')

            result_text = None
            for kd in active_keys:
                result_text, _err_vision = _ekstrak_satu_gambar(kd, img_bytes, mime_type)
                if result_text:
                    break
                # Toast detail untuk admin, singkat untuk user biasa
                if st.session_state.user_role == "admin":
                    _nama_kv = kd.get("name", "?")
                    st.toast(f"⚠️ Vision Key [{_nama_kv}] gagal: {_err_vision[:80]}", icon="🔑")
                else:
                    st.toast(f"Mencoba server Vision cadangan untuk {label_idx}...", icon="📡")

            all_results.append((idx, src_name, result_text))

            pct = int(5 + ((idx + 1) / jumlah_gambar) * 55)
            progress_bar.progress(pct)

        # ── STEP 4: EVALUASI HASIL & HITUNG BIAYA AKTUAL ─────────────
        berhasil = [(idx, nama, teks) for idx, nama, teks in all_results if teks and teks.strip()]
        gagal    = [(idx, nama) for idx, nama, teks in all_results if not teks or not teks.strip()]

        if not berhasil:
            status_box.empty()
            st.error("❌ Gagal mengekstrak teks dari semua gambar. Server Vision sedang sibuk atau gambar tidak terbaca.")
            return None

        if gagal:
            nama_gagal = ", ".join([f"gambar {idx+1} ({nama})" for idx, nama in gagal])
            st.warning(f"⚠️ {len(gagal)} gambar gagal: **{nama_gagal}**. Biaya hanya dihitung untuk gambar yang berhasil.")

        # Biaya aktual hanya untuk gambar yang BERHASIL (berdasarkan posisi aslinya)
        total_rp_aktual  = sum(_harga_reguler(idx) for idx, _, _ in berhasil)
        total_mnt_aktual = sum(_menit_b2b(idx)     for idx, _, _ in berhasil)
        jumlah_berhasil  = len(berhasil)

        progress_bar.progress(65)
        status_box.info("💳 Memproses tagihan...")

        # ── STEP 5: POTONG BIAYA AKTUAL ───────────────────────────────
        if not is_admin:
            u_doc = db.collection('users').document(st.session_state.current_user)

            if vid:
                # --- POTONG TANGKI B2B ---
                curr_staff = v_data.get("staff_usage", {})
                uek = st.session_state.current_user
                if uek not in curr_staff:
                    curr_staff[uek] = {"minutes_used": 0, "docs_generated": 0}
                curr_staff[uek]["minutes_used"]   += total_mnt_aktual
                curr_staff[uek]["docs_generated"] += 1
                v_ref.update({
                    "used_quota_minutes":        firestore.Increment(total_mnt_aktual),
                    "total_documents_generated": firestore.Increment(1),
                    "staff_usage":               curr_staff
                })
                rincian_b2b = " + ".join([f"{_menit_b2b(idx)} mnt" for idx, _, _ in berhasil])
                st.toast(f"🏛️ Tangki terpotong {total_mnt_aktual} menit ({rincian_b2b})", icon="⏳")

            else:
                # --- POTONG SALDO REGULER/AIO ---
                saldo_baru = u_info.get("saldo", 0) - total_rp_aktual
                u_doc.update({"saldo": saldo_baru})
                rincian_rp = " + ".join([f"{_fmt_rp(_harga_reguler(idx))}" for idx, _, _ in berhasil])
                st.toast(f"💳 Saldo terpotong {_fmt_rp(total_rp_aktual)} ({rincian_rp})", icon="✔")

        # ── STEP 6: SUSUN TEKS HASIL ──────────────────────────────────
        # Urutkan berdasarkan posisi asli gambar
        berhasil_sorted = sorted(berhasil, key=lambda x: x[0])

        if jumlah_berhasil == 1:
            result_text = berhasil_sorted[0][2].strip()
        else:
            parts = []
            for idx, nama, teks in berhasil_sorted:
                header = f"[HALAMAN {idx + 1} — {nama}]"
                parts.append(f"{header}\n{teks.strip()}")
            result_text = ("\n\n" + ("─" * 50) + "\n\n").join(parts)

        progress_bar.progress(80)

        # ── STEP 7: INJEKSI NYAWA FUP (untuk Tab AI) ─────────────────
        u_info_akhir = get_user(st.session_state.current_user) if st.session_state.logged_in else {}
        vid_fup      = u_info_akhir.get("active_corporate_voucher") if u_info_akhir else None

        if vid_fup or is_admin:
            st.session_state.sisa_nyawa_dok = u_info_akhir.get("fup_dok_harian_limit", 35) if u_info_akhir else 35
            st.session_state.is_using_aio   = False
        elif u_info_akhir and u_info_akhir.get("bank_menit", 0) > 0:
            st.session_state.sisa_nyawa_dok = u_info_akhir.get("fup_dok_harian_limit", 35)
            st.session_state.is_using_aio   = True
        else:
            max_fup = 2
            if u_info_akhir:
                for pkt in u_info_akhir.get("inventori", []):
                    pn = pkt.get("nama", "").upper()
                    if "AIO" not in pn and pkt.get("kuota", 0) > 0:
                        if "ENTERPRISE" in pn:  max_fup = max(max_fup, 15)
                        elif "VIP" in pn:        max_fup = max(max_fup, 8)
                        elif "EKSEKUTIF" in pn:  max_fup = max(max_fup, 6)
                        elif "STARTER" in pn:    max_fup = max(max_fup, 4)
            st.session_state.sisa_nyawa_dok = max_fup
            st.session_state.is_using_aio   = False

        # ── STEP 8: SIMPAN KE SESSION STATE & FIREBASE ────────────────
        filename_clean = os.path.splitext(source_names[0])[0] if source_names else "Vision"
        label_file = (
            f"Vision_{filename_clean}" if jumlah_berhasil == 1
            else f"Vision_{jumlah_berhasil}Halaman_{filename_clean}"
        )

        st.session_state.transcript                    = result_text
        st.session_state.filename                      = label_file
        st.session_state.ai_result                     = ""
        st.session_state.ai_prefix                     = ""
        st.session_state.is_text_upload                = True
        st.session_state.durasi_audio_kotor            = 0
        st.session_state.chat_history                  = []
        st.session_state.chat_usage_count              = 0
        st.session_state.custom_template_last_file     = ""
        st.session_state.custom_template_result        = ""

        if st.session_state.logged_in:
            db.collection('users').document(st.session_state.current_user).update({
                "draft_transcript": result_text,
                "draft_filename":   label_file,
                "draft_ai_result":  "",
                "draft_ai_prefix":  "",
                "is_text_upload":   True
            })
            invalidate_user_cache(st.session_state.current_user)

        progress_bar.progress(100)

        # ── STEP 9: TAMPILKAN HASIL & STRUK ───────────────────────────
        label_gambar_info = f"{jumlah_berhasil} Gambar" if jumlah_berhasil > 1 else "1 Gambar"

        if is_admin:
            struk_biaya = "*(Admin — Gratis)*"
        elif vid:
            struk_biaya = f"🏛️ Tangki Instansi: **{total_mnt_aktual} menit** ({jumlah_berhasil} gambar)"
        else:
            struk_biaya = f"💳 Saldo terpotong: **{_fmt_rp(total_rp_aktual)}** ({jumlah_berhasil} gambar)"

        status_box.success(
            f"✔ **Selesai!** Teks berhasil diekstrak dari **{label_gambar_info}**.\n\n"
            f"{struk_biaya}\n\n"
            f"Lanjutkan ke **🧠 Analisis AI** untuk membuat dokumen."
        )

        st.markdown("**📄 Pratinjau Hasil Ekstraksi:**")
        preview_teks = result_text[:3000] + ("..." if len(result_text) > 3000 else "")
        preview_box.markdown(
            f'<div style="background:#F8F9FA; border:1px solid #DDD; border-radius:10px; '
            f'padding:15px; color:#333; font-size:13px; line-height:1.6; max-height:220px; '
            f'overflow-y:auto; white-space:pre-wrap; word-wrap:break-word; margin-bottom:20px; '
            f'-webkit-user-select:none; user-select:none;">{preview_teks}</div>',
            unsafe_allow_html=True
        )

        # 🔧 MIGRASI: components.html → st.html(unsafe_allow_javascript=True)
        # window.parent.document → document, window.parent.scrollTo → window.scrollTo
        # Body styling dihapus karena tidak lagi di iframe (akan apply ke main page kalau dibiarkan).
        btn_html = """
        <style>
            .btn-vision {
                background-color:#000; color:#FFF;
                font-family:'Plus Jakarta Sans',sans-serif;
                border:none; padding:14px 20px; font-size:16px; font-weight:700;
                border-radius:10px; width:100%; cursor:pointer; transition:all 0.2s;
                box-shadow:0 4px 6px rgba(0,0,0,0.1); display:block; box-sizing:border-box;
            }
            .btn-vision:hover { background-color:#333; transform:translateY(-2px); }
        </style>
        <button class="btn-vision" onclick="
            var tabs = document.querySelectorAll('button[data-baseweb=\\'tab\\']');
            var t = Array.from(tabs).find(tab => tab.innerText.includes('Analisis AI'));
            if(t){ t.click(); window.scrollTo({top:0, behavior:'smooth'}); }
        ">🧠 Lanjut ke Analisis AI</button>
        """
        st.html(btn_html, unsafe_allow_javascript=True)

        return result_text

    except Exception as e:
        status_box.empty()
        st.error(f"Error Vision Mode: {str(e)}")
        return None
    finally:
        stt_css.empty()