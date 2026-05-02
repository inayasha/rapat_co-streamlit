import hashlib
import uuid
import time
import math
import re
import requests
import datetime
import streamlit as st
import streamlit.components.v1 as components
from firebase_admin import firestore
from database import (
    db, get_user, get_system_config, get_pricing_config,
    berikan_paket_ke_user, cek_status_pembayaran_duitku,
    eksekusi_pembayaran, redeem_voucher, check_expired
)

# ==========================================
# 3. SIDEBAR & ETALASE HARGA (DUITKU)
# ==========================================
# 🚀 FIX: Menambahkan parameter baru (nama_lengkap, no_hp, alamat, kota, kode_pos)
def buat_tagihan_duitku(nama_paket, harga, user_email, corporate_name="", nama_lengkap="", no_hp="", alamat="", kota="", kode_pos=""):
    """Menghubungi server API Duitku POP untuk meminta Link Pembayaran"""
    import hashlib
    import uuid
    import time
    import requests
    import streamlit as st
    
    # 🔒 MENGAMBIL KUNCI PRODUCTION DARI BRANKAS RAHASIA STREAMLIT (SECRETS)
    try:
        merchant_code = st.secrets["duitku"]["merchant_code"]
        api_key = st.secrets["duitku"]["api_key"]
    except KeyError:
        st.error("⚠️ Kunci API Duitku belum dikonfigurasi di Streamlit Secrets!")
        return None
    
    # 🚀 URL API Resmi Duitku POP (PRODUCTION)
    url = "https://api-prod.duitku.com/api/merchant/createInvoice"
    
    # Membuat Order ID unik & Timestamp waktu saat ini
    order_id = f"TOM-{nama_paket.split()[0].upper()}-{uuid.uuid4().hex[:6].upper()}"
    timestamp = str(int(time.time() * 1000))
    harga_int = int(harga)
    
    # Sistem Keamanan Baru Duitku (SHA-256)
    sign_string = merchant_code + timestamp + api_key
    signature = hashlib.sha256(sign_string.encode('utf-8')).hexdigest()
    
    headers = {
        "Content-Type": "application/json",
        "x-duitku-signature": signature,
        "x-duitku-timestamp": timestamp,
        "x-duitku-merchantcode": merchant_code
    }
    
    nama_depan = user_email.split('@')[0][:20]
    
    # 🚀 FIX: Menentukan variabel fallback jika data tidak diisi (misal untuk paket Reguler)
    first_name = nama_lengkap if nama_lengkap else nama_depan
    last_name = ""
    phone = no_hp if no_hp else "081234567890"
    address_inv = alamat if alamat else "Jakarta"
    city_inv = kota if kota else "Jakarta"
    postal_inv = kode_pos if kode_pos else "10000"
    
    # --- FASE 1: NOMENKLATUR SPJ B2G/B2B ---
    if corporate_name:
        if "Standard" in nama_paket:
            product_detail = f"Lisensi TOM'STT AI - {corporate_name} (Paket 550 Jam, Max 15 Users)"
        elif "Ultimate" in nama_paket:
            product_detail = f"Lisensi TOM'STT AI - {corporate_name} (Paket 1.100 Jam, Max 30 Users)"
        else:
            product_detail = f"Lisensi TOM'STT AI - {corporate_name}"
        item_name = product_detail
    else:
        product_detail = f"Paket {nama_paket} - TOM'STT AI"
        item_name = f"Paket {nama_paket}"
        
    payload = {
        "merchantCode": merchant_code,
        "paymentAmount": harga_int,
        "merchantOrderId": order_id,
        "productDetails": product_detail,
        "email": user_email,
        "phoneNumber": phone, 
        "customerVaName": first_name, 
        "itemDetails": [{
            "name": item_name,
            "price": harga_int,
            "quantity": 1
        }],
        "customerDetail": {
            "firstName": first_name,
            "lastName": last_name,
            "email": user_email,
            "phoneNumber": phone,
            "billingAddress": {
                "firstName": first_name,
                "lastName": last_name,
                "address": address_inv,
                "city": city_inv,
                "postalCode": postal_inv,
                "phone": phone,
                "countryCode": "ID"
            }
        },
        "callbackUrl": "https://tomstt-webhook-duitku.tommy-huawei.workers.dev", 
        "returnUrl": "https://rapat-dev.streamlit.app", 
        "expiryPeriod": 60 
    }
    
    try:
        response = requests.post(url, json=payload, headers=headers)
        
        # Pengecekan ekstra: Mencegah error 'Expecting value' (Jika Duitku membalas HTML)
        if "application/json" not in response.headers.get("Content-Type", ""):
            st.error("Sistem pembayaran sedang sibuk. Silahkan hubungi admin.")
            return None
            
        res_data = response.json()
        
        # Sukses! Ambil link pembayarannya
        if res_data.get("statusCode") == "00":
            return res_data.get("paymentUrl"), order_id
        else:
            # Jika Duitku menolak isian kita, ia akan memberi tahu alasannya
            error_msg = res_data.get('statusMessage') or str(res_data)
            st.error(f"Transaksi Ditolak: {error_msg}")
            return None
            
    except Exception as e:
        st.error(f"Koneksi ke sistem pembayaran gagal: {e}")
        return None
        
@st.dialog("🛡️ Panel Admin B2G/B2B", width="large")
def show_b2g_admin_panel():
    user_email = st.session_state.current_user
    user_data = get_user(user_email)
    vid = user_data.get("active_corporate_voucher")
    
    if not vid:
        st.error("Data Lisensi Instansi tidak ditemukan.")
        return
        
    v_ref = db.collection('vouchers').document(vid)
    v_doc = v_ref.get()
    if not v_doc.exists:
        st.error("Lisensi B2B tidak valid atau telah dihapus.")
        return
        
    v_data = v_doc.to_dict()
    corp_name = v_data.get("corporate_name", "Instansi")
    
    st.markdown(f"<h3 style='color: #0056b3; margin-top: -10px;'>🏛️ {corp_name}</h3>", unsafe_allow_html=True)
    
    # SCORECARDS
    max_users = v_data.get("max_users", 0)
    staff_usage = v_data.get("staff_usage", {})
    used_seats = len(staff_usage)
    sisa_kursi = max_users - used_seats
    
    kuota_total = v_data.get("shared_quota_minutes", 0)
    kuota_terpakai = v_data.get("used_quota_minutes", 0)
    sisa_kuota = kuota_total - kuota_terpakai
    total_docs = v_data.get("total_documents_generated", 0)
    
    # 🚀 FIX: Kalkulasi Total Generate AI (Hitung langsung dari sub-collection 'history' tiap user)
    total_ai_all = 0
    for s_email, s_metrics in staff_usage.items():
        if isinstance(s_metrics, dict):
            try:
                # Mengambil jumlah arsip yang riil disimpan oleh user tersebut
                arsip_count = len(db.collection('users').document(s_email).collection('history').get())
                s_metrics['ai_generated'] = arsip_count
                total_ai_all += arsip_count
            except:
                s_metrics['ai_generated'] = 0
    
    # 🚀 FIX: Mengubah 3 kolom menjadi 4 kolom untuk menambahkan info Total Generate AI
    col1, col2, col3, col4 = st.columns(4)
    
    with col1:
        st.markdown(f"<div style='background-color: #f8f9fa; padding: 15px; border-radius: 8px; border: 1px solid #e0e0e0; text-align: center;'><div style='font-size: 13px; color: #666; font-weight: bold;'>👥 Sisa Kuota User</div><div style='font-size: 24px; font-weight: 800; color: #111;'>{sisa_kursi} <span style='font-size: 14px; color: #888;'>/ {max_users}</span></div></div>", unsafe_allow_html=True)
        
    with col2:
        h_conv_main = int(sisa_kuota // 60)
        m_conv_main = int(sisa_kuota % 60)
        
        konversi_main = ""
        if h_conv_main > 0:
            if m_conv_main > 0:
                konversi_main = f" <span style='font-size: 14px; color: #888;'>({h_conv_main} Jam {m_conv_main} Mnt)</span>"
            else:
                konversi_main = f" <span style='font-size: 14px; color: #888;'>({h_conv_main} Jam)</span>"

        st.markdown(f"<div style='background-color: #f8f9fa; padding: 15px; border-radius: 8px; border: 1px solid #e0e0e0; text-align: center;'><div style='font-size: 13px; color: #666; font-weight: bold;'>⏱️ Sisa Kuota Menit Audio/Teks</div><div style='font-size: 24px; font-weight: 800; color: #111;'>{sisa_kuota:,} <span style='font-size: 14px; color: #888;'>Mnt</span>{konversi_main}</div></div>".replace(',', '.'), unsafe_allow_html=True)
        
    with col3:
        # 🚀 FIX: Mengubah narasi dari Dokumen yang Disimpan menjadi File Audio/Teks yang Diproses
        st.markdown(f"<div style='background-color: #f8f9fa; padding: 15px; border-radius: 8px; border: 1px solid #e0e0e0; text-align: center;'><div style='font-size: 13px; color: #666; font-weight: bold;'>📄 Total File Audio/Teks yang Diproses</div><div style='font-size: 24px; font-weight: 800; color: #111;'>{total_docs}</div></div>", unsafe_allow_html=True)
        
    with col4:
        # 🚀 KOTAK BARU: Mengambil nilai total_ai_all dari kalkulasi Arsip real-time di atas
        st.markdown(f"<div style='background-color: #f8f9fa; padding: 15px; border-radius: 8px; border: 1px solid #e0e0e0; text-align: center;'><div style='font-size: 13px; color: #666; font-weight: bold;'>🧠 Total Generate AI</div><div style='font-size: 24px; font-weight: 800; color: #111;'>{total_ai_all} x</div></div>", unsafe_allow_html=True)    

    st.write("")
    
    # MANAJEMEN STAF (WHITELIST)
    st.markdown("#### 🗣️ Manajemen Akses User")
    st.caption("Tambahkan email user/staf anda di bawah ini untuk memberikan akses.")
    
    with st.form("form_add_staff", clear_on_submit=True):
        col_add1, col_add2 = st.columns([3, 1])
        with col_add1:
            new_staff = st.text_input("Email User Baru", placeholder="contoh: budi@instansi.go.id", label_visibility="collapsed").strip().lower()
        with col_add2:
            submit_staff = st.form_submit_button("➕ Tambah User", width='stretch')
            
        if submit_staff:
            import re
            email_pattern = r'^[\w\.-]+@[\w\.-]+\.\w+$'
            
            if not new_staff:
                st.error("⚠️ Email tidak boleh kosong.")
            elif not re.match(email_pattern, new_staff):
                # 🚀 FIX: Tolak jika format bukan email
                st.error("⚠️ Format email tidak valid! Gunakan alamat email yang benar.")
            elif new_staff in staff_usage:
                st.error("⚠️ User sudah terdaftar.")
            elif sisa_kursi <= 0:
                st.error("❌ Kuota user habis! Silahkan hapus akses user lain atau Top-Up/Beli Paket Lain.")
            else:
                # 🚀 FIX: Menambahkan key "ai_generated" ke dalam inisialisasi default
                staff_usage[new_staff] = {"minutes_used": 0, "docs_generated": 0, "ai_generated": 0}
                v_ref.update({"staff_usage": staff_usage})
                
                # Suntik DNA otomatis ke user jika akunnya sudah pernah dibuat
                u_ref = db.collection('users').document(new_staff)
                u_snap = u_ref.get()
                
                if u_snap.exists:
                    u_ref.update({"active_corporate_voucher": vid})
                    u_data = u_snap.to_dict()
                    
                    # Cek apakah user sudah punya paket personal sebelumnya
                    if u_data.get('inventori') or u_data.get('bank_menit', 0) > 0:
                        st.success(f"✔ User {new_staff} berhasil ditambahkan! (Sistem mendeteksi user ini memiliki paket personal. Kuota pribadinya otomatis dibekukan sementara dan kini ia menggunakan kuota B2G/B2B).")
                    else:
                        st.success(f"✔ User {new_staff} berhasil ditambahkan dan dihubungkan ke Instansi!")
                else:
                    st.success(f"✔ User {new_staff} berhasil didaftarkan ke dalam Instansi!")
                

    st.markdown("**👤 Daftar User Aktif**")
 
    with st.expander(f"👥 Lihat Daftar User ({len(staff_usage)} Aktif)", expanded=False):
 
        # 🚀 FIX: Konversi ke list agar aman saat ada penghapusan, dan pastikan Admin Instansi ada di urutan teratas
        staff_list = list(staff_usage.items())
        staff_list.sort(key=lambda x: 0 if x[0] == user_email else 1)
 
        for staff_email, metrics in staff_list:
 
            # 🚀 FIX: Pelacak Sinyal Tombol Hapus (State Interception)
            if st.session_state.get(f"revoke_{staff_email}", False):
                if staff_email in staff_usage:
                    del staff_usage[staff_email]
                    v_ref.update({"staff_usage": staff_usage})
 
                u_ref = db.collection('users').document(staff_email)
                if u_ref.get().exists:
                    u_ref.update({"active_corporate_voucher": firestore.DELETE_FIELD})
 
                st.toast(f"Akses {staff_email} berhasil dihapus!", icon="🗑️")
                continue
 
            # Label judul expander per user (tampilkan badge PIC untuk admin instansi)
            if staff_email == user_email:
                label_user = f"👤 {staff_email}  🔴 PIC"
            else:
                label_user = f"👤 {staff_email}"
 
            with st.expander(label_user, expanded=False):
                col_s1, col_s2, col_s3, col_s4 = st.columns([3, 2, 2, 2])
 
                safe_minutes = metrics.get('minutes_used', 0) if isinstance(metrics, dict) else 0
                safe_docs    = metrics.get('docs_generated', 0) if isinstance(metrics, dict) else 0
                safe_ai      = metrics.get('ai_generated', 0) if isinstance(metrics, dict) else 0
 
                with col_s1:
                    h_conv = int(safe_minutes // 60)
                    m_conv = int(safe_minutes % 60)
                    konversi_teks = ""
                    if h_conv > 0:
                        if m_conv > 0:
                            konversi_teks = f" <span style='color: #888; font-size: 12px;'>({h_conv} Jam {m_conv} Menit)</span>"
                        else:
                            konversi_teks = f" <span style='color: #888; font-size: 12px;'>({h_conv} Jam)</span>"
                    st.markdown(f"<div style='font-size: 14px; margin-top: 2px;'>⏱️ {safe_minutes} Menit{konversi_teks}</div>", unsafe_allow_html=True)
 
                with col_s2:
                    st.markdown(f"<div style='font-size: 14px; margin-top: 2px;'>📄 {safe_docs} File Diproses</div>", unsafe_allow_html=True)
 
                with col_s3:
                    st.markdown(f"<div style='font-size: 14px; margin-top: 2px;'>🧠 {safe_ai} x Generate AI</div>", unsafe_allow_html=True)
 
                with col_s4:
                    if staff_email != user_email:
                        with st.popover("❌ Hapus Akses", width='stretch'):
                            st.markdown(f"Yakin menghapus akses **{staff_email}**?")
                            st.button("🚨 Ya, Hapus", key=f"revoke_{staff_email}", width='stretch')
 
    st.markdown("---")
    
    st.markdown("#### 🎨 Kustomisasi Co-Branding")
    st.caption("Sesuaikan nama dan logo instansi/korporasi yang tampil di halaman utama aplikasi.")
 
    # ── SUB-SECTION 1: NAMA INSTANSI ─────────────────────
    with st.expander("✏️ Nama Instansi/Korporasi", expanded=False):
 
        # Nama default dari Duitku (tidak pernah diubah)
        nama_default  = v_data.get("corporate_name", "")
        # Nama override jika PIC pernah menyimpan kustom
        nama_kustom   = v_data.get("cobrand_display_name", "")
        # Pre-fill input dengan nama kustom jika ada, fallback ke nama default
        nama_tampil   = nama_kustom if nama_kustom else nama_default
 
        if nama_kustom:
            st.info(f"📌 Nama default dari pembelian: **{nama_default}**")
 
        new_nama = st.text_input(
            "Nama Instansi di Halaman Utama",
            value=nama_tampil,
            placeholder=nama_default,
            key=f"input_nama_{vid}"
        )
 
        col_n1, col_n2 = st.columns(2)
 
        with col_n1:
            if st.button("💾 Simpan Nama", type="primary", width='stretch', key=f"save_nama_{vid}"):
                new_nama_strip = new_nama.strip()
                if not new_nama_strip:
                    st.error("⚠️ Nama tidak boleh kosong.")
                elif new_nama_strip == nama_default and not nama_kustom:
                    st.info("Nama sudah sama dengan nama default, tidak ada perubahan.")
                else:
                    v_ref.update({"cobrand_display_name": new_nama_strip})
                    # Invalidasi cache agar header halaman utama langsung berubah
                    cache_key = f"corp_name_{vid}"
                    if cache_key in st.session_state:
                        del st.session_state[cache_key]
                    st.success(f"✔ Nama berhasil diubah ke: **{new_nama_strip}**")
                    import time; time.sleep(1.5)
                    st.rerun()
 
        with col_n2:
            # Tombol reset hanya tampil jika ada nama kustom yang aktif
            if nama_kustom:
                if st.button("🗑️ Reset ke Default", type="secondary", width='stretch', key=f"reset_nama_{vid}"):
                    v_ref.update({"cobrand_display_name": firestore.DELETE_FIELD})
                    cache_key = f"corp_name_{vid}"
                    if cache_key in st.session_state:
                        del st.session_state[cache_key]
                    st.success(f"✔ Nama direset ke default: **{nama_default}**")
                    import time; time.sleep(1.5)
                    st.rerun()
            else:
                st.button("🗑️ Reset ke Default", width='stretch', disabled=True,
                          help="Nama masih menggunakan nama default dari pembelian.", key=f"reset_nama_dis_{vid}")
 
    # ── SUB-SECTION 2: LOGO UTAMA ────────────────────────
    with st.expander("📸 Logo Instansi/Korporasi", expanded=False):
 
        logo_saat_ini = v_data.get("cobrand_logo_url", "")
 
        if logo_saat_ini:
            st.markdown("**Logo Saat Ini:**")
            st.image(logo_saat_ini, width=150)
 
            if st.button("🗑️ Hapus Logo", type="secondary", width='stretch'):
                with st.spinner("Menghapus logo..."):
                    try:
                        import time
                        import hashlib
                        import requests
                        import re
 
                        match = re.search(r'(TOMSTT_B2B_LOGO/[^/.]+)', logo_saat_ini)
                        if match:
                            public_id = match.group(1)
                            try:
                                cloud_name = st.secrets["cloudinary"]["cloud_name"]
                                api_key    = st.secrets["cloudinary"]["api_key"]
                                api_secret = st.secrets["cloudinary"]["api_secret"]
 
                                timestamp_d = str(int(time.time()))
                                sign_str_d  = f"public_id={public_id}&timestamp={timestamp_d}{api_secret}"
                                signature_d = hashlib.sha1(sign_str_d.encode('utf-8')).hexdigest()
 
                                del_url  = f"https://api.cloudinary.com/v1_1/{cloud_name}/image/destroy"
                                del_data = {
                                    'public_id': public_id,
                                    'api_key':   api_key,
                                    'timestamp': timestamp_d,
                                    'signature': signature_d
                                }
                                requests.post(del_url, data=del_data)
                            except:
                                pass
 
                        v_ref.update({'cobrand_logo_url': firestore.DELETE_FIELD})
 
                        cache_key      = f"corp_name_{vid}"
                        cache_logo_key = f"corp_logo_{vid}"
                        if cache_key      in st.session_state: del st.session_state[cache_key]
                        if cache_logo_key in st.session_state: del st.session_state[cache_logo_key]
 
                        st.success("✔ Logo berhasil dihapus!")
                        time.sleep(1.5)
                        st.rerun()
                    except Exception as e:
                        st.error(f"Terjadi kesalahan: {e}")
        else:
            st.info("**Syarat File:**\n- **Format:** JPG, JPEG, atau PNG\n- **Ukuran Maksimal:** 1 MB\n- **Saran Dimensi:** 300 x 300 piksel (Rasio 1:1 proporsional)")
            uploaded_logo = st.file_uploader("Upload logo", type=['png', 'jpg', 'jpeg'], key=f"up_logo_{vid}")
 
            if uploaded_logo is not None:
                if uploaded_logo.size > 1048576:
                    st.error("❌ Gagal! Ukuran gambar terlalu besar. Maksimal 1 MB.")
                else:
                    st.success("✔ File gambar memenuhi syarat.")
                    if st.button("☁️ Upload & Simpan Logo", type="primary", width='stretch'):
                        with st.spinner("Mengupload logo ke server..."):
                            try:
                                import time
                                import hashlib
                                import requests
                                try:
                                    cloud_name = st.secrets["cloudinary"]["cloud_name"]
                                    api_key    = st.secrets["cloudinary"]["api_key"]
                                    api_secret = st.secrets["cloudinary"]["api_secret"]
                                except KeyError:
                                    st.error("⚠️ Kredensial Cloudinary belum diset di secrets.")
                                    st.stop()
 
                                timestamp_c = str(int(time.time()))
                                folder_name = "TOMSTT_B2B_LOGO"
                                sign_str    = f"folder={folder_name}&timestamp={timestamp_c}{api_secret}"
                                signature   = hashlib.sha1(sign_str.encode('utf-8')).hexdigest()
                                url_cloud   = f"https://api.cloudinary.com/v1_1/{cloud_name}/image/upload"
                                files       = {'file': (uploaded_logo.name, uploaded_logo.getvalue(), uploaded_logo.type)}
                                data        = {'api_key': api_key, 'timestamp': timestamp_c, 'folder': folder_name, 'signature': signature}
 
                                res = requests.post(url_cloud, files=files, data=data).json()
                                if 'secure_url' in res:
                                    final_img_url = res['secure_url']
                                    v_ref.update({'cobrand_logo_url': final_img_url})
 
                                    cache_key      = f"corp_name_{vid}"
                                    cache_logo_key = f"corp_logo_{vid}"
                                    if cache_key      in st.session_state: del st.session_state[cache_key]
                                    if cache_logo_key in st.session_state: del st.session_state[cache_logo_key]
 
                                    st.success("✔ Logo berhasil disimpan & diterapkan!")
                                    time.sleep(1.5)
                                    st.rerun()
                                else:
                                    st.error("❌ Gagal mengupload gambar.")
                            except Exception as e:
                                st.error(f"Terjadi kesalahan koneksi: {e}")
 
    st.markdown("---")
    
    st.markdown("#### 🏛️ Sertifikat, Bukti Pembelian & Kebutuhan Laporan Audit")
    st.caption("Download berkas administrasi untuk pencairan anggaran atau pelaporan.")

    with st.expander("📄 Lihat & Download Dokumen", expanded=False):
        c_spj1, c_spj2 = st.columns(2)
        
        with c_spj1:
            # 🚀 FITUR BARU: Auto-Generate SPJ & Invoice (HTML A4 format ready for Print/PDF)
            import datetime
            # 🚀 FIX: Mengonversi waktu server cloud (UTC) menjadi WIB (UTC+7)
            wib_tz = datetime.timezone(datetime.timedelta(hours=7))
            now = datetime.datetime.now(wib_tz)
            bulan_indo = ["Januari", "Februari", "Maret", "April", "Mei", "Juni", "Juli", "Agustus", "September", "Oktober", "November", "Desember"]
            now_str = f"{now.day} {bulan_indo[now.month - 1]} {now.year}"

            # 🚀 Baca snapshot harga saat beli dari voucher (akurat, tidak berubah meski harga diubah admin)
            harga_invoice_ini   = v_data.get("harga_beli", 0)
            harga_coret_invoice = v_data.get("harga_coret_beli", 0)
            aktif_coret_invoice = v_data.get("aktif_coret_beli", False)

            # Fallback untuk voucher lama yang belum punya snapshot (migrasi natural)
            if harga_invoice_ini == 0:
                if kuota_total >= 66000:
                    harga_invoice_ini = 9900000
                elif kuota_total >= 33000:
                    harga_invoice_ini = 5900000
                else:
                    harga_invoice_ini = user_data.get('total_spending', 0)

            total_rp = f"Rp {harga_invoice_ini:,}".replace(',', '.')

            # 🚀 Siapkan baris harga HTML untuk SPJ (dengan atau tanpa harga coret)
            if aktif_coret_invoice and harga_coret_invoice > 0:
                harga_coret_rp  = f"Rp {harga_coret_invoice:,}".replace(',', '.')
                baris_harga_spj = (
                    f"<td>"
                    f"<span style='text-decoration:line-through; color:#999; font-size:12px;'>{harga_coret_rp}</span><br>"
                    f"<strong style='color:#28a745; font-size:14px;'>{total_rp}</strong>"
                    f"<span style='background:#e8f5e9; color:#2e7d32; padding:2px 6px; border-radius:4px; "
                    f"font-size:10px; margin-left:6px; font-weight:bold;'>HARGA PROMO</span>"
                    f"</td>"
                )
            else:
                baris_harga_spj = f"<td><strong style='color:#28a745; font-size:14px;'>{total_rp}</strong></td>"
            
            # 🚀 AMANAT BPK: Menarik Nomor Invoice dari Database Voucher Instansi
            no_invoice = v_data.get("order_id", f"INV-MANUAL-{vid}")
            tgl_pembelian = now_str # Fallback
            
            # Tarik tanggal pembuatan voucher dari database (Lebih Akurat)
            created_val = v_data.get("created_at")
            if created_val:
                try:
                    wib_tz = datetime.timezone(datetime.timedelta(hours=7))
                    created_wib = created_val.astimezone(wib_tz)
                    tgl_pembelian = created_wib.strftime("%d %b %Y, %H:%M WIB")
                except: pass    
            
            html_spj = f"""
            <!DOCTYPE html>
            <html lang="id">
            <head>
                <meta charset="UTF-8">
                <title>Bukti Pembelian & Sertifikat - {corp_name}</title>
                <style>
                    /* CSS Latar Belakang Layar (Browser View) */
                    body {{ 
                        font-family: 'Helvetica Neue', Helvetica, Arial, sans-serif; 
                        background-color: #f0f2f5; /* Latar abu-abu untuk menonjolkan kertas */
                        padding: 40px 20px; 
                        margin: 0; 
                        color: #333; 
                        line-height: 1.4; 
                        font-size: 12px; 
                        -webkit-print-color-adjust: exact; 
                        print-color-adjust: exact; 
                    }}
                    
                    /* CSS Kertas A4 Melayang */
                    .container {{ 
                        width: 210mm; /* Lebar standar A4 */
                        min-height: 297mm; /* Tinggi standar A4 */
                        margin: 0 auto; /* Posisi di tengah layar */
                        padding: 20mm; /* Ruang bernapas di dalam kertas */
                        background-color: white; /* Warna kertas */
                        box-shadow: 0 10px 30px rgba(0, 0, 0, 0.15); /* Efek bayangan kertas elegan */
                        border-radius: 4px; /* Sedikit melengkung di ujung kertas */
                        box-sizing: border-box; /* Pastikan padding tidak menambah ukuran A4 */
                        position: relative; /* Diperlukan untuk watermark */
                        overflow: hidden; /* Mencegah watermark tembus/keluar dari batas kertas */
                        z-index: 1;
                    }}
                    
                    /* 🚀 CSS WATERMARK LOGO BERULANG (DIAGONAL MIRING ATAS KANAN) */
                    .container::before {{
                        content: "";
                        position: absolute;
                        top: -50%;
                        left: -50%;
                        width: 200%;
                        height: 200%;
                        background-image: url('https://res.cloudinary.com/tomstt/image/upload/v1774703242/Logo_1_wvwoid.png');
                        background-repeat: repeat;
                        background-size: 160px; /* Lebar logo watermark */
                        opacity: 0.04; /* Transparansi sangat tipis agar tidak menutupi teks */
                        transform: rotate(-30deg); /* Kemiringan ke atas kanan */
                        z-index: -1; /* Berada paling belakang */
                        pointer-events: none; /* Tidak mengganggu klik teks */
                        -webkit-print-color-adjust: exact;
                        print-color-adjust: exact;
                    }}
                    
                    /* Bagian Kop Surat / Header */
                    .header {{ text-align: center; margin-bottom: 10px; }}
                    .logo {{ max-width: 120px; height: auto; margin-bottom: 3px; }}
                    .tagline {{ font-size: 11px; font-weight: bold; color: #444; margin: 3px 0; font-style: italic; }}
                    .divider {{ border-bottom: 2px solid #0056b3; margin-bottom: 25px; }}
                    
                    /* Judul Dokumen */
                    .title {{ font-size: 16px; font-weight: 800; color: #0056b3; margin: 0 0 25px 0; text-align: center; text-transform: uppercase; }}
                    
                    /* Kotak Rincian */
                    .box {{ border: 1px solid #e0e0e0; padding: 12px 18px; border-radius: 6px; margin-bottom: 20px; background-color: #fcfcfc; page-break-inside: avoid; }}
                    h3 {{ margin-top: 0; color: #111; border-bottom: 1px solid #eaeaea; padding-bottom: 8px; font-size: 13px; margin-bottom: 10px; }}
                    table {{ width: 100%; border-collapse: collapse; }}
                    th, td {{ padding: 8px 5px; border-bottom: 1px dashed #ccc; text-align: left; font-size: 12px; }}
                    th {{ color: #555; width: 40%; font-weight: 600; }}
                    
                    /* Tanda Tangan & Footer */
                    .ttd-box {{ float: right; text-align: center; margin-top: 25px; width: 200px; page-break-inside: avoid; }}
                    .footer {{ text-align: center; margin-top: 40px; font-size: 10px; color: #999; border-top: 1px solid #eee; padding-top: 15px; page-break-inside: avoid; clear: both; }}
                    
                    /* CSS Khusus Mode Cetak (Print) */
                    @page {{ 
                        size: A4; 
                        margin: 0; /* Margin ditangani oleh container */
                    }}
                    @media print {{
                        body {{ 
                            background-color: transparent; /* Hilangkan latar abu saat diprint */
                            padding: 0; 
                        }}
                        .container {{ 
                            box-shadow: none; /* Hilangkan bayangan saat diprint */
                            border-radius: 0;
                            margin: 0;
                            width: 100%; /* Gunakan full width kertas saat diprint */
                        }}
                    }}
                </style>
            </head>
            <body>
                <div class="container">
                    <div class="header">
                        <img src="https://res.cloudinary.com/tomstt/image/upload/v1774703242/Logo_1_wvwoid.png" class="logo" alt="TOM'STT AI Logo">
                        <p class="tagline">The First AI Purpose-Built for Indonesian Transcription and Document Automation</p>
                    </div>
                    <div class="divider"></div>
                    
                    <h2 class="title">Sertifikat Lisensi & Bukti Pembelian</h2>
                    
                    <div class="box">
                        <h3>Identitas Pemegang Lisensi</h3>
                        <table>
                            <tr><th>Nama Instansi / Perusahaan</th><td><strong>{corp_name}</strong></td></tr>
                            <tr><th>Penanggung Jawab (PIC)</th><td>{user_email}</td></tr>
                            <tr><th>ID Lisensi Sistem</th><td><span style="background-color:#e6f3ff; padding:3px 6px; border-radius:4px; font-family:monospace; color:#0056b3; font-weight:bold;">{vid}</span></td></tr>
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
                        <em>Dokumen ini dibuat secara otomatis oleh sistem <strong>TOM'STT AI</strong> dan merupakan dokumen elektronik yang sah. Dokumen ini dapat digunakan sebagai kelengkapan administrasi pencairan anggaran, pelaporan keuangan, serta bukti valid kepemilikan lisensi Enterprise B2G/B2B sesuai dengan Nomor Invoice dari Payment Gateway yang tercantum. Invoice dari Payment Gateway dikirim ke email Penanggung Jawab (PIC) pada saat membeli dan membayar produk ini.</em>
                    </p>
                    
                    <div class="ttd-box">
                        <p style="margin-bottom: 40px; font-size: 12px;">{tgl_pembelian}<br><strong>Tim Administrasi TOM'STT AI</strong></p>
                    </div>
                    
                    <div class="footer">
                        &copy; 2026 TOM'STT AI |
                        <a href="https://tom-stt.com" target="_blank" style="color:#0056b3; text-decoration:none;">https://tom-stt.com</a>
                    </div>
                </div>
                
                <script>
                    // Otomatis memunculkan dialog Print/Save as PDF saat file dibuka
                    window.onload = function() {{ window.print(); }}
                </script>
            </body>
            </html>
            """
            
            st.download_button(
                label="Download Bukti Pembelian & Sertifikat",
                data=html_spj.encode('utf-8'),
                file_name=f"Sertifikat_Invoice_{corp_name.replace(' ', '_')}.html",
                mime="text/html",
                width='stretch'
            )
            
        with c_spj2:
            # 🚀 FIX: Aktifkan Ekspor CSV Log Audit menggunakan Pandas
            if staff_usage:
                import pandas as pd
                df_audit = pd.DataFrame.from_dict(staff_usage, orient='index').reset_index()
                df_audit.rename(columns={'index': 'Email Staf', 'minutes_used': 'Durasi AI (Menit)', 'docs_generated': 'Total Dokumen'}, inplace=True)
                df_audit['Instansi'] = corp_name
                df_audit['ID Lisensi'] = vid
                csv_data = df_audit.to_csv(index=False).encode('utf-8')
                
                st.download_button(
                    label="Download Log Audit (CSV)",
                    data=csv_data,
                    file_name=f"Log_Audit_{corp_name.replace(' ', '_')}.csv",
                    mime="text/csv",
                    width='stretch'
                )
            else:
                st.button("Ekspor Log Audit (CSV)", width='stretch', disabled=True, help="Belum ada data user untuk diekspor.")

    st.write("")
    if st.button("Top-Up & Tambah Kapasitas (Beli Paket B2G/B2B)", width='stretch'):
        st.session_state.open_pricing_modal = True # 🚀 FIX: Simpan ke memori agar Pop-Up kedua dipanggil dari luar
        st.rerun()


def _fmt_harga_plain(entry: dict) -> str:
    """Return string harga untuk label tombol & expander (tanpa HTML)."""
    harga = entry.get("harga", 0)
    return f"Rp {harga:,}".replace(',', '.')

def _render_harga(entry: dict):
    """
    Render harga di UI dengan dukungan harga coret.
    Jika aktif_coret=True: tampilkan harga_coret dicoret + harga aktual besar & merah.
    Jika tidak: tampilkan harga aktual saja dengan warna biru.
    """
    harga       = entry.get("harga", 0)
    harga_coret = entry.get("harga_coret", 0)
    aktif_coret = entry.get("aktif_coret", False)

    harga_str       = f"Rp {harga:,}".replace(',', '.')
    harga_coret_str = f"Rp {harga_coret:,}".replace(',', '.')

    if aktif_coret and harga_coret > 0:
        st.markdown(
            f"<div style='text-align:center; margin-top:-10px; margin-bottom:8px;'>"
            f"<span style='color:#999; font-size:1rem; text-decoration:line-through;'>{harga_coret_str}</span><br>"
            f"<span style='color:#e74c3c; font-size:2.2rem; font-weight:800;'>{harga_str}</span>"
            f"</div>",
            unsafe_allow_html=True
        )
    else:
        st.markdown(
            f"<h2 style='text-align:center; color:#0056b3; margin-top:-10px; font-size:2.2rem; font-weight:800;'>"
            f"{harga_str}</h2>",
            unsafe_allow_html=True
        )

@st.dialog("🛒 Beli Paket & Top-Up", width="large")
def show_pricing_dialog():
    user_email = st.session_state.current_user
    sys_config = get_system_config()
    pc         = get_pricing_config()  # 🚀 Sumber kebenaran harga — selalu terbaru dari Firestore
    is_aio_active = sys_config.get("is_aio_active", True)
    is_reguler_active = sys_config.get("is_reguler_active", True)
    is_b2b_sys_active = sys_config.get("is_b2b_sys_active", True) # 🚀 FIX: Menangkap sinyal sakelar B2B dari Admin
    
    # 🚀 FIX: Pelacak Mode Enterprise (Mengunci Pembelian Eceran)
    u_info_cek = get_user(user_email)
    is_b2b_active = bool(u_info_cek and u_info_cek.get("active_corporate_voucher"))
    
    # 🚀 KOTAK REDEEM VOUCHER (Tampil di atas tabs, hanya untuk non-B2B)
    if not is_b2b_active:
        col_v1, col_v2 = st.columns([3, 1])
        with col_v1:
            input_voucher = st.text_input("🎁 Punya Kode Voucher / Promo?", placeholder="Masukkan kode di sini...", key="input_vc").strip().upper()
        with col_v2:
            st.write("")
            if st.button("Klaim Voucher", width='stretch', type="primary"):
                if not st.session_state.logged_in:
                    st.error("⚠️ Silahkan Login terlebih dahulu!")
                elif input_voucher:
                    with st.spinner("Memeriksa kode..."):
                        sukses, pesan = redeem_voucher(user_email, input_voucher)
                        if sukses:
                            st.success(pesan)
                            st.toast("Voucher berhasil diklaim!", icon="🎁")
                        else:
                            st.error(pesan)
                else:
                    st.warning("Silahkan masukkan kode terlebih dahulu.")
        st.markdown("---")

    # 🚀 LOGIKA TAB EKSKLUSIF B2B
    if is_b2b_active:
        st.info("🏛️ **Mode B2G/B2B Aktif:** Akun Anda terhubung dengan Lisensi Instansi. Pembelian paket eceran dinonaktifkan.")
        # Hanya munculkan 1 Tab
        tabs = st.tabs(["🏛️ Paket B2G / B2B"])
        tab_b2b = tabs[0]
        tab_paket = tab_aio = tab_saldo = None
    else:
        # Munculkan Semua Tab untuk User Reguler
        tabs = st.tabs(["📦 PAKET REGULER", "🕒 PAKET ALL-IN-ONE", "🏛️ Paket B2G / B2B", "💳 TOP-UP"])
        tab_paket, tab_aio, tab_b2b, tab_saldo = tabs[0], tabs[1], tabs[2], tabs[3]
    
    if tab_aio is not None:
        with tab_aio:
            if not is_aio_active:
                st.markdown("""
                <div style="background-color: #fff3cd; border-left: 5px solid #ffeeba; padding: 12px 15px; margin-bottom: 15px; border-radius: 6px;">
                    <b style="color: #856404; font-size: 16px;">🚧 SOLD OUT / MAINTENANCE:</b><br>
                    <span style="color: #856404; font-size: 14.5px; line-height: 1.5; display: inline-block; margin-top: 5px;">Penjualan Paket All-In-One saat ini sedang ditutup sementara untuk menjaga kapasitas server. Silahkan cek kembali nanti atau pilih <b>Paket Reguler</b>.</span>
                </div>
                """, unsafe_allow_html=True)
                
            st.info("💡 **Bebas Durasi & AI Sepuasnya!** Paket ini menggunakan sistem 'Bank Waktu'. Anda bebas mengupload audio panjang maupun pendek tanpa takut terpotong batas menit per file.")
            
            with st.expander(f"🥉 AIO 10 JAM - {_fmt_harga_plain(pc['AIO10'])}", expanded=False):
                _render_harga(pc['AIO10'])
                st.markdown("""
                <div style='font-size: 14px; color: #333;'>
                    <ul style='margin-bottom: 10px;'>
                        <li>⏱️ <b>Saldo Universal:</b> 10 Jam atau 600 Menit <i>(Memotong durasi audio ATAU estimasi panjang teks)</i></li>
                        <li>🤝 <b>FUP:</b> 20x Ekstrak AI <b>Per Dokumen Per Hari</b></li>
                        <li>🚀 <b>Batas Ukuran Audio & Teks:</b> Otomatis mengikuti <i>tier</i> yang Anda miliki.</li>
                        <li>💬 <b>Chatbot AI:</b> 20x–40x Tanya / Dokumen (Gratis)</li>
                        <li>📅 <b>Masa Aktif:</b> 30 Hari (Maks Akumulasi 150 Hari)</li>
                        <li>🗂️ <b>Arsip:</b> Akses riwayat Cloud</li>
                        <li>⚡ <b>Server STT:</b> Prioritas Standar</li>
                        <li>🎁 <b>Bonus Saldo:</b> Rp 10.000</li>
                    </ul>
                </div>
                """, unsafe_allow_html=True)
                
                if is_aio_active:
                    if st.button(f"🛒 Beli AIO 10 JAM ({_fmt_harga_plain(pc['AIO10'])})", key="buy_aio10", type="primary", width='stretch', disabled=is_b2b_active):
                        if not st.session_state.logged_in: st.error("Silahkan Login terlebih dahulu.")
                        else:
                            with st.spinner("Mencetak tagihan..."):
                                link_bayar, order_id = buat_tagihan_duitku("AIO10", pc['AIO10']['harga'], user_email)
                                if link_bayar: 
                                    db.collection('users').document(user_email).update({"pending_trx": firestore.ArrayUnion([{"order_id": order_id, "paket": "AIO10"}])})
                                    st.link_button("💳 Lanjut Bayar", link_bayar, width='stretch')
                else:
                    st.button("🚫 Sedang Ditutup", disabled=True, width='stretch', key="dis_aio10")

            with st.expander(f"🥈 AIO 30 JAM - {_fmt_harga_plain(pc['AIO30'])}", expanded=False):
                _render_harga(pc['AIO30'])
                st.markdown("""
                <div style='font-size: 14px; color: #333;'>
                    <ul style='margin-bottom: 10px;'>
                        <li>⏱️ <b>Saldo Universal:</b> 30 Jam atau 1.800 Menit <i>(Memotong durasi audio ATAU estimasi panjang teks)</i></li>
                        <li>🤝 <b>FUP:</b> 30x Ekstrak AI <b>Per Dokumen Per Hari</b></li>
                        <li>🚀 <b>Batas Ukuran Audio & Teks:</b> Otomatis mengikuti <i>tier</i> yang Anda miliki.</li>
                        <li>💬 <b>Chatbot AI:</b> 20x–40x Tanya / Dokumen (Gratis)</li>
                        <li>📅 <b>Masa Aktif:</b> 60 Hari (Maks Akumulasi 150 Hari)</li>
                        <li>🗂️ <b>Arsip:</b> Akses riwayat Cloud</li>
                        <li>⚡ <b>Server STT:</b> Prioritas Server Tertinggi & STT Kilat</li>
                        <li>🎁 <b>Bonus Saldo:</b> Rp 25.000</li>
                    </ul>
                </div>
                """, unsafe_allow_html=True)
                
                if is_aio_active:
                    if st.button(f"🛒 Beli AIO 30 JAM ({_fmt_harga_plain(pc['AIO30'])})", key="buy_aio30", type="primary", width='stretch', disabled=is_b2b_active):
                        if not st.session_state.logged_in: st.error("Silahkan Login terlebih dahulu.")
                        else:
                            with st.spinner("Mencetak tagihan..."):
                                link_bayar, order_id = buat_tagihan_duitku("AIO30", pc['AIO30']['harga'], user_email)
                                if link_bayar: 
                                    db.collection('users').document(user_email).update({"pending_trx": firestore.ArrayUnion([{"order_id": order_id, "paket": "AIO30"}])})
                                    st.link_button("💳 Lanjut Bayar", link_bayar, width='stretch')
                else:
                    st.button("🚫 Sedang Ditutup", disabled=True, width='stretch', key="dis_aio30")

            with st.expander(f"🥇 AIO 100 JAM - {_fmt_harga_plain(pc['AIO100'])}", expanded=False):
                _render_harga(pc['AIO100'])
                st.markdown("""
                <div style='font-size: 14px; color: #333;'>
                    <ul style='margin-bottom: 10px;'>
                        <li>⏱️ <b>Saldo Universal:</b> 100 Jam atau 6.000 Menit <span style='color: #e74c3c; font-weight: bold;'>(Tarif Termurah: ± Rp 216/menit)</span></li>
                        <li>🤝 <b>FUP:</b> 40x Ekstrak AI <b>Per Dokumen Per Hari</b></li>
                        <li>🚀 <b>Batas Ukuran Audio & Teks:</b> Otomatis mengikuti <i>tier</i> yang Anda miliki.</li>
                        <li>💬 <b>Chatbot AI:</b> 20x–40x Tanya / Dokumen (Gratis)</li>
                        <li>📅 <b>Masa Aktif:</b> 90 Hari (Maks Akumulasi 150 Hari)</li>
                        <li>🗂️ <b>Arsip:</b> Akses riwayat Cloud</li>
                        <li>⚡ <b>Server STT:</b> Prioritas Server Tertinggi & STT Kilat</li>
                        <li>🎁 <b>Bonus Saldo:</b> Rp 75.000</li>
                    </ul>
                </div>
                """, unsafe_allow_html=True)
                
                if is_aio_active:
                    if st.button(f"🛒 Beli AIO 100 JAM ({_fmt_harga_plain(pc['AIO100'])})", key="buy_aio100", type="primary", width='stretch', disabled=is_b2b_active):
                        if not st.session_state.logged_in: st.error("Silahkan Login terlebih dahulu.")
                        else:
                            with st.spinner("Mencetak tagihan..."):
                                link_bayar, order_id = buat_tagihan_duitku("AIO100", pc['AIO100']['harga'], user_email)
                                if link_bayar: 
                                    db.collection('users').document(user_email).update({"pending_trx": firestore.ArrayUnion([{"order_id": order_id, "paket": "AIO100"}])})
                                    st.link_button("💳 Lanjut Bayar", link_bayar, width='stretch')
                else:
                    st.button("🚫 Sedang Ditutup", disabled=True, width='stretch', key="dis_aio100")

    if tab_b2b is not None:
        with tab_b2b:
            # 🚀 FIX: Peringatan Pemeliharaan jika Sakelar Dimatikan
            if not is_b2b_sys_active:
                st.markdown("""
                <div style="background-color: #fff3cd; border-left: 5px solid #ffeeba; padding: 12px 15px; margin-bottom: 15px; border-radius: 6px;">
                    <b style="color: #856404; font-size: 16px;">🚧 MAINTENANCE / PEMELIHARAAN:</b><br>
                    <span style="color: #856404; font-size: 14.5px; line-height: 1.5; display: inline-block; margin-top: 5px;">Pembelian Lisensi Master B2G & B2B saat ini sedang ditutup sementara untuk pemeliharaan sistem. Silakan hubungi Admin atau cek kembali nanti.</span>
                </div>
                """, unsafe_allow_html=True)

            with st.expander("Lihat Keuntungan Eksklusif B2G & B2B", expanded=False):
                st.markdown("""
                <div style="background-color: #f8f9fa; border-left: 5px solid #0056b3; padding: 20px; border-radius: 8px; margin-bottom: 10px; border-right: 1px solid #e0e0e0; border-top: 1px solid #e0e0e0; border-bottom: 1px solid #e0e0e0;">
                    <h4 style="color: #0056b3; margin-top: 0; margin-bottom: 15px;">Keuntungan Eksklusif B2G & B2B</h4>
                    <ul style="color: #333; font-size: 14.5px; line-height: 1.6; padding-left: 20px;">
                        <li style="margin-bottom: 8px;">🤝 <b>Co-Branding B2G/B2B:</b> Antarmuka aplikasi bertransformasi menggunakan nama instansi Anda (Contoh: Ditjen XYZ &times; TOM'STT AI).</li>
                        <li style="margin-bottom: 8px;">🔑 <b>Dasbor Admin B2G/B2B:</b> Kendali mutlak. Tambah/hapus akses user. Pantau jumlah dokumen & durasi user anda secara real-time.</li>
                        <li style="margin-bottom: 8px;">⏱️ <b>Sistem Shared Pool:</b> Seluruh staf menggunakan satu tangki kuota bersama. Tidak ada sisa kuota individual yang hangus.</li>
                        <li style="margin-bottom: 8px;">📄 <b>Zero-Touch SPJ:</b> Download digital PDF Invoice dan Sertifikat Lisensi secara instan untuk kebutuhan administrasi pembayaran & Log Audit bagi Auditor.</li>
                        <li>🎨 <b>Efisiensi Custom Template AI:</b> Rancang format laporan tanpa memotong kuota dokumen. Hanya dipotong dari tangki waktu: 15 Menit (Buat Baru) & 5 Menit (Revisi).</li>
                    </ul>
                    <div style="background-color: #e6f3ff; color: #0056b3; padding: 10px; border-radius: 5px; font-size: 13px; margin-top: 15px; font-weight: 600;">
                        📌 Kebijakan Akumulasi: Jika Top-Up, Kuota Jam terakumulasi penuh. Masa Aktif ditambah 270 Hari (Batas maksimal akumulasi masa aktif 365 Hari / 1 Tahun).
                    </div>
                </div>
                """, unsafe_allow_html=True)

            # ── DETEKSI PAKET EXISTING & BILLING PROFILE ────────
            # Cek apakah user adalah PIC B2B aktif dan paket apa yang dimiliki
            existing_vid     = u_info_cek.get("active_corporate_voucher") if u_info_cek else None
            existing_paket   = None  # "Standard" | "Ultimate" | None

            if existing_vid:
                try:
                    v_snap = db.collection('vouchers').document(existing_vid).get()
                    if v_snap.exists:
                        v_snap_data    = v_snap.to_dict()
                        kuota_existing = v_snap_data.get("shared_quota_minutes", 0)
                        if kuota_existing >= 66000:
                            existing_paket = "Ultimate"
                        elif kuota_existing >= 33000:
                            existing_paket = "Standard"
                except:
                    pass

            # Ambil billing_profile dari user doc (pre-fill)
            bp = u_info_cek.get("billing_profile", {}) if u_info_cek else {}
            bp_corp   = bp.get("corporate_name", "")
            bp_nama   = bp.get("nama_lengkap", "")
            bp_hp     = bp.get("no_hp", "")
            bp_alamat = bp.get("alamat", "")
            bp_kota   = bp.get("kota", "")
            bp_kodepos= bp.get("kodepos", "")

            # Jika ada data lama → expander terbuka agar user bisa verifikasi
            # Jika buyer baru (kosong) → expander terbuka agar tahu wajib diisi
            form_expanded = False

            # ── PESAN PEMBATASAN PAKET ──────────────────────────
            def _pesan_salah_paket(paket_dimiliki, paket_lain):
                st.caption(
                    f"⚠️ Instansi Anda menggunakan Paket **{paket_dimiliki}**. "
                    f"Untuk membeli Paket **{paket_lain}**, gunakan email PIC lain yang belum terdaftar di sistem."
                )

            # ── KOLOM DUA PAKET ─────────────────────────────────
            col_b1, col_b2 = st.columns(2)

            # ════════════════════════════════════════════════════
            # PAKET STANDARD
            # ════════════════════════════════════════════════════
            with col_b1:
                with st.container(border=True):
                    st.markdown("<h4 style='text-align: center; color: #111;'>Paket B2G/B2B Standard</h4>", unsafe_allow_html=True)
                    _render_harga(pc['B2B_Standard'])
                    st.markdown("""
                    <div style='font-size: 14px; color: #444; margin-bottom: 15px;'>
                        <ul style='padding-left: 15px;'>
                            <li>🔑 <b>Dasbor Admin:</b> Pengelolaan User/Staf</li>
                            <li>🤝 <b>Co-Branding:</b> Nama Instansi Anda pada Antarmuka Sistem</li>
                            <li>⏱️ <b>Kapasitas Tangki:</b> 550 Jam Audio/Teks (33.000 Menit) Seluruh User/Staf Menggunakan Satu Tangki Kuota Bersama</li>
                            <li>👥 <b>Batas User:</b> Maksimal 15 Akun</li>
                            <li>📅 <b>Masa Aktif:</b> 270 Hari (3 Triwulan) Maks. 365 Hari</li>
                            <li>⚡ <b>Akses:</b> Prioritas Server Tertinggi</li>
                        </ul>
                    </div>
                    """, unsafe_allow_html=True)

                    # Disable jika user existing Ultimate
                    disabled_standard = (existing_paket == "Ultimate")

                    with st.expander("Silahkan Isi untuk Pertanggungjawaban Keuangan / Invoice (WAJIB)", expanded=form_expanded):
                        corporate_input_1 = st.text_input("Nama Instansi / Perusahaan:", key="corp_in_1", value=bp_corp, placeholder="Contoh: PT ABC / Divisi IT").strip()
                        nama_lengkap_1    = st.text_input("Nama Lengkap (Sesuai KTP):", key="nama_in_1", value=bp_nama, placeholder="Contoh: Budi Santoso").strip()
                        no_hp_1           = st.text_input("Nomor Telepon / WhatsApp:", key="hp_in_1", value=bp_hp, placeholder="Contoh: 081234567890").strip()
                        alamat_1          = st.text_area("Alamat Penagihan:", key="alamat_in_1", value=bp_alamat, placeholder="Contoh: Gedung A Lt. 2, Jl. Sudirman No. 1...").strip()
                        col_alamat_1a, col_alamat_1b = st.columns(2)
                        with col_alamat_1a:
                            kota_1    = st.text_input("Kota/Kab:", key="kota_in_1", value=bp_kota, placeholder="Contoh: Jakarta Selatan").strip()
                        with col_alamat_1b:
                            kodepos_1 = st.text_input("Kode Pos:", key="kodepos_in_1", value=bp_kodepos, placeholder="Contoh: 12345").strip()

                    # 🚀 FITUR BARU: OPSI PRIVASI BUYER
                    st.write("")
                    st.markdown("**🛡️ Opsi Privasi & Keamanan Data**")
                    opsi_arsip_1 = st.radio(
                        "Simpan Riwayat Arsip Dokumen Anda?",
                        ["Arsip ON (Standar)", "Arsip OFF (Zero Data Retention)"],
                        index=1, key="arsip_b2b_1",
                        help="Jika OFF, sistem menjamin keamanan tingkat tinggi. Dokumen tidak disimpan di server."
                    )
                    st.write("")

                    if disabled_standard:
                        st.button("🚫 Paket Tidak Tersedia", disabled=True, width='stretch', key="dis_std_salah_paket")
                        _pesan_salah_paket("Ultimate", "Standard")
                    elif is_b2b_sys_active:
                        if st.button("🛒 Beli Paket B2G/B2B Standard", key="buy_b2b_standard", type="primary", width='stretch'):
                            if not st.session_state.logged_in:
                                st.error("Silahkan Login terlebih dahulu.")
                            elif not corporate_input_1:
                                st.error("⚠️ Silahkan isi Nama Instansi / Perusahaan Anda terlebih dahulu.")
                            elif not nama_lengkap_1:
                                st.error("⚠️ Silahkan isi Nama Lengkap Anda.")
                            elif not no_hp_1:
                                st.error("⚠️ Silahkan isi Nomor Telepon Anda.")
                            elif not alamat_1:
                                st.error("⚠️ Silahkan isi Alamat Penagihan Anda.")
                            elif not kota_1:
                                st.error("⚠️ Silahkan isi Kota penagihan Anda.")
                            elif not kodepos_1:
                                st.error("⚠️ Silahkan isi Kode Pos penagihan Anda.")
                            else:
                                with st.spinner("Mencetak tagihan..."):
                                    mode_rahasia_1 = "Shadow Retention (v1)" if "OFF" in opsi_arsip_1 else "Normal"

                                    link_bayar, order_id = buat_tagihan_duitku(
                                        "B2B_Standard", pc['B2B_Standard']['harga'], user_email,
                                        corporate_name=corporate_input_1,
                                        nama_lengkap=nama_lengkap_1,
                                        no_hp=no_hp_1, alamat=alamat_1,
                                        kota=kota_1, kode_pos=kodepos_1
                                    )
                                    if link_bayar:
                                        # 🚀 Susun billing_profile untuk dititipkan ke pending_trx
                                        billing_profile_baru = {
                                            "corporate_name": corporate_input_1,
                                            "nama_lengkap":   nama_lengkap_1,
                                            "no_hp":          no_hp_1,
                                            "alamat":         alamat_1,
                                            "kota":           kota_1,
                                            "kodepos":        kodepos_1
                                        }
                                        db.collection('users').document(user_email).update({
                                            # Simpan ke user doc langsung (pre-fill berikutnya)
                                            "billing_profile": billing_profile_baru,
                                            "pending_trx": firestore.ArrayUnion([{
                                                "order_id":       order_id,
                                                "paket":          "B2B_Standard",
                                                "corporate_name": corporate_input_1,
                                                "security_mode":  mode_rahasia_1,
                                                # 🚀 Titipkan billing_profile agar webhook bisa salin ke voucher
                                                "billing_profile": billing_profile_baru
                                            }])
                                        })
                                        st.link_button("💳 Lanjut Bayar", link_bayar, width='stretch')
                    else:
                        st.button("🚫 Sedang Ditutup", disabled=True, width='stretch', key="dis_b2b_standard")

            # ════════════════════════════════════════════════════
            # PAKET ULTIMATE
            # ════════════════════════════════════════════════════
            with col_b2:
                with st.container(border=True):
                    st.markdown("<h4 style='text-align: center; color: #111;'>Paket B2G/B2B Ultimate</h4>", unsafe_allow_html=True)
                    _render_harga(pc['B2B_Ultimate'])
                    st.markdown("""
                    <div style='font-size: 14px; color: #444; margin-bottom: 15px;'>
                        <ul style='padding-left: 15px;'>
                            <li>🔑 <b>Dasbor Admin:</b> Pengelolaan User/Staf</li>
                            <li>🤝 <b>Co-Branding:</b> Nama Instansi Anda pada Antarmuka Sistem</li>
                            <li>⏱️ <b>Kapasitas Tangki:</b> 1.100 Jam Audio/Teks (66.000 Menit) Seluruh User/Staf Menggunakan Satu Tangki Kuota Bersama</li>
                            <li>👥 <b>Batas User:</b> Maksimal 30 Akun</li>
                            <li>📅 <b>Masa Aktif:</b> 270 Hari (3 Triwulan) Maks. 365 Hari</li>
                            <li>⚡ <b>Akses:</b> Prioritas Server Tertinggi</li>
                        </ul>
                    </div>
                    """, unsafe_allow_html=True)

                    # Disable jika user existing Standard
                    disabled_ultimate = (existing_paket == "Standard")

                    with st.expander("Silahkan Isi untuk Pertanggungjawaban Keuangan / Invoice (WAJIB)", expanded=form_expanded):
                        corporate_input_2 = st.text_input("Nama Instansi / Perusahaan:", key="corp_in_2", value=bp_corp, placeholder="Contoh: Ditjen XYZ - Kementerian ABC").strip()
                        nama_lengkap_2    = st.text_input("Nama Lengkap (Sesuai KTP):", key="nama_in_2", value=bp_nama, placeholder="Contoh: Budi Santoso").strip()
                        no_hp_2           = st.text_input("Nomor Telepon / WhatsApp:", key="hp_in_2", value=bp_hp, placeholder="Contoh: 081234567890").strip()
                        alamat_2          = st.text_area("Alamat Penagihan:", key="alamat_in_2", value=bp_alamat, placeholder="Contoh: Gedung A Lt. 2, Jl. Sudirman No. 1...").strip()
                        col_alamat_2a, col_alamat_2b = st.columns(2)
                        with col_alamat_2a:
                            kota_2    = st.text_input("Kota/Kab:", key="kota_in_2", value=bp_kota, placeholder="Contoh: Jakarta Selatan").strip()
                        with col_alamat_2b:
                            kodepos_2 = st.text_input("Kode Pos:", key="kodepos_in_2", value=bp_kodepos, placeholder="Contoh: 12345").strip()

                    # 🚀 FITUR BARU: OPSI PRIVASI BUYER (B2B ULTIMATE)
                    st.write("")
                    st.markdown("**🛡️ Opsi Privasi & Keamanan Data**")
                    opsi_arsip_2 = st.radio(
                        "Simpan Riwayat Arsip Dokumen Anda?",
                        ["Arsip ON (Standar)", "Arsip OFF (Zero Data Retention)"],
                        index=1, key="arsip_b2b_2",
                        help="Jika OFF, sistem menjamin keamanan tingkat tinggi. Dokumen tidak disimpan di server."
                    )
                    st.write("")

                    if disabled_ultimate:
                        st.button("🚫 Paket Tidak Tersedia", disabled=True, width='stretch', key="dis_ult_salah_paket")
                        _pesan_salah_paket("Standard", "Ultimate")
                    elif is_b2b_sys_active:
                        if st.button("🛒 Beli Paket B2G/B2B Ultimate", key="buy_b2b_ultimate", type="primary", width='stretch'):
                            if not st.session_state.logged_in:
                                st.error("Silahkan Login terlebih dahulu.")
                            elif not corporate_input_2:
                                st.error("⚠️ Silahkan isi Nama Instansi / Perusahaan Anda terlebih dahulu.")
                            elif not nama_lengkap_2:
                                st.error("⚠️ Silahkan isi Nama Lengkap Anda.")
                            elif not no_hp_2:
                                st.error("⚠️ Silahkan isi Nomor Telepon Anda.")
                            elif not alamat_2:
                                st.error("⚠️ Silahkan isi Alamat Penagihan Anda.")
                            elif not kota_2:
                                st.error("⚠️ Silahkan isi Kota penagihan Anda.")
                            elif not kodepos_2:
                                st.error("⚠️ Silahkan isi Kode Pos penagihan Anda.")
                            else:
                                with st.spinner("Mencetak tagihan..."):
                                    mode_rahasia_2 = "Shadow Retention (v1)" if "OFF" in opsi_arsip_2 else "Normal"

                                    link_bayar, order_id = buat_tagihan_duitku(
                                        "B2B_Ultimate", pc['B2B_Ultimate']['harga'], user_email,
                                        corporate_name=corporate_input_2,
                                        nama_lengkap=nama_lengkap_2,
                                        no_hp=no_hp_2, alamat=alamat_2,
                                        kota=kota_2, kode_pos=kodepos_2
                                    )
                                    if link_bayar:
                                        # 🚀 Susun billing_profile untuk dititipkan ke pending_trx
                                        billing_profile_baru = {
                                            "corporate_name": corporate_input_2,
                                            "nama_lengkap":   nama_lengkap_2,
                                            "no_hp":          no_hp_2,
                                            "alamat":         alamat_2,
                                            "kota":           kota_2,
                                            "kodepos":        kodepos_2
                                        }
                                        db.collection('users').document(user_email).update({
                                            # Simpan ke user doc langsung (pre-fill berikutnya)
                                            "billing_profile": billing_profile_baru,
                                            "pending_trx": firestore.ArrayUnion([{
                                                "order_id":        order_id,
                                                "paket":           "B2B_Ultimate",
                                                "corporate_name":  corporate_input_2,
                                                "security_mode":   mode_rahasia_2,
                                                # 🚀 Titipkan billing_profile agar webhook bisa salin ke voucher
                                                "billing_profile": billing_profile_baru
                                            }])
                                        })
                                        st.link_button("💳 Lanjut Bayar", link_bayar, width='stretch')
                    else:
                        st.button("🚫 Sedang Ditutup", disabled=True, width='stretch', key="dis_b2b_ultimate")

    if tab_paket is not None:
        with tab_paket:
            if not is_reguler_active:
                st.markdown("""
                <div style="background-color: #fff3cd; border-left: 5px solid #ffeeba; padding: 12px 15px; margin-bottom: 15px; border-radius: 6px;">
                    <b style="color: #856404; font-size: 16px;">🚧 SOLD OUT / MAINTENANCE:</b><br>
                    <span style="color: #856404; font-size: 14.5px; line-height: 1.5; display: inline-block; margin-top: 5px;">Penjualan Paket Reguler saat ini sedang ditutup sementara untuk menjaga kapasitas server. Silahkan cek kembali nanti atau pilih <b>Paket All-In-One</b>.</span>
                </div>
                """, unsafe_allow_html=True)
                
            st.info("💡 **Bebas ***Stacking*** Paket!** Beli lebih dari 1 paket untuk menumpuk kuota, menggabungkan saldo, dan memperpanjang masa aktif paket hingga maksimal **150 Hari**.")
            
            with st.expander(f"LITE (Maks. 45 Menit/File) - {_fmt_harga_plain(pc['LITE'])}", expanded=False):
                _render_harga(pc['LITE'])
                st.markdown("""
                <div style='font-size: 14px; color: #333;'>
                <div style='margin-bottom: 8px; color: #d68910; font-weight: 500;'><i>Setara ± Rp 215 / menit audio</i></div>
                    <ul style='margin-bottom: 10px;'>
                        <li>📄 <b>Kuota:</b> 3 Dokumen <i>(1 Kuota = 1 File Audio)</i></li>
                        <li>🤝 <b>FUP:</b> 2x Ekstrak AI <b>Per Dokumen</b></li>
                        <li>⏱️ <b>Batas Audio:</b> Maks. 45 Menit / Kuota</li>
                        <li>💬 <b>Chatbot:</b> 2x Tanya AI / Dokumen (Gratis)</li>
                        <li>📅 <b>Masa Aktif:</b> 14 Hari</li>
                        <li>🗑️ <b>Arsip:</b> Sekali pakai (Tanpa riwayat)</li>
                        <li>⚡ <b>Server STT:</b> Prioritas Standar</li>
                        <li>🎁 <b>Bonus Saldo:</b> Rp 2.500</li>
                    </ul>
                </div>
                """, unsafe_allow_html=True)
                if is_reguler_active:
                    if st.button(f"🛒 Beli LITE ({_fmt_harga_plain(pc['LITE'])})", width='stretch', key="buy_lite", type="primary", disabled=is_b2b_active):
                        if not st.session_state.logged_in: st.error("Silahkan Login terlebih dahulu.")
                        else:
                            with st.spinner("Mencetak tagihan..."):
                                link_bayar, order_id = buat_tagihan_duitku("LITE", pc['LITE']['harga'], user_email)
                                if link_bayar: 
                                    db.collection('users').document(user_email).update({"pending_trx": firestore.ArrayUnion([{"order_id": order_id, "paket": "LITE"}])})
                                    st.link_button("💳 Lanjut Bayar", link_bayar, width='stretch')
                else:
                    st.button("🚫 Sedang Ditutup", disabled=True, width='stretch', key="dis_lite")

            with st.expander(f"STARTER (Maks. 60 Menit/File) - {_fmt_harga_plain(pc['STARTER'])}", expanded=False):
                _render_harga(pc['STARTER'])
                st.markdown("""
                <div style='font-size: 14px; color: #333;'>
                <div style='margin-bottom: 8px; color: #d68910; font-weight: 500;'><i>Setara ± Rp 148 / menit audio</i></div>
                    <ul style='margin-bottom: 10px;'>
                        <li>📄 <b>Kuota:</b> 10 Dokumen <i>(1 Kuota = 1 File Audio)</i></li>
                        <li>🤝 <b>FUP:</b> 4x Ekstrak AI <b>Per Dokumen</b></li>
                        <li>⏱️ <b>Batas Audio:</b> Maks. 60 Menit / Kuota</li>
                        <li>💬 <b>Chatbot:</b> 4x Tanya AI / Dokumen (Gratis)</li>
                        <li>📅 <b>Masa Aktif:</b> 30 Hari</li>
                        <li>🗑️ <b>Arsip:</b> Sekali pakai (Tanpa riwayat)</li>
                        <li>⚡ <b>Server STT:</b> Prioritas Standar</li>
                        <li>🎁 <b>Bonus Saldo:</b> Rp 5.000</li>
                    </ul>
                </div>
                """, unsafe_allow_html=True)
                if is_reguler_active:
                    if st.button(f"🛒 Beli STARTER ({_fmt_harga_plain(pc['STARTER'])})", width='stretch', key="buy_starter", type="primary", disabled=is_b2b_active):
                        if not st.session_state.logged_in: st.error("Silahkan Login terlebih dahulu.")
                        else:
                            with st.spinner("Mencetak tagihan..."):
                                link_bayar, order_id = buat_tagihan_duitku("STARTER", pc['STARTER']['harga'], user_email)
                                if link_bayar: 
                                    db.collection('users').document(user_email).update({"pending_trx": firestore.ArrayUnion([{"order_id": order_id, "paket": "STARTER"}])})
                                    st.link_button("💳 Lanjut Bayar", link_bayar, width='stretch')
                else:
                    st.button("🚫 Sedang Ditutup", disabled=True, width='stretch', key="dis_starter")

            with st.expander(f"EKSEKUTIF (Maks. 90 Menit/File) - {_fmt_harga_plain(pc['EKSEKUTIF'])}", expanded=False):
                _render_harga(pc['EKSEKUTIF'])
                st.markdown("""
                <div style='font-size: 14px; color: #333;'>
                <div style='margin-bottom: 8px; color: #d68910; font-weight: 500;'><i>Setara ± Rp 110 / menit audio</i></div>
                    <ul style='margin-bottom: 10px;'>
                        <li>📄 <b>Kuota:</b> 30 Dokumen <i>(1 Kuota = 1 File Audio ATAU 1 File Teks)</i></li>
                        <li>🤝 <b>FUP:</b> 8x Ekstrak AI <b>Per Dokumen</b></li>
                        <li>⏱️ <b>Batas Audio:</b> Maks. 90 Menit / Kuota</li>
                        <li>📝 <b>Batas Teks:</b> Maks. 90.000 Karakter / Kuota</li>
                        <li>💬 <b>Chatbot:</b> 8x Tanya AI / Dokumen (Gratis)</li>
                        <li>📅 <b>Masa Aktif:</b> 45 Hari</li>
                        <li>🗂️ <b>Arsip:</b> Akses riwayat Cloud</li>
                        <li>⚡ <b>Server STT:</b> Prioritas Standar</li>
                        <li>🎁 <b>Bonus Saldo:</b> Rp 15.000</li>
                    </ul>
                </div>
                """, unsafe_allow_html=True)
                if is_reguler_active:
                    if st.button(f"🛒 Beli EKSEKUTIF ({_fmt_harga_plain(pc['EKSEKUTIF'])})", width='stretch', key="buy_exec", type="primary", disabled=is_b2b_active):
                        if not st.session_state.logged_in: st.error("Silahkan Login terlebih dahulu.")
                        else:
                            with st.spinner("Mencetak tagihan..."):
                                link_bayar, order_id = buat_tagihan_duitku("EKSEKUTIF", pc['EKSEKUTIF']['harga'], user_email)
                                if link_bayar: 
                                    db.collection('users').document(user_email).update({"pending_trx": firestore.ArrayUnion([{"order_id": order_id, "paket": "EKSEKUTIF"}])})
                                    st.link_button("💳 Lanjut Bayar", link_bayar, width='stretch')
                else:
                    st.button("🚫 Sedang Ditutup", disabled=True, width='stretch', key="dis_exec")

            with st.expander(f"VIP (Maks. 150 Menit/File) - {_fmt_harga_plain(pc['VIP'])}", expanded=False):
                _render_harga(pc['VIP'])
                st.markdown("""
                <div style='font-size: 14px; color: #333;'>
                <div style='margin-bottom: 8px; color: #d68910; font-weight: 500;'><i>Setara ± Rp 61 / menit audio</i></div>
                    <ul style='margin-bottom: 10px;'>
                        <li>📄 <b>Kuota:</b> 65 Dokumen <i>(1 Kuota = 1 File Audio ATAU 1 File Teks)</i></li>
                        <li>🤝 <b>FUP:</b> 12x Ekstrak AI <b>Per Dokumen</b></li>
                        <li>⏱️ <b>Batas Audio:</b> Maks. 150 Menit / Kuota</li>
                        <li>📝 <b>Batas Teks:</b> Maks. 150.000 Karakter / Kuota</li>
                        <li>💬 <b>Chatbot:</b> 12x Tanya AI / Dokumen (Gratis)</li>
                        <li>⚡ <b>Server Prioritas:</b> Tanpa antrean, akurasi absolut</li>
                        <li>📅 <b>Masa Aktif:</b> 60 Hari</li>
                        <li>🗂️ <b>Arsip:</b> Akses riwayat Cloud</li>
                        <li>⚡ <b>Server STT:</b> Prioritas Server Tertinggi & STT Kilat</li>
                        <li>🎁 <b>Bonus Saldo:</b> Rp 30.000</li>
                    </ul>
                </div>
                """, unsafe_allow_html=True)
                st.markdown("""
                <div style="background-color: #e8f5e9; border-left: 5px solid #2e7d32; padding: 12px 15px; margin-bottom: 15px; border-radius: 6px;">
                    <b style="color: #2e7d32; font-size: 16px;">🔥 PROMO UPGRADE:</b><br>
                    <span style="color: #1b5e20; font-size: 14.5px; line-height: 1.5; display: inline-block; margin-top: 5px;">Beli VIP sekarang, seluruh <b>sisa tiket Lite/Starter/Eksekutif</b> Anda otomatis naik kelas ke Server Prioritas (STT) tanpa biaya tambahan!</span>
                </div>
                """, unsafe_allow_html=True)
                if is_reguler_active:
                    if st.button(f"🛒 Beli VIP ({_fmt_harga_plain(pc['VIP'])})", width='stretch', key="buy_vip", type="primary", disabled=is_b2b_active):
                        if not st.session_state.logged_in: st.error("Silahkan Login terlebih dahulu.")
                        else:
                            with st.spinner("Mencetak tagihan..."):
                                link_bayar, order_id = buat_tagihan_duitku("VIP", pc['VIP']['harga'], user_email)
                                if link_bayar: 
                                    db.collection('users').document(user_email).update({"pending_trx": firestore.ArrayUnion([{"order_id": order_id, "paket": "VIP"}])})
                                    st.link_button("💳 Lanjut Bayar", link_bayar, width='stretch')
                else:
                    st.button("🚫 Sedang Ditutup", disabled=True, width='stretch', key="dis_vip")

            with st.expander(f"ENTERPRISE (Maks. 240 Menit/File) - {_fmt_harga_plain(pc['ENTERPRISE'])}", expanded=False):
                _render_harga(pc['ENTERPRISE'])
                st.markdown("""
                <div style='font-size: 14px; color: #333;'>
                <div style='margin-bottom: 8px; color: #d68910; font-weight: 500;'><i>Setara ± Rp 33 / menit audio</i></div>
                    <ul style='margin-bottom: 10px;'>
                        <li>📄 <b>Kuota:</b> 150 Dokumen <i>(1 Kuota = 1 File Audio ATAU 1 File Teks)</i></li>
                        <li>🤝 <b>FUP:</b> 20x Ekstrak AI <b>Per Dokumen</b></li>
                        <li>⏱️ <b>Batas Audio:</b> Maks. 240 Menit / Kuota (Bebas Hambatan)</li>
                        <li>📝 <b>Batas Teks:</b> Maks. 240.000 Karakter / Kuota</li>
                        <li>💬 <b>Chatbot:</b> 20x Tanya AI / Dokumen (Gratis)</li>
                        <li>⚡ <b>Server Prioritas:</b> Tanpa antrean, akurasi absolut</li>
                        <li>📅 <b>Masa Aktif:</b> 90 Hari</li>
                        <li>🗂️ <b>Arsip:</b> Akses riwayat Cloud</li>
                        <li>⚡ <b>Server STT:</b> Prioritas Server Tertinggi & STT Kilat</li>
                        <li>🎁 <b>Bonus Saldo:</b> Rp 75.000</li>
                    </ul>
                </div>
                """, unsafe_allow_html=True)
                if is_reguler_active:
                    if st.button(f"🛒 Beli ENTERPRISE ({_fmt_harga_plain(pc['ENTERPRISE'])})", width='stretch', key="buy_enterprise", type="primary", disabled=is_b2b_active):
                        if not st.session_state.logged_in: st.error("Silahkan Login terlebih dahulu.")
                        else:
                            with st.spinner("Mencetak tagihan..."):
                                link_bayar, order_id = buat_tagihan_duitku("ENTERPRISE", pc['ENTERPRISE']['harga'], user_email)
                                if link_bayar: 
                                    db.collection('users').document(user_email).update({"pending_trx": firestore.ArrayUnion([{"order_id": order_id, "paket": "ENTERPRISE"}])})
                                    st.link_button("💳 Lanjut Bayar", link_bayar, width='stretch')
                else:
                    st.button("🚫 Sedang Ditutup", disabled=True, width='stretch', key="dis_enterprise")
                    
            st.write("") # Spasi pemisah
            st.warning("⚠️ **Aturan Kuota Reguler (1 File = 1 Kuota):** \nSistem paket reguler memotong kuota berbasis **jumlah dokumen**, bukan akumulasi menit. Sisa menit yang tidak terpakai dari batas maksimal per file tidak dapat diakumulasi.\n\n*(Contoh: Mengupload audio 20 menit pada paket LITE akan tetap memotong 1 Kuota penuh, sisa 25 menit hangus).*\n\n💡 Jika Anda menginginkan pemakaian fleksibel yang diakumulasi tanpa ada sisa menit yang hangus dan jatah FUP Ekstrak AI yang lebih fleksibel, kami sangat menyarankan Anda beralih ke **Paket All-In-One (AIO)**.")

    if tab_saldo is not None:
        with tab_saldo:
            st.info("💡 **Dompet:** Isi ulang saldo utama, atau perpanjang masa aktif paket Anda.")
            
            with st.expander("Top-Up Saldo Reguler", expanded=False):
                st.caption("Isi ulang saldo untuk bayar subsidi kelebihan karakter teks dan Chatbot AI.")
                st.warning("**Catatan:** Harga tagihan sudah termasuk Biaya Layanan Payment Gateway (Flat Rp 3.000).")
                
                col_s1, col_s2 = st.columns(2)
                with col_s1:
                    st.markdown("**Saldo Rp 10.000**")
                    if st.button(f"💳 Bayar {_fmt_harga_plain(pc['Topup10k'])}", width='stretch', key="topup_10", type="primary", disabled=is_b2b_active):
                        if not st.session_state.logged_in: st.error("Silahkan Login terlebih dahulu.")
                        else:
                            with st.spinner("Mencetak tagihan..."):
                                link_bayar, order_id = buat_tagihan_duitku("Topup10k", pc['Topup10k']['harga'], user_email)
                                if link_bayar: 
                                    db.collection('users').document(user_email).update({"pending_trx": firestore.ArrayUnion([{"order_id": order_id, "paket": "Topup10k"}])})
                                    st.link_button("💳 Lanjut Bayar", link_bayar, width='stretch')
                    
                    st.markdown("---")
                    
                    st.markdown("**Saldo Rp 20.000**")
                    if st.button(f"💳 Bayar {_fmt_harga_plain(pc['Topup20k'])}", width='stretch', key="topup_20", type="primary", disabled=is_b2b_active):
                        if not st.session_state.logged_in: st.error("Silahkan Login terlebih dahulu.")
                        else:
                            with st.spinner("Mencetak tagihan..."):
                                link_bayar, order_id = buat_tagihan_duitku("Topup20k", pc['Topup20k']['harga'], user_email)
                                if link_bayar: 
                                    db.collection('users').document(user_email).update({"pending_trx": firestore.ArrayUnion([{"order_id": order_id, "paket": "Topup20k"}])})
                                    st.link_button("💳 Lanjut Bayar", link_bayar, width='stretch')
                                    
                    st.markdown("<div style='margin-bottom: 30px;'></div>", unsafe_allow_html=True)

                with col_s2:
                    st.markdown("**Saldo Rp 30.000**")
                    if st.button(f"💳 Bayar {_fmt_harga_plain(pc['Topup30k'])}", width='stretch', key="topup_30", type="primary", disabled=is_b2b_active):
                        if not st.session_state.logged_in: st.error("Silahkan Login terlebih dahulu.")
                        else:
                            with st.spinner("Mencetak tagihan..."):
                                link_bayar, order_id = buat_tagihan_duitku("Topup30k", pc['Topup30k']['harga'], user_email)
                                if link_bayar: 
                                    db.collection('users').document(user_email).update({"pending_trx": firestore.ArrayUnion([{"order_id": order_id, "paket": "Topup30k"}])})
                                    st.link_button("💳 Lanjut Bayar", link_bayar, width='stretch')
                    
                    st.markdown("---")
                    
                    st.markdown("**Saldo Rp 40.000**")
                    if st.button(f"💳 Bayar {_fmt_harga_plain(pc['Topup40k'])}", width='stretch', key="topup_40", type="primary", disabled=is_b2b_active):
                        if not st.session_state.logged_in: st.error("Silahkan Login terlebih dahulu.")
                        else:
                            with st.spinner("Mencetak tagihan..."):
                                link_bayar, order_id = buat_tagihan_duitku("Topup40k", pc['Topup40k']['harga'], user_email)
                                if link_bayar: 
                                    db.collection('users').document(user_email).update({"pending_trx": firestore.ArrayUnion([{"order_id": order_id, "paket": "Topup40k"}])})
                                    st.link_button("💳 Lanjut Bayar", link_bayar, width='stretch')

            with st.expander("Perpanjang Masa Aktif", expanded=False):
                st.markdown("*Jadwal rapat sedang kosong? Perpanjang napas kuota Anda agar tidak hangus sia-sia.*\n* **Mendapatkan:** Tambahan 30 Hari masa aktif.\n* **Berlaku Untuk:** Seluruh sisa kuota & saldo yang ada di dompet Anda saat ini.")
                if st.button(f"🛒 Beli Ekstensi 30 Hari - {_fmt_harga_plain(pc['EkstensiWaktu'])}", width='stretch', key="buy_ekstensi", type="primary", disabled=is_b2b_active):
                    if not st.session_state.logged_in: st.error("Silahkan Login terlebih dahulu.")
                    else:
                        with st.spinner("Mencetak tagihan..."):
                            link_bayar, order_id = buat_tagihan_duitku("EkstensiWaktu", pc['EkstensiWaktu']['harga'], user_email)
                            if link_bayar: 
                                db.collection('users').document(user_email).update({"pending_trx": firestore.ArrayUnion([{"order_id": order_id, "paket": "EkstensiWaktu"}])})
                                st.link_button("💳 Lanjut Bayar", link_bayar, width='stretch')
                            
    # 🚀 FIX: Sembunyikan Info dan Marketing jika User adalah B2B/B2G
    if not is_b2b_active:
        # KOTAK INFO (DIGABUNG DALAM COLLAPSE BOX MENGGUNAKAN MARKDOWN ASLI)
        st.markdown("---")
        with st.expander("INFO & KETENTUAN PAKET", expanded=False):
            st.markdown("""
            **💡 Ketentuan Sistem & Kuota:**
            * 🥇 **Prioritas Otomatis (AIO):** Jika Anda memiliki Paket AIO dan Reguler secara bersamaan, sistem **selalu memprioritaskan** pemotongan dari Bank Waktu AIO Anda tanpa perlu memilih.
            * ⚖️ **Subsidi Silang (*Fallback*):** Menyambung poin di atas, jika sisa Bank Menit AIO Anda tidak cukup untuk memproses sebuah file, barulah sistem akan otomatis beralih memotong **1 Kuota Reguler** Anda sebagai cadangan.
            * 📄 **Aturan Upload Teks (.txt):** Mengupload dokumen teks manual akan memotong kuota utama Anda.
              👉 **User AIO:** Memotong **Saldo Universal (Menit)** berdasarkan estimasi panjang teks.
              👉 **User Reguler:** Memotong **1 Kuota** (sama seperti mengupload 1 file audio).
            * 🛡️ ***Tier* Tertinggi Selalu Aman:** Anda bebas menumpuk berbagai jenis paket. Sistem selalu memberikan batas *tier* tertinggi berdasarkan paket aktif yang Anda miliki. Membeli paket kecil tidak akan menurunkan status *tier* tinggi Anda.
            * 💬 **Tanya AI (Chatbot):** Jika jatah gratis habis, dikenakan tarif ringan **Rp 1.000 / pertanyaan** (memotong Saldo Utama).

            ---
            
            **👑 Keistimewaan *Tier* AIO:**
            Jika Anda memiliki Bank Menit AIO aktif, seluruh dokumen Anda akan menggunakan **FUP Harian AIO** (Misal: 20x–40x klik/file/hari tergantung paket) yang bebas digunakan untuk file apapun di hari tersebut. Ini sangat menguntungkan dibanding FUP Reguler yang batasannya akan hangus jika Anda berganti file.
            
            *Limit FUP AIO akan di-reset (kembali penuh) secara otomatis setiap jam 00:00 WIB.*
            """)
            
        # KOTAK MARKETING B2B
        st.markdown("""
        <div style="background-color: #f8f9fa; border-left: 5px solid #0056b3; padding: 15px; border-radius: 5px; margin-top: 15px; margin-bottom: 15px;">
            <b>Enterprise Deployment</b><br>
            <span style="font-size: 14px; color: #555;">
            TOM'STT AI dapat diimplementasikan secara kustom untuk kebutuhan instansi/korporasi, termasuk integrasi sistem dan deployment pada infrastruktur terdedikasi. 
            Hubungi tim kami untuk konsultasi dan penawaran resmi 
            <a href="mailto:admin@tom-stt.com?subject=Enterprise%20Deployment%20-%20TOM'STT%20AI" style="color: #0056b3; font-weight: bold; text-decoration: underline;">
            di sini
            </a>.
            </span>
        </div>
        """, unsafe_allow_html=True)
    # 🎯 DUAL-BRANDING NOTE — Tampil untuk SEMUA user (B2B + Reguler)
    # Diletakkan di luar `if not is_b2b_active` agar B2B juga melihat keterangan ini
    st.markdown("""
    <div style="text-align: center; font-size: 11px; color: #888; margin-top: 18px; padding: 10px 12px; border-top: 1px dashed #ddd;">
        Pembayaran diproses oleh <b>TOM'STT AI</b> (tom-stt.com) — produk yang sama dengan <b><a href="https://rapat.co" target="_blank" style="color:#e74c3c; text-decoration:none;">TEMAN RAPAT</a></b> (rapat.co)
    </div>
    """, unsafe_allow_html=True)
