import math
import streamlit as st
import firebase_admin
from firebase_admin import credentials
from firebase_admin import firestore
from firebase_admin import auth
from google.cloud.firestore_v1.base_query import FieldFilter

# --- FIREBASE INITIALIZATION ---
# @st.cache_resource menyimpan koneksi Firestore di memori server.
# Objek ini di-share antar user (aman — Firestore client stateless & thread-safe),
# dan tidak dibuat ulang setiap cold start. Login/cookie/session_state tidak terpengaruh.
@st.cache_resource
def _init_firebase():
    if "firebase" not in st.secrets:
        st.error("⚠️ Kredensial Firebase belum di-set di Streamlit Secrets. Ikuti panduan untuk memasukkan JSON Firebase.")
        st.stop()
    if not firebase_admin._apps:
        cred = credentials.Certificate(dict(st.secrets["firebase"]))
        firebase_admin.initialize_app(cred)
    return firestore.client()

db = _init_firebase()

# --- FUNGSI DATABASE FIREBASE (USER) ---
def get_user(username):
    if not username: return None

    # Pastikan container cache selalu ada di session_state
    # (sebelumnya hanya dicek, tidak pernah dibuat jika belum ada)
    if 'temp_user_data' not in st.session_state:
        st.session_state.temp_user_data = {}

    # ⚡ BACA DARI MEMORI LOKAL (Jika sudah pernah diambil di rerun yang sama)
    if username in st.session_state.temp_user_data:
        return st.session_state.temp_user_data[username]

    # ☁️ JIKA BELUM, AMBIL DARI FIREBASE
    doc = db.collection('users').document(username).get()
    data = doc.to_dict() if doc.exists else None

    # 💾 SIMPAN KE MEMORI LOKAL
    # Guard 'temp_user_data' dihapus karena sudah pasti ada dari blok di atas
    if data:
        st.session_state.temp_user_data[username] = data

    return data

def invalidate_user_cache(username):
    """
    Hapus cache user dari session_state setelah ada write ke Firestore.

    Dipanggil setiap kali data user di Firestore diubah dalam rerun yang sama,
    agar pemanggilan get_user() berikutnya membaca data segar — bukan snapshot lama.

    Aman dipanggil meski temp_user_data belum ada (tidak akan crash).
    """
    if 'temp_user_data' in st.session_state:
        st.session_state.temp_user_data.pop(username, None)

def save_user(username, password, role):
    user_ref = db.collection('users').document(username)
    existing_user = user_ref.get()
    
    if existing_user.exists:
        user_ref.update({"password": password, "role": role})
    else:
        user_ref.set({
            "password": password,
            "role": role,
            "inventori": [],           
            "saldo": 0,
            "bank_menit": 0,                
            "tanggal_expired": "Selamanya",
            "pending_trx": [], 
            "created_at": firestore.SERVER_TIMESTAMP
        })

def delete_user(username):
    # 1. Hapus dari Firebase Auth terlebih dahulu (Krusial agar tidak bisa login lagi)
    try:
        user_record = auth.get_user_by_email(username)
        auth.delete_user(user_record.uid)
    except Exception as e:
        error_msg = str(e).lower()
        # Jika gagal bukan karena user sudah tidak ada, HENTIKAN PROSES!
        if "not_found" not in error_msg and "no user record" not in error_msg:
            import streamlit as st
            st.error(f"Gagal mencabut akses Login (Auth) karena: {e}. Penghapusan dibatalkan.")
            return False 

    # 2. HAPUS SUB-COLLECTION 'history' (PENTING!)
    # Jika tidak dihapus, ID user akan tetap terlihat di Console Firestore sebagai "Dokumen Hantu"
    try:
        history_docs = db.collection('users').document(username).collection('history').stream()
        for doc in history_docs:
            doc.reference.delete()
    except Exception as e:
        pass # Lanjut terus walaupun kosong/error

    # 3. HAPUS DOKUMEN UTAMA PROFIL USER
    # Diletakkan di atas agar dijamin terhapus tanpa menunggu proses eksternal selesai
    try:
        db.collection('users').document(username).delete()
    except Exception as e:
        import streamlit as st
        st.warning(f"Gagal menghapus data profil Firestore: {e}")

    # 4. Hapus data terkait di collection eksternal (Pembersihan Lanjutan)
    try:
        # Daftar collection eksternal yang mungkin menyimpan jejak user.
        collections_to_clean = ["transcriptions", "folders", "transactions", "topup_requests", "chats", "riwayat_ai"]
        
        for col_name in collections_to_clean:
            # Cari dan hapus berdasarkan field 'username'
            docs_by_username = db.collection(col_name).where("username", "==", username).stream()
            for doc in docs_by_username:
                doc.reference.delete()
                
            # Cari dan hapus berdasarkan field 'user_id'
            docs_by_user_id = db.collection(col_name).where("user_id", "==", username).stream()
            for doc in docs_by_user_id:
                doc.reference.delete()
    except Exception as e:
        pass # Abaikan jika gagal agar tidak mengganggu notifikasi sukses utama

    return True

def berikan_paket_ke_user(username, user_data, nama_paket, corporate_name="", order_id_duitku="", security_mode="Normal", billing_profile=None):
    """Menyuntikkan Paket/Saldo saat Duitku bilang 'Lunas'"""
    
    # --- PENCATATAN TOTAL SPENDING (LTV) ---
    # 🚀 Baca harga aktual dari pricing_config (mengikuti harga terbaru jika dev ubah via panel)
    pricing  = get_pricing_config()
    harga_map = {k: v["harga"] for k, v in pricing.items()}
    
    nominal_masuk = harga_map.get(nama_paket, 0)
    # 🚀 FIX: Paksa jadi integer agar kebal crash tipe data teks
    new_spending = int(user_data.get("total_spending", 0)) + nominal_masuk 
    user_data["total_spending"] = new_spending
    db.collection('users').document(username).update({"total_spending": new_spending})

    # 🚀 Pisahkan pencatatan spending B2B agar revenue reguler & B2B tidak campur
    if nama_paket in ["B2B_Standard", "B2B_Ultimate"]:
        new_spending_b2b = int(user_data.get("spending_b2b", 0)) + nominal_masuk
        user_data["spending_b2b"] = new_spending_b2b
        db.collection('users').document(username).update({"spending_b2b": new_spending_b2b})

    # =======================================================
    # 🏛️ FASE 3, 5, & 6: MESIN B2G/B2B (SHARED POOL & TOP-UP)
    # =======================================================
    if nama_paket in ["B2B_Standard", "B2B_Ultimate"]:
        import datetime
        import uuid
        now = datetime.datetime.now(datetime.timezone.utc)
        
        # Penentuan Tangki Berdasarkan Paket
        if nama_paket == "B2B_Standard":
            tambahan_quota = 33000 # 550 jam * 60 menit
            max_users_baru = 15
        else:
            tambahan_quota = 66000 # 1100 jam * 60 menit
            max_users_baru = 30

        # 🚀 Snapshot harga saat transaksi (untuk SPJ akurat tidak berubah meski harga diubah admin)
        pricing_snap = pricing.get(nama_paket, {})

        existing_vid = user_data.get("active_corporate_voucher")
        
        # SKENARIO 1: TOP-UP (Akumulasi Tangki & Masa Aktif)
        if existing_vid:
            v_ref = db.collection('vouchers').document(existing_vid)
            v_doc = v_ref.get()
            if v_doc.exists:
                v_data = v_doc.to_dict()
                
                # Akumulasi Masa Aktif (Maks 365 Hari)
                current_exp = v_data.get("valid_until")
                try:
                    exp_date = current_exp if not isinstance(current_exp, str) else datetime.datetime.fromisoformat(current_exp.replace("Z", "+00:00"))
                    base_date = now if exp_date < now else exp_date
                except: base_date = now
                
                new_exp_date = base_date + datetime.timedelta(days=270)
                max_exp_date = now + datetime.timedelta(days=365)
                if new_exp_date > max_exp_date: new_exp_date = max_exp_date

                # Eksekusi Top-Up
                update_payload_topup = {
                    "shared_quota_minutes": firestore.Increment(tambahan_quota),
                    "max_users": max(v_data.get("max_users", 0), max_users_baru),
                    "valid_until": new_exp_date,
                    "corporate_name": corporate_name if corporate_name else v_data.get("corporate_name", ""),
                    # 🚀 Simpan snapshot harga saat top-up (timpa agar selalu harga pembelian terakhir)
                    "harga_beli":       pricing_snap.get("harga", 0),
                    "harga_coret_beli": pricing_snap.get("harga_coret", 0),
                    "aktif_coret_beli": pricing_snap.get("aktif_coret", False),
                }
                # 🚀 Simpan billing_profile ke voucher (ditimpa agar selalu data PIC terbaru)
                if billing_profile:
                    update_payload_topup["billing_profile"] = billing_profile
                    db.collection('users').document(username).update({"billing_profile": billing_profile})

                v_ref.update(update_payload_topup)
                return user_data

        # SKENARIO 2: BUAT RUMAH BARU UNTUK INSTANSI
        vid = f"CORP-{uuid.uuid4().hex[:8].upper()}"
        new_exp_date = now + datetime.timedelta(days=270)
        voucher_payload = {
            "order_id": order_id_duitku if order_id_duitku else f"INV-MANUAL-{vid}",
            "tipe_voucher": "B2G/B2B Shared Pool",
            "tipe": "B2B_CORPORATE",
            "corporate_name": corporate_name,
            "admin_email": username,
            "shared_quota_minutes": tambahan_quota,
            "used_quota_minutes": 0,
            "total_documents_generated": 0,
            "max_users": max_users_baru,
            "valid_until": new_exp_date,
            "security_mode": security_mode,
            # 🚀 Snapshot harga saat beli pertama (untuk SPJ akurat)
            "harga_beli":       pricing_snap.get("harga", 0),
            "harga_coret_beli": pricing_snap.get("harga_coret", 0),
            "aktif_coret_beli": pricing_snap.get("aktif_coret", False),
            "spj_documents": {},
            "created_at": firestore.SERVER_TIMESTAMP
        }
        # 🚀 Sertakan billing_profile ke voucher baru jika tersedia
        if billing_profile:
            voucher_payload["billing_profile"] = billing_profile
            db.collection('users').document(username).update({"billing_profile": billing_profile})

        db.collection('vouchers').document(vid).set(voucher_payload)

        # Suntik DNA B2B Admin ke Profil PIC
        user_data["is_b2g_admin"] = True
        user_data["active_corporate_voucher"] = vid
        db.collection('users').document(username).update({
            "is_b2g_admin": True,
            "active_corporate_voucher": vid
        })
        
        return user_data

    # --- 🛡️ FASE 1 (BLUEPRINT 2026): CONFIG KASTA & METADATA ---
    # fup_per_file = jatah klik AI per sesi (Reguler)
    # fup_harian = jatah klik AI per hari (AIO)
    config = {
        "LITE": {
            "nama": "LITE", "kuota": 3, "hari": 14, "bonus": 2500, 
            "limit_audio": 45, "limit_teks": 45000, "fup_per_file": 2
        },
        "STARTER": {
            "nama": "STARTER", "kuota": 10, "hari": 30, "bonus": 5000, 
            "limit_audio": 60, "limit_teks": 60000, "fup_per_file": 4
        },
        "EKSEKUTIF": {
            "nama": "EKSEKUTIF", "kuota": 30, "hari": 45, "bonus": 15000, 
            "limit_audio": 90, "limit_teks": 90000, "fup_per_file": 8
        },
        "VIP": {
            "nama": "VIP", "kuota": 65, "hari": 60, "bonus": 30000, 
            "limit_audio": 150, "limit_teks": 150000, "fup_per_file": 12
        },
        "ENTERPRISE": {
            "nama": "ENTERPRISE", "kuota": 150, "hari": 90, "bonus": 75000, 
            "limit_audio": 240, "limit_teks": 240000, "fup_per_file": 20
        },
        "AIO10": {
            "nama": "AIO 10 JAM", "kuota": 9999, "hari": 30, "bonus": 10000, 
            "bank_menit": 600, "fup_harian": 35, "limit_audio": 9999, "limit_teks": 999999
        },
        "AIO30": {
            "nama": "AIO 30 JAM", "kuota": 9999, "hari": 60, "bonus": 25000, 
            "bank_menit": 1800, "fup_harian": 50, "limit_audio": 9999, "limit_teks": 999999
        },
        "AIO100": {
            "nama": "AIO 100 JAM", "kuota": 9999, "hari": 90, "bonus": 75000, 
            "bank_menit": 6000, "fup_harian": 75, "limit_audio": 9999, "limit_teks": 999999
        }
    }

    # --- 🛡️ FASE 4: LOGIKA ADD-ON ECERAN & TOP-UP ---
    if nama_paket.startswith("Topup") or nama_paket == "EkstensiWaktu":
        import datetime
        now = datetime.datetime.now(datetime.timezone.utc)
        
        # ADD-ON: Ekstensi Waktu (+30 Hari, Tanpa tambah tiket)
        if nama_paket == "EkstensiWaktu":
            current_exp = user_data.get("tanggal_expired")
            if current_exp and current_exp != "Selamanya":
                try:
                    exp_date = current_exp if not isinstance(current_exp, str) else datetime.datetime.fromisoformat(current_exp.replace("Z", "+00:00"))
                    base_date = now if exp_date < now else exp_date
                except: base_date = now
            else: base_date = now
            
            new_exp_date = base_date + datetime.timedelta(days=30)
            if new_exp_date > now + datetime.timedelta(days=150): new_exp_date = now + datetime.timedelta(days=150)
            user_data["tanggal_expired"] = new_exp_date
            db.collection('users').document(username).update({"tanggal_expired": new_exp_date})
            return user_data
        
        # 3. ADD-ON: Saldo Reguler
        else:
            nominal = 0
            if nama_paket == "Topup10k": nominal = 10000
            elif nama_paket == "Topup20k": nominal = 20000
            elif nama_paket == "Topup30k": nominal = 30000
            elif nama_paket == "Topup40k": nominal = 40000
            
            new_saldo = user_data.get("saldo", 0) + nominal
            user_data["saldo"] = new_saldo
            db.collection('users').document(username).update({"saldo": new_saldo})
            return user_data

    # JIKA PEMBELIAN PAKET UTAMA
    if nama_paket in config:
        cfg = config[nama_paket]
        import datetime
        now = datetime.datetime.now(datetime.timezone.utc)
        current_exp = user_data.get("tanggal_expired")
        
        if current_exp and current_exp != "Selamanya":
            try:
                exp_date = current_exp if not isinstance(current_exp, str) else datetime.datetime.fromisoformat(current_exp.replace("Z", "+00:00"))
                base_date = now if exp_date < now else exp_date
            except: base_date = now
        else: base_date = now

        new_exp_date = base_date + datetime.timedelta(days=cfg["hari"])
        if new_exp_date > now + datetime.timedelta(days=150): new_exp_date = now + datetime.timedelta(days=150)

        inventori = user_data.get("inventori", [])
        ditemukan = False
        for pkt in inventori:
            # 🚀 FIX: Gunakan .get() dan panggil 'limit_audio' agar kebal KeyError
            if pkt.get('nama', '').upper() == cfg.get('nama', '').upper() and pkt.get('batas_durasi') == cfg.get('limit_audio'):
                pkt['kuota'] += cfg.get('kuota', 0)
                ditemukan = True
                break
                
        if not ditemukan:
            # Tetap simpan dengan nama huruf besar jika buat baru
            inventori.append({
                "nama": cfg.get('nama', ''), 
                "kuota": cfg.get('kuota', 0), 
                "batas_durasi": cfg.get('limit_audio', 45)
            })

        new_saldo = user_data.get("saldo", 0) + cfg.get('bonus', 0)
        
        # Injeksi Bank Menit jika ini adalah paket AIO
        new_bank_menit = user_data.get("bank_menit", 0) + cfg.get('bank_menit', 0)

        # --- FASE 1: DYNAMIC SCANNER (ANTI-CRASH & BACA KASTA TERKINI) ---
        # 1. Pastikan semua default dijadikan Integer
        max_aud = int(cfg.get("limit_audio", 45))
        max_txt = int(cfg.get("limit_teks", 45000))
        max_fup = int(cfg.get("fup_per_file", 2))
        max_fup_h = int(cfg.get("fup_harian", 0))
        
        # 2. Pindai dompet mencari kasta tertinggi DARI TIKET YANG BELUM HABIS
        for pkt in inventori:
            p_nama = pkt.get("nama", "").upper()
            if int(pkt.get("kuota", 0)) > 0 or "AIO" in p_nama: 
                if "ENTERPRISE" in p_nama: max_aud = max(max_aud, 240); max_txt = max(max_txt, 240000); max_fup = max(max_fup, 20)
                elif "VIP" in p_nama: max_aud = max(max_aud, 150); max_txt = max(max_txt, 150000); max_fup = max(max_fup, 12)
                elif "EKSEKUTIF" in p_nama: max_aud = max(max_aud, 90); max_txt = max(max_txt, 90000); max_fup = max(max_fup, 8)
                elif "STARTER" in p_nama: max_aud = max(max_aud, 60); max_txt = max(max_txt, 60000); max_fup = max(max_fup, 4)
                
                if "AIO" in p_nama: 
                    max_aud = 9999; max_txt = 999999
                    if "100" in p_nama: max_fup_h = max(max_fup_h, 40)
                    elif "30" in p_nama: max_fup_h = max(max_fup_h, 30)
                    else: max_fup_h = max(max_fup_h, 20)

        update_data = {
            "inventori": inventori, 
            "saldo": int(new_saldo), 
            "tanggal_expired": new_exp_date, 
            "bank_menit": int(new_bank_menit),
            "batas_audio_menit": max_aud,
            "batas_teks_karakter": max_txt,
            "fup_dok_per_file": max_fup,
            "fup_dok_harian_limit": max_fup_h,
            # 🛒 Catat pembelian terakhir untuk sorting di admin panel
            "last_purchase_at":    firestore.SERVER_TIMESTAMP,
            "last_purchase_nama":  nama_paket,
            "last_purchase_harga": nominal_masuk,
        }

        user_data.update(update_data)
        db.collection('users').document(username).update(update_data)
        
    return user_data
    
def cek_status_pembayaran_duitku(username, user_data):
    """Menanyakan ke Duitku status tagihan yang gantung"""
    import hashlib
    import requests
    import streamlit as st
    
    pending_trx = user_data.get("pending_trx", [])
    if not pending_trx: return user_data

    # 🔒 MENGAMBIL KUNCI PRODUCTION DARI BRANKAS RAHASIA STREAMLIT (SECRETS)
    try:
        merchant_code = st.secrets["duitku"]["merchant_code"]
        api_key = st.secrets["duitku"]["api_key"]
    except KeyError:
        print("⚠️ Kunci API Duitku belum dikonfigurasi di Streamlit Secrets!")
        return user_data

    # 🚀 URL API Status Duitku (PRODUCTION)
    url = "https://api-prod.duitku.com/api/merchant/transactionStatus"

    sisa_pending = []
    ada_perubahan = False
    for trx in pending_trx:
        order_id       = trx.get("order_id")
        paket          = trx.get("paket")
        corporate_name = trx.get("corporate_name", "")
        sec_mode       = trx.get("security_mode", "Normal")
        # 🚀 Ambil billing_profile yang dititipkan saat checkout
        billing_prof   = trx.get("billing_profile", None)
 
        sign_str  = merchant_code + order_id + api_key
        signature = hashlib.md5(sign_str.encode('utf-8')).hexdigest()
 
        try:
            res    = requests.post(url, json={"merchantCode": merchant_code, "merchantOrderId": order_id, "signature": signature}).json()
            status = res.get("statusCode")
 
            if status == "00": # LUNAS
                # 🚀 Teruskan billing_profile ke berikan_paket_ke_user
                user_data = berikan_paket_ke_user(username, user_data, paket, corporate_name, order_id, sec_mode, billing_profile=billing_prof)
                st.toast(f"Tagihan {paket} Lunas! Paket/Saldo ditambahkan.", icon="✔")
                ada_perubahan = True
            elif status in ["01", "02"]: # PENDING
                sisa_pending.append(trx)
            else: # EXPIRED
                st.toast(f"⚠️ Tagihan {paket} kadaluarsa/dibatalkan.", icon="❌")
                ada_perubahan = True
        except Exception as e:
            print(f"Error Duitku Polling: {e}")
            sisa_pending.append(trx)
    if ada_perubahan:
        user_data["pending_trx"] = sisa_pending
        db.collection('users').document(username).update({"pending_trx": sisa_pending})
        
        # 🚀 AUTO-REFRESH DOMPET JIKA ADA TAGIHAN LUNAS
        if 'temp_user_data' in st.session_state:
            del st.session_state['temp_user_data']
        st.rerun() # 🚀 FIX: Paksa muat ulang layar seketika agar dompet otomatis berubah!
        
    return user_data
    
def check_expired(username, user_data):
    """SATPAM: Mengecek kedaluwarsa & MIGRASI OTOMATIS data lama."""
    if not user_data or user_data.get("role") == "admin": return user_data 
    
    # 1. AUTO-MIGRASI DATA LAMA KE FORMAT INVENTORI
    if "paket_aktif" in user_data and "inventori" not in user_data:
        paket_lama = user_data.get("paket_aktif", "Freemium")
        kuota_lama = user_data.get("kuota", 0)
        batas_lama = user_data.get("batas_durasi", 10)
        
        inventori_baru = []
        if paket_lama != "Freemium" and kuota_lama > 0:
            inventori_baru.append({"nama": paket_lama, "kuota": kuota_lama, "batas_durasi": batas_lama})
        user_data["inventori"] = inventori_baru
    
    # 2. CEK KEDALUWARSA GLOBAL
    exp_val = user_data.get("tanggal_expired")
    if exp_val and exp_val != "Selamanya":
        import datetime
        now = datetime.datetime.now(datetime.timezone.utc)
        try:
            exp_date = datetime.datetime.fromisoformat(exp_val.replace("Z", "+00:00")) if isinstance(exp_val, str) else exp_val
            if now > exp_date:
                st.toast("⚠️ Masa aktif habis. Inventori, Saldo & Bank Waktu di-reset.", icon="🚨")
                # 🚀 FIX: Turunkan kembali batas kasta ke titik terendah (Freemium)
                reset_kasta = {
                    "inventori": [], "saldo": 0, "bank_menit": 0, "tanggal_expired": firestore.DELETE_FIELD,
                    "batas_audio_menit": 45, "batas_teks_karakter": 45000, "fup_dok_per_file": 2, "fup_dok_harian_limit": 0
                }
                db.collection('users').document(username).update(reset_kasta)
                user_data.update(reset_kasta)
                user_data.pop("tanggal_expired", None)
        except: pass
            
    return user_data
    
def hitung_estimasi_menit(teks):
    """Menghitung estimasi dengan perlindungan Anti-Spacing Hack"""
    if not teks: return 0
    jumlah_kata = len(teks.split())
    jumlah_karakter = len(teks)
    
    # 🛡️ ANTI-SPACING HACK: Jika 1 kata > 15 huruf (Dimanipulasi)
    if jumlah_kata > 0 and (jumlah_karakter / jumlah_kata) > 15:
        jumlah_kata = math.ceil(jumlah_karakter / 7) # Paksa hitung per 7 huruf
        
    durasi = math.ceil(jumlah_kata / 130)
    return durasi if durasi > 0 else 1
    
def cek_pembayaran_teks(user_data, jumlah_karakter, index_paket):
    """🛡️ Sistem Limit Berjenjang & Subsidi Silang untuk Upload Teks (.TXT)"""
    if user_data.get("role") == "admin": return True, "Akses Admin (Gratis)", 0
        
    saldo = user_data.get("saldo", 0)
    inventori = user_data.get("inventori", [])
    TARIF_PER_5K = 500 # Tarif baru: Rp 500 per 5.000 karakter ekstra
    
    # 1. Tentukan Soft Limit berdasarkan Kasta Paket tertinggi
    soft_limit = 75000 # Default Freemium/Lite
    for pkt in inventori:
        nama_pkt_up = pkt["nama"].upper()
        if "ENTERPRISE" in nama_pkt_up: soft_limit = max(soft_limit, 400000)
        elif "VIP" in nama_pkt_up: soft_limit = max(soft_limit, 300000)
        elif "EKSEKUTIF" in nama_pkt_up: soft_limit = max(soft_limit, 200000)
        elif "STARTER" in nama_pkt_up or "PRO" in nama_pkt_up: soft_limit = max(soft_limit, 100000)
        
    kelebihan = max(0, jumlah_karakter - soft_limit)
    biaya_subsidi = math.ceil(kelebihan / 5000) * TARIF_PER_5K # Dibagi 5.000

    if index_paket == -1: # Tanpa paket
        biaya_murni = math.ceil(jumlah_karakter / 5000) * TARIF_PER_5K
        if saldo >= biaya_murni: return True, f"Saldo terpotong Rp {biaya_murni:,}", biaya_murni
        else: return False, f"Saldo kurang. Butuh Rp {biaya_murni:,}", 0
    
    if 0 <= index_paket < len(inventori):
        paket = inventori[index_paket]
        if kelebihan <= 0:
            return True, f"1 Kuota '{paket['nama']}' Terpakai.", 0
        else:
            if saldo >= biaya_subsidi: 
                return True, f"1 Kuota '{paket['nama']}' + Saldo Rp {biaya_subsidi:,} (Subsidi Teks Ekstra).", biaya_subsidi
            else: 
                return False, f"Saldo kurang! Teks kelebihan {kelebihan:,} huruf. Butuh tambahan Rp {biaya_subsidi:,}.", 0
            
    return False, "Sistem Gagal Membaca Paket.", 0

def cek_pembayaran(user_data, durasi_menit, index_paket):
    """Mengecek kesanggupan bayar berdasarkan pilihan Dropdown User (Support Dompet Hibrida)."""
    if user_data.get("role") == "admin": return True, "Akses Admin (Gratis)", 0
        
    saldo = user_data.get("saldo", 0)
    inventori = user_data.get("inventori", [])
    bank_menit = user_data.get("bank_menit", 0)
    TARIF = 350
    
    # Skenario 1: Bayar Pakai Saldo Murni
    if index_paket == -1:
        biaya = durasi_menit * TARIF
        if saldo >= biaya: return True, f"Saldo terpotong Rp {biaya:,}", biaya
        else: return False, f"Saldo kurang. Butuh Rp {biaya:,}", 0
    
    # Skenario 2: Bayar Pakai Inventori Paket / All-In-One + Subsidi Silang
    if 0 <= index_paket < len(inventori):
        paket = inventori[index_paket]
        batas = paket.get("batas_durasi", 10)
        
        # 🚀 JIKA INI PAKET ALL-IN-ONE (Ditandai dengan batas 9999)
        if batas == 9999:
            if bank_menit > 0:
                return True, f"{paket['nama']} (Akses AI Ekstrak Gratis).", 0
            else:
                return False, "⚠️ Bank Waktu AIO Anda telah habis. Silahkan perpanjang paket.", 0
        
        # 📦 JIKA INI PAKET REGULER
        else:
            if durasi_menit <= batas:
                return True, f"📦 1 Kuota '{paket['nama']}' Terpakai.", 0
            else:
                biaya_subsidi = (durasi_menit - batas) * TARIF
                if saldo >= biaya_subsidi: return True, f"📦 1 Kuota '{paket['nama']}' + Saldo Rp {biaya_subsidi:,} terpakai.", biaya_subsidi
                else: return False, f"Saldo kurang untuk bayar kelebihan waktu (Butuh Rp {biaya_subsidi:,}).", 0
            
    return False, "Sistem Gagal Membaca Paket.", 0

def eksekusi_pembayaran(username, user_data_lama, index_paket, potong_saldo, durasi_menit=0):
    """Mengeksekusi pemotongan secara presisi dengan Anti-Race Condition (Support AIO)."""
    if user_data_lama.get("role") == "admin": return 
    
    user_ref = db.collection('users').document(username)
    
    # 🛡️ GEMBOK TRANSAKSI (Mencegah pencurian tiket via multi-tab)
    @firestore.transactional
    def update_in_transaction(transaction, ref):
        snapshot = ref.get(transaction=transaction)
        if not snapshot.exists: return
        user_data = snapshot.to_dict()
        
        new_saldo = user_data.get("saldo", 0) - potong_saldo
        updates = {"saldo": new_saldo}
        
        if index_paket != -1:
            inventori = user_data.get("inventori", [])
            if 0 <= index_paket < len(inventori):
                paket = inventori[index_paket]
                # 🚀 LOGIKA ALL-IN-ONE (Gratis Sepuasnya)
                if paket.get("batas_durasi") == 9999:
                    pass # Tidak memotong saldo atau bank menit karena Ekstrak AI adalah Gratis
                # 📦 LOGIKA REGULER (Potong Kuota 1x Ekstrak)
                else:
                    inventori[index_paket]["kuota"] -= 1
                    if inventori[index_paket]["kuota"] <= 0:
                        inventori.pop(index_paket) 
                    updates["inventori"] = inventori
                
        transaction.update(ref, updates)
        
    transaction = db.transaction()
    update_in_transaction(transaction, user_ref)
    
def redeem_voucher(username, kode_voucher):
    """Mengecek dan mengeksekusi voucher dengan aman, menambah masa aktif max 150 hari, dan memberikan BONUS SALDO."""
    kode_voucher = kode_voucher.upper().strip()
    v_ref = db.collection('vouchers').document(kode_voucher)
    v_doc = v_ref.get()
    
    if not v_doc.exists:
        return False, "❌ Voucher tidak ditemukan atau salah ketik."
        
    v_data = v_doc.to_dict()
    
    # 1. Cek Kuota & Riwayat (Sistem Anti-Curang)
    if v_data.get('jumlah_terklaim', 0) >= v_data.get('max_klaim', 1):
        return False, "❌ Kuota klaim voucher ini sudah habis."
    # Cek apakah username sudah ada di riwayat (Mendukung format lama & format baru ber-tanggal)
    sudah_klaim = any(username == r.split(" (")[0] for r in v_data.get('riwayat_pengguna', []))
    if sudah_klaim:
        return False, "❌ Anda sudah pernah mengklaim voucher ini."
        
    user_ref = db.collection('users').document(username)
    
    # 2. Transaksi Aman 
    @firestore.transactional
    def eksekusi_klaim(transaction, user_ref, v_ref):
        u_snap = user_ref.get(transaction=transaction)
        u_data = u_snap.to_dict()
        v_latest = v_ref.get(transaction=transaction).to_dict()
        
        # ====================================================
        # 🚀 JALUR 1: LISENSI MASTER B2B (CORPORATE)
        # ====================================================
        if v_latest.get('tipe') == 'B2B_CORPORATE':
            import datetime
            now = datetime.datetime.now(datetime.timezone.utc)
            wib_tz = datetime.timezone(datetime.timedelta(hours=7))
            waktu_wib = now.astimezone(wib_tz).strftime("%d %b %Y, %H:%M WIB")
            klaim_str = f"{username} ({waktu_wib})"
            
            corp_name = v_latest.get('corporate_name', 'Instansi')
            staff_usage = v_latest.get('staff_usage', {})
            
            # Masukkan PIC (Pengklaim Pertama) ke dalam tangki instansi
            if username not in staff_usage:
                staff_usage[username] = {"minutes_used": 0, "docs_generated": 0, "ai_generated": 0}
            
            # Suntik DNA B2B ke profil user (Menjadikannya PIC / Admin Instansi)
            transaction.update(user_ref, {
                "is_b2g_admin": True,
                "active_corporate_voucher": v_latest['kode_voucher']
            })
            
            # Segel Voucher agar tidak bisa diklaim orang lain lagi, dan catat siapa PIC-nya
            transaction.update(v_ref, {
                "jumlah_terklaim": firestore.Increment(1), 
                "riwayat_pengguna": firestore.ArrayUnion([klaim_str]),
                "staff_usage": staff_usage,
                "admin_email": username
            })
            
            pesan_sukses = f"🏛️ Lisensi B2B untuk **{corp_name}** berhasil diaktifkan! Anda kini adalah Admin Instansi."
            return True, pesan_sukses

        # ====================================================
        # 🚀 JALUR 2: VOUCHER REGULER / PUBLIK
        # ====================================================
        # Ambil Saldo Saat Ini
        current_saldo = u_data.get("saldo", 0)
        
        # Hitung Tanggal Expired
        import datetime
        now = datetime.datetime.now(datetime.timezone.utc)
        current_exp = u_data.get("tanggal_expired")
        
        if current_exp and current_exp != "Selamanya":
            try:
                exp_date = current_exp if not isinstance(current_exp, str) else datetime.datetime.fromisoformat(current_exp.replace("Z", "+00:00"))
                base_date = now if exp_date < now else exp_date
            except: base_date = now
        else:
            base_date = now
            
        # --- 🚀 FIX 1: TENTUKAN TAMBAHAN HARI & BONUS SALDO (100% SINKRON DENGAN HARGA ASLI) ---
        # Menggunakan .get() sebagai tameng ekstra agar kebal dari error KeyError di masa depan
        nama_pkt_v = v_latest.get('nama_paket', 'LITE').upper()
        
        hari_tambah = 14
        bonus_saldo = 2500  # Default LITE (Disamakan dengan asli)
        
        if "STARTER" in nama_pkt_v: 
            hari_tambah = 30; bonus_saldo = 5000
        elif "EKSEKUTIF" in nama_pkt_v: 
            hari_tambah = 45; bonus_saldo = 15000
        elif "VIP" in nama_pkt_v: 
            hari_tambah = 60; bonus_saldo = 30000 # Disamakan dengan asli
        elif "ENTERPRISE" in nama_pkt_v: 
            hari_tambah = 90; bonus_saldo = 75000 # Disamakan dengan asli
        elif "AIO 100" in nama_pkt_v:  # 🚀 FIX: Cek "AIO 100" SEBELUM "AIO 10" — "AIO 10" adalah substring dari "AIO 100 JAM"!
            hari_tambah = 90; bonus_saldo = 75000
        elif "AIO 30" in nama_pkt_v: 
            hari_tambah = 60; bonus_saldo = 25000
        elif "AIO 10" in nama_pkt_v: 
            hari_tambah = 30; bonus_saldo = 10000 # Injeksi Logika AIO yang terlewat
        
        # Kalkulasi Expired (Maks 150 Hari)
        new_exp_date = base_date + datetime.timedelta(days=hari_tambah)
        max_exp_date = now + datetime.timedelta(days=150)
        if new_exp_date > max_exp_date: new_exp_date = max_exp_date
            
        # Suntikkan Paket ke Array Inventori
        inventori = u_data.get("inventori", [])
        ditemukan = False
        for pkt in inventori:
            if pkt['nama'].upper() == nama_pkt_v and pkt['batas_durasi'] == v_latest['batas_durasi']:
                pkt['kuota'] += v_latest['kuota_paket']
                ditemukan = True
                break
        if not ditemukan:
            inventori.append({"nama": nama_pkt_v, "kuota": v_latest['kuota_paket'], "batas_durasi": v_latest['batas_durasi']})
            
        # Eksekusi Pembaruan Saldo & Bank Menit
        new_saldo = current_saldo + bonus_saldo
        new_bank_menit = u_data.get("bank_menit", 0) + v_latest.get('bank_menit', 0)
        
        # --- 🚀 FIX 2: UPDATE KASTA LIMIT AUDIO, TEKS & FUP (AGAR USER VOUCHER OTOMATIS NAIK KELAS) ---
        max_aud = 45
        max_txt = 45000
        max_fup = 2
        max_fup_h = 0
        
        for pkt in inventori:
            p_nama = pkt.get("nama", "").upper()
            if int(pkt.get("kuota", 0)) > 0 or "AIO" in p_nama: 
                if "ENTERPRISE" in p_nama: max_aud = max(max_aud, 240); max_txt = max(max_txt, 240000); max_fup = max(max_fup, 20)
                elif "VIP" in p_nama: max_aud = max(max_aud, 150); max_txt = max(max_txt, 150000); max_fup = max(max_fup, 12)
                elif "EKSEKUTIF" in p_nama: max_aud = max(max_aud, 90); max_txt = max(max_txt, 90000); max_fup = max(max_fup, 8)
                elif "STARTER" in p_nama: max_aud = max(max_aud, 60); max_txt = max(max_txt, 60000); max_fup = max(max_fup, 4)
                
                if "AIO" in p_nama: 
                    max_aud = 9999; max_txt = 999999
                    if "100" in p_nama: max_fup_h = max(max_fup_h, 40)
                    elif "30" in p_nama: max_fup_h = max(max_fup_h, 30)
                    else: max_fup_h = max(max_fup_h, 20)
        
        # Format Waktu Klaim (WIB)
        wib_tz = datetime.timezone(datetime.timedelta(hours=7))
        waktu_wib = now.astimezone(wib_tz).strftime("%d %b %Y, %H:%M WIB")
        klaim_str = f"{username} ({waktu_wib})"
        
        transaction.update(user_ref, {
            "inventori": inventori, 
            "tanggal_expired": new_exp_date,
            "saldo": new_saldo,
            "bank_menit": new_bank_menit,
            "batas_audio_menit": max_aud,      # INJEKSI LIMIT BARU
            "batas_teks_karakter": max_txt,    # INJEKSI LIMIT BARU
            "fup_dok_per_file": max_fup,       # INJEKSI FUP BARU
            "fup_dok_harian_limit": max_fup_h  # INJEKSI FUP HARIAN BARU
        })
        transaction.update(v_ref, {"jumlah_terklaim": firestore.Increment(1), "riwayat_pengguna": firestore.ArrayUnion([klaim_str])})
        
        pesan_sukses = f"Paket {v_latest['nama_paket']} + Bonus Saldo Rp {bonus_saldo:,} berhasil ditambahkan!"
        return True, pesan_sukses.replace(',', '.')
        
    transaction = db.transaction()
    try:
        success, msg = eksekusi_klaim(transaction, user_ref, v_ref)
        return success, msg
    except Exception as e:
        return False, f"Terjadi kesalahan sistem: {str(e)}"
        
# --- FUNGSI DATABASE FIREBASE (API KEYS & LOAD BALANCER) ---
@st.cache_data(ttl=60)
def get_all_api_keys():
    """Ambil semua API keys dari Firestore. Cached 60 detik.
    Gunakan get_all_api_keys.clear() setelah mutasi (add/update/delete key)."""
    docs = db.collection('api_keys').stream()
    return [{"id": doc.id, **doc.to_dict()} for doc in docs]

def add_api_key(name, provider, key_string, limit):
    import datetime
    wib_tz = datetime.timezone(datetime.timedelta(hours=7))
    today_str = datetime.datetime.now(wib_tz).strftime("%Y-%m-%d")
    
    db.collection('api_keys').add({
        "name": name,
        "provider": provider,
        "key": key_string,
        "limit": int(limit),
        "used": 0,
        "is_active": True,
        "last_reset_date": today_str
    })

def delete_api_key(doc_id):
    db.collection('api_keys').document(doc_id).delete()

def toggle_api_key(doc_id, current_status):
    db.collection('api_keys').document(doc_id).update({"is_active": not current_status})

def increment_api_usage(doc_id, current_used, count=1):
    """Increment API usage counter secara atomic. count>1 untuk Vision (weighted)."""
    db.collection('api_keys').document(doc_id).update({"used": firestore.Increment(count)})

def get_active_keys(provider):
    import datetime

    wib_tz = datetime.timezone(datetime.timedelta(hours=7))
    now_wib = datetime.datetime.now(wib_tz)

    # Reset jam berbeda per provider (sesuai jadwal RPD Google/Groq/Cohere)
    # Gemini  → 15:00 WIB (Google Pacific Time reset)
    # Groq    → 07:00 WIB (00:00 UTC)
    # Cohere  → 07:00 WIB (00:00 UTC)
    if provider == "Gemini":
        reset_hour = 15
    else:
        reset_hour = 7

    today_str     = now_wib.strftime('%Y-%m-%d')
    reset_hour_str = str(reset_hour)

    keys_ref = db.collection('api_keys').where(filter=FieldFilter("provider", "==", provider)).where(filter=FieldFilter("is_active", "==", True)).stream()
    valid_keys = []
    now_utc = datetime.datetime.now(datetime.timezone.utc)

    for doc in keys_ref:
        data = doc.to_dict()
        doc_id = doc.id

        # 🚀 LAZY RESET: dua kondisi reset —
        # 1. Tanggal berbeda (hari berganti)
        # 2. Tanggal sama tapi jam reset belum dicatat (baru melewati jam reset hari ini)
        last_date = data.get('last_reset_date', '')
        last_hour = data.get('last_reset_hour', '')
        should_reset = False

        if last_date != today_str:
            # Hari berganti — selalu reset
            should_reset = True
        elif last_hour != reset_hour_str and now_wib.hour >= reset_hour:
            # Hari sama, tapi baru melewati jam reset hari ini
            should_reset = True

        if should_reset:
            db.collection('api_keys').document(doc_id).update({
                "used": 0,
                "last_reset_date": today_str,
                "last_reset_hour": reset_hour_str
            })
            data['used'] = 0
            data['last_reset_date'] = today_str
            data['last_reset_hour'] = reset_hour_str

        # ⏳ Skip key yang sedang rate-limited sementara (429 RPM)
        rate_limited_until = data.get('rate_limited_until')
        if rate_limited_until:
            try:
                if isinstance(rate_limited_until, datetime.datetime):
                    exp = rate_limited_until
                    if exp.tzinfo is None:
                        exp = exp.replace(tzinfo=datetime.timezone.utc)
                    if now_utc < exp:
                        continue   # masih dalam window 60 detik, skip
                    else:
                        # Window sudah lewat — bersihkan field ini
                        db.collection('api_keys').document(doc_id).update({
                            "rate_limited_until": firestore.DELETE_FIELD
                        })
            except Exception:
                pass

        data['id'] = doc_id
        if data['used'] < data['limit']:
            valid_keys.append(data)

    # 🔢 Prioritas: free tier dulu (is_paid=False), baru berbayar
    # Di dalam tier yang sama, sort by used ascending (paling sedikit dipakai duluan)
    valid_keys.sort(key=lambda x: (x.get('is_paid', False), x.get('used', 0)))
    return valid_keys


def mark_key_rate_limited(doc_id: str, seconds: int = 60):
    """
    Tandai key sebagai rate-limited sementara (429 RPM).
    Key akan otomatis aktif kembali setelah `seconds` detik.
    Dipanggil dari app.py sebagai pengganti set used=limit.
    """
    import datetime
    until = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=seconds)
    try:
        db.collection('api_keys').document(doc_id).update({
            "rate_limited_until": until
        })
    except Exception:
        pass
	
@st.cache_data(ttl=60)
def get_system_config():
    """Mengambil pengaturan global dari Firestore (Sakelar Groq & Feature Flags)"""

    default_config = {
        "use_groq_stt": False, 
        "groq_b2b_admin_bypass": True, # 🚀 SAKELAR BARU BYPASS B2B & ADMIN
        "groq_model": "whisper-large-v3", 
        "allowed_packages": ["EKSEKUTIF", "VIP", "ENTERPRISE", "AIO 30 JAM", "AIO 100 JAM"],
        "is_aio_active": True,
        "is_rekam_active": True,
        "is_reguler_active": True,
        "is_b2b_sys_active": True, # 🚀 FIX: Mendaftarkan default memori untuk sakelar B2B
        "archive_allowed_packages": ["EKSEKUTIF", "VIP", "ENTERPRISE", "AIO 10 JAM", "AIO 30 JAM", "AIO 100 JAM"], # 🚀 LACI BARU HAK ARSIP
        "is_announcement_active": False,
        "ann_title": "📢 Pengumuman Sistem",
        "ann_body": "",
        "ann_points": ["", "", "", "", ""],
        "ann_btn_text": "",
        "ann_btn_url": "",
        "ann_timestamp": "",
        # --- TAMBAHAN BLUEPRINT POP-UP ---
        "is_popup_active": False,
        "vision_allowed_packages": [],   # Kosong = hanya B2B/B2G by default
        "vision_api_weight": 5,          # 1 panggilan Vision = 5 panggilan di load balancer
        "is_vision_active": True,        # Sakelar on/off tab Upload Gambar
        "is_custom_template_active": True, # Sakelar on/off fitur AI Custom Template
        "is_engine_gemini_active": True,   # Sakelar tampilkan/sembunyikan pilihan Gemini
        "is_engine_groq_active": True,     # Sakelar tampilkan/sembunyikan pilihan Groq
        "is_engine_cohere_active": True,   # Sakelar tampilkan/sembunyikan pilihan Cohere
        "popup_image_url": "",
        "popup_target_url": "",
        "popup_version": 1
    }

    try:
        doc = db.collection('settings').document('system_config').get()
        if doc.exists:
            data = doc.to_dict()
            for key, val in default_config.items():
                if key not in data:
                    data[key] = val
            return data
        else:
            db.collection('settings').document('system_config').set(default_config)
            return default_config
    except:
        return default_config

@st.cache_data(ttl=60)
def get_pricing_config():
    """
    Mengambil konfigurasi harga dari Firestore.
    Di-cache 60 detik. Panggil get_pricing_config.clear() setelah admin ubah harga.

    Struktur per paket:
      harga       -> harga aktual yang ditagihkan ke buyer
      harga_coret -> harga lama yang ditampilkan dicoret (jika promo aktif)
      aktif_coret -> bool, apakah mode harga coret sedang aktif
    """
    DEFAULT_PRICING = {
        "LITE":          {"harga": 29000,   "harga_coret": 0, "aktif_coret": False},
        "STARTER":       {"harga": 89000,   "harga_coret": 0, "aktif_coret": False},
        "EKSEKUTIF":     {"harga": 299000,  "harga_coret": 0, "aktif_coret": False},
        "VIP":           {"harga": 599000,  "harga_coret": 0, "aktif_coret": False},
        "ENTERPRISE":    {"harga": 1199000, "harga_coret": 0, "aktif_coret": False},
        "AIO10":         {"harga": 189000,  "harga_coret": 0, "aktif_coret": False},
        "AIO30":         {"harga": 489000,  "harga_coret": 0, "aktif_coret": False},
        "AIO100":        {"harga": 1299000, "harga_coret": 0, "aktif_coret": False},
        "B2B_Standard":  {"harga": 5900000, "harga_coret": 0, "aktif_coret": False},
        "B2B_Ultimate":  {"harga": 9900000, "harga_coret": 0, "aktif_coret": False},
        "EkstensiWaktu": {"harga": 38000,   "harga_coret": 0, "aktif_coret": False},
        "Topup10k":      {"harga": 13000,   "harga_coret": 0, "aktif_coret": False},
        "Topup20k":      {"harga": 23000,   "harga_coret": 0, "aktif_coret": False},
        "Topup30k":      {"harga": 33000,   "harga_coret": 0, "aktif_coret": False},
        "Topup40k":      {"harga": 43000,   "harga_coret": 0, "aktif_coret": False},
    }
    try:
        doc = db.collection('settings').document('pricing_config').get()
        if doc.exists:
            data = doc.to_dict()
            for key, val in DEFAULT_PRICING.items():
                if key not in data:
                    data[key] = val
                else:
                    for subkey, subval in val.items():
                        if subkey not in data[key]:
                            data[key][subkey] = subval
            return data
        else:
            db.collection('settings').document('pricing_config').set(DEFAULT_PRICING)
            return DEFAULT_PRICING
    except:
        return DEFAULT_PRICING

# =============================================================================
# RECORDER TOKEN — Desktop Recorder Auth
# =============================================================================
import secrets as _secrets
import datetime as _dt

_RECORDER_TTL_MENIT = 15


def _cek_punya_paket_aktif(user_info: dict) -> tuple:
    if not user_info:
        return False, ""
    if user_info.get("role") == "admin":
        return True, "Admin"
    vid = user_info.get("active_corporate_voucher")
    if vid:
        try:
            v_doc = db.collection("vouchers").document(vid).get()
            if v_doc.exists:
                v_data = v_doc.to_dict() or {}
                corp = v_data.get("corporate_name", "B2B/B2G")
                return True, corp
        except Exception:
            pass
        return True, "B2B/B2G"
    inventori = user_info.get("inventori", [])
    if inventori:
        nama_paket = inventori[-1].get("nama", "Premium") if inventori else "Premium"
        return True, nama_paket
    return False, ""


def generate_recorder_token(uid: str, user_info: dict) -> dict:
    boleh, nama_paket = _cek_punya_paket_aktif(user_info)
    if not boleh:
        return {"error": "Tidak ada paket aktif. Upgrade paket untuk menggunakan Desktop Recorder."}
    nama = user_info.get("nama", "")
    if not nama:
        nama = uid.split("@")[0].replace(".", " ").title()
    token = _secrets.token_hex(4).upper()
    expires_at = _dt.datetime.now(_dt.timezone.utc) + _dt.timedelta(minutes=_RECORDER_TTL_MENIT)
    try:
        db.collection("recorder_tokens").document(token).set({
            "uid":        uid,
            "nama":       nama,
            "paket":      nama_paket,
            "status":     "active",
            "created_at": firestore.SERVER_TIMESTAMP,
            "expires_at": expires_at,
        })
        return {"token": token, "nama": nama, "paket": nama_paket}
    except Exception as e:
        return {"error": f"Gagal membuat token: {str(e)[:80]}"}


def validate_recorder_token(token: str) -> dict:
    if not token or len(token) < 4:
        return None
    try:
        doc = db.collection("recorder_tokens").document(token.upper()).get()
        if not doc.exists:
            return None
        data = doc.to_dict() or {}
        if data.get("status") != "active":
            return None
        expires_at = data.get("expires_at")
        if expires_at:
            now = _dt.datetime.now(_dt.timezone.utc)
            exp = expires_at
            if isinstance(exp, _dt.datetime) and exp.tzinfo is None:
                exp = exp.replace(tzinfo=_dt.timezone.utc)
            if isinstance(exp, _dt.datetime) and now > exp:
                db.collection("recorder_tokens").document(token.upper()).delete()
                return None
        return {"status": "active", "nama": data.get("nama", "User"), "paket": data.get("paket", "-")}
    except Exception:
        return None


def cleanup_expired_recorder_tokens():
    """
    Hapus token recorder yang sudah expired.
    Panggil sesekali dari panel admin untuk bersih-bersih Firestore.
    """
    try:
        now  = _dt.datetime.now(_dt.timezone.utc)
        docs = db.collection("recorder_tokens").stream()
        deleted = 0
        for doc in docs:
            data = doc.to_dict() or {}
            exp  = data.get("expires_at")
            if exp:
                if isinstance(exp, _dt.datetime) and exp.tzinfo is None:
                    exp = exp.replace(tzinfo=_dt.timezone.utc)
                if isinstance(exp, _dt.datetime) and now > exp:
                    doc.reference.delete()
                    deleted += 1
        return deleted
    except Exception:
        return 0
