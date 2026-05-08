import streamlit as st
import streamlit.components.v1 as components
import datetime
import time
import hashlib
import requests
import re
import random
import string
import uuid
import pandas as pd
from firebase_admin import firestore, auth
from database import (
    db, get_system_config, get_all_api_keys, get_pricing_config,
    save_user, delete_user,
    add_api_key, delete_api_key, toggle_api_key
)
from engine_stt import create_docx

def _fmt_rp(angka: int) -> str:
    """Format angka ke string Rupiah. Misal: 5900000 → 'Rp 5.900.000'"""
    return f"Rp {angka:,}".replace(',', '.')

def render_tab_admin():
    import time   # <-- TAMBAHKAN INI
    """Seluruh konten Tab ⚙️ Panel Admin — hanya untuk Super Admin Developer."""
    # --- 📢 PENGUMUMAN & INFORMASI ---
    st.markdown("#### 📢 Pengumuman & Informasi")
    st.caption("Kelola papan pengumuman teks dan pop-up gambar promo untuk halaman depan.")

    sys_config = get_system_config()

    with st.expander("✏️ Kelola Pengumuman", expanded=False):

        # 🚀 FITUR BARU: Tombol Sapu Bersih (Diletakkan di LUAR form)
        if st.button("🧹 Kosongkan Formulir (Buat Pengumuman Baru)"):
            st.session_state.clear_ann_form = True
            st.rerun()

        is_clear = st.session_state.get("clear_ann_form", False)

        with st.form("form_announcement"):
            st.info("💡 Kosongkan kotak yang tidak diperlukan. Sistem akan otomatis merakitnya menjadi desain HTML yang rapi.")

            toggle_ann = st.toggle("Tampilkan Pengumuman di Layar User", value=sys_config.get("is_announcement_active", False))

            st.markdown("**1. Header & Teks Utama**")
            new_a_title = st.text_input("Judul Pengumuman", value="" if is_clear else sys_config.get("ann_title", ""))
            new_a_body = st.text_area("Paragraf Pembuka / Isi Utama", value="" if is_clear else sys_config.get("ann_body", ""), height=100)

            st.markdown("**2. Poin-Poin Detail (Opsional)**")
            curr_points = ["", "", "", "", ""] if is_clear else sys_config.get("ann_points", ["", "", "", "", ""])
            while len(curr_points) < 5: curr_points.append("")

            new_p1 = st.text_input("Poin 1", value=curr_points[0])
            new_p2 = st.text_input("Poin 2", value=curr_points[1])
            new_p3 = st.text_input("Poin 3", value=curr_points[2])
            new_p4 = st.text_input("Poin 4", value=curr_points[3])
            new_p5 = st.text_input("Poin 5", value=curr_points[4])

            st.markdown("**3. Tombol Link Keluar (Opsional)**")
            c_btn1, c_btn2 = st.columns(2)
            with c_btn1:
                new_a_btn_text = st.text_input("Teks Tombol (Misal: Baca Selengkapnya)", value="" if is_clear else sys_config.get("ann_btn_text", ""))
            with c_btn2:
                new_a_btn_url = st.text_input("URL Link (Misal: https://...)", value="" if is_clear else sys_config.get("ann_btn_url", ""))

            st.markdown("**4. Tipe Publikasi**")
            tipe_publikasi = st.radio(
                "Pilih jenis label waktu yang akan tampil di layar user:",
                ["Dipublikasikan pada", "Terakhir diperbarui"],
                index=0 if is_clear else (1 if sys_config.get("ann_time_label", "Terakhir diperbarui") == "Terakhir diperbarui" else 0),
                horizontal=True
            )

            st.write("")
            if st.form_submit_button("💾 Simpan & Publikasikan", width='stretch'):
                import datetime
                wib_tz = datetime.timezone(datetime.timedelta(hours=7))
                now_str = datetime.datetime.now(wib_tz).strftime("%d %b %Y, %H:%M WIB")

                saved_points = [new_p1, new_p2, new_p3, new_p4, new_p5]

                db.collection('settings').document('system_config').set({
                    "is_announcement_active": toggle_ann,
                    "ann_title": new_a_title,
                    "ann_body": new_a_body,
                    "ann_points": saved_points,
                    "ann_btn_text": new_a_btn_text,
                    "ann_btn_url": new_a_btn_url,
                    "ann_timestamp": now_str,
                    "ann_time_label": tipe_publikasi
                }, merge=True)

                st.session_state.clear_ann_form = False
                get_system_config.clear()
                st.toast("Pengumuman berhasil diperbarui!", icon="✔")
                time.sleep(0.8)
                st.rerun()

    import time
    import hashlib
    import requests

    try:
        cloud_name = st.secrets["cloudinary"]["cloud_name"]
        api_key = st.secrets["cloudinary"]["api_key"]
        api_secret = st.secrets["cloudinary"]["api_secret"]
    except KeyError:
        st.error("⚠️ Kredensial Cloudinary belum di-set di Streamlit Secrets.")
        st.stop()

    history_gambar = sys_config.get("popup_history", [])
    current_img_url = sys_config.get("popup_image_url", "")
    curr_version = sys_config.get("popup_version", 1)

    with st.expander("📝 Kelola Pop-Up", expanded=False):
        with st.form("form_popup_promo"):
            toggle_popup = st.toggle("🚀 Aktifkan Pop-Up Promo", value=sys_config.get("is_popup_active", False))

            st.info("💡 Isi kombinasi Gambar dan Teks sesuka Anda. Kosongkan jika tidak ingin ditampilkan.")

            uploaded_promo = st.file_uploader("Upload Gambar Baru (JPG/PNG)", type=["jpg", "jpeg", "png"])
            new_popup_text = st.text_area("Teks Keterangan (Opsional)", value=sys_config.get("popup_text", ""), placeholder="Ketik informasi detail, syarat promo, dll...", height=120)
            new_popup_url = st.text_input("URL Target (Untuk Tombol 'Lihat Detail')", value=sys_config.get("popup_target_url", ""), placeholder="https://...")

            hapus_gambar = False
            if current_img_url:
                st.caption(f"🔗 Gambar tayang saat ini: {current_img_url}")
                hapus_gambar = st.checkbox("🗑️ Copot Gambar dari Tayangan (Hanya copot, tidak hapus dari histori)")

            st.write("")
            if st.form_submit_button("💾 Simpan Pengaturan", width='stretch', key="btn_save_popup_final"):
                final_img_url = current_img_url

                if hapus_gambar:
                    final_img_url = ""

                if uploaded_promo is not None:
                    timestamp = str(int(time.time()))
                    folder_name = "TOMSTT_POPUP"

                    sign_str = f"folder={folder_name}&timestamp={timestamp}{api_secret}"
                    signature = hashlib.sha1(sign_str.encode('utf-8')).hexdigest()

                    url_cloud = f"https://api.cloudinary.com/v1_1/{cloud_name}/image/upload"
                    files = {'file': (uploaded_promo.name, uploaded_promo.getvalue(), uploaded_promo.type)}
                    data = {'api_key': api_key, 'timestamp': timestamp, 'folder': folder_name, 'signature': signature}

                    try:
                        res = requests.post(url_cloud, files=files, data=data).json()
                        if 'secure_url' in res and 'public_id' in res:
                            final_img_url = res['secure_url']
                            pub_id = res['public_id']
                            history_gambar.insert(0, {"url": final_img_url, "public_id": pub_id})
                            st.success("✔ Gambar berhasil diupload!")
                        else:
                            st.error("❌ Gagal mengupload gambar.")
                    except Exception as e:
                        st.error(f"Error Koneksi Cloudinary: {e}")

                db.collection('settings').document('system_config').set({
                    "is_popup_active": toggle_popup,
                    "popup_image_url": final_img_url,
                    "popup_text": new_popup_text,
                    "popup_target_url": new_popup_url,
                    "popup_history": history_gambar,
                    "popup_version": curr_version + 1
                }, merge=True)

                get_system_config.clear()
                st.toast("Pengaturan Pop-Up diperbarui!", icon="✔")
                time.sleep(0.8)
                st.rerun()

    with st.expander("📂 Kelola Histori Gambar Pop-Up", expanded=False):
        if not history_gambar:
            st.info("Belum ada histori gambar yang tersimpan. Upload gambar baru untuk mulai membuat galeri.")
        else:
            st.caption("Pilih gambar lama untuk ditayangkan kembali, atau hapus gambar secara permanen dari server.")

            cols = st.columns(3)
            for idx, item in enumerate(history_gambar):
                with cols[idx % 3]:
                    st.markdown(f'<div style="border:1px solid #eee; padding:10px; border-radius:10px; margin-bottom:15px;">', unsafe_allow_html=True)
                    st.image(item["url"], width='stretch')

                    if item["url"] == current_img_url:
                        st.success("✔ Sedang Tayang")
                    else:
                        if st.button("✨ Gunakan", key=f"use_img_{idx}", width='stretch'):
                            db.collection('settings').document('system_config').set({
                                "popup_image_url": item["url"],
                                "popup_version": curr_version + 1
                            }, merge=True)
                            get_system_config.clear()
                            st.rerun()

                    if st.button("🗑️ Hapus Permanen", key=f"del_img_{idx}", width='stretch', type="secondary"):
                        pub_id = item["public_id"]
                        timestamp = str(int(time.time()))
                        sign_str = f"public_id={pub_id}&timestamp={timestamp}{api_secret}"
                        signature = hashlib.sha1(sign_str.encode('utf-8')).hexdigest()

                        del_url = f"https://api.cloudinary.com/v1_1/{cloud_name}/image/destroy"
                        requests.post(del_url, data={'public_id': pub_id, 'api_key': api_key, 'timestamp': timestamp, 'signature': signature})

                        history_gambar.pop(idx)

                        update_data = {"popup_history": history_gambar}
                        if item["url"] == current_img_url:
                            update_data["popup_image_url"] = ""
                            update_data["popup_version"] = curr_version + 1

                        db.collection('settings').document('system_config').set(update_data, merge=True)
                        get_system_config.clear()
                        st.toast("Gambar berhasil dihapus permanen!", icon="🗑️")
                        time.sleep(0.8)
                        st.rerun()

                    st.markdown('</div>', unsafe_allow_html=True)

    st.markdown("---")

    # --- 💰 KELOLA HARGA PAKET ---
    st.markdown("#### 💰 Kelola Harga Paket")
    st.caption("Ubah harga aktual dan aktifkan harga coret untuk promosi. Cache diperbarui otomatis dalam 60 detik.")

    @st.fragment
    def _render_kelola_harga():
        with st.expander("✏️ Atur Harga & Promo", expanded=False):

            pc = get_pricing_config()

            LABEL_MAP = {
                "LITE":          "LITE (Rp.29.000)",
                "STARTER":       "STARTER (Rp. 89.000)",
                "EKSEKUTIF":     "EKSEKUTIF (Rp. 299.000)",
                "VIP":           "VIP (Rp. 599.000)",
                "ENTERPRISE":    "ENTERPRISE (Rp. 1.199.000)",
                "AIO10":         "AIO 10 JAM (Rp. 189.000)",
                "AIO30":         "AIO 30 JAM (Rp. 489.000)",
                "AIO100":        "AIO 100 JAM (Rp. 1.299.000)",
                "B2B_Standard":  "B2G/B2B Standard (Rp. 5.900.000)",
                "B2B_Ultimate":  "B2G/B2B Ultimate (Rp. 9.900.000)",
                "EkstensiWaktu": "Ekstensi Waktu 30 Hari (Rp. 38.000)",
                "Topup10k":      "Top-Up Saldo Rp 10.000 (Rp. 13.000)",
                "Topup20k":      "Top-Up Saldo Rp 20.000 (Rp. 23.000)",
                "Topup30k":      "Top-Up Saldo Rp 30.000 (Rp. 33.000)",
                "Topup40k":      "Top-Up Saldo Rp 40.000 (Rp. 43.000)",
            }

            updated_pricing = {}

            for key, label in LABEL_MAP.items():
                entry = pc.get(key, {"harga": 0, "harga_coret": 0, "aktif_coret": False})
                st.markdown(f"**{label}**")
                col_p1, col_p2, col_p3 = st.columns([2, 2, 1])

                with col_p1:
                    new_harga = st.number_input(
                        "Harga Aktual (Rp)", min_value=0,
                        value=int(entry.get("harga", 0)),
                        step=1000, key=f"harga_{key}",
                        label_visibility="collapsed"
                    )
                with col_p2:
                    new_harga_coret = st.number_input(
                        "Harga Coret (Rp)", min_value=0,
                        value=int(entry.get("harga_coret", 0)),
                        step=1000, key=f"coret_{key}",
                        label_visibility="collapsed",
                        help="Harga lama yang akan ditampilkan dicoret. Isi 0 jika tidak ada."
                    )
                with col_p3:
                    new_aktif = st.toggle(
                        "Coret", value=bool(entry.get("aktif_coret", False)),
                        key=f"aktif_{key}",
                        help="Aktifkan untuk tampilkan harga coret di halaman buyer."
                    )

                updated_pricing[key] = {
                    "harga":       new_harga,
                    "harga_coret": new_harga_coret,
                    "aktif_coret": new_aktif
                }

                st.markdown("<div style='margin-bottom: 4px;'></div>", unsafe_allow_html=True)

            st.markdown("---")
            if st.button("💾 Simpan Semua Perubahan Harga", type="primary", width='stretch'):
                db.collection('settings').document('pricing_config').set(updated_pricing)
                get_pricing_config.clear()
                st.success("✔ Harga berhasil disimpan! Perubahan akan tampil dalam beberapa detik.")
                st.rerun()

    _render_kelola_harga()

    st.markdown("---")

    # --- 🗂️ PENGATURAN HAK AKSES ARSIP & UPLOAD TEKS ---
    st.write("")
    st.markdown("#### 🗂️ Hak Akses Fitur Premium (Arsip & Upload Teks)")
    st.caption("Tentukan paket mana saja yang diizinkan untuk mengakses fitur eksklusif di bawah ini.")

    with st.expander("⚙️ Buka Pengaturan Hak Akses", expanded=False):
        all_packages = ["LITE", "STARTER", "EKSEKUTIF", "VIP", "ENTERPRISE", "AIO 10 JAM", "AIO 30 JAM", "AIO 100 JAM"]

        st.markdown("**1. Hak Akses Tab Arsip (Cloud Storage)**")
        current_archive_pkgs = sys_config.get("archive_allowed_packages", ["EKSEKUTIF", "VIP", "ENTERPRISE", "AIO 10 JAM", "AIO 30 JAM", "AIO 100 JAM"])
        selected_archive_pkgs = st.multiselect(
            "Paket yang diizinkan melihat riwayat Arsip:",
            options=all_packages,
            default=[p for p in current_archive_pkgs if p in all_packages]
        )

        st.markdown("---")

        st.markdown("**2. Hak Akses Upload File Manual (.pdf, .docx, .txt)**")
        current_txt_pkgs = sys_config.get("txt_allowed_packages", ["EKSEKUTIF", "VIP", "ENTERPRISE", "AIO 10 JAM", "AIO 30 JAM", "AIO 100 JAM"])
        selected_txt_pkgs = st.multiselect(
            "Paket yang diizinkan upload file .pdf, .docx, .txt:",
            options=all_packages,
            default=[p for p in current_txt_pkgs if p in all_packages]
        )

        if selected_archive_pkgs != current_archive_pkgs or selected_txt_pkgs != current_txt_pkgs:
            st.write("")
            if st.button("💾 Simpan Perubahan Hak Akses", type="primary", width='stretch'):
                db.collection('settings').document('system_config').set({
                    "archive_allowed_packages": selected_archive_pkgs,
                    "txt_allowed_packages": selected_txt_pkgs
                }, merge=True)
                get_system_config.clear()
                st.toast("Hak Akses berhasil diperbarui!", icon="✔")
                time.sleep(0.8)
                st.rerun()

    st.markdown("---")

    # --- 🚧 MODE PEMELIHARAAN (FEATURE FLAGS) ---
    st.markdown("#### 🚧 Mode Pemeliharaan Sistem")
    st.caption("Matikan sakelar ini untuk menutup akses penjualan atau fitur secara halus tanpa membuat aplikasi error.")

    sys_config = get_system_config()

    with st.expander("⚙️ Buka Pengaturan Pemeliharaan", expanded=False):
        st.markdown("**🛒 Penjualan**")
        col_m1, col_m2, col_m3 = st.columns(3)
        with col_m1:
            toggle_aio = st.toggle("Penjualan AIO", value=sys_config.get("is_aio_active", True), help="Sembunyikan tombol beli AIO.")
        with col_m2:
            toggle_reguler = st.toggle("Penjualan Reguler", value=sys_config.get("is_reguler_active", True), help="Sembunyikan tombol beli Reguler.")
        with col_m3:
            toggle_b2b = st.toggle("Penjualan B2B", value=sys_config.get("is_b2b_sys_active", True), help="Sembunyikan tombol beli paket Instansi.")

        st.markdown("**🎙️ Input Suara & Gambar**")
        col_m4, col_m5 = st.columns(2)
        with col_m4:
            toggle_rekam = st.toggle("Rekam Suara (Mic)", value=sys_config.get("is_rekam_active", True), help="Blokir rekaman langsung dari web.")
        with col_m5:
            toggle_vision = st.toggle("Upload Gambar (Vision)", value=sys_config.get("is_vision_active", True), help="Matikan untuk menutup tab Upload Gambar. Admin selalu bisa akses.")

        st.markdown("**🤖 Fitur AI**")
        col_f1 = st.columns(1)[0]
        with col_f1:
            toggle_custom_template = st.toggle(
                "🎨 AI Custom Template",
                value=sys_config.get("is_custom_template_active", True),
                help="Matikan untuk menyembunyikan expander AI Custom Template dari semua user. Admin tetap bisa akses."
            )

        st.markdown("**⚙️ Pilihan Mesin AI saat Generate Dokumen**")
        st.caption("Matikan untuk menyembunyikan pilihan mesin tersebut dari user. Minimal 1 mesin harus aktif.")
        col_e1, col_e2, col_e3 = st.columns(3)
        with col_e1:
            toggle_gemini = st.toggle(
                "Gemini (Cerdas & Stabil)",
                value=sys_config.get("is_engine_gemini_active", True),
                help="Sembunyikan pilihan Gemini dari radio button mesin AI."
            )
        with col_e2:
            toggle_groq = st.toggle(
                "Groq (Super Cepat)",
                value=sys_config.get("is_engine_groq_active", True),
                help="Sembunyikan pilihan Groq dari radio button mesin AI."
            )
        with col_e3:
            toggle_cohere = st.toggle(
                "Cohere (Detail & Formal)",
                value=sys_config.get("is_engine_cohere_active", True),
                help="Sembunyikan pilihan Cohere dari radio button mesin AI."
            )

        # Validasi: minimal 1 mesin harus aktif
        if not toggle_gemini and not toggle_groq and not toggle_cohere:
            st.error("⚠️ Minimal 1 mesin AI harus aktif! Perubahan tidak disimpan.")
        else:
            changed = (
                toggle_aio             != sys_config.get("is_aio_active",               True) or
                toggle_rekam           != sys_config.get("is_rekam_active",              True) or
                toggle_reguler         != sys_config.get("is_reguler_active",            True) or
                toggle_b2b             != sys_config.get("is_b2b_sys_active",            True) or
                toggle_vision          != sys_config.get("is_vision_active",             True) or
                toggle_custom_template != sys_config.get("is_custom_template_active",    True) or
                toggle_gemini          != sys_config.get("is_engine_gemini_active",      True) or
                toggle_groq            != sys_config.get("is_engine_groq_active",        True) or
                toggle_cohere          != sys_config.get("is_engine_cohere_active",      True)
            )
            if changed:
                db.collection('settings').document('system_config').set({
                    "is_aio_active":               toggle_aio,
                    "is_reguler_active":            toggle_reguler,
                    "is_b2b_sys_active":            toggle_b2b,
                    "is_rekam_active":              toggle_rekam,
                    "is_vision_active":             toggle_vision,
                    "is_custom_template_active":    toggle_custom_template,
                    "is_engine_gemini_active":      toggle_gemini,
                    "is_engine_groq_active":        toggle_groq,
                    "is_engine_cohere_active":      toggle_cohere,
                }, merge=True)
                get_system_config.clear()
                st.toast("Status Pemeliharaan Berhasil Diperbarui!", icon="✔️")
                time.sleep(0.8)
                st.rerun()

    st.markdown("---")

    # ==========================================
    # 📷 KONFIGURASI VISION MODE  ← BARU
    # ==========================================
    st.markdown("#### 📷 Konfigurasi Vision Mode")
    st.caption("Atur hak akses paket untuk fitur Upload Gambar. Secara default hanya B2G/B2B yang dapat mengakses. Admin Developer selalu bypass.")

    sys_config = get_system_config()

    with st.expander("⚙️ Buka Pengaturan Vision Mode", expanded=False):
        all_packages_vision = ["LITE", "STARTER", "EKSEKUTIF", "VIP", "ENTERPRISE",
                               "AIO 10 JAM", "AIO 30 JAM", "AIO 100 JAM"]
        current_vision_pkgs = sys_config.get("vision_allowed_packages", [])
        selected_vision_pkgs = st.multiselect(
            "Paket yang diizinkan menggunakan Vision Mode (selain B2G/B2B & Admin yang selalu bisa):",
            options=all_packages_vision,
            default=[p for p in current_vision_pkgs if p in all_packages_vision],
            help="Kosongkan = hanya B2G/B2B. Admin Developer selalu bypass tanpa syarat."
        )

        st.markdown("---")

        # Ringkasan status key Vision dari pool yang ada
        all_keys_now = list(get_all_api_keys())
        vision_keys_aktif   = [k for k in all_keys_now if k.get("is_vision") and k.get("is_active") and k.get("provider") == "Gemini"]
        vision_keys_backup  = [k for k in all_keys_now if k.get("provider") == "Gemini Vision" and k.get("is_active")]

        col_v1, col_v2 = st.columns(2)
        with col_v1:
            st.metric("📷 Gemini Vision Keys Aktif (Tier 1)", len(vision_keys_aktif),
                      help="Key Gemini existing yang di-checklist sebagai Vision Key.")
        with col_v2:
            st.metric("🔑 Backup Vision Keys Aktif (Tier 2)", len(vision_keys_backup),
                      help="Key provider 'Gemini Vision' sebagai backup jika Tier 1 habis.")

        if not vision_keys_aktif and not vision_keys_backup:
            st.warning("⚠️ **Belum ada API Key Vision yang aktif!** Centang '📷 Gunakan sebagai Vision Key' pada minimal satu key Gemini di bagian Daftar API Key di bawah.")

        # --- SETELAH blok warning ---
        st.markdown("---")
        st.markdown("**⚖️ Bobot API Vision di Load Balancer**")
        st.caption(
            "Karena Vision mengonsumsi token jauh lebih banyak dari generate teks biasa, "
            "setiap 1 panggilan Vision dihitung sebagai N panggilan di load balancer. "
            "Ini membuat limit harian lebih akurat dan threshold 80% lebih tepat."
        )
        vision_api_weight = st.number_input(
            "1 Panggilan Vision = ? Panggilan Reguler",
            min_value=1, max_value=50,
            value=int(sys_config.get("vision_api_weight", 5)),
            step=1,
            key="vision_api_weight_input",
            help="Default: 5. Naikkan jika API Vision cepat habis, turunkan jika terlalu konservatif."
        )

        # --- UPDATE tombol simpan (ganti yang lama) ---
        st.write("")
        if st.button("💾 Simpan Pengaturan Vision", type="primary", width='stretch', key="btn_save_vision_cfg"):
            db.collection("settings").document("system_config").set({
                "vision_allowed_packages": selected_vision_pkgs,
                "vision_api_weight":       vision_api_weight
            }, merge=True)
            get_system_config.clear()
            st.toast("Pengaturan Vision berhasil disimpan!", icon="✔")
            time.sleep(0.8)
            st.rerun()

        if vision_keys_aktif:
            with st.expander(f"👁️ Lihat {len(vision_keys_aktif)} Key Vision Tier 1 Aktif", expanded=False):
                for vk in vision_keys_aktif:
                    sisa = vk['limit'] - vk.get('used', 0)
                    pct  = int((vk.get('used', 0) / vk['limit']) * 100) if vk['limit'] > 0 else 0
                    warna = "#fdeced" if pct >= 80 else "#e6f3ff"
                    st.markdown(
                        f"<div style='background:{warna}; padding:8px 12px; border-radius:8px; "
                        f"margin-bottom:6px; font-size:13px;'>"
                        f"<b>{vk['name']}</b> — Sisa: <b>{sisa}</b>/{vk['limit']} ({pct}%)"
                        f"{'  ⚠️ Mendekati Limit' if pct >= 80 else ''}</div>",
                        unsafe_allow_html=True
                    )

    st.markdown("---")

    # --- SAKELAR GLOBAL GROQ WHISPER ---
    st.markdown("#### 🚀 Konfigurasi Mesin Transkrip (STT) Global")
    st.caption("Atur mesin utama, hak akses paket, dan model yang digunakan untuk mengubah suara menjadi teks.")
    sys_config = get_system_config()

    with st.expander("⚙️ Buka Konfigurasi Mesin STT", expanded=False):
        st.markdown("**Pengaturan Prioritas B2G/B2B**")
        groq_b2b_admin_bypass = st.toggle(
            "⚡ Aktifkan Groq Whisper untuk B2G/B2B & Admin (Independen dari sakelar retail)",
            value=sys_config.get("groq_b2b_admin_bypass", True),
            help="Jika ON, Admin & seluruh staf B2G/B2B SELALU dapat Groq Whisper — bahkan jika sakelar retail di bawah dimatikan."
        )

        st.markdown("---")

        st.markdown("**Pengaturan User Retail**")
        use_groq = st.toggle("⚡ Aktifkan Groq Whisper API untuk User Publik", value=sys_config.get("use_groq_stt", False))

        if use_groq:
            st.success("Groq Whisper Publik AKTIF. Silahkan atur hak akses paket di bawah ini:")

            valid_options = ["LITE", "STARTER", "EKSEKUTIF", "VIP", "ENTERPRISE", "AIO 10 JAM", "AIO 30 JAM", "AIO 100 JAM"]
            raw_defaults = sys_config.get("allowed_packages", ["EKSEKUTIF", "VIP", "ENTERPRISE", "AIO 30 JAM", "AIO 100 JAM"])

            safe_defaults = []
            for p in raw_defaults:
                p_upper = p.upper()
                if "PRO" in p_upper: p_upper = "STARTER"
                if p_upper in valid_options and p_upper not in safe_defaults:
                    safe_defaults.append(p_upper)

            if not safe_defaults:
                safe_defaults = ["EKSEKUTIF", "VIP", "ENTERPRISE", "AIO 30 JAM", "AIO 100 JAM"]

            allowed_packages = st.multiselect(
                "🎯 Pilih Paket Publik yang berhak akses Groq:",
                valid_options,
                default=safe_defaults
            )

            model_choice = st.selectbox(
                "⚙️ Pilih Model Whisper yang digunakan:",
                ["Whisper V3 Large (Akurasi Tinggi, $0.111/jam)", "Whisper Large v3 Turbo (Super Cepat & Murah, $0.04/jam)"],
                index=0 if "turbo" not in sys_config.get("groq_model", "") else 1
            )
            groq_model_str = "whisper-large-v3-turbo" if "Turbo" in model_choice else "whisper-large-v3"

        else:
            groq_model_str = sys_config.get("groq_model", "whisper-large-v3")
            allowed_packages = sys_config.get("allowed_packages", ["EKSEKUTIF", "VIP", "ENTERPRISE", "AIO 30 JAM", "AIO 100 JAM"])
            st.info("💡 Saat ini sakelar Publik OFF. Seluruh user (di luar B2B/Admin) menggunakan **Google Speech Recognition** (Gratis).")

        st.write("")
        if st.button("💾 Simpan Pengaturan STT", type="primary", width='stretch'):
            db.collection('settings').document('system_config').set({
                "use_groq_stt": use_groq,
                "groq_b2b_admin_bypass": groq_b2b_admin_bypass,
                "groq_model": groq_model_str,
                "allowed_packages": allowed_packages
            }, merge=True)
            get_system_config.clear()
            st.toast("Pengaturan Global STT berhasil disimpan!", icon="💾")
            time.sleep(0.8)
            st.rerun()

    st.markdown("---")

    # --- MANAJEMEN API KEY & LOAD BALANCER ---

    # ── Fetch model list dinamis per provider (cache 48 jam) ──
    FALLBACK_GEMINI = ["gemini-2.5-flash", "gemini-3.1-flash-lite-preview"]
    FALLBACK_GROQ   = ["llama-3.3-70b-versatile", "llama-3.1-8b-instant"]
    FALLBACK_COHERE = ["command-a-03-2025", "command-r-plus-08-2024"]

    @st.cache_data(ttl=172800)  # 48 jam
    def _fetch_gemini_models(api_key: str) -> list:
        try:
            import google.generativeai as _genai
            _genai.configure(api_key=api_key)
            models = []
            for m in _genai.list_models():
                if "generateContent" not in m.supported_generation_methods:
                    continue
                name = m.name.replace("models/", "")
                # Filter: hanya flash dan pro yang relevan untuk text gen
                if not any(kw in name for kw in ["flash", "pro"]):
                    continue
                # Exclude non-text-gen variants
                if any(kw in name for kw in ["tts", "audio", "live", "image", "vision", "embed"]):
                    continue
                models.append(name)
            return sorted(set(models)) if models else FALLBACK_GEMINI
        except Exception:
            return FALLBACK_GEMINI

    @st.cache_data(ttl=172800)
    def _fetch_groq_models(api_key: str) -> list:
        try:
            import requests as _req
            resp = _req.get(
                "https://api.groq.com/openai/v1/models",
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=10
            )
            data = resp.json().get("data", [])
            models = []
            for m in data:
                mid = m.get("id", "")
                # Hanya text/chat models — exclude STT, TTS, guard, vision
                if any(kw in mid.lower() for kw in ["whisper", "tts", "guard", "vision", "embed"]):
                    continue
                models.append(mid)
            return sorted(models) if models else FALLBACK_GROQ
        except Exception:
            return FALLBACK_GROQ

    @st.cache_data(ttl=172800)
    def _fetch_cohere_models(api_key: str) -> list:
        try:
            import cohere as _cohere
            co = _cohere.Client(api_key=api_key, timeout=90)
            # Filter hanya model yang support endpoint 'chat'
            resp    = co.models.list(endpoint="chat")
            models  = [m.name for m in resp.models if m.name]
            return sorted(models) if models else FALLBACK_COHERE
        except Exception:
            return FALLBACK_COHERE

    def _get_models_for_provider(provider: str, all_keys_list: list) -> list:
        """Ambil list model untuk provider tertentu, menggunakan key paid jika ada."""
        provider_keys = [k for k in all_keys_list if k.get("provider") == provider and k.get("is_active")]
        if not provider_keys:
            if provider == "Gemini": return FALLBACK_GEMINI
            if provider == "Groq":   return FALLBACK_GROQ
            if provider == "Cohere": return FALLBACK_COHERE
            return []
        # Prioritas: paid key dulu, lalu key manapun
        paid_keys = [k for k in provider_keys if k.get("is_paid")]
        chosen_key = (paid_keys or provider_keys)[0]
        api_key = chosen_key["key"]
        if provider == "Gemini": return _fetch_gemini_models(api_key)
        if provider == "Groq":   return _fetch_groq_models(api_key)
        if provider == "Cohere": return _fetch_cohere_models(api_key)
        return []

    # ── Dialog Edit API Key ──

    # ── Dialog Edit API Key ──

    @st.dialog("✏️ Edit API Key")
    def dialog_edit_api(doc_id, current_name, current_limit, current_is_vision=False,
                        current_model="", current_is_paid=False):
        key_doc_snap = db.collection("api_keys").document(doc_id).get()
        key_doc_data = key_doc_snap.to_dict() if key_doc_snap.exists else {}
        provider     = key_doc_data.get("provider", "")
        is_gemini    = provider == "Gemini"
        has_model    = provider in ("Gemini", "Groq", "Cohere")

        with st.form(f"form_edit_{doc_id}"):
            edit_name  = st.text_input("Nama Key", value=current_name)
            edit_key   = st.text_input("Update API Key (KOSONGKAN jika tidak ingin diubah)", type="password")
            edit_limit = st.number_input("Batas Limit Kuota/Hari", min_value=1, value=int(current_limit))

            edit_is_vision = False
            edit_model     = current_model
            edit_is_paid   = current_is_paid

            if is_gemini:
                edit_is_vision = st.toggle(
                    "📷 Gunakan sebagai Vision Key (Vision Mode)",
                    value=bool(current_is_vision),
                    help="Aktifkan agar key ini diprioritaskan untuk Vision Mode."
                )

            if has_model:
                _model_list = _get_models_for_provider(provider, list(get_all_api_keys()))
                _default    = current_model if current_model in _model_list else (_model_list[0] if _model_list else "")
                _idx        = _model_list.index(_default) if _default in _model_list else 0
                edit_model  = st.selectbox(
                    f"🤖 Model {provider}",
                    _model_list,
                    index=_idx,
                    help=f"Model yang digunakan key ini. Diambil dari API {provider} (cache 48 jam)."
                )
                edit_is_paid = st.toggle(
                    "💳 Key Berbayar (Paid)",
                    value=bool(current_is_paid),
                    help="Paid: increment weighted berdasarkan panjang teks. Free: flat +1."
                )

            if st.form_submit_button("Simpan Perubahan", width='stretch'):
                update_data = {"name": edit_name, "limit": edit_limit}
                if edit_key.strip():
                    update_data["key"] = edit_key.strip()
                if is_gemini:
                    update_data["is_vision"] = edit_is_vision
                if has_model:
                    update_data["is_paid"]   = edit_is_paid
                if has_model and edit_model:
                    update_data["model"] = edit_model
                db.collection("api_keys").document(doc_id).update(update_data)
                get_all_api_keys.clear()
                st.success("✔ Berhasil diubah!")
                st.rerun()

    @st.dialog("⚠️ Konfirmasi Hapus API Key")
    def dialog_hapus_api(doc_id, api_name):
        st.warning(f"Anda yakin ingin menghapus API Key **{api_name}**?")
        st.info("Kunci ini akan dihapus dari bank dan tidak bisa digunakan lagi oleh sistem.")
        c1, c2 = st.columns(2)
        with c1:
            if st.button("❌ Batal", width='stretch', key=f"cancel_api_{doc_id}"):
                st.rerun()
        with c2:
            if st.button("🚨 Ya, Hapus!", width='stretch', key=f"confirm_api_{doc_id}"):
                delete_api_key(doc_id)
                get_all_api_keys.clear()
                st.toast(f"✔ API Key '{api_name}' berhasil dihapus!")
                time.sleep(0.8)
                st.rerun()

    @st.dialog("⚠️ Konfirmasi Reset Kuota API")
    def dialog_reset_api():
        st.warning("Anda yakin ingin me-reset (meng-nol-kan) seluruh pemakaian API Key hari ini?")
        st.info("Tindakan ini akan membuat semua kunci yang habis (Limit Reached) segar kembali dan bisa digunakan oleh sistem.")
        c1, c2 = st.columns(2)
        with c1:
            if st.button("❌ Batal", width='stretch'):
                st.rerun()
        with c2:
            if st.button("🌿 Ya, Reset Semua!", width='stretch', key="conf_reset_api"):
                all_api_docs = db.collection('api_keys').stream()
                for doc in all_api_docs:
                    db.collection('api_keys').document(doc.id).update({"used": 0})
                get_all_api_keys.clear()
                st.toast("Seluruh kuota API berhasil di-reset menjadi 0!", icon="🌿")
                time.sleep(0.8)
                st.rerun()

    # ── Ambil & lazy-reset data key ──
    import datetime
    wib_tz = datetime.timezone(datetime.timedelta(hours=7))
    today_str = datetime.datetime.now(wib_tz).strftime("%Y-%m-%d")

    all_keys = list(get_all_api_keys())

    needs_cache_clear = False
    for key in all_keys:
        if key.get('last_reset_date', '') != today_str:
            db.collection('api_keys').document(key['id']).update({
                "used": 0,
                "last_reset_date": today_str
            })
            key['used'] = 0
            key['last_reset_date'] = today_str
            needs_cache_clear = True
    if needs_cache_clear:
        get_all_api_keys.clear()

    all_keys.sort(key=lambda x: (x.get('provider', ''), x.get('name', '')))

    # ── Rekap jumlah key per provider ──
    count_gemini       = sum(1 for k in all_keys if k.get('provider') == 'Gemini')
    count_groq         = sum(1 for k in all_keys if k.get('provider') == 'Groq')
    count_cohere       = sum(1 for k in all_keys if k.get('provider') == 'Cohere')
    count_groq_whisper = sum(1 for k in all_keys if k.get('provider') == 'Groq Whisper')
    count_gemini_vision_provider = sum(1 for k in all_keys if k.get('provider') == 'Gemini Vision')
    count_gemini_vision_flag     = sum(1 for k in all_keys if k.get('is_vision') and k.get('provider') == 'Gemini')
    count_gemini_paid = sum(1 for k in all_keys if k.get('provider') == 'Gemini' and k.get('is_paid'))
    count_groq_paid   = sum(1 for k in all_keys if k.get('provider') == 'Groq'   and k.get('is_paid'))
    count_cohere_paid = sum(1 for k in all_keys if k.get('provider') == 'Cohere' and k.get('is_paid'))

    # Pre-compute label paid agar tidak ada nested f-string di dalam st.markdown
    _paid_style = 'color:#155724;font-size:12px;'
    lbl_gemini_paid = f'&nbsp;<span style="{_paid_style}">({count_gemini_paid} paid)</span>' if count_gemini_paid else ''
    lbl_groq_paid   = f'&nbsp;<span style="{_paid_style}">({count_groq_paid} paid)</span>'   if count_groq_paid   else ''
    lbl_cohere_paid = f'&nbsp;<span style="{_paid_style}">({count_cohere_paid} paid)</span>' if count_cohere_paid else ''

    st.markdown("#### 📋 Daftar API Key & Sisa Kuota")
    st.caption("Pantau ketersediaan dan atur ulang limit API harian sistem Anda.")

    with st.expander("📊 Buka Rekap Kuota & Reset API", expanded=False):
        col_rekap, col_reset = st.columns([3, 1])
        with col_rekap:
            st.markdown(f"""
            <div style="display: flex; gap: 12px; margin-bottom: 20px; flex-wrap: wrap;">
                <div style="background-color: #f0f2f6; padding: 6px 16px; border-radius: 20px; font-size: 14px; color: #333; font-weight: 600; border: 1px solid #e4e4e4; box-shadow: 0 1px 2px rgba(0,0,0,0.05);">
                    🧠 Gemini: <span style="color: #e74c3c; font-weight: 800; font-size: 15px;">{count_gemini}</span>{lbl_gemini_paid}
                </div>
                <div style="background-color: #f0f2f6; padding: 6px 16px; border-radius: 20px; font-size: 14px; color: #333; font-weight: 600; border: 1px solid #e4e4e4; box-shadow: 0 1px 2px rgba(0,0,0,0.05);">
                    ⚡ Groq: <span style="color: #e74c3c; font-weight: 800; font-size: 15px;">{count_groq}</span>{lbl_groq_paid}
                </div>
                <div style="background-color: #f0f2f6; padding: 6px 16px; border-radius: 20px; font-size: 14px; color: #333; font-weight: 600; border: 1px solid #e4e4e4; box-shadow: 0 1px 2px rgba(0,0,0,0.05);">
                    🧭 Cohere: <span style="color: #e74c3c; font-weight: 800; font-size: 15px;">{count_cohere}</span>{lbl_cohere_paid}
                </div>
                <div style="background-color: #f0f2f6; padding: 6px 16px; border-radius: 20px; font-size: 14px; color: #333; font-weight: 600; border: 1px solid #e4e4e4; box-shadow: 0 1px 2px rgba(0,0,0,0.05);">
                    🎙️ G-Whisper: <span style="color: #e74c3c; font-weight: 800; font-size: 15px;">{count_groq_whisper}</span>
                </div>
                <div style="background-color: #e6f3ff; padding: 6px 16px; border-radius: 20px; font-size: 14px; color: #0056b3; font-weight: 600; border: 1px solid #cce5ff; box-shadow: 0 1px 2px rgba(0,0,0,0.05);">
                    📷 Vision (Flag): <span style="color: #e74c3c; font-weight: 800; font-size: 15px;">{count_gemini_vision_flag}</span>
                </div>
                <div style="background-color: #e6f3ff; padding: 6px 16px; border-radius: 20px; font-size: 14px; color: #0056b3; font-weight: 600; border: 1px solid #cce5ff; box-shadow: 0 1px 2px rgba(0,0,0,0.05);">
                    📷 Vision (Backup): <span style="color: #e74c3c; font-weight: 800; font-size: 15px;">{count_gemini_vision_provider}</span>
                </div>
            </div>
            """, unsafe_allow_html=True)
        with col_reset:
            if st.button("Reset Kuota", type="primary", width='stretch'):
                dialog_reset_api()

    # ── Form Tambah API Key Baru (dengan is_vision support) ──
    with st.expander("➕ Tambah API Key Baru"):
        # Provider di LUAR form agar perubahan langsung trigger rerun & update model list
        new_provider = st.selectbox(
            "Provider",
            ["Gemini", "Groq", "Cohere", "Groq Whisper", "Gemini Vision"],
            key="add_key_provider"
        )
        with st.form("form_add_key"):
            col1, col2 = st.columns(2)
            with col1:
                new_name = st.text_input("Nama Key (Misal: Akun Istri)")
            with col2:
                new_limit = st.number_input("Batas Limit Kuota/Hari", min_value=1, value=200)
            new_key_str = st.text_input("Paste API Key", type="password")

            # Field model untuk Gemini, Groq, Cohere
            new_is_vision = False
            new_model     = ""
            new_is_paid   = False
            if new_provider in ("Gemini", "Groq", "Cohere"):
                if new_provider == "Gemini":
                    new_is_vision = st.checkbox(
                        "📷 Gunakan sebagai Vision Key (Vision Mode)",
                        value=False,
                        help="Key ini akan diprioritaskan untuk Vision Mode."
                    )
                _model_list_new = _get_models_for_provider(new_provider, list(get_all_api_keys()))
                new_model = st.selectbox(
                    f"🤖 Model {new_provider}",
                    _model_list_new,
                    index=0,
                    help=f"Model diambil dari API {new_provider} (cache 48 jam). Refresh panel jika list belum update."
                )
                new_is_paid = st.checkbox(
                    "💳 Key Berbayar (Paid)",
                    value=False,
                    help="Centang untuk key berbayar (Production). Increment akan weighted berdasarkan panjang teks."
                )

            if st.form_submit_button("💾 Simpan Kunci API", width='stretch'):
                if new_name and new_key_str:
                    doc_data = {
                        "name":            new_name,
                        "provider":        new_provider,
                        "key":             new_key_str,
                        "limit":           int(new_limit),
                        "used":            0,
                        "is_active":       True,
                        "is_vision":       new_is_vision,
                        "last_reset_date": today_str,
                    }
                    if new_provider in ("Gemini", "Groq", "Cohere") and new_model:
                        doc_data["model"] = new_model
                    if new_provider in ("Gemini", "Groq", "Cohere"):
                        doc_data["is_paid"] = new_is_paid
                    db.collection("api_keys").add(doc_data)
                    get_all_api_keys.clear()
                    st.success("✔ API Key berhasil ditambahkan ke Bank!")
                    st.rerun()
                else:
                    st.error("Isi Nama dan API Key!")

    # ── Daftar API Key Tersimpan (grouped, compact, paginated) ──
    with st.expander("👁️ Lihat & Kelola API Key Tersimpan"):

        KEYS_PER_PAGE = 10

        def _render_key_group(group_keys: list, page_key: str):
            """Render satu group provider dengan card compact dan pagination."""
            if not group_keys:
                st.caption("Tidak ada key untuk provider ini.")
                return

            total_keys  = len(group_keys)
            if page_key not in st.session_state:
                st.session_state[page_key] = 0
            page_g = st.session_state[page_key]
            # Guard: jangan melebihi halaman terakhir
            page_g = max(0, min(page_g, (total_keys - 1) // KEYS_PER_PAGE))
            st.session_state[page_key] = page_g

            start       = page_g * KEYS_PER_PAGE
            end         = start + KEYS_PER_PAGE
            keys_slice  = group_keys[start:end]
            total_pages = max(1, -(-total_keys // KEYS_PER_PAGE))

            st.caption(
                f"Menampilkan {start+1}–{min(end, total_keys)} dari {total_keys} key  ·  "
                f"Halaman {page_g+1}/{total_pages}"
            )

            for k in keys_slice:
                doc_id       = k['id']
                sisa_kuota   = k['limit'] - k.get('used', 0)
                is_active    = k.get('is_active', False)
                is_exhausted = is_active and (k.get('used', 0) >= k['limit'])

                if not is_active:
                    status_icon  = "🔴"
                    status_text  = "NONAKTIF"
                    status_color = "#f0f0f0"
                elif is_exhausted:
                    status_icon  = "🟠"
                    status_text  = "LIMIT HABIS"
                    status_color = "#fdeced"
                else:
                    status_icon  = "🟢"
                    status_text  = "AKTIF"
                    status_color = "#e6f3ff"

                # Badges ringkas
                badges = ""
                if k.get("provider") == "Gemini Vision":
                    badges += " <span style='background:#fce8ff;color:#7b00b3;padding:1px 6px;border-radius:8px;font-size:11px;font-weight:700;'>📷 V-Backup</span>"
                elif k.get("is_vision") and k.get("provider") == "Gemini":
                    badges += " <span style='background:#e6f3ff;color:#0056b3;padding:1px 6px;border-radius:8px;font-size:11px;font-weight:700;'>📷 Vision</span>"
                if k.get("provider") == "Groq Whisper":
                    badges += " <span style='background:#f3e6ff;color:#5b0db3;padding:1px 6px;border-radius:8px;font-size:11px;font-weight:700;'>🎙️ Whisper</span>"

                model_name_disp = k.get("model", "")
                if model_name_disp and k.get("provider") in ("Gemini", "Groq", "Cohere"):
                    _short = model_name_disp
                    if "2.5-flash"       in model_name_disp: _short = "2.5F"
                    elif "3.1"           in model_name_disp: _short = "3.1L"
                    elif "llama-3.3-70b" in model_name_disp: _short = "L3.3-70B"
                    elif "llama-3.1-8b"  in model_name_disp: _short = "L3.1-8B"
                    elif "command-a-03"  in model_name_disp: _short = "Cmd-A"
                    elif "command-r-plus" in model_name_disp: _short = "CmdR+"
                    elif len(model_name_disp) > 10:           _short = model_name_disp[:10]
                    badges += (
                        f" <span style='background:#fff3cd;color:#856404;padding:1px 6px;"
                        f"border-radius:8px;font-size:11px;font-weight:700;'>⚙️ {_short}</span>"
                    )
                if k.get("is_paid") and k.get("provider") in ("Gemini", "Groq", "Cohere"):
                    badges += " <span style='background:#d4edda;color:#155724;padding:1px 6px;border-radius:8px;font-size:11px;font-weight:700;'>💳 Paid</span>"

                st.markdown(f"""
                <div style="background:{status_color};padding:8px 10px;margin-bottom:6px;
                            border-radius:8px;border:1px solid #ddd;">
                    <div style="font-weight:700;color:#111;font-size:13px;">
                        {k['name']}{badges}
                        &nbsp;{status_icon} <span style="font-weight:500;font-size:12px;color:#555;">{status_text}</span>
                    </div>
                    <div style="font-size:12px;color:#444;margin-top:2px;">
                        Limit: <b>{sisa_kuota}</b>/{k['limit']} &nbsp;·&nbsp; Pakai: {k.get('used', 0)}
                    </div>
                </div>
                """, unsafe_allow_html=True)

                ca1, ca2, ca3 = st.columns([1, 1, 1])
                with ca1:
                    if st.button("✏️ Edit", key=f"edit_{doc_id}", width='stretch'):
                        dialog_edit_api(
                            doc_id, k['name'], k['limit'],
                            k.get('is_vision', False),
                            k.get('model', ''),
                            k.get('is_paid', False)
                        )
                with ca2:
                    btn_label = "🔴 Matikan" if k.get('is_active') else "🟢 Hidupkan"
                    if st.button(btn_label, key=f"tog_{doc_id}", width='stretch'):
                        toggle_api_key(doc_id, k.get('is_active'))
                        get_all_api_keys.clear()
                        st.rerun()
                with ca3:
                    if st.button("🗑️ Hapus", key=f"del_{doc_id}", width='stretch'):
                        dialog_hapus_api(doc_id, k['name'])

            # ── Navigasi halaman (hanya muncul jika >1 halaman) ──
            if total_pages > 1:
                st.markdown("---")
                col_prev, col_info_pg, col_next = st.columns([1, 2, 1])
                with col_prev:
                    if page_g > 0:
                        if st.button("◀ Sebelumnya", key=f"prev_{page_key}", width='stretch'):
                            st.session_state[page_key] = page_g - 1
                            st.rerun()
                with col_info_pg:
                    st.markdown(
                        f"<div style='text-align:center;padding-top:8px;color:#555;'>"
                        f"Halaman <b>{page_g+1}</b> dari <b>{total_pages}</b></div>",
                        unsafe_allow_html=True
                    )
                with col_next:
                    if end < total_keys:
                        if st.button("Berikutnya ▶", key=f"next_{page_key}", width='stretch'):
                            st.session_state[page_key] = page_g + 1
                            st.rerun()

        # ── Pisahkan & urutkan keys per group ──
        gemini_keys = [k for k in all_keys if k.get('provider') in ('Gemini', 'Gemini Vision')]
        groq_keys   = [k for k in all_keys if k.get('provider') in ('Groq', 'Groq Whisper')]
        cohere_keys = [k for k in all_keys if k.get('provider') == 'Cohere']

        # Gemini: Vision Backup → Vision Flag → Paid → Regular
        def _sort_gemini(k):
            if k.get('provider') == 'Gemini Vision': return 0
            if k.get('is_vision'):                    return 1
            if k.get('is_paid'):                      return 2
            return 3
        gemini_keys.sort(key=_sort_gemini)

        # Groq: Whisper dulu → Paid → regular
        groq_keys.sort(key=lambda k: (0 if k.get('provider') == 'Groq Whisper' else 1, 0 if k.get('is_paid') else 1))

        # Cohere: Paid dulu → regular
        cohere_keys.sort(key=lambda k: 0 if k.get('is_paid') else 1)

        group_defs = [
            ("🧠 Gemini", gemini_keys, "api_page_gemini"),
            ("⚡ Groq",   groq_keys,   "api_page_groq"),
            ("🧭 Cohere", cohere_keys, "api_page_cohere"),
        ]

        for group_label, group_keys, page_key in group_defs:
            with st.expander(f"{group_label} ({len(group_keys)} key)", expanded=False):
                _render_key_group(group_keys, page_key)

    st.markdown("---")

    # --- GENERATOR VOUCHER ---
    st.markdown("#### 🎫 Generator Voucher Promo")
    st.caption("Buat kode akses untuk diberikan secara manual kepada instansi/klien atau sebagai promo gratis.")

    with st.expander("➕ Buat Voucher Baru"):
        mode_voucher = st.radio("Tipe Voucher yang akan dibuat:", ["Voucher Publik / Reguler", "Lisensi B2G/B2B (Instansi)"], horizontal=True)
        st.markdown("---")

        if mode_voucher == "Voucher Publik / Reguler":
            paket_default_map = {"LITE": 3, "STARTER": 10, "EKSEKUTIF": 30, "VIP": 65, "ENTERPRISE": 150, "AIO 10 JAM": 9999, "AIO 30 JAM": 9999, "AIO 100 JAM": 9999}
            v_paket_sementara = st.selectbox("Pilih Paket Dasar yang Diberikan", ["LITE", "STARTER", "EKSEKUTIF", "VIP", "ENTERPRISE", "AIO 10 JAM", "AIO 30 JAM", "AIO 100 JAM"], key="v_paket_sel")

            with st.form("form_voucher_reg"):
                v_kode = st.text_input("Custom Kode Voucher (Kosongkan jika ingin dibuat acak otomatis)", placeholder="Contoh: TOMSTT-VIP01").strip().upper()

                is_aio = "AIO" in v_paket_sementara
                if is_aio:
                    bank_menit_map = {"AIO 10 JAM": 600, "AIO 30 JAM": 1800, "AIO 100 JAM": 6000}
                    v_kuota_custom = 9999
                    v_bank_menit = st.number_input("Waktu STT yang Diberikan (Menit):", min_value=60, value=bank_menit_map[v_paket_sementara], step=60)
                else:
                    v_kuota_custom = st.number_input(f"Batas Kuota Tiket yang Diberikan:", min_value=1, value=paket_default_map[v_paket_sementara])
                    v_bank_menit = 0

                col_t1, col_t2 = st.columns(2)
                with col_t1: v_tipe = st.radio("Tipe Voucher", ["Eksklusif (1x Pakai)", "Massal (Multi-Klaim)"])
                with col_t2: v_kuota_klaim = st.number_input("Batas Klaim (Berapa Orang)", min_value=1, value=10)

                if st.form_submit_button("🔨 Generate Voucher Reguler", width='stretch'):
                    if not v_kode: v_kode = "TOM-" + ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))

                    durasi_map = {"LITE": 45, "STARTER": 60, "EKSEKUTIF": 90, "VIP": 150, "ENTERPRISE": 240, "AIO 10 JAM": 9999, "AIO 30 JAM": 9999, "AIO 100 JAM": 9999}
                    max_k = 1 if v_tipe == "Eksklusif (1x Pakai)" else v_kuota_klaim

                    if db.collection('vouchers').document(v_kode).get().exists:
                        st.error(f"❌ Kode '{v_kode}' sudah pernah dibuat! Silahkan gunakan kode lain.")
                    else:
                        db.collection('vouchers').document(v_kode).set({
                            "kode_voucher": v_kode,
                            "nama_paket": v_paket_sementara,
                            "kuota_paket": v_kuota_custom,
                            "batas_durasi": durasi_map[v_paket_sementara],
                            "bank_menit": v_bank_menit,
                            "tipe": v_tipe,
                            "max_klaim": int(max_k),
                            "jumlah_terklaim": 0,
                            "riwayat_pengguna": [],
                            "created_at": firestore.SERVER_TIMESTAMP
                        })
                        st.success(f"✔ Berhasil! Kode Voucher: **{v_kode}** siap digunakan.")
                        st.rerun()
        else:
            st.info("💡 **Info B2G/B2B:** Kode ini adalah *Master License*. Hanya perlu diklaim 1x oleh PIC Instansi. Setelah diklaim, PIC dapat mendaftarkan stafnya melalui Panel Admin B2B.")

            pc_admin = get_pricing_config()
            PAKET_B2B_MAP = {
                f"Standard ({_fmt_rp(pc_admin['B2B_Standard']['harga'])} — 550 Jam, 15 User)": {
                    "quota": 33000, "max_users": 15, "paket_key": "B2B_Standard"
                },
                f"Ultimate ({_fmt_rp(pc_admin['B2B_Ultimate']['harga'])} — 1.100 Jam, 30 User)": {
                    "quota": 66000, "max_users": 30, "paket_key": "B2B_Ultimate"
                },
                "Custom": {"quota": 12000, "max_users": 10, "paket_key": None},
            }
            pilihan_paket = st.selectbox(
                "Pilih Paket B2G/B2B:",
                list(PAKET_B2B_MAP.keys()),
                key="sel_paket_b2b",
                help="Standard & Ultimate akan mengisi otomatis kuota dan batas staf. Pilih Custom untuk isi manual."
            )
            paket_info        = PAKET_B2B_MAP[pilihan_paket]
            default_quota     = paket_info["quota"]
            default_max_users = paket_info["max_users"]
            paket_key         = paket_info["paket_key"]

            if paket_key:
                default_harga_beli       = pc_admin[paket_key]["harga"]
                default_harga_coret_beli = pc_admin[paket_key]["harga_coret"]
                default_aktif_coret_beli = pc_admin[paket_key]["aktif_coret"]
            else:
                default_harga_beli       = 0
                default_harga_coret_beli = 0
                default_aktif_coret_beli = False

            with st.form("form_voucher_b2b"):
                col_b1, col_b2 = st.columns(2)
                with col_b1:
                    v_kode_b2b = st.text_input("Kode Lisensi B2G/B2B (Kosongkan untuk otomatis)", placeholder="Contoh: KEMENHUB-2026").strip().upper()
                with col_b2:
                    b2b_corp_name = st.text_input("Nama Instansi / Perusahaan *", placeholder="Contoh: Kementerian Kominfo").strip()

                col_b3, col_b4 = st.columns(2)
                with col_b3:
                    b2b_shared_quota = st.number_input("Total Kuota Instansi (Menit)*", min_value=60, value=default_quota, step=60)
                with col_b4:
                    b2b_max_users = st.number_input("Batas Maksimal Staf/User*", min_value=1, value=default_max_users)

                st.markdown("---")
                st.markdown("**🧾 Harga Invoice (untuk SPJ & Sertifikat)**")
                st.caption("Pre-filled dari pricing config. Ubah manual jika ada kesepakatan harga khusus.")
                col_h1, col_h2, col_h3 = st.columns([2, 2, 1])
                with col_h1:
                    b2b_harga_beli = st.number_input("Harga Aktual (Rp)*", min_value=0, value=default_harga_beli, step=100000, key="admin_harga_beli")
                with col_h2:
                    b2b_harga_coret = st.number_input("Harga Coret (Rp)", min_value=0, value=default_harga_coret_beli, step=100000, key="admin_harga_coret", help="Isi 0 jika tidak ada harga coret.")
                with col_h3:
                    b2b_aktif_coret = st.toggle("Tampilkan Coret", value=default_aktif_coret_beli, key="admin_aktif_coret")

                st.markdown("---")
                st.markdown("**🛡️ Pengaturan Keamanan Data (Zero Retention Policy)**")
                sec_mode_b2b = st.selectbox(
                    "Pilih Tingkat Keamanan Penyimpanan:",
                    ["Normal", "Shadow Retention (v1)", "Zero Retention (v0)"],
                    help="V1: Data disimpan diam-diam (Hanya Developer yang bisa lihat). V0: Data benar-benar tidak disimpan ke server."
                )

                if st.form_submit_button("🏛️ Generate Lisensi Instansi (B2B)", width='stretch'):
                    if not b2b_corp_name:
                        st.error("⚠️ Nama Instansi wajib diisi!")
                    else:
                        if not v_kode_b2b:
                            v_kode_b2b = "B2B-" + ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))

                        if db.collection('vouchers').document(v_kode_b2b).get().exists:
                            st.error(f"❌ Kode '{v_kode_b2b}' sudah terpakai! Silakan gunakan kode lain.")
                        else:
                            db.collection('vouchers').document(v_kode_b2b).set({
                                "kode_voucher":              v_kode_b2b,
                                "tipe":                      "B2B_CORPORATE",
                                "tipe_voucher":              "B2G/B2B Shared Pool",
                                "corporate_name":            b2b_corp_name,
                                "shared_quota_minutes":      b2b_shared_quota,
                                "used_quota_minutes":        0,
                                "max_users":                 b2b_max_users,
                                "security_mode":             sec_mode_b2b,
                                "harga_beli":                b2b_harga_beli,
                                "harga_coret_beli":          b2b_harga_coret,
                                "aktif_coret_beli":          b2b_aktif_coret,
                                "staff_usage":               {},
                                "total_documents_generated": 0,
                                "max_klaim":                 1,
                                "jumlah_terklaim":           0,
                                "riwayat_pengguna":          [],
                                "created_at":                firestore.SERVER_TIMESTAMP
                            })
                            st.success(f"✔ Lisensi **{pilihan_paket}** berhasil dibuat dengan Mode {sec_mode_b2b}! Berikan kode **{v_kode_b2b}** kepada PIC.")
                            st.rerun()

    # ── Dialog Hapus Akun B2G/B2B ──
    @st.dialog("🗑️ Delete Akun B2G/B2B")
    def dialog_hapus_b2b(v_id, corp_name, pic_email):
        st.error("⚠️ PERINGATAN BAHAYA!")
        st.markdown(f"Anda akan menghapus **seluruh data instansi {corp_name}** secara permanen dari database.")
        st.markdown("Tindakan ini akan:")
        st.markdown("- Menghapus data sisa kuota dan riwayat instansi.\n- Mencabut hak Admin dari PIC.\n- Memutuskan akses (revoke) seluruh staf yang terhubung ke instansi ini.")

        st.info("Ketik **HAPUS** pada kolom di bawah ini untuk mengonfirmasi tindakan Anda.")
        konfirmasi = st.text_input("Ketik konfirmasi:").strip().upper()

        if st.button("🚨 Ya, Hapus Permanen Instansi Ini", type="primary", width='stretch'):
            if konfirmasi != "HAPUS":
                st.error("❌ Anda harus mengetik kata HAPUS dengan benar untuk melanjutkan.")
            else:
                try:
                    users_ref = db.collection('users').where('active_corporate_voucher', '==', v_id).stream()
                    for u in users_ref:
                        db.collection('users').document(u.id).update({
                            'role': 'User',
                            'is_b2g_admin': False,
                            'instansi': firestore.DELETE_FIELD,
                            'active_corporate_voucher': firestore.DELETE_FIELD
                        })

                    db.collection('vouchers').document(v_id).delete()

                    st.success(f"✔ Seluruh data instansi {corp_name} berhasil dihapus permanen!")
                    time.sleep(1.5)
                    st.rerun()
                except Exception as e:
                    st.error(f"Terjadi kesalahan saat menghapus: {e}")

    # ── Daftar Voucher Aktif & Riwayat ──
    with st.expander("👁️ Lihat Daftar Voucher Aktif & Riwayat"):

        col_vtitle_top, col_vrefresh = st.columns([4, 1])
        with col_vtitle_top:
            st.caption("Menampilkan seluruh riwayat voucher Anda.")
        with col_vrefresh:
            if st.button("🌿 Refresh", key="refresh_voucher_list", width='stretch'):
                if 'admin_vouchers_cache' in st.session_state:
                    del st.session_state['admin_vouchers_cache']

        if 'admin_vouchers_cache' not in st.session_state:
            with st.spinner("Memuat data voucher..."):
                _vref = db.collection('vouchers').order_by('created_at', direction=firestore.Query.DESCENDING).stream()
                st.session_state.admin_vouchers_cache = [{'id': v.id, **v.to_dict()} for v in _vref]

        @st.dialog("⚠️ Konfirmasi Sapu Bersih")
        def dialog_sapu_bersih_voucher():
            st.warning("Anda yakin ingin menghapus SEMUA voucher reguler yang sudah kedaluwarsa/habis?")
            st.info("Tindakan ini akan membersihkan database dari voucher merah secara permanen. (Lisensi Instansi/B2B aman dari penghapusan ini).")
            c1, c2 = st.columns(2)
            with c1:
                if st.button("❌ Batal", width='stretch'):
                    st.rerun()
            with c2:
                if st.button("🚨 Ya, Bersihkan!", width='stretch', key="conf_sapu_voucher"):
                    all_vouchers = db.collection('vouchers').stream()
                    count_deleted = 0
                    for v in all_vouchers:
                        vd_temp = v.to_dict()
                        if vd_temp.get("tipe") == "B2B_CORPORATE" or vd_temp.get("tipe_voucher") == "B2G/B2B Shared Pool":
                            continue
                        sisa_kuota = vd_temp.get('max_klaim', 0) - vd_temp.get('jumlah_terklaim', 0)
                        if sisa_kuota <= 0:
                            db.collection('vouchers').document(v.id).delete()
                            count_deleted += 1

                    if count_deleted > 0:
                        st.toast(f"✔ {count_deleted} voucher reguler kedaluwarsa berhasil dibersihkan!", icon="🧹")
                    else:
                        st.toast("💡 Tidak ada voucher habis yang perlu dibersihkan.")
                    time.sleep(1.5)
                    st.rerun()

        col_vbtn_clean, _ = st.columns([2, 3])
        with col_vbtn_clean:
            if st.button("🧹 Bersihkan Semua Voucher Habis", type="secondary", width='stretch'):
                dialog_sapu_bersih_voucher()

        st.markdown("---")

        for vd_raw in st.session_state.get('admin_vouchers_cache', []):
            vd = vd_raw
            kode_v = vd.get('kode_voucher', vd.get('id', ''))
            max_klaim_aman = vd.get('max_klaim', 0)
            sisa = max_klaim_aman - vd.get('jumlah_terklaim', 0)
            status_v = "🟢 AKTIF" if sisa > 0 else "🔴 HABIS"
            riwayat = vd.get('riwayat_pengguna', [])

            col_info, col_btn1, col_btn2 = st.columns([5, 1.5, 1.5])

            with col_info:
                if vd.get("tipe") == "B2B_CORPORATE":
                    corp_name = vd.get('corporate_name', 'Instansi')
                    kuota_jam = vd.get('shared_quota_minutes', 0) // 60
                    st.markdown(f"🏛️ **{kode_v}** &nbsp;|&nbsp; Instansi: **{corp_name}** &nbsp;|&nbsp; Kuota: **{kuota_jam} Jam** &nbsp;|&nbsp; 🏛️ CORPORATE")
                else:
                    st.markdown(f"**{kode_v}** &nbsp;|&nbsp; Paket: {vd.get('nama_paket', '')} &nbsp;|&nbsp; Sisa Klaim: **{sisa}** &nbsp;|&nbsp; {status_v}")

                if riwayat:
                    riwayat_html = "<br>".join([f"👤 {r}" for r in riwayat])
                    if vd.get("tipe") == "B2B_CORPORATE":
                        st.markdown(f"<div style='font-size: 13.5px; color: #444; margin-top: 8px; background: #fff; padding: 12px; border-radius: 8px; border: 1px solid #ddd; line-height: 1.6;'><b>PIC Pendaftar:</b><br>{riwayat_html}</div>", unsafe_allow_html=True)
                    else:
                        st.markdown(f"<div style='font-size: 13.5px; color: #444; margin-top: 8px; background: #fff; padding: 12px; border-radius: 8px; border: 1px solid #ddd; line-height: 1.6;'><b>Riwayat Penggunaan:</b><br>{riwayat_html}</div>", unsafe_allow_html=True)

            with col_btn1:
                if sisa > 0 or vd.get("tipe") == "B2B_CORPORATE":
                    if st.button("Hapus Voucher", key=f"del_v_{kode_v}", type="tertiary"):
                        if vd.get("tipe") == "B2B_CORPORATE":
                            corp_name = vd.get('corporate_name', 'Instansi')
                            pic_email = vd.get('admin_email', '-')
                            dialog_hapus_b2b(kode_v, corp_name, pic_email)
                        else:
                            db.collection('vouchers').document(kode_v).delete()
                            if 'admin_vouchers_cache' in st.session_state: del st.session_state['admin_vouchers_cache']
                            st.rerun()

            with col_btn2:
                if vd.get("tipe") != "B2B_CORPORATE":
                    if sisa == 0:
                        if st.button("Hapus Log", key=f"del_log_habis_{kode_v}", type="tertiary"):
                            db.collection('vouchers').document(kode_v).delete()
                            if 'admin_vouchers_cache' in st.session_state: del st.session_state['admin_vouchers_cache']
                            st.rerun()
                    elif len(riwayat) > 0:
                        if st.button("Hapus Log", key=f"del_log_aktif_{kode_v}", type="tertiary"):
                            db.collection('vouchers').document(kode_v).update({
                                "riwayat_pengguna": [],
                                "jumlah_terklaim": 0
                            })
                            st.rerun()

    st.markdown("---")

    # ── Dialog Ganti PIC Instansi ──
    @st.dialog("💼 Ganti PIC Instansi")
    def dialog_ganti_pic(v_id, old_pic, corp_name):
        st.write(f"Instansi: **{corp_name}**")
        st.write(f"PIC Saat Ini: `{old_pic}`")
        st.markdown("---")

        new_email = st.text_input("📧 Masukkan Email PIC Baru", placeholder="contoh: pic.baru@perusahaan.com").strip().lower()
        st.info("⚠️ PIC baru harus sudah terdaftar (punya akun) di TOM'STT AI.")

        if st.button("🚀 Proses Transfer Admin", type="primary", width='stretch'):
            if not new_email:
                st.error("Masukkan email PIC baru!")
            else:
                try:
                    new_user_ref = db.collection('users').document(new_email).get()
                    if not new_user_ref.exists:
                        st.error("❌ Email baru belum terdaftar di sistem. Minta PIC baru login/daftar dulu.")
                        return

                    db.collection('vouchers').document(v_id).update({'admin_email': new_email})
                    db.collection('users').document(new_email).update({
                        'role': 'B2G',
                        'is_b2g_admin': True,
                        'instansi': corp_name,
                        'active_corporate_voucher': v_id,
                        'is_suspended': False
                    })

                    if old_pic != '-':
                        db.collection('users').document(old_pic).update({
                            'role': 'User',
                            'is_b2g_admin': False
                        })

                    st.success(f"✔ PIC Berhasil dipindah ke {new_email}!")
                    time.sleep(1.5)
                    st.rerun()
                except Exception as e:
                    st.error(f"Terjadi kesalahan: {e}")

    # --- 🏛️ MASTER DATA B2B (GOD MODE) ---
    st.markdown("#### 🏛️ Master Data B2G/B2B")

    with st.expander("👁️ Lihat & Kelola Database B2G/B2B"):

        col_b2b_title, col_b2b_refresh = st.columns([4, 1])
        with col_b2b_title:
            st.caption("Pantau seluruh instansi klien, sisa tangki kuota, dan masa aktif lisensi.")
        with col_b2b_refresh:
            if st.button("🌿 Refresh", key="refresh_b2b_list", width='stretch'):
                if 'admin_b2b_cache' in st.session_state:
                    del st.session_state['admin_b2b_cache']

        if 'admin_b2b_cache' not in st.session_state:
            with st.spinner("Memuat data B2B/B2G..."):
                _b2b_ref = db.collection('vouchers').stream()
                _b2b_raw = []
                for v in _b2b_ref:
                    v_data = v.to_dict()
                    if v_data.get("tipe_voucher") == "B2G/B2B Shared Pool" or v_data.get("tipe") == "B2B_CORPORATE":
                        v_data['id'] = v.id
                        _b2b_raw.append(v_data)
                st.session_state.admin_b2b_cache = _b2b_raw

        b2b_list = st.session_state.get('admin_b2b_cache', [])

        spending_per_pic = {}
        total_rev_b2b    = 0

        for klien_pre in b2b_list:
            pic_pre = klien_pre.get('admin_email', '')
            if pic_pre and pic_pre not in spending_per_pic:
                try:
                    pic_doc_pre = db.collection('users').document(pic_pre).get()
                    spending_per_pic[pic_pre] = pic_doc_pre.to_dict() if pic_doc_pre.exists else {}
                except:
                    spending_per_pic[pic_pre] = {}
            pic_data_pre = spending_per_pic.get(pic_pre, {})
            ts = int(pic_data_pre.get('spending_b2b', pic_data_pre.get('total_spending', 0)))
            total_rev_b2b += ts

        total_rev_b2b_fmt = f"Rp {total_rev_b2b:,}".replace(',', '.')

        col_b2b_rev1, col_b2b_rev2 = st.columns(2)
        with col_b2b_rev1:
            st.metric("💰 Total Revenue B2G/B2B", total_rev_b2b_fmt)
        with col_b2b_rev2:
            st.metric("🏛️ Total Instansi Terdaftar", len(b2b_list))
        st.markdown("---")

        if not b2b_list:
            st.info("Belum ada klien B2G/B2B yang terdaftar di Firestore.")
        else:
            for klien in b2b_list:
                pic_email = klien.get('admin_email', '-')
                corp_name = klien.get('corporate_name', 'Instansi')

                with st.expander(f"🏛️ {corp_name} (PIC: {pic_email})"):
                    col_k1, col_k2, col_k3 = st.columns(3)

                    kuota_total = klien.get('shared_quota_minutes', 0)
                    kuota_pakai = klien.get('used_quota_minutes', 0)
                    sisa_kuota  = kuota_total - kuota_pakai
                    max_users   = klien.get('max_users', 0)

                    with col_k1:
                        st.metric("Sisa Tangki", f"{sisa_kuota:,} Mnt".replace(',', '.'))
                    with col_k2:
                        st.metric("Dokumen AI", f"{klien.get('total_documents_generated', 0)}")
                    with col_k3:
                        st.metric("Maks Staf", f"{max_users}")

                    total_spending_rp = 0
                    riwayat_trx = []
                    status_pic_html = "<span style='color:#7f8c8d; font-weight:bold;'>❓ Status Tidak Diketahui</span>"

                    if pic_email != '-':
                        pic_data = spending_per_pic.get(pic_email, {})
                        if pic_data:
                            total_spending_rp = pic_data.get('total_spending', 0)
                            riwayat_trx = pic_data.get('riwayat_transaksi', [])
                            if pic_data.get('is_suspended', False):
                                status_pic_html = "<span style='color:#e67e22; font-weight:bold;'>🟡 PIC Ditangguhkan (Suspend)</span>"
                            elif pic_data.get('role') in ['B2B', 'B2G'] or pic_data.get('is_b2g_admin') == True:
                                status_pic_html = "<span style='color:#27ae60; font-weight:bold;'>🟢 PIC Aktif</span>"
                            else:
                                status_pic_html = "<span style='color:#e74c3c; font-weight:bold;'>🔴 Akses PIC Telah Dicabut</span>"
                        else:
                            status_pic_html = "<span style='color:#7f8c8d; font-weight:bold;'>❓ Akun PIC Terhapus</span>"

                    teks_rupiah = f"Rp {total_spending_rp:,}".replace(',', '.')
                    st.markdown(
                        f"**ID Lisensi:** `{klien['id']}`<br>"
                        f"👤 **Status Akses PIC:** {status_pic_html}<br>"
                        f"💰 **Total Nilai Pembelian:** <span style='color: #28a745; font-weight: bold;'>{teks_rupiah}</span>",
                        unsafe_allow_html=True
                    )

                    exp_date = klien.get('valid_until')
                    if exp_date:
                        try:
                            if not isinstance(exp_date, str):
                                st.write(f"📅 **Masa Aktif:** {exp_date.strftime('%d %b %Y')}")
                            else:
                                st.write(f"📅 **Masa Aktif:** {exp_date[:10]}")
                        except: pass

                    st.markdown("---")
                    current_mode = klien.get('security_mode', 'Normal')

                    col_sm1, col_sm2 = st.columns([3, 2])
                    with col_sm1:
                        new_mode = st.selectbox(
                            "🛡️ Mode Keamanan Instansi",
                            ["Normal", "Shadow Retention (v1)", "Zero Retention (v0)"],
                            index=["Normal", "Shadow Retention (v1)", "Zero Retention (v0)"].index(current_mode),
                            key=f"sec_sel_{klien['id']}",
                            label_visibility="collapsed"
                        )
                    with col_sm2:
                        if st.button("Ubah Mode", key=f"up_sec_{klien['id']}", width='stretch'):
                            db.collection('vouchers').document(klien['id']).update({"security_mode": new_mode})
                            st.toast(f"✔ Mode Keamanan {corp_name} berhasil diubah ke {new_mode}!")
                            if 'admin_b2b_cache' in st.session_state: del st.session_state['admin_b2b_cache']
                            time.sleep(0.8)
                            st.rerun()
                    st.write("")

                    if st.button(f"💼 Ganti PIC {corp_name}", key=f"swap_pic_{klien['id']}", width='stretch'):
                        dialog_ganti_pic(klien['id'], pic_email, corp_name)

                    # Generate SPJ HTML
                    wib_tz = datetime.timezone(datetime.timedelta(hours=7))
                    now = datetime.datetime.now(wib_tz)
                    bulan_indo = ["Januari","Februari","Maret","April","Mei","Juni","Juli","Agustus","September","Oktober","November","Desember"]
                    now_str = f"{now.day} {bulan_indo[now.month - 1]} {now.year}"

                    no_invoice    = f"INV-MANUAL-{klien['id']}"
                    tgl_pembelian = now_str

                    if riwayat_trx and isinstance(riwayat_trx, list):
                        try:
                            last_trx = riwayat_trx[-1]
                            no_invoice = last_trx.get('order_id', last_trx.get('id_transaksi', no_invoice))
                            tgl_raw = last_trx.get('tanggal', last_trx.get('waktu', last_trx.get('created_at', now_str)))
                            tgl_pembelian = str(tgl_raw)
                        except: pass

                    harga_aktual = klien.get('harga_beli', total_spending_rp)
                    harga_coret  = klien.get('harga_coret_beli', 0)
                    aktif_coret  = klien.get('aktif_coret_beli', False)

                    if aktif_coret and harga_coret > 0:
                        teks_coret  = f"Rp {int(harga_coret):,}".replace(",", ".")
                        teks_aktual = f"Rp {int(harga_aktual):,}".replace(",", ".")
                        baris_harga_spj = f"<td><span style='text-decoration: line-through; color: #888; margin-right: 8px;'>{teks_coret}</span><strong style='color:#28a745; font-size:14px;'>{teks_aktual}</strong></td>"
                    else:
                        teks_aktual = f"Rp {int(harga_aktual):,}".replace(",", ".")
                        baris_harga_spj = f"<td><strong style='color:#28a745; font-size:14px;'>{teks_aktual}</strong></td>"

                    html_spj_admin = f"""
                    <!DOCTYPE html>
                    <html lang="id">
                    <head>
                        <meta charset="UTF-8">
                        <title>Bukti Pembelian & Sertifikat - {corp_name}</title>
                        <style>
                            @page {{ size: A4; margin: 0; }}
                            body {{ font-family: 'Helvetica Neue', Helvetica, Arial, sans-serif; background-color: #f0f2f5; padding: 40px 20px; margin: 0; color: #333; line-height: 1.4; font-size: 12px; -webkit-print-color-adjust: exact; print-color-adjust: exact; }}
                            .container {{ width: 210mm; height: 297mm; margin: 0 auto; padding: 18mm; background-color: white; box-shadow: 0 10px 30px rgba(0, 0, 0, 0.15); border-radius: 4px; box-sizing: border-box; position: relative; overflow: hidden; z-index: 1; }}
                            .container::before {{ content: ""; position: absolute; top: -50%; left: -50%; width: 200%; height: 200%; background-image: url('https://res.cloudinary.com/tomstt/image/upload/v1774703242/Logo_1_wvwoid.png'); background-repeat: repeat; background-size: 160px; opacity: 0.04; transform: rotate(-30deg); z-index: -1; pointer-events: none; -webkit-print-color-adjust: exact; print-color-adjust: exact; }}
                            .header {{ text-align: center; margin-bottom: 10px; }}
                            .logo {{ max-width: 120px; height: auto; margin-bottom: 3px; }}
                            .tagline {{ font-size: 11px; font-weight: bold; color: #444; margin: 3px 0; font-style: italic; }}
                            .divider {{ border-bottom: 2px solid #0056b3; margin-bottom: 20px; }}
                            .title {{ font-size: 16px; font-weight: 800; color: #0056b3; margin: 0 0 20px 0; text-align: center; text-transform: uppercase; }}
                            .box {{ border: 1px solid #e0e0e0; padding: 12px 16px; border-radius: 6px; margin-bottom: 15px; background-color: #fcfcfc; page-break-inside: avoid; }}
                            h3 {{ margin-top: 0; color: #111; border-bottom: 1px solid #eaeaea; padding-bottom: 6px; font-size: 13px; margin-bottom: 8px; }}
                            table {{ width: 100%; border-collapse: collapse; }}
                            th, td {{ padding: 6px 4px; border-bottom: 1px dashed #ccc; text-align: left; font-size: 12px; }}
                            th {{ color: #555; width: 40%; font-weight: 600; }}
                            .ttd-box {{ float: right; text-align: center; margin-top: 20px; width: 180px; page-break-inside: avoid; }}
                            .footer {{ position: absolute; bottom: 15mm; left: 0; width: 100%; text-align: center; font-size: 10px; color: #999; border-top: 1px solid #eee; padding-top: 10px; }}
                            @media print {{ body {{ background-color: transparent; padding: 0; }} .container {{ box-shadow: none; border-radius: 0; margin: 0; }} }}
                        </style>
                    </head>
                    <body>
                        <div class="container">
                            <div class="header">
                                <img src="https://res.cloudinary.com/tomstt/image/upload/v1774703242/Logo_1_wvwoid.png" class="logo" alt="TOM'STT AI Logo">
                                <p class="tagline">The First AI Purpose-Built for Indonesian Transcription and Document Automation</p>
                            </div>
                            <div class="divider"></div>
                            <h2 class="title">Sertifikat Lisensi & Bukti Pembelian (Dev Copy)</h2>
                            <div class="box">
                                <h3>Identitas Pemegang Lisensi</h3>
                                <table>
                                    <tr><th>Nama Instansi / Perusahaan</th><td><strong>{corp_name}</strong></td></tr>
                                    <tr><th>Penanggung Jawab (PIC)</th><td>{pic_email}</td></tr>
                                    <tr><th>ID Lisensi Sistem</th><td><span style="background-color:#e6f3ff; padding:3px 6px; border-radius:4px; font-family:monospace; color:#0056b3; font-weight:bold;">{klien['id']}</span></td></tr>
                                    <tr><th>Tanggal Pembelian</th><td><strong>{tgl_pembelian}</strong></td></tr>
                                </table>
                            </div>
                            <div class="box">
                                <h3>Rincian Paket & Pembayaran</h3>
                                <table>
                                    <tr><th>Nomor Invoice (Ref Payment Gateway)</th><td><strong style="color:#d35400;">{no_invoice}</strong></td></tr>
                                    <tr><th>Kapasitas Total Durasi Waktu</th><td>{kuota_total:,.0f} Menit</td></tr>
                                    <tr><th>Batas Maksimal User</th><td>{max_users} Akun Pengguna</td></tr>
                                    <tr><th>Total Nilai Pembelian</th>{baris_harga_spj}</tr>
                                    <tr><th>Status Lisensi Aktif</th><td><span style="color: green; font-weight: bold;">✔️ TERVERIFIKASI</span></td></tr>
                                </table>
                            </div>
                            <p style="font-size: 11px; text-align: justify; color: #555; line-height: 1.5; margin-bottom: 5px;">
                                <em>Dokumen ini dibuat secara otomatis oleh sistem <strong>TOM'STT AI</strong> dan merupakan dokumen elektronik yang sah.</em>
                            </p>
                            <div class="ttd-box">
                                <p style="margin-bottom: 40px; font-size: 12px;">{tgl_pembelian}<br><strong>Tim Administrasi TOM'STT AI</strong></p>
                            </div>
                            <div class="footer">
                                &copy; 2026 TOM'STT AI | <a href="https://tom-stt.com" target="_blank" style="color:#0056b3; text-decoration:none;">https://tom-stt.com</a>
                            </div>
                        </div>
                        <script> window.onload = function() {{ window.print(); }} </script>
                    </body>
                    </html>
                    """

                    st.write("")
                    col_dl1, col_dl2 = st.columns(2)
                    with col_dl1:
                        st.download_button(
                            label="Download Bukti Pembelian & Sertifikat",
                            data=html_spj_admin.encode('utf-8'),
                            file_name=f"Sertifikat_Invoice_{corp_name.replace(' ', '_')}.html",
                            mime="text/html",
                            width='stretch',
                            key=f"dl_spj_{klien['id']}"
                        )
                    with col_dl2:
                        staff_dict = klien.get('staff_usage', {})
                        if staff_dict:
                            df_b2b = pd.DataFrame.from_dict(staff_dict, orient='index').reset_index()
                            df_b2b.rename(columns={'index': 'Email Staf', 'minutes_used': 'Durasi AI (Mnt)', 'docs_generated': 'Total Dokumen'}, inplace=True)
                            csv_b2b = df_b2b.to_csv(index=False).encode('utf-8')
                            st.download_button("Download Log Audit (CSV)", data=csv_b2b, file_name=f"Log_Dev_{corp_name}.csv", mime="text/csv", width='stretch', key=f"dl_dev_{klien['id']}")
                        else:
                            st.button("📥 Download Log Audit (CSV)", disabled=True, width='stretch', key=f"dl_dev_dis_{klien['id']}")

                    if st.button("🗑️ Delete Akun B2G/B2B", key=f"del_b2b_btn_{klien['id']}", width='stretch'):
                        dialog_hapus_b2b(klien['id'], corp_name, pic_email)

                    st.markdown("---")
                    st.markdown("##### 🏛️ Daftar User Instansi")

                    staff_list = list(staff_dict.items())
                    staff_list.sort(key=lambda x: 0 if x[0] == pic_email else 1)

                    for staff_email, metrics in staff_list:
                        with st.container(border=True):
                            is_pic = (staff_email == pic_email)
                            badge  = "👑 PIC / Admin" if is_pic else "👤 Staf"

                            u_data = {}
                            try:
                                u_snap = db.collection('users').document(staff_email).get()
                                if u_snap.exists:
                                    u_data = u_snap.to_dict()
                            except: pass

                            tgl_daftar = u_data.get("created_at", "Belum Terdaftar")
                            if not isinstance(tgl_daftar, str) and tgl_daftar != "Belum Terdaftar":
                                try:
                                    wib_tz2 = datetime.timezone(datetime.timedelta(hours=7))
                                    tgl_daftar = tgl_daftar.astimezone(wib_tz2).strftime("%d %b %Y, %H:%M")
                                except: tgl_daftar = str(tgl_daftar)

                            st.markdown(f"**{badge}** | `{staff_email}`")
                            st.caption(f"📅 Terdaftar: {tgl_daftar} &nbsp;|&nbsp; ⏱️ {metrics.get('minutes_used', 0)} Mnt &nbsp;|&nbsp; 📄 {metrics.get('docs_generated', 0)} Dokumen")

    st.markdown("---")

    # --- MANAJEMEN USER ---
    st.markdown("#### 👥 Manajemen User")

    @st.dialog("📂 Arsip Dokumen Pengguna", width="large")
    def dialog_lihat_arsip(target_user):
        st.markdown(f"**Melihat Brankas:** `{target_user}`")
        st.markdown("---")

        history_ref = db.collection('users').document(target_user).collection('history').order_by('created_at', direction=firestore.Query.DESCENDING).stream()

        ada_data = False
        for doc in history_ref:
            ada_data = True
            h_data = doc.to_dict()
            h_id   = doc.id
            h_date = h_data.get("created_at")

            tgl_str = "Waktu tidak diketahui"
            if h_date:
                try:
                    wib_tz3 = datetime.timezone(datetime.timedelta(hours=7))
                    tgl_str = h_date.astimezone(wib_tz3).strftime("%d %b %Y, %H:%M WIB")
                except: pass

            f_name = h_data.get("filename", "Dokumen")
            prefix = h_data.get("ai_prefix", "")

            # Badge API info (hanya ada di history baru)
            input_type   = h_data.get("input_type", "")
            stt_provider = h_data.get("stt_provider", "")
            stt_model    = h_data.get("stt_model", "")
            ai_provider  = h_data.get("ai_provider", "")
            ai_model     = h_data.get("ai_model", "")

            api_badge = ""
            if input_type:
                input_icon = "🎙️ Audio" if input_type == "audio" else "📝 Teks"
                stt_str    = f" · {stt_provider} ({stt_model})" if stt_provider and stt_provider != "-" else ""
                ai_str     = f" · 🧠 {ai_provider} ({ai_model})" if ai_provider else ""
                api_badge  = f"\n`{input_icon}{stt_str}{ai_str}`"

            with st.expander(f"📄 {prefix}{f_name}  ({tgl_str}){api_badge}"):
                tab_a_ai, tab_a_trans = st.tabs(["🧠 Hasil AI", "🎙️ Transkrip Asli"])

                with tab_a_ai:
                    teks_ai = h_data.get("ai_result", "")
                    st.markdown(f"<div style='max-height: 250px; overflow-y: auto; padding: 10px; background-color: #f9f9f9; border-radius: 5px; border: 1px solid #ddd; margin-bottom: 15px;'>{teks_ai}</div>", unsafe_allow_html=True)
                    col_d1, col_d2 = st.columns(2)
                    with col_d1:
                        st.download_button("💾 Download .TXT", teks_ai, f"{prefix}{f_name}.txt", "text/plain", key=f"dl_a_txt_{h_id}", width='stretch')
                    with col_d2:
                        docx_file = create_docx(teks_ai, f"{prefix}{f_name}")
                        st.download_button("📄 Download .DOCX", data=docx_file, file_name=f"{prefix}{f_name}.docx", mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document", key=f"dl_a_docx_{h_id}", width='stretch')

                with tab_a_trans:
                    teks_tr = h_data.get("transcript", "")
                    st.markdown(f"<div class='no-select' style='max-height: 250px; overflow-y: auto; padding: 10px; background-color: #f9f9f9; border-radius: 5px; border: 1px solid #ddd; margin-bottom: 15px;'>{teks_tr}</div>", unsafe_allow_html=True)
                    st.download_button("💾 Download .TXT", teks_tr, f"Transkrip_{f_name}.txt", "text/plain", key=f"dl_a_tr_{h_id}", width='stretch')

        if not ada_data:
            st.info("Brankas arsip pengguna ini masih kosong.")

    @st.dialog("✏️ Edit Dompet Manual")
    def dialog_edit_dompet(user_id, current_saldo, current_bank_menit, current_exp, inventori_user):
        st.markdown(f"**Target Akun:** `{user_id}`")
        with st.form(f"form_edit_dompet_{user_id}"):
            new_saldo      = st.number_input("Saldo Utama (Rp)", value=int(current_saldo), step=1000)
            new_bank_menit = st.number_input("Bank Waktu AIO (Menit)", value=int(current_bank_menit), step=60)

            st.markdown("---")
            st.markdown("**📦 Edit Kuota Paket Reguler:**")
            updated_kuota = {}
            ada_reguler = False
            if inventori_user:
                for i, pkt in enumerate(inventori_user):
                    if pkt.get('batas_durasi') != 9999:
                        ada_reguler = True
                        updated_kuota[i] = st.number_input(f"Sisa Tiket - {pkt['nama']}", value=int(pkt['kuota']), min_value=0, step=1)

            if not ada_reguler:
                st.caption("User ini tidak memiliki paket reguler yang aktif.")
            st.markdown("---")

            try:
                if isinstance(current_exp, str) and current_exp != "Selamanya":
                    parsed_exp = datetime.datetime.fromisoformat(current_exp.replace("Z", "+00:00")).date()
                elif isinstance(current_exp, datetime.datetime):
                    parsed_exp = current_exp.date()
                else:
                    parsed_exp = datetime.date.today() + datetime.timedelta(days=30)
            except:
                parsed_exp = datetime.date.today() + datetime.timedelta(days=30)

            is_forever    = st.checkbox("Masa Aktif Selamanya (Bypass)", value=(current_exp == "Selamanya"))
            new_exp_date  = st.date_input("Tanggal Kedaluwarsa", value=parsed_exp, disabled=is_forever)

            if st.form_submit_button("💾 Simpan Perubahan", width='stretch'):
                if is_forever:
                    final_exp = "Selamanya"
                else:
                    final_exp = datetime.datetime.combine(new_exp_date, datetime.datetime.min.time(), tzinfo=datetime.timezone.utc)

                final_inventori = []
                if inventori_user:
                    for i, pkt in enumerate(inventori_user):
                        new_pkt = pkt.copy()
                        if i in updated_kuota:
                            new_pkt['kuota'] = updated_kuota[i]
                        if new_pkt.get('batas_durasi') == 9999 or new_pkt.get('kuota', 0) > 0:
                            final_inventori.append(new_pkt)

                db.collection('users').document(user_id).update({
                    "saldo": new_saldo,
                    "bank_menit": new_bank_menit,
                    "tanggal_expired": final_exp,
                    "inventori": final_inventori
                })
                st.toast(f"✔ Dompet {user_id} berhasil diupdate!")
                if 'admin_users_cache' in st.session_state: del st.session_state['admin_users_cache']
                time.sleep(0.8)
                st.rerun()

    @st.dialog("⚠️ Konfirmasi Hapus Akun")
    def dialog_hapus_user(user_id):
        st.warning(f"Anda yakin ingin menghapus pengguna **{user_id}** secara permanen?")
        st.info("Tindakan ini akan menghapus dompet di Firestore dan akses login di Firebase Auth. Data tidak dapat dipulihkan.")
        c1, c2 = st.columns(2)
        with c1:
            if st.button("❌ Batal", width='stretch'):
                st.rerun()
        with c2:
            if st.button("🚨 Ya, Hapus!", width='stretch', key=f"confirm_{user_id}"):
                delete_user(user_id)
                st.toast(f"✔ User {user_id} berhasil dihapus permanen!")
                if 'admin_users_cache' in st.session_state: del st.session_state['admin_users_cache']
                time.sleep(0.8)
                st.rerun()

    @st.dialog("🚫 Cabut Akses Admin Instansi")
    def dialog_cabut_admin(target_email):
        st.warning(f"Apakah Anda yakin ingin mencabut status Admin B2G/B2B dari **{target_email}**?")
        st.info("Akun ini akan dikembalikan menjadi 'User' reguler. Data instansi akan dihapus dari profil ini, namun riwayat transkripsinya akan tetap aman.")
        col1, col2 = st.columns(2)
        with col1:
            if st.button("Batal", width='stretch'):
                st.rerun()
        with col2:
            if st.button("🚨 Ya, Cabut", type="primary", width='stretch'):
                try:
                    db.collection('users').document(target_email).update({
                        'role': 'User',
                        'instansi': firestore.DELETE_FIELD,
                        'is_b2g_admin': firestore.DELETE_FIELD,
                        'active_corporate_voucher': firestore.DELETE_FIELD,
                        'is_suspended': firestore.DELETE_FIELD
                    })
                    st.success("Akses admin instansi berhasil dicabut!")
                    if 'admin_users_cache' in st.session_state: del st.session_state['admin_users_cache']
                    if 'admin_b2b_cache' in st.session_state: del st.session_state['admin_b2b_cache']
                    time.sleep(1)
                    st.rerun()
                except Exception as e:
                    st.error(f"Gagal mencabut akses: {e}")

    @st.dialog("❚❚ Kelola Status Penangguhan")
    def dialog_suspend_admin(target_email, is_currently_suspended):
        if is_currently_suspended:
            st.info(f"Akun **{target_email}** saat ini DITANGGUHKAN.")
            st.write("Apakah Anda ingin memulihkan (Unsuspend) akses admin B2B/B2G akun ini?")
            if st.button("▶ Ya, Pulihkan Akses", type="primary", width='stretch'):
                try:
                    db.collection('users').document(target_email).update({'is_suspended': False})
                    st.success("Akses berhasil dipulihkan!")
                    time.sleep(1)
                    st.rerun()
                except Exception as e:
                    st.error(f"Gagal memulihkan akses: {e}")
        else:
            st.warning(f"Apakah Anda yakin ingin MENANGGUHKAN akses admin dari **{target_email}**?")
            st.write("Akun ini masih terdaftar sebagai B2G/B2B, namun tidak akan bisa membuka Panel Instansi sampai Anda memulihkannya.")
            if st.button("❚❚ Ya, Tangguhkan", type="primary", width='stretch'):
                try:
                    db.collection('users').document(target_email).update({'is_suspended': True})
                    st.success("Akun berhasil ditangguhkan!")
                    time.sleep(1)
                    st.rerun()
                except Exception as e:
                    st.error(f"Gagal menangguhkan akses: {e}")

    with st.expander("➕ Tambah Akun"):
        with st.form("user_form"):
            add_email = st.text_input("Email / Username Baru").strip()
            add_pwd   = st.text_input("Password", type="password")
            add_role  = st.selectbox("Role", ["user", "admin"])

            if st.form_submit_button("💾 Simpan Data User", width='stretch'):
                if add_email and add_pwd:
                    if len(add_pwd) < 6:
                        st.error("❌ Password minimal harus 6 karakter!")
                    else:
                        with st.spinner("Membuat & mendaftarkan akun..."):
                            try:
                                try:
                                    user_record = auth.get_user_by_email(add_email)
                                    auth.update_user(user_record.uid, password=add_pwd)
                                except:
                                    auth.create_user(email=add_email, password=add_pwd, email_verified=True)

                                save_user(add_email, add_pwd, add_role)
                                st.success(f"✔ Akun {add_email} berhasil dibuat & sudah bisa login!")
                                st.rerun()
                            except Exception as e:
                                err_msg = str(e)
                                if "MALFORMED_EMAIL" in err_msg or "invalid email" in err_msg.lower():
                                    st.error("❌ Format email tidak valid (Gunakan format user@email.com)")
                                else:
                                    st.error(f"❌ Terjadi kesalahan sistem: {err_msg}")
                else:
                    st.error("❌ Isi Username dan Password terlebih dahulu!")

    with st.expander("👁️ Lihat Daftar & Analisis Pengguna Aktif"):

        col_usr_title, col_usr_refresh = st.columns([4, 1])
        with col_usr_title:
            st.caption("Analisis seluruh pengguna terdaftar.")
        with col_usr_refresh:
            if st.button("🌿 Refresh", key="refresh_users_list", width='stretch'):
                if 'admin_users_cache' in st.session_state:
                    del st.session_state['admin_users_cache']

        if 'admin_users_cache' not in st.session_state:
            with st.spinner("Memuat data pengguna..."):
                _uref = db.collection('users').stream()
                _users_raw = []
                for doc in _uref:
                    u_data = doc.to_dict()
                    u_data['id'] = doc.id
                    _users_raw.append(u_data)
                st.session_state.admin_users_cache = _users_raw

        all_users_raw = st.session_state.get('admin_users_cache', [])

        total_rev_reguler = sum(
            max(0, int(u.get('total_spending', 0)) - int(u.get('spending_b2b', 0)))
            for u in all_users_raw
        )
        total_rev_reguler_fmt = f"Rp {total_rev_reguler:,}".replace(',', '.')
        total_user_bayar = sum(1 for u in all_users_raw if u.get('total_spending', 0) > 0)

        col_rev1, col_rev2, col_rev3 = st.columns(3)
        with col_rev1:
            st.metric("💰 Total Revenue Reguler", total_rev_reguler_fmt)
        with col_rev2:
            st.metric("👤 Total Akun Terdaftar", len(all_users_raw))
        with col_rev3:
            st.metric("🛒 Akun Pernah Bertransaksi", total_user_bayar)

        st.markdown("---")
        st.markdown("##### 🔍 Filter & Urutkan Pengguna")

        if "search_user_email" not in st.session_state:
            st.session_state.search_user_email = ""

        def clear_search():
            st.session_state.search_user_email = ""

        col_tipe, col_search = st.columns(2)
        with col_tipe:
            filter_tipe = st.radio(
                "Tipe User:",
                ["Semua", "Reguler", "Admin Instansi (PIC)", "Member Instansi"],
                horizontal=False,
                key="filter_tipe_user"
            )
        with col_search:
            search_q = st.text_input("Cari Email:", key="search_user_email", placeholder="Ketik alamat email...").strip().lower()
            if search_q != "":
                st.button("✖️ Hapus Pencarian", on_click=clear_search, type="secondary", width='stretch')

        sort_opt = st.selectbox("Urutkan berdasarkan:", [
            "Terbaru (Tanggal Daftar)", "Terlama (Tanggal Daftar)",
            "Pembelian Terakhir (Terbaru)", "Total Spending (Tertinggi)", "Total Aset (Tertinggi)",
            "Arsip Terbanyak", "Arsip Terbaru", "Abjad (A-Z)", "Abjad (Z-A)"
        ])

        filtered_users = []
        for u in all_users_raw:
            if search_q and search_q not in u['id'].lower():
                continue
            is_pic    = bool(u.get('is_b2g_admin'))
            is_member = bool(u.get('active_corporate_voucher')) and not is_pic
            if filter_tipe == "Admin Instansi (PIC)" and not is_pic: continue
            elif filter_tipe == "Member Instansi" and not is_member: continue
            elif filter_tipe == "Reguler" and (is_pic or is_member): continue
            filtered_users.append(u)

        def get_timestamp(user_dict):
            t = user_dict.get('created_at')
            try: return t.timestamp() if t else 0
            except: return 0

        for u_data in filtered_users:
            inventori   = u_data.get('inventori', [])
            saldo       = u_data.get('saldo', 0)
            bank_menit_user = u_data.get('bank_menit', 0)
            estimasi_rupiah = saldo
            if inventori:
                for pkt in inventori:
                    nama_up = pkt.get('nama', '').upper()
                    kuota   = pkt.get('kuota', 0)
                    if "LITE" in nama_up:        estimasi_rupiah += kuota * (29000 / 3)
                    elif "STARTER" in nama_up:   estimasi_rupiah += kuota * (89000 / 10)
                    elif "EKSEKUTIF" in nama_up: estimasi_rupiah += kuota * (299000 / 30)
                    elif "VIP" in nama_up:        estimasi_rupiah += kuota * (599000 / 65)
                    elif "ENTERPRISE" in nama_up: estimasi_rupiah += kuota * (1199000 / 150)
            if bank_menit_user > 0:
                estimasi_rupiah += bank_menit_user * 270
            u_data['calc_asset']    = estimasi_rupiah
            u_data['calc_spending'] = u_data.get('total_spending', 0)

        if sort_opt in ["Arsip Terbanyak", "Arsip Terbaru"]:
            with st.spinner("⏳ Mengambil data aktivitas arsip pengguna... (Mohon tunggu)"):
                for u in filtered_users:
                    arsip_ref = db.collection('users').document(u['id']).collection('history').get()
                    u['calc_arsip_count'] = len(arsip_ref)
                    latest_time = 0
                    for a_doc in arsip_ref:
                        t = a_doc.to_dict().get('created_at')
                        try:
                            ts = t.timestamp() if t else 0
                            if ts > latest_time: latest_time = ts
                        except: pass
                    u['calc_arsip_latest'] = latest_time

        if sort_opt == "Terbaru (Tanggal Daftar)":         filtered_users.sort(key=get_timestamp, reverse=True)
        elif sort_opt == "Terlama (Tanggal Daftar)":       filtered_users.sort(key=get_timestamp, reverse=False)
        elif sort_opt == "Pembelian Terakhir (Terbaru)":
            def get_last_purchase_ts(u):
                t = u.get('last_purchase_at')
                try: return t.timestamp() if t else 0
                except: return 0
            filtered_users.sort(key=get_last_purchase_ts, reverse=True)
        elif sort_opt == "Total Spending (Tertinggi)":     filtered_users.sort(key=lambda x: x['calc_spending'], reverse=True)
        elif sort_opt == "Total Aset (Tertinggi)":         filtered_users.sort(key=lambda x: x['calc_asset'], reverse=True)
        elif sort_opt == "Arsip Terbanyak":                filtered_users.sort(key=lambda x: x.get('calc_arsip_count', 0), reverse=True)
        elif sort_opt == "Arsip Terbaru":                  filtered_users.sort(key=lambda x: x.get('calc_arsip_latest', 0), reverse=True)
        elif sort_opt == "Abjad (A-Z)":                   filtered_users.sort(key=lambda x: x['id'].lower(), reverse=False)
        elif sort_opt == "Abjad (Z-A)":                   filtered_users.sort(key=lambda x: x['id'].lower(), reverse=True)

        # ── Pagination ──────────────────────────────────────────
        USERS_PER_PAGE = 30
        total_filtered = len(filtered_users)

        # Reset halaman jika filter/sort/search berubah
        _filter_key = f"{filter_tipe}|{search_q}|{sort_opt}"
        if st.session_state.get('_admin_users_filter_key') != _filter_key:
            st.session_state['_admin_users_filter_key'] = _filter_key
            st.session_state['admin_users_page'] = 0

        if 'admin_users_page' not in st.session_state:
            st.session_state['admin_users_page'] = 0

        page     = st.session_state['admin_users_page']
        page_start = page * USERS_PER_PAGE
        page_end   = page_start + USERS_PER_PAGE
        users_page = filtered_users[page_start:page_end]
        total_pages = max(1, -(-total_filtered // USERS_PER_PAGE))  # ceiling division

        st.markdown(f"**Total Pengguna Ditampilkan:** {total_filtered} Akun &nbsp;|&nbsp; Halaman **{page + 1}** / {total_pages}")
        st.markdown("---")

        for u_data in users_page:
            user_id = u_data['id']
            role    = u_data.get('role', 'user')

            created_at = u_data.get('created_at')
            tgl_daftar = "Data lama"
            if created_at:
                try:
                    wib_tz4 = datetime.timezone(datetime.timedelta(hours=7))
                    tgl_daftar = created_at.astimezone(wib_tz4).strftime("%d %b %Y, %H:%M WIB")
                except:
                    tgl_daftar = created_at.strftime("%d %b %Y")

            inventori   = u_data.get('inventori', [])
            paket_teks  = []
            if inventori:
                for pkt in inventori:
                    paket_teks.append(f"{pkt.get('nama', '')} ({pkt.get('kuota', 0)}x)")

            bank_menit_user = u_data.get('bank_menit', 0)

            if paket_teks:
                paket_html = "<ul style='margin-top: 5px; margin-bottom: 5px; padding-left: 20px;'>"
                for pt in paket_teks:
                    paket_html += f"<li>{pt}</li>"
                paket_html += "</ul>"
            else:
                paket_html = "<div style='margin-top: 5px; margin-bottom: 5px; margin-left: 5px;'>- Belum ada / Habis</div>"

            if bank_menit_user > 0:
                jam_admin  = bank_menit_user // 60
                menit_admin = bank_menit_user % 60
                if jam_admin > 0 and menit_admin > 0:   waktu_admin_str = f"{jam_admin} Jam {menit_admin} Menit"
                elif jam_admin > 0:                      waktu_admin_str = f"{jam_admin} Jam"
                else:                                    waktu_admin_str = f"{bank_menit_user} Menit"
                paket_html += f"<div style='margin-left: 5px; margin-bottom: 10px; color:#e74c3c; font-weight: bold;'>⏱️ Waktu AIO: {waktu_admin_str}</div>"
            else:
                paket_html += "<div style='margin-bottom: 10px;'></div>"

            vid_aktif = u_data.get('active_corporate_voucher')
            b2b_badge_title = ""

            if vid_aktif:
                if 'b2b_name_cache' not in st.session_state:
                    st.session_state.b2b_name_cache = {}
                if vid_aktif not in st.session_state.b2b_name_cache:
                    try:
                        v_doc_super = db.collection('vouchers').document(vid_aktif).get()
                        if v_doc_super.exists:
                            st.session_state.b2b_name_cache[vid_aktif] = v_doc_super.to_dict().get('corporate_name', 'Instansi')
                        else:
                            st.session_state.b2b_name_cache[vid_aktif] = "Instansi (Tidak Ditemukan)"
                    except:
                        st.session_state.b2b_name_cache[vid_aktif] = "Instansi"

                nama_instansi   = st.session_state.b2b_name_cache[vid_aktif]
                b2b_badge_title = f" | 🏛️ {nama_instansi}"

                if "- Belum ada" in paket_html:
                    paket_html = paket_html.replace("<div style='margin-top: 5px; margin-bottom: 5px; margin-left: 5px;'>- Belum ada / Habis</div>", "")

                paket_html += f"<div style='margin-left: 5px; margin-bottom: 10px; color:#0056b3; font-weight: 800;'>Mode B2G/B2B: {nama_instansi}</div>"

            str_rupiah   = f"Rp {int(u_data['calc_asset']):,}".replace(",", ".")
            str_spending = f"Rp {int(u_data['calc_spending']):,}".replace(",", ".")

            info_tambahan = ""
            if sort_opt == "Pembelian Terakhir (Terbaru)":
                lp_at    = u_data.get('last_purchase_at')
                lp_nama  = u_data.get('last_purchase_nama', '-')
                lp_harga = u_data.get('last_purchase_harga', 0)
                if lp_at:
                    try:
                        wib_lp = datetime.timezone(datetime.timedelta(hours=7))
                        tgl_lp = lp_at.astimezone(wib_lp).strftime("%d %b %Y")
                        harga_lp = f"Rp {int(lp_harga):,}".replace(",", ".")
                        info_tambahan = f" | 🛒 {tgl_lp} · {lp_nama} · {harga_lp}"
                    except:
                        info_tambahan = f" | 🛒 {lp_nama}"
                else:
                    info_tambahan = " | 🛒 Belum ada pembelian"
            elif sort_opt == "Arsip Terbanyak":
                info_tambahan = f" | 🗂️ {u_data.get('calc_arsip_count', 0)} Arsip"
            elif sort_opt == "Arsip Terbaru":
                if u_data.get('calc_arsip_latest', 0) > 0:
                    dt_latest = datetime.datetime.fromtimestamp(u_data['calc_arsip_latest'], tz=datetime.timezone(datetime.timedelta(hours=7)))
                    info_tambahan = f" | 📜 {dt_latest.strftime('%d %b, %H:%M')}"
                else:
                    info_tambahan = " | 📜 Belum ada arsip"

            with st.expander(f"👤 {user_id}{b2b_badge_title}  (Terdaftar: {tgl_daftar[:11]}){info_tambahan}"):
                col_info, col_aksi = st.columns([3, 2])

                with col_info:
                    st.markdown(
                        f"**Role:** `{role}`<br>"
                        f"<div style='font-size: 14px; color: #555; margin-top: 10px;'>"
                        f"📅 <b>Waktu Daftar:</b> {tgl_daftar}<br>"
                        f"📦 <b>Paket Aktif:</b>{paket_html}"
                        f"💼 <b>Est. Sisa Aset:</b> {str_rupiah}<br>"
                        f"💰 <b>Total Spending:</b> <span style='color:#27ae60; font-weight:bold;'>{str_spending}</span>"
                        f"</div>",
                        unsafe_allow_html=True
                    )

                with col_aksi:
                    if st.button("📂 Lihat Arsip", key=f"arsip_usr_{user_id}", width='stretch'):
                        dialog_lihat_arsip(user_id)
                    if st.button("✏️ Edit Dompet", key=f"edit_usr_{user_id}", width='stretch'):
                        dialog_edit_dompet(user_id, u_data.get('saldo', 0), bank_menit_user, u_data.get('tanggal_expired', 'Selamanya'), inventori)

                    if u_data.get('role') in ['B2B', 'B2G'] or u_data.get('is_b2g_admin') == True:
                        is_suspended = u_data.get('is_suspended', False)
                        btn_label = "▶ Unsuspend Admin B2G/B2B" if is_suspended else "❚❚ Suspend Admin B2G/B2B"
                        if st.button(btn_label, key=f"susp_adm_{user_id}", width='stretch'):
                            dialog_suspend_admin(user_id, is_suspended)
                        if st.button("🚫 Revoke Admin B2G/B2B", key=f"revoke_adm_{user_id}", width='stretch'):
                            dialog_cabut_admin(user_id)

                    is_self = (user_id == st.session_state.current_user)
                    if not is_self:
                        _, col_tengah, _ = st.columns([1, 2, 1])
                        with col_tengah:
                            if st.button("🗑️ Hapus Akun", key=f"del_usr_{user_id}", type="tertiary", width='stretch'):
                                dialog_hapus_user(user_id)
                    else:
                        st.markdown("<div style='text-align: center; color: gray; font-size: 0.8em;'><i>*(Akun Anda Sendiri)*</i></div>", unsafe_allow_html=True)

        # ── Navigasi Halaman ───────────────────────────────────
        st.markdown("---")
        col_prev, col_info_pg, col_next = st.columns([1, 2, 1])
        with col_prev:
            if page > 0:
                if st.button("◀ Sebelumnya", key="admin_users_prev", width='stretch'):
                    st.session_state['admin_users_page'] = page - 1
                    st.rerun()
        with col_info_pg:
            st.markdown(f"<div style='text-align:center; padding-top:8px; color:#555;'>Halaman <b>{page+1}</b> dari <b>{total_pages}</b></div>", unsafe_allow_html=True)
        with col_next:
            if page_end < total_filtered:
                if st.button("Berikutnya ▶", key="admin_users_next", width='stretch'):
                    st.session_state['admin_users_page'] = page + 1
                    st.rerun()
