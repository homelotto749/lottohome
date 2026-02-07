import os
import io
import json
import random
import uuid
from datetime import datetime

# Веб-сервер
from flask import Flask, render_template, request, redirect, url_for, session, flash
import requests

# База данных
import firebase_admin
from firebase_admin import credentials, firestore, auth

# Графика и QR
from PIL import Image, ImageDraw, ImageFont
import qrcode

# Штрих-коды
import barcode
from barcode.writer import ImageWriter

# Облако
import cloudinary
import cloudinary.uploader

app = Flask(__name__)
app.secret_key = os.environ.get('FLASK_SECRET_KEY', 'super_secret_key')

# ==========================================
# 1. НАСТРОЙКИ
# ==========================================

# Firebase
if not firebase_admin._apps:
    if 'FIREBASE_CRED_JSON' in os.environ:
        cred_dict = json.loads(os.environ['FIREBASE_CRED_JSON'])
        cred = credentials.Certificate(cred_dict)
    else:
        cred_path = "cred.json"
        if os.path.exists(cred_path):
            cred = credentials.Certificate(cred_path)
        else:
            cred = None
            print("CRITICAL: Ключ Firebase не найден!")

    if cred:
        firebase_admin.initialize_app(cred)

db = firestore.client()
FIREBASE_API_KEY = os.environ.get('FIREBASE_API_KEY', 'LOCAL_KEY')

# Cloudinary
cloudinary.config(
  cloud_name = os.environ.get('CLOUDINARY_CLOUD_NAME'),
  api_key = os.environ.get('CLOUDINARY_API_KEY'),
  api_secret = os.environ.get('CLOUDINARY_API_SECRET')
)

# ==========================================
# 2. ГЕНЕРАЦИЯ КАРТИНОК
# ==========================================

def create_ticket_image(ticket_data, tr_id, broadcast_link=None):
    """
    Рисует билет.
    tr_id - ID покупки (для штрих-кода).
    """
    width, height = 650, 280
    img = Image.new('RGB', (width, height), color='white')
    draw = ImageDraw.Draw(img)
    primary_color = "#4B0082" 
    
    # Шрифты
    try:
        font_path = os.path.join(os.path.dirname(__file__), 'font.ttf')
        font_header = ImageFont.truetype(font_path, 28)
        font_text = ImageFont.truetype(font_path, 18)
        font_nums = ImageFont.truetype(font_path, 24)
        font_small = ImageFont.truetype(font_path, 12)
        font_id = ImageFont.truetype(font_path, 14) # Шрифт для ID покупки
    except:
        font_header = ImageFont.load_default()
        font_text = ImageFont.load_default()
        font_nums = ImageFont.load_default()
        font_small = ImageFont.load_default()
        font_id = ImageFont.load_default()

    # Дизайн
    draw.rectangle([(0, 0), (width, 60)], fill=primary_color)
    draw.text((20, 15), "HOMELOTO 7/49", font=font_header, fill="white")
    
    full_ticket_id = f"{ticket_data['draw_id']}-{ticket_data['ticket_number']}"
    draw.text((450, 20), f"#{full_ticket_id}", font=font_header, fill="white")
    
    date_text = str(ticket_data.get('draw_date', '---')).replace('T', ' ')
    draw.text((20, 70), f"Тираж: {ticket_data['draw_id']}", font=font_text, fill="black")
    draw.text((150, 70), f"Дата: {date_text}", font=font_text, fill="black")
    draw.text((20, 100), f"Цена: 100 руб", font=font_text, fill="black")
    
    # Числа
    numbers = ticket_data['numbers']
    start_x, start_y, gap = 30, 160, 65
    for i, num in enumerate(numbers):
        x = start_x + (i * gap)
        y = start_y
        draw.ellipse([x, y, x+50, y+50], outline=primary_color, width=3)
        if hasattr(draw, 'textlength'):
             txt_w = draw.textlength(str(num), font=font_nums)
             txt_x = x + (50 - txt_w) / 2
        else:
             txt_x = x + 15
        draw.text((txt_x, y + 12), str(num), font=font_nums, fill="black")

    # --- ШТРИХ-КОД (Кодируем ID покупки) ---
    try:
        rv = io.BytesIO()
        Code128 = barcode.get_barcode_class('code128')
        # Кодируем tr_id (цифры)
        my_barcode = Code128(tr_id, writer=ImageWriter())
        my_barcode.write(rv, options={'text_distance': 1, 'module_height': 8, 'write_text': False})
        rv.seek(0)
        
        bc_img = Image.open(rv).rotate(90, expand=True)
        bc_img.thumbnail((60, 200)) # Чуть уже
        img.paste(bc_img, (580, 70))
        
        # Пишем ID покупки вертикально рядом
        txt_img = Image.new('RGBA', (200, 30), (255, 255, 255, 0))
        txt_draw = ImageDraw.Draw(txt_img)
        txt_draw.text((0, 0), f"Check: {tr_id}", font=font_id, fill="black")
        txt_rotated = txt_img.rotate(90, expand=True)
        img.paste(txt_rotated, (550, 70), txt_rotated)

    except Exception as e:
        print(f"Barcode Error: {e}")
        draw.rectangle([(580, 70), (620, 200)], outline="#eee")

    # QR
    if broadcast_link:
        try:
            qr = qrcode.make(broadcast_link).resize((80, 80))
            img.paste(qr, (450, 70))
            draw.text((450, 155), "Live", font=font_small, fill="black")
        except: pass

    # Загрузка
    img_byte_arr = io.BytesIO()
    img.save(img_byte_arr, format='PNG')
    img_byte_arr.seek(0)
    
    try:
        res = cloudinary.uploader.upload(img_byte_arr, folder="homeloto_tickets")
        return res['secure_url']
    except Exception as e:
        print(f"Cloudinary Error: {e}")
        return "https://via.placeholder.com/650x280?text=Error+Cloudinary"

def create_receipt_image(transaction_id, items, total, date_str, address_text=""):
    width, height = 300, 450 + (len(items) * 20)
    img = Image.new('RGB', (width, height), color='white')
    draw = ImageDraw.Draw(img)
    try: font = ImageFont.truetype(os.path.join(os.path.dirname(__file__), 'font.ttf'), 14)
    except: font = ImageFont.load_default()
    
    y = 20
    draw.text((80, y), "HOMELOTO CHECK", font=font, fill="black"); y += 30
    if address_text:
        for i in range(0, len(address_text), 30):
            draw.text((20, y), address_text[i:i+30], font=font, fill="black"); y += 15
        y += 15
    draw.text((20, y), f"Date: {date_str}", font=font, fill="black"); y += 20
    draw.text((20, y), f"ID: {transaction_id}", font=font, fill="black"); y += 30
    
    for item in items:
        draw.text((20, y), f"#{item['num']} (T-{item['draw']}) 100r", font=font, fill="black"); y += 20
    draw.text((20, y+20), f"TOTAL: {total} RUB", font=font, fill="black")
    
    qr = qrcode.make(f"CHECK:{transaction_id}").resize((100, 100))
    img.paste(qr, (100, y+50))
    
    buf = io.BytesIO()
    img.save(buf, format='PNG'); buf.seek(0)
    try: return cloudinary.uploader.upload(buf, folder="homeloto_receipts")['secure_url']
    except: return ""

# ==========================================
# 3. МАРШРУТЫ (ROUTES)
# ==========================================

# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---
def get_transaction_details(tr_id):
    """Ищет транзакцию и подгружает данные билетов"""
    tr_doc = db.collection('transactions').document(tr_id).get()
    if not tr_doc.exists: return None
    
    tr_data = tr_doc.to_dict()
    ticket_ids = tr_data.get('tickets', [])
    
    tickets_info = []
    for tid in ticket_ids:
        t_doc = db.collection('tickets').document(tid).get()
        if t_doc.exists:
            t_data = t_doc.to_dict()
            t_data['id'] = tid
            tickets_info.append(t_data)
    return tickets_info

# --- ГЛАВНЫЕ СТРАНИЦЫ ---

@app.route('/')
def index():
    if 'user_id' in session: return render_template('index.html', role=session.get('role'), email=session.get('email'))
    return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form['email']; password = request.form['password']
        url = f"https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key={FIREBASE_API_KEY}"
        try:
            r = requests.post(url, json={"email": email, "password": password, "returnSecureToken": True}).json()
            if 'error' in r: return render_template('login.html', error="Неверный email или пароль")
            uid = r['localId']
            u = db.collection('users').document(uid).get()
            role = u.to_dict().get('role', 'none') if u.exists else 'none'
            session['user_id'] = uid; session['role'] = role; session['email'] = email
            return redirect(url_for('index'))
        except: return render_template('login.html', error="Ошибка входа")
    return render_template('login.html')

@app.route('/register', methods=['POST'])
def register():
    try:
        u = auth.create_user(email=request.form['email'], password=request.form['password'])
        db.collection('users').document(u.uid).set({'email': request.form['email'], 'role': 'none'})
        return render_template('login.html', error="Регистрация успешна! Ждите активации.")
    except: return render_template('login.html', error="Ошибка регистрации")

@app.route('/logout')
def logout(): session.clear(); return redirect(url_for('login'))

# --- ОРГАНИЗАТОР ---

@app.route('/organizer')
def organizer_panel():
    if session.get('role') not in ['org', 'admin']: return redirect(url_for('index'))
    dl = [{'id': d.id, **d.to_dict()} for d in db.collection('draws').stream()]
    dl.sort(key=lambda x: x['id'], reverse=True)
    return render_template('organizer.html', email=session.get('email'), draws=dl)

@app.route('/create_draw', methods=['POST'])
def create_draw():
    did = request.form['draw_id']
    if db.collection('draws').document(did).get().exists: 
        flash(f'Тираж {did} уже существует!', 'error'); return redirect(url_for('organizer_panel'))
    
    cnt = int(request.form['ticket_count'])
    db.collection('draws').document(did).set({
        'date': request.form['draw_date'], 'jackpot': int(request.form['jackpot']),
        'total_tickets': cnt, 'broadcast_link': request.form.get('broadcast_link', ''),
        'status': 'open', 'winning_numbers': []
    })
    batch = db.batch()
    for i in range(1, cnt+1):
        nums = sorted(random.sample(range(1, 50), 7))
        batch.set(db.collection('tickets').document(f"{did}-{i:03d}"), {
            'draw_id': did, 'ticket_number': f"{i:03d}", 'numbers': nums,
            'status': 'available', 'price': 100, 'win_amount': 0, 'draw_date': request.form['draw_date']
        })
    batch.commit()
    flash('Тираж создан!', 'success')
    return redirect(url_for('organizer_panel'))

@app.route('/play_draw/<draw_id>')
def play_draw_page(draw_id): return render_template('play_draw.html', draw_id=draw_id)

@app.route('/run_draw_logic', methods=['POST'])
def run_draw_logic():
    did = request.form['draw_id']; wn = sorted([int(request.form[f'n{i}']) for i in range(1, 8)])
    dr = db.collection('draws').document(did); jp = dr.get().to_dict().get('jackpot', 10000)
    batch = db.batch(); wins = 0
    for t in db.collection('tickets').where('draw_id', '==', did).where('status', '==', 'sold').stream():
        mt = len(set(t.to_dict()['numbers']).intersection(set(wn)))
        pz = {2:100, 3:500, 4:2000, 5:10000, 6:50000, 7:jp}.get(mt, 0)
        if pz > 0: wins += 1
        batch.update(db.collection('tickets').document(t.id), {'matches_count': mt, 'win_amount': pz, 'status': 'checked'})
    batch.update(dr, {'status': 'closed', 'winning_numbers': wn}); batch.commit()
    flash(f'Тираж завершен! Победителей: {wins}', 'success'); return redirect(url_for('organizer_panel'))

@app.route('/draw_details/<draw_id>')
def draw_details(draw_id):
    if session.get('role') not in ['org', 'admin']: return redirect(url_for('index'))
    dr = db.collection('draws').document(draw_id).get().to_dict(); dr['id'] = draw_id
    tkts = []
    for t in db.collection('tickets').where('draw_id', '==', draw_id).where('win_amount', '>', 0).stream():
        d = t.to_dict(); d['id'] = t.id
        if request.args.get('matches') and request.args.get('matches') != 'all':
            if str(d.get('matches_count')) != request.args.get('matches'): continue
        tkts.append(d)
    tkts.sort(key=lambda x: x['win_amount'], reverse=True)
    return render_template('draw_details.html', draw=dr, tickets=tkts)

# --- КАССИР ---

@app.route('/cashier')
def cashier_panel():
    if session.get('role') not in ['cass', 'admin']: return redirect(url_for('index'))
    draws = [{'id': d.id, **d.to_dict()} for d in db.collection('draws').where('status', '==', 'open').stream()]
    sel = request.args.get('draw_id'); tkts = []
    if sel:
        res = db.collection('tickets').where('draw_id', '==', sel).where('status', '==', 'available').stream()
        tkts = sorted([{'id': t.id, **t.to_dict()} for t in res], key=lambda x: x['ticket_number'])
    return render_template('cashier.html', email=session.get('email'), draws=draws, current_draw=sel, tickets=tkts)

@app.route('/buy_tickets', methods=['POST'])
def buy_tickets():
    if session.get('role') not in ['cass', 'admin']: return redirect(url_for('index'))
    
    ids = request.form.getlist('ticket_ids')
    draw_id = request.form.get('draw_id')
    
    if not ids: return redirect(url_for('cashier_panel', draw_id=draw_id))
    
    # ID транзакции
    tr_id = datetime.now().strftime("%Y%m%d%H%M%S") + str(random.randint(10, 99))
    now = datetime.now()
    batch = db.batch()
    sold_data = []
    
    # Продажа билетов
    for tid in ids:
        d = db.collection('tickets').document(tid).get().to_dict(); d['id'] = tid
        sold_data.append(d)
        batch.update(db.collection('tickets').document(tid), {
            'status': 'sold', 'purchase_date': now, 'transaction_id': tr_id, 
            'payment_method': request.form['payment_method'], 'sold_by': session.get('email')
        })
    batch.commit()
    
    # Генерация картинок билетов
    draw_info = db.collection('draws').document(draw_id).get().to_dict()
    broadcast_link = draw_info.get('broadcast_link')
    imgs = [create_ticket_image(t, tr_id, broadcast_link) for t in sold_data]
    
    # --- ИЗМЕНЕНИЕ: ПОЛУЧЕНИЕ АДРЕСА ---
    # Берем адрес из профиля КАССИРА (того, кто сейчас в сессии)
    user_doc = db.collection('users').document(session['user_id']).get()
    shop_addr = user_doc.to_dict().get('shop_address', 'Адрес не указан')
    
    # Генерируем чек с ЭТИМ адресом
    rec_url = create_receipt_image(
        tr_id, 
        [{'num': t['ticket_number'], 'draw': t['draw_id']} for t in sold_data], 
        len(ids)*100, 
        now.strftime("%Y-%m-%d %H:%M"), 
        shop_addr # <-- Передаем личный адрес
    )
    
    # Сохраняем в историю
    db.collection('transactions').document(tr_id).set({
        'id': tr_id, 'date': now, 'amount': len(ids)*100, 'seller': session.get('email'),
        'tickets': ids, 'ticket_urls': imgs, 'receipt_url': rec_url
    })
    
    return render_template('print_view.html', tickets_imgs=imgs, receipt_img=rec_url)

# --- НОВЫЕ ФУНКЦИИ (ИСТОРИЯ И ВЫПЛАТА ПО ШТРИХУ) ---

@app.route('/cashier_history')
def cashier_history():
    if session.get('role') not in ['cass', 'admin']: return redirect(url_for('index'))
    stream = db.collection('transactions').where('seller', '==', session.get('email')).stream()
    history = []
    for doc in stream:
        d = doc.to_dict()
        d['date_str'] = d['date'].strftime("%Y-%m-%d %H:%M") if d.get('date') else "---"
        history.append(d)
    history.sort(key=lambda x: x.get('date_str', ''), reverse=True)
    return render_template('cashier_history.html', transactions=history)

@app.route('/reprint/<tr_id>')
def reprint(tr_id):
    if session.get('role') not in ['cass', 'admin']: return redirect(url_for('index'))
    doc = db.collection('transactions').document(tr_id).get()
    if not doc.exists: return "Чек не найден"
    d = doc.to_dict()
    return render_template('print_view.html', tickets_imgs=d.get('ticket_urls', []), receipt_img=d.get('receipt_url', ''))

@app.route('/payout_scan_page')
def payout_scan_page():
    if session.get('role') not in ['cass', 'admin']: return redirect(url_for('index'))
    return render_template('payout_scan.html')

@app.route('/payout_scan_check', methods=['POST'])
def payout_scan_check():
    if session.get('role') not in ['cass', 'admin']: return redirect(url_for('index'))
    tr_id = request.form['tr_id'].strip()
    tickets = get_transaction_details(tr_id)
    if tickets is None: return render_template('payout_scan.html', error="Чек не найден!", tr_id=tr_id)
    return render_template('payout_scan.html', result=True, tr_id=tr_id, tickets=tickets)

@app.route('/payout_from_scan', methods=['POST'])
def payout_from_scan():
    if session.get('role') not in ['cass', 'admin']: return redirect(url_for('index'))
    ticket_id = request.form['ticket_id']
    tr_id = request.form['tr_id']
    ref = db.collection('tickets').document(ticket_id)
    if ref.get().to_dict().get('status') != 'paid':
        ref.update({'status': 'paid', 'paid_at': datetime.now(), 'paid_by': session.get('email')})
        flash(f'Выплачено: {ticket_id}', 'success')
    return render_template('payout_scan.html', result=True, tr_id=tr_id, tickets=get_transaction_details(tr_id))

# Старые маршруты проверки (оставили на всякий случай)
@app.route('/check_ticket_page')
def check_ticket_page(): return render_template('check_ticket.html')

@app.route('/check_ticket', methods=['POST'])
def check_ticket():
    tid = request.form['ticket_full_id'].strip()
    doc = db.collection('tickets').document(tid).get()
    if not doc.exists: return render_template('check_ticket.html', message="Не найден!", searched_id=tid)
    return render_template('check_ticket.html', ticket={**doc.to_dict(), 'id': tid}, searched_id=tid)

@app.route('/payout', methods=['POST'])
def payout():
    db.collection('tickets').document(request.form['ticket_id']).update({'status': 'paid'})
    return redirect(url_for('check_ticket_page'))

@app.route('/settings')
def settings():
    # Пускаем кассира и админа
    if session.get('role') not in ['cass', 'admin']: return redirect(url_for('index'))
    
    # Берем адрес из профиля ТЕКУЩЕГО пользователя
    user_doc = db.collection('users').document(session['user_id']).get()
    
    # Если адреса нет, будет пустая строка
    current_address = user_doc.to_dict().get('shop_address', '')
    
    return render_template('settings.html', address=current_address)

@app.route('/save_settings', methods=['POST'])
def save_settings():
    if session.get('role') not in ['cass', 'admin']: return redirect(url_for('index'))
    
    new_address = request.form['shop_address']
    
    # Сохраняем адрес В ПРОФИЛЬ пользователя (merge=True, чтобы не стереть роль и email)
    db.collection('users').document(session['user_id']).set(
        {'shop_address': new_address}, 
        merge=True
    )
    
    flash('Ваш адрес торговой точки сохранен!', 'success')
    return redirect(url_for('settings'))

@app.route('/save_settings', methods=['POST'])
def save_settings():
    db.collection('config').document('main').set({'shop_address': request.form['shop_address']}, merge=True)
    return redirect(url_for('settings'))

# --- АНАЛИТИКА ОРГАНИЗАТОРА ---

@app.route('/org_stats')
def org_stats():
    if session.get('role') not in ['org', 'admin']: return redirect(url_for('index'))
    
    # Считаем продажи по каждому продавцу
    # (Это не супер-оптимально для 1млн записей, но для старта пойдет)
    transactions = db.collection('transactions').stream()
    sellers = {}
    
    for tr in transactions:
        d = tr.to_dict()
        email = d.get('seller', 'Неизвестно')
        if email not in sellers:
            sellers[email] = {'email': email, 'count': 0, 'total': 0}
        
        # Считаем билеты в транзакции
        tickets_count = len(d.get('tickets', []))
        sellers[email]['count'] += tickets_count
        sellers[email]['total'] += d.get('amount', 0)
        
    return render_template('organizer_stats.html', sellers=list(sellers.values()))

@app.route('/draw_map/<draw_id>')
def draw_map(draw_id):
    if session.get('role') not in ['org', 'admin']: return redirect(url_for('index'))
    
    # Получаем ВСЕ билеты тиража
    tickets_stream = db.collection('tickets').where('draw_id', '==', draw_id).stream()
    tickets = sorted([t.to_dict() for t in tickets_stream], key=lambda x: x['ticket_number'])
    
    return render_template('organizer_stats.html', draw_id=draw_id, tickets=tickets)

@app.route('/seller_history/<email>')
def seller_history(email):
    if session.get('role') not in ['org', 'admin']: return redirect(url_for('index'))
    
    # Используем шаблон кассира, но показываем данные другого человека
    stream = db.collection('transactions').where('seller', '==', email).stream()
    history = []
    for doc in stream:
        d = doc.to_dict()
        d['date_str'] = d['date'].strftime("%Y-%m-%d %H:%M") if d.get('date') else "---"
        history.append(d)
    history.sort(key=lambda x: x.get('date_str', ''), reverse=True)
    
    return render_template('cashier_history.html', transactions=history)

if __name__ == '__main__':
    app.run(debug=True)


