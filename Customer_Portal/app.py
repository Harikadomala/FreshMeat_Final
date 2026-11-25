import os
import math
import random
import pymysql
from dotenv import load_dotenv
from pymysql.cursors import DictCursor
import smtplib
from email.message import EmailMessage
from datetime import datetime
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
from werkzeug.security import generate_password_hash, check_password_hash
from dotenv import load_dotenv
load_dotenv()

print("ENV FILE LOADED:", os.path.exists(".env"))
print("MAIL USERNAME:", bool(os.getenv("MAIL_USERNAME")))
print("MAIL PASSWORD:", bool(os.getenv("MAIL_PASSWORD")))

app = Flask(__name__)
app.secret_key = 'premium_fresh_meat_secret_key_2026'
# MySQL connection settings are read from environment variables.
# Create the database once in MySQL: CREATE DATABASE premium_meat_store;
class HybridRow(dict):
    def __getitem__(self, key):
        if isinstance(key, int):
            return list(self.values())[key]
        return super().__getitem__(key)


class MySQLCursor:
    def __init__(self, cursor):
        self.cursor = cursor

    def execute(self, sql, params=None):
        sql = sql.replace(
            'INSERT OR IGNORE INTO',
            'INSERT IGNORE INTO'
        ).replace('?', '%s')

        self.cursor.execute(sql, params or ())
        return self

    def executemany(self, sql, params):
        sql = sql.replace(
            'INSERT OR IGNORE INTO',
            'INSERT IGNORE INTO'
        ).replace('?', '%s')

        self.cursor.executemany(sql, params)
        return self

    def fetchone(self):
        row = self.cursor.fetchone()
        return HybridRow(row) if row is not None else None

    def fetchall(self):
        return [
            HybridRow(row)
            for row in self.cursor.fetchall()
        ]

    @property
    def lastrowid(self):
        return self.cursor.lastrowid

    @property
    def rowcount(self):
        return self.cursor.rowcount


class MySQLConnection:
    def __init__(self, conn):
        self.conn = conn

    def cursor(self):
        return MySQLCursor(self.conn.cursor())

    def execute(self, sql, params=None):
        cur = self.cursor()
        cur.execute(sql, params)
        return cur

    def commit(self):
        return self.conn.commit()

    def rollback(self):
        return self.conn.rollback()

    def close(self):
        return self.conn.close()




def get_db():
    print("Password loaded:", bool(os.getenv("DB_PASSWORD")))

    return MySQLConnection(pymysql.connect(
        host=os.getenv('DB_HOST', 'localhost'),
        user=os.getenv('DB_USER', 'root'),
        password=os.getenv('DB_PASSWORD'),
        database=os.getenv('DB_NAME', 'premiumfreshmeat'),
        port=int(os.getenv('DB_PORT', '3306')),
        charset='utf8mb4',
        cursorclass=DictCursor,
        autocommit=False
    ))



def send_email_otp(recipient, otp):
    host = os.getenv('MAIL_SERVER', 'smtp.gmail.com')
    port = int(os.getenv('MAIL_PORT', '587'))

    sender = os.getenv('MAIL_USERNAME', '').strip()
    password = os.getenv('MAIL_PASSWORD', '').strip()
    from_email = os.getenv('MAIL_FROM', sender).strip()

    if not sender or not password:
        raise RuntimeError(
            'Email configuration missing. Check MAIL_USERNAME and MAIL_PASSWORD in .env'
        )

    message = EmailMessage()
    message['Subject'] = 'Your Premium Fresh Meat Verification OTP'
    message['From'] = from_email
    message['To'] = recipient.strip()

    message.set_content(f"""
Hello,

Your OTP for Premium Fresh Meat registration is: {otp}

This OTP is valid for 5 minutes.

Please do not share this OTP with anyone.

If you did not request this, please ignore this email.

Thank you,
Premium Fresh Meat Team
""")

    with smtplib.SMTP(host, port, timeout=20) as server:
        server.ehlo()
        server.starttls()
        server.ehlo()
        server.login(sender, password)
        server.send_message(message)

    print(f"OTP email sent successfully to {recipient}")


def init_db():
    conn = get_db()
    c = conn.cursor()

    # 1. Customer accounts table
    c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INT AUTO_INCREMENT PRIMARY KEY,
            name VARCHAR(255) NOT NULL,
            mobile VARCHAR(30) UNIQUE NOT NULL,
            email VARCHAR(255) UNIQUE NOT NULL,
            password TEXT NOT NULL,
            role VARCHAR(20) NOT NULL DEFAULT 'customer'
        )
    """)

    # 2. Customer delivery addresses
    c.execute("""
        CREATE TABLE IF NOT EXISTS addresses (
            id INT AUTO_INCREMENT PRIMARY KEY,
            user_id INT,
            label VARCHAR(100),
            full_address TEXT,
            city VARCHAR(255),
            pincode VARCHAR(20),
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    """)

    # 3. Customer wishlist
    # Product IDs refer to the existing shared products table.
    c.execute("""
        CREATE TABLE IF NOT EXISTS wishlist (
            id INT AUTO_INCREMENT PRIMARY KEY,
            user_id INT NOT NULL,
            product_id BIGINT NOT NULL,
            UNIQUE (user_id, product_id),
            FOREIGN KEY (user_id) REFERENCES users(id),
            FOREIGN KEY (product_id) REFERENCES products(id)
        )
    """)

    conn.commit()
    conn.close()


init_db()

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            flash('Please login to continue.', 'warning')
            return redirect(url_for('auth', mode='login'))
        return f(*args, **kwargs)
    return decorated_function

def get_discounted_price(product):
    return float(product['price_per_kg'])

def get_user_wishlist_ids():
    if 'user_id' in session:
        conn = get_db()
        rows = conn.execute('SELECT product_id FROM wishlist WHERE user_id = ?', (session['user_id'],)).fetchall()
        conn.close()
        return [r['product_id'] for r in rows]
    return session.get('guest_wishlist', [])

@app.context_processor
def inject_globals():
    cart = session.get('cart', {})
    cart_count = sum(item['qty'] for item in cart.values())
    wishlist_ids = get_user_wishlist_ids()
    return dict(
        cart_count=cart_count,
        wishlist_ids=wishlist_ids,
        wishlist_count=len(wishlist_ids),
        current_user=session.get('user_name'),
        get_discounted_price=get_discounted_price
    )

# 1. HOME PAGE
@app.route('/')
def home():
    conn = get_db()

    featured_products = conn.execute("""
        SELECT
            id,
            seller_id,
            name,
            description,
            category,
            price AS price_per_kg,
            0 AS discount_percent,
            image AS image_url,
            CASE
                WHEN quantity > 0 AND status = 'Active' THEN 1
                ELSE 0
            END AS in_stock,
            1 AS featured
        FROM products
        WHERE status = 'Active'
        ORDER BY created_at DESC
        LIMIT 4
    """).fetchall()

    all_products = conn.execute("""
        SELECT
            id,
            seller_id,
            name,
            description,
            category,
            price AS price_per_kg,
            0 AS discount_percent,
            image AS image_url,
            CASE
                WHEN quantity > 0 AND status = 'Active' THEN 1
                ELSE 0
            END AS in_stock,
            0 AS featured
        FROM products
        WHERE status = 'Active'
        ORDER BY created_at DESC
        LIMIT 4
    """).fetchall()

    conn.close()

    return render_template(
        'index.html',
        featured=featured_products,
        products=all_products
    )
# 3. PRODUCT PAGE (Multiple Pages with Pagination + Category & Search)
@app.route('/products')
def products():
    category = request.args.get('category', 'All')
    search_query = request.args.get('q', '').strip()
    page = request.args.get('page', 1, type=int)
    per_page = 6

    product_columns = """
        id,
        seller_id,
        name,
        description,
        category,
        price AS price_per_kg,
        0 AS discount_percent,
        image AS image_url,
        CASE
            WHEN quantity > 0 AND status = 'Active' THEN 1
            ELSE 0
        END AS in_stock
    """

    conn = get_db()

    if search_query:
        like_q = f'%{search_query}%'

        total_items = conn.execute("""
            SELECT COUNT(*) FROM products
            WHERE status = 'Active'
            AND (name LIKE ? OR category LIKE ? OR description LIKE ?)
        """, (like_q, like_q, like_q)).fetchone()[0]

        total_pages = max(1, math.ceil(total_items / per_page))
        page = max(1, min(page, total_pages))
        offset = (page - 1) * per_page

        items = conn.execute(f"""
            SELECT {product_columns}
            FROM products
            WHERE status = 'Active'
            AND (name LIKE ? OR category LIKE ? OR description LIKE ?)
            ORDER BY created_at DESC
            LIMIT ? OFFSET ?
        """, (like_q, like_q, like_q, per_page, offset)).fetchall()

    elif category != 'All':
        total_items = conn.execute("""
            SELECT COUNT(*) FROM products
            WHERE status = 'Active' AND category = ?
        """, (category,)).fetchone()[0]

        total_pages = max(1, math.ceil(total_items / per_page))
        page = max(1, min(page, total_pages))
        offset = (page - 1) * per_page

        items = conn.execute(f"""
            SELECT {product_columns}
            FROM products
            WHERE status = 'Active' AND category = ?
            ORDER BY created_at DESC
            LIMIT ? OFFSET ?
        """, (category, per_page, offset)).fetchall()

    else:
        total_items = conn.execute("""
            SELECT COUNT(*) FROM products WHERE status = 'Active'
        """).fetchone()[0]

        total_pages = max(1, math.ceil(total_items / per_page))
        page = max(1, min(page, total_pages))
        offset = (page - 1) * per_page

        items = conn.execute(f"""
            SELECT {product_columns}
            FROM products
            WHERE status = 'Active'
            ORDER BY created_at DESC
            LIMIT ? OFFSET ?
        """, (per_page, offset)).fetchall()

    conn.close()

    return render_template(
        'products.html',
        products=items,
        active_category=category,
        search_query=search_query,
        page=page,
        total_pages=total_pages,
        total_items=total_items
    )

# FULL-SCREEN PRODUCT DETAIL PAGE + SUGGESTED ITEMS AT BOTTOM
@app.route('/product/<int:product_id>')
def product_detail(product_id):
    product_columns = """
        id,
        seller_id,
        name,
        description,
        category,
        price AS price_per_kg,
        0 AS discount_percent,
        image AS image_url,
        CASE
            WHEN quantity > 0 AND status = 'Active' THEN 1
            ELSE 0
        END AS in_stock
    """

    conn = get_db()

    product = conn.execute(f"""
        SELECT {product_columns}
        FROM products
        WHERE id = ? AND status = 'Active'
    """, (product_id,)).fetchone()

    if not product:
        conn.close()
        flash('Product not found.', 'danger')
        return redirect(url_for('products'))

    same_cat = conn.execute(f"""
        SELECT {product_columns}
        FROM products
        WHERE category = ?
        AND id != ?
        AND status = 'Active'
        LIMIT 4
    """, (product['category'], product_id)).fetchall()

    suggestions = list(same_cat)

    if len(suggestions) < 4:
        needed = 4 - len(suggestions)
        existing_ids = [product_id] + [s['id'] for s in suggestions]
        placeholders = ','.join('?' for _ in existing_ids)

        more = conn.execute(f"""
            SELECT {product_columns}
            FROM products
            WHERE id NOT IN ({placeholders})
            AND status = 'Active'
            LIMIT ?
        """, (*existing_ids, needed)).fetchall()

        suggestions.extend(more)

    conn.close()

    return render_template(
        'product_detail.html',
        product=product,
        suggestions=suggestions
    )

# WISHLIST PAGE & ACTIONS (Supports AJAX so screen never jumps to top)
@app.route('/wishlist')
def wishlist():
    w_ids = get_user_wishlist_ids()
    items = []

    if w_ids:
        placeholders = ','.join('?' for _ in w_ids)

        conn = get_db()
        items = conn.execute(f"""
            SELECT
                id,
                seller_id,
                name,
                description,
                category,
                price AS price_per_kg,
                0 AS discount_percent,
                image AS image_url,
                CASE
                    WHEN quantity > 0 AND status = 'Active' THEN 1
                    ELSE 0
                END AS in_stock
            FROM products
            WHERE id IN ({placeholders})
            AND status = 'Active'
        """, w_ids).fetchall()

        conn.close()

    return render_template('wishlist.html', products=items)

#wish-list toggle
@app.route('/wishlist/toggle/<int:product_id>', methods=['POST'])
def toggle_wishlist(product_id):
    is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'

    conn = get_db()

    product = conn.execute("""
        SELECT id, name
        FROM products
        WHERE id = ? AND status = 'Active'
    """, (product_id,)).fetchone()

    if not product:
        conn.close()
        if is_ajax:
            return jsonify({
                'success': False,
                'message': 'Product not found'
            }), 404
        return redirect(request.referrer or url_for('products'))

    in_wishlist = False

    if 'user_id' in session:
        user_id = session['user_id']

        existing = conn.execute(
            'SELECT id FROM wishlist WHERE user_id = ? AND product_id = ?',
            (user_id, product_id)
        ).fetchone()

        if existing:
            conn.execute(
                'DELETE FROM wishlist WHERE user_id = ? AND product_id = ?',
                (user_id, product_id)
            )
            msg = f'Removed {product["name"]} from your wishlist.'
        else:
            conn.execute(
                'INSERT INTO wishlist (user_id, product_id) VALUES (?, ?)',
                (user_id, product_id)
            )
            msg = f'Saved {product["name"]} to your wishlist!'
            in_wishlist = True

        conn.commit()

    else:
        guest_w = session.get('guest_wishlist', [])

        if product_id in guest_w:
            guest_w.remove(product_id)
            msg = f'Removed {product["name"]} from your wishlist.'
        else:
            guest_w.append(product_id)
            msg = f'Saved {product["name"]} to your wishlist!'
            in_wishlist = True

        session['guest_wishlist'] = guest_w

    conn.close()

    w_count = len(get_user_wishlist_ids())

    if is_ajax:
        return jsonify({
            'success': True,
            'in_wishlist': in_wishlist,
            'wishlist_count': w_count,
            'message': msg
        })

    flash(msg, 'success' if in_wishlist else 'info')
    return redirect(request.referrer or url_for('wishlist'))
#wish-list move to cart

@app.route('/wishlist/move-to-cart/<int:product_id>', methods=['POST'])
def move_to_cart(product_id):
    conn = get_db()

    product = conn.execute("""
        SELECT
            id,
            name,
            price AS price_per_kg,
            image AS image_url,
            CASE
                WHEN quantity > 0 AND status = 'Active' THEN 1
                ELSE 0
            END AS in_stock
        FROM products
        WHERE id = ? AND status = 'Active'
    """, (product_id,)).fetchone()

    if not product or product['in_stock'] == 0:
        conn.close()
        flash('Sorry, this item is currently out of stock.', 'danger')
        return redirect(url_for('wishlist'))

    cart_data = session.get('cart', {})
    pid = str(product_id)
    final_price = get_discounted_price(product)

    if pid in cart_data:
        cart_data[pid]['qty'] += 1
    else:
        cart_data[pid] = {
            'name': product['name'],
            'image': product['image_url'],
            'price': final_price,
            'qty': 1
        }

    session['cart'] = cart_data

    if 'user_id' in session:
        conn.execute(
            'DELETE FROM wishlist WHERE user_id = ? AND product_id = ?',
            (session['user_id'], product_id)
        )
        conn.commit()
    else:
        guest_w = session.get('guest_wishlist', [])

        if product_id in guest_w:
            guest_w.remove(product_id)
            session['guest_wishlist'] = guest_w

    conn.close()

    flash(f'Moved {product["name"]} from your wishlist to cart!', 'success')
    return redirect(url_for('cart'))

# 2. ROLE-BASED REGISTRATION, LOGIN & FORGOT PASSWORD
@app.route('/auth', methods=['GET', 'POST'])
def auth():
    mode = request.args.get('mode', 'choose_role')
    role = request.args.get('role', '').strip().lower()

    if request.method == 'POST':
        action = request.form.get('action')

        conn = get_db()

        if action == 'register':
            name = request.form.get('name', '').strip()
            mobile = request.form.get('mobile', '').strip()
            email = request.form.get('email', '').strip()
            password = request.form.get('password', '')

            existing = conn.execute(
                'SELECT id FROM users WHERE email = ? OR mobile = ?',
                (email, mobile)
            ).fetchone()

            if existing:
                flash('Email or Mobile number already registered!', 'danger')
                conn.close()
                return redirect(url_for('auth', mode='register', role='customer'))

            conn.execute("""
                INSERT INTO users (name, mobile, email, password, role)
                VALUES (?, ?, ?, ?, ?)
            """, (
                name,
                mobile,
                email,
                generate_password_hash(password),
                'customer'
            ))

            conn.commit()

            user = conn.execute(
                'SELECT * FROM users WHERE email = ?',
                (email,)
            ).fetchone()

            session['user_id'] = user['id']
            session['user_name'] = user['name']
            session['user_role'] = 'customer'

            for pid in session.pop('guest_wishlist', []):
                conn.execute(
                    'INSERT IGNORE INTO wishlist (user_id, product_id) VALUES (?, ?)',
                    (user['id'], pid)
                )

            conn.commit()
            conn.close()

            flash(f'Customer registration successful! Welcome, {user["name"]}.', 'success')
            return redirect(url_for('home'))

        elif action == 'login':
            identifier = request.form.get('identifier', '').strip()
            password = request.form.get('password', '')

            user = conn.execute(
                'SELECT * FROM users WHERE email = ? OR mobile = ?',
                (identifier, identifier)
            ).fetchone()

            if user and check_password_hash(user['password'], password):
                user_role = (user.get('role') or 'customer').lower()

                if user_role != 'customer':
                    flash('Please use the appropriate Seller or Rider application to login.', 'warning')
                    conn.close()
                    return redirect(url_for('auth', mode='login', role='customer'))

                session['user_id'] = user['id']
                session['user_name'] = user['name']
                session['user_role'] = user_role

                for pid in session.pop('guest_wishlist', []):
                    conn.execute(
                        'INSERT IGNORE INTO wishlist (user_id, product_id) VALUES (?, ?)',
                        (user['id'], pid)
                    )

                conn.commit()
                conn.close()

                flash(f'Welcome back, {user["name"]}!', 'success')
                return redirect(url_for('home'))

            flash('Invalid Email/Mobile or Password.', 'danger')
            conn.close()
            return redirect(url_for('auth', mode='login', role='customer'))

        elif action == 'forgot_send_otp':
            mobile = request.form.get('mobile', '').strip()

            user = conn.execute(
                'SELECT * FROM users WHERE mobile = ?',
                (mobile,)
            ).fetchone()

            if not user:
                flash('No account found with this mobile number.', 'danger')
                conn.close()
                return redirect(url_for('auth', mode='forgot', role='customer'))

            otp = str(random.randint(1000, 9999))

            session['reset_data'] = {
                'mobile': mobile,
                'otp': otp,
                'role': 'customer'
            }

            flash(f'Password Reset OTP sent to {mobile}! (Demo OTP: {otp})', 'info')
            conn.close()

            return redirect(url_for('auth', mode='reset', role='customer'))

        elif action == 'reset_password':
            otp = request.form.get('otp', '').strip()
            new_pass = request.form.get('new_password', '')
            reset_data = session.get('reset_data')

            if reset_data and otp == reset_data['otp']:
                conn.execute(
                    'UPDATE users SET password = ? WHERE mobile = ?',
                    (generate_password_hash(new_pass), reset_data['mobile'])
                )

                conn.commit()
                session.pop('reset_data', None)
                conn.close()

                flash('Password updated successfully! Please login.', 'success')
                return redirect(url_for('auth', mode='login', role='customer'))

            flash('Invalid OTP entered.', 'danger')
            conn.close()
            return redirect(url_for('auth', mode='reset', role='customer'))

        conn.close()

    return render_template('auth.html', mode=mode, role=role)

@app.route('/logout')
def logout():
    session.clear()
    flash('You have been logged out.', 'info')
    return redirect(url_for('home'))

# 4. SHOPPING CART
@app.route('/cart')
def cart():
    cart_data = session.get('cart', {})
    cart_items = []
    subtotal = 0.0

    for pid, item in cart_data.items():
        line_total = round(item['price'] * item['qty'], 2)
        subtotal += line_total
        cart_items.append({
            'id': pid,
            'name': item['name'],
            'image': item['image'],
            'price': item['price'],
            'qty': item['qty'],
            'line_total': line_total
        })

    delivery_charge = 0.0 if (subtotal == 0 or subtotal >= 500) else 40.0
    grand_total = round(subtotal + delivery_charge, 2)

    return render_template(
        'cart.html',
        cart_items=cart_items,
        subtotal=round(subtotal, 2),
        delivery_charge=delivery_charge,
        grand_total=grand_total
    )
#Add to cart
@app.route('/cart/add/<int:product_id>', methods=['POST'])
def add_to_cart(product_id):
    is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'

    conn = get_db()

    product = conn.execute("""
        SELECT
            id,
            name,
            price AS price_per_kg,
            image AS image_url,
            quantity,
            status
        FROM products
        WHERE id = ? AND status = 'Active'
    """, (product_id,)).fetchone()

    conn.close()

    if not product or product['quantity'] <= 0:
        msg = 'Sorry, this item is currently out of stock.'

        if is_ajax:
            return jsonify({'success': False, 'message': msg}), 400

        flash(msg, 'danger')
        return redirect(request.referrer or url_for('products'))

    qty_to_add = request.form.get('qty', 1, type=int)

    if qty_to_add < 1:
        qty_to_add = 1

    cart_data = session.get('cart', {})
    pid = str(product_id)
    final_price = float(product['price_per_kg'])

    if pid in cart_data:
        cart_data[pid]['qty'] += qty_to_add
    else:
        cart_data[pid] = {
            'name': product['name'],
            'image': product['image_url'],
            'price': final_price,
            'qty': qty_to_add
        }

    session['cart'] = cart_data

    cart_count = sum(item['qty'] for item in cart_data.values())
    msg = f'Added {product["name"]} ({qty_to_add} kg) to cart!'

    if is_ajax:
        return jsonify({
            'success': True,
            'cart_count': cart_count,
            'message': msg
        })

    flash(msg, 'success')
    return redirect(request.referrer or url_for('products'))
@app.route('/cart/update/<pid>/<action>', methods=['GET', 'POST'])
def update_cart(pid, action):
    is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'
    cart_data = session.get('cart', {})
    item_qty = 0
    line_total = 0.0

    if pid in cart_data:
        if action == 'inc':
            cart_data[pid]['qty'] += 1
        elif action == 'dec':
            cart_data[pid]['qty'] -= 1
            if cart_data[pid]['qty'] <= 0:
                del cart_data[pid]
        elif action == 'remove':
            del cart_data[pid]

        if pid in cart_data:
            item_qty = cart_data[pid]['qty']
            line_total = round(cart_data[pid]['price'] * item_qty, 2)

    session['cart'] = cart_data

    subtotal = round(sum(item['price'] * item['qty'] for item in cart_data.values()), 2)
    delivery_charge = 0.0 if (subtotal == 0 or subtotal >= 500) else 40.0
    grand_total = round(subtotal + delivery_charge, 2)
    cart_count = sum(item['qty'] for item in cart_data.values())
    amount_needed_for_free = round(max(0.0, 500.0 - subtotal), 2)

    if is_ajax:
        return jsonify({
            'success': True,
            'removed': pid not in cart_data,
            'qty': item_qty,
            'line_total': line_total,
            'subtotal': subtotal,
            'delivery_charge': delivery_charge,
            'grand_total': grand_total,
            'cart_count': cart_count,
            'amount_needed_for_free': amount_needed_for_free
        })

    return redirect(url_for('cart'))

# 5. CHECKOUT & STRICT ONLINE PAYMENT VALIDATION

@app.route('/checkout', methods=['GET', 'POST'])
@login_required
def checkout():
    cart_data = session.get('cart', {})

    if not cart_data:
        flash('Your cart is empty!', 'warning')
        return redirect(url_for('products'))

    conn = get_db()
    cursor = conn.cursor()

    try:
        addresses = cursor.execute(
            'SELECT * FROM addresses WHERE user_id = ?',
            (session['user_id'],)
        ).fetchall()

        subtotal = sum(
            float(item['price']) * float(item['qty'])
            for item in cart_data.values()
        )

        delivery_charge = 0.0 if subtotal >= 500 else 40.0
        grand_total = round(subtotal + delivery_charge, 2)

        if request.method == 'POST':

            # Only Cash on Delivery is supported until a real
            # payment gateway is connected.
            payment_method = request.form.get(
                'payment_method', 'Cash on Delivery'
            )

            if payment_method not in ('Cash on Delivery', 'COD'):
                flash(
                    'Online payment is not available yet. Please select Cash on Delivery.',
                    'danger'
                )
                conn.close()
                return redirect(url_for('checkout'))

            # Get the logged-in customer's details.
            customer = cursor.execute(
                'SELECT name, mobile FROM users WHERE id = ?',
                (session['user_id'],)
            ).fetchone()

            if not customer:
                flash('Customer account not found. Please log in again.', 'danger')
                conn.close()
                return redirect(url_for('auth', mode='login', role='customer'))

            # Get or save the delivery address.
            addr_choice = request.form.get('address_choice')

            if addr_choice == 'new':
                label = request.form.get('label', 'Home').strip()
                full_addr = request.form.get('full_address', '').strip()
                city = request.form.get('city', '').strip()
                pincode = request.form.get('pincode', '').strip()

                if not full_addr or not city or not pincode:
                    flash('Please fill in all delivery address fields.', 'danger')
                    conn.close()
                    return redirect(url_for('checkout'))

                cursor.execute(
                    '''INSERT INTO addresses
                    (user_id, label, full_address, city, pincode)
                    VALUES (?, ?, ?, ?, ?)''',
                    (session['user_id'], label, full_addr, city, pincode)
                )

                selected_address = (
                    f"{label}: {full_addr}, {city} - {pincode}"
                )

            else:
                addr_row = cursor.execute(
                    '''SELECT * FROM addresses
                    WHERE id = ? AND user_id = ?''',
                    (addr_choice, session['user_id'])
                ).fetchone()

                if not addr_row:
                    flash('Please select a valid delivery address.', 'danger')
                    conn.close()
                    return redirect(url_for('checkout'))

                selected_address = (
                    f"{addr_row['label']}: {addr_row['full_address']}, "
                    f"{addr_row['city']} - {addr_row['pincode']}"
                )

            # Validate products and stock using the actual database values.
            validated_items = []
            seller_items = {}
            subtotal = 0.0
            total_qty = 0.0

            for cart_key, item in cart_data.items():

                product_id = int(item.get('pid', cart_key))
                quantity = float(item['qty'])

                if quantity <= 0:
                    raise ValueError('Invalid product quantity.')

                product = cursor.execute(
                    '''SELECT id, seller_id, name, price, quantity, status
                    FROM products
                    WHERE id = ? AND status = 'Active'
                    FOR UPDATE''',
                    (product_id,)
                ).fetchone()

                if not product:
                    raise ValueError(
                        f"{item['name']} is no longer available."
                    )

                available_stock = float(product['quantity'])

                if quantity > available_stock:
                    raise ValueError(
                        f"Only {available_stock:g} kg of "
                        f"{product['name']} is available."
                    )

                price = float(product['price'])
                item_total = round(price * quantity, 2)

                validated_items.append({
                    'product_id': product['id'],
                    'seller_id': product['seller_id'],
                    'name': product['name'],
                    'quantity': quantity,
                    'price': price
                })

                seller_id = product['seller_id']

                if seller_id not in seller_items:
                    seller_items[seller_id] = []

                seller_items[seller_id].append(
                    f"{product['name']} - {quantity:g} kg"
                )

                subtotal += item_total
                total_qty += quantity

            subtotal = round(subtotal, 2)
            delivery_charge = 0.0 if subtotal >= 500 else 40.0
            grand_total = round(subtotal + delivery_charge, 2)

            # Create the order using the EXISTING shared orders columns.
            cursor.execute(
                '''INSERT INTO orders
                (customer_name, customer_phone, customer_address,
                 total_amount, order_status)
                VALUES (?, ?, ?, ?, ?)''',
                (
                    customer['name'],
                    customer['mobile'],
                    selected_address,
                    grand_total,
                    'Pending'
                )
            )

            order_id = cursor.lastrowid

            # Insert individual products into order_items.
            for item in validated_items:

                cursor.execute(
                    '''INSERT INTO order_items
                    (order_id, product_id, seller_id, quantity, price)
                    VALUES (?, ?, ?, ?, ?)''',
                    (
                        order_id,
                        item['product_id'],
                        item['seller_id'],
                        item['quantity'],
                        item['price']
                    )
                )

                # Reduce available product stock.
                cursor.execute(
                    '''UPDATE products
                    SET quantity = quantity - ?
                    WHERE id = ? AND quantity >= ?''',
                    (
                        item['quantity'],
                        item['product_id'],
                        item['quantity']
                    )
                )

                if cursor.rowcount != 1:
                    raise ValueError(
                        f"Stock could not be updated for {item['name']}."
                    )

            # Notify each seller about their part of the order.
            delivery_date = request.form.get('delivery_date', '')
            delivery_time = request.form.get('delivery_time', '')

            for seller_id, items in seller_items.items():

                message = (
                    f"New Order #{order_id}. "
                    f"Customer: {customer['name']}. "
                    f"Phone: {customer['mobile']}. "
                    f"Address: {selected_address}. "
                    f"Items: {', '.join(items)}. "
                    f"Total order amount: Rs. {grand_total:.2f}. "
                    f"Payment: Cash on Delivery."
                )

                if delivery_date or delivery_time:
                    message += (
                        f" Preferred delivery: "
                        f"{delivery_date} {delivery_time}."
                    )

                cursor.execute(
                    '''INSERT INTO notifications
                    (seller_id, title, message)
                    VALUES (?, ?, ?)''',
                    (
                        seller_id,
                        'New Customer Order',
                        message
                    )
                )

            # Save the order, items, stock updates and notifications together.
            conn.commit()

            session.pop('cart', None)

            flash(
                f'Order #{order_id} confirmed! Thank you for ordering.',
                'success'
            )

            conn.close()
            return redirect(url_for('orders'))

        today_str = datetime.now().strftime('%Y-%m-%d')

        conn.close()

        return render_template(
            'checkout.html',
            addresses=addresses,
            cart_data=cart_data,
            subtotal=round(subtotal, 2),
            delivery_charge=delivery_charge,
            grand_total=grand_total,
            today_str=today_str
        )

    except Exception as e:
        conn.rollback()
        print('Checkout error:', e)
        conn.close()

        flash(str(e), 'danger')
        return redirect(url_for('checkout'))


# 6. MY ORDERS & TRACKING

@app.route('/orders')
@login_required
def orders():
    conn = get_db()
    cursor = conn.cursor()

    try:
        customer = cursor.execute(
            'SELECT mobile FROM users WHERE id = ?',
            (session['user_id'],)
        ).fetchone()

        if not customer:
            flash('Customer account not found. Please log in again.', 'danger')
            conn.close()
            return redirect(url_for('login'))

        user_orders = cursor.execute(
            '''
            SELECT
                id,
                id AS order_code,
                customer_name,
                customer_phone,
                customer_address,
                total_amount,
                order_status,
                order_status AS delivery_status,
                created_at,
                created_at AS order_date
            FROM orders
            WHERE customer_phone = ?
            ORDER BY id DESC
            ''',
            (customer['mobile'],)
        ).fetchall()

        conn.close()

        return render_template(
            'orders.html',
            orders=user_orders
        )

    except Exception as e:
        conn.close()
        print('Order history error:', e)
        flash('Unable to load your orders right now.', 'danger')
        return redirect(url_for('products'))


#Search Suggestions
@app.route('/api/search-suggestions')
def search_suggestions():
    q = request.args.get('q', '').strip()

    if len(q) < 1:
        return jsonify([])

    conn = get_db()
    like_q = f'%{q}%'

    rows = conn.execute("""
        SELECT
            id,
            name,
            category,
            price AS price_per_kg,
            image AS image_url,
            CASE
                WHEN quantity > 0 AND status = 'Active' THEN 1
                ELSE 0
            END AS in_stock
        FROM products
        WHERE status = 'Active'
        AND (name LIKE ? OR category LIKE ? OR description LIKE ?)
        LIMIT 6
    """, (like_q, like_q, like_q)).fetchall()

    conn.close()

    results = []

    for r in rows:
        results.append({
            'id': r['id'],
            'name': r['name'],
            'category': r['category'],
            'price': get_discounted_price(r),
            'image_url': r['image_url'],
            'in_stock': r['in_stock']
        })

    return jsonify(results)


@app.route('/orders/cancel/<int:order_id>', methods=['POST'])
@login_required
def cancel_order(order_id):
    reason_choice = request.form.get('reason_choice', '').strip()
    custom_reason = request.form.get('custom_reason', '').strip()

    if not reason_choice:
        flash('Please select a reason for cancelling the order.', 'warning')
        return redirect(url_for('orders'))

    if reason_choice == 'Other' and not custom_reason:
        flash('Please specify your cancellation reason.', 'warning')
        return redirect(url_for('orders'))

    full_reason = (
        f"{reason_choice}: {custom_reason}"
        if reason_choice == 'Other'
        else reason_choice
    )

    conn = get_db()
    cursor = conn.cursor()

    try:
        # Find the logged-in customer's mobile number.
        customer = cursor.execute(
            'SELECT mobile FROM users WHERE id = ?',
            (session['user_id'],)
        ).fetchone()

        if not customer:
            raise ValueError('Customer account not found.')

        # Find this customer's order.
        order = cursor.execute(
            '''SELECT id, order_status
            FROM orders
            WHERE id = ? AND customer_phone = ?''',
            (order_id, customer['mobile'])
        ).fetchone()

        if not order:
            raise ValueError('Order not found.')

        if order['order_status'] != 'Pending':
            raise ValueError(
                'This order can no longer be cancelled.'
            )

        # Check whether any seller has accepted the order.
        accepted = cursor.execute(
            '''SELECT id
            FROM seller_order_actions
            WHERE order_id = ? AND status = 'Accepted'
            LIMIT 1''',
            (order_id,)
        ).fetchone()

        if accepted:
            raise ValueError(
                'The seller has already accepted this order. '
                'Cancellation is not available.'
            )

        # Check whether a rider has already been assigned.
        assignment = cursor.execute(
            '''SELECT id
            FROM rider_assignments
            WHERE order_id = ?
            LIMIT 1''',
            (order_id,)
        ).fetchone()

        if assignment:
            raise ValueError(
                'A rider has already been assigned. '
                'Cancellation is not available.'
            )

        # Get the products and quantities in this order.
        order_items = cursor.execute(
            '''SELECT product_id, seller_id, quantity
            FROM order_items
            WHERE order_id = ?''',
            (order_id,)
        ).fetchall()

        if not order_items:
            raise ValueError('Order items could not be found.')

        # Cancel the order.
        cursor.execute(
            '''UPDATE orders
            SET order_status = 'Cancelled'
            WHERE id = ? AND customer_phone = ?
            AND order_status = 'Pending' ''',
            (order_id, customer['mobile'])
        )

        if cursor.rowcount != 1:
            raise ValueError('Unable to cancel this order.')

        # Restore product stock.
        for item in order_items:
            cursor.execute(
                '''UPDATE products
                SET quantity = quantity + ?
                WHERE id = ?''',
                (item['quantity'], item['product_id'])
            )

        # Inform the sellers about the cancellation.
        seller_ids = set(item['seller_id'] for item in order_items)

        for seller_id in seller_ids:
            cursor.execute(
                '''INSERT INTO notifications
                (seller_id, title, message)
                VALUES (?, ?, ?)''',
                (
                    seller_id,
                    'Order Cancelled',
                    f'Customer cancelled Order #{order_id}. '
                    f'Reason: {full_reason}'
                )
            )

        conn.commit()

        flash(
            f'Order #{order_id} has been cancelled successfully.',
            'info'
        )

    except Exception as e:
        conn.rollback()
        print('Cancellation error:', e)
        flash(str(e), 'warning')

    finally:
        conn.close()

    return redirect(url_for('orders'))

#Return

@app.route('/orders/return/<int:order_id>', methods=['POST'])
@login_required
def return_order(order_id):
    reason_choice = request.form.get('reason_choice', '').strip()
    custom_reason = request.form.get('custom_reason', '').strip()

    if not reason_choice:
        flash('Please select a reason for returning the order.', 'warning')
        return redirect(url_for('orders'))

    if reason_choice == 'Other' and not custom_reason:
        flash('Please specify your return reason.', 'warning')
        return redirect(url_for('orders'))

    full_reason = (
        f"{reason_choice}: {custom_reason}"
        if reason_choice == 'Other'
        else reason_choice
    )

    conn = get_db()
    cursor = conn.cursor()

    try:
        # Step 1: Get the logged-in customer.
        customer = cursor.execute(
            'SELECT name, mobile FROM users WHERE id = ?',
            (session['user_id'],)
        ).fetchone()

        if not customer:
            raise ValueError('Customer account not found.')

        # Step 2: Verify that this order belongs to the customer
        # and has been delivered.
        order = cursor.execute(
            '''SELECT id, customer_name, customer_phone,
                      customer_address, order_status
               FROM orders
               WHERE id = ? AND customer_phone = ?
               FOR UPDATE''',
            (order_id, customer['mobile'])
        ).fetchone()

        if not order:
            raise ValueError('Order not found.')

        if order['order_status'] != 'Delivered':
            raise ValueError(
                'A return can only be requested after delivery.'
            )

        # Step 3: Get every product belonging to this order.
        items = cursor.execute(
            '''SELECT id, product_id, seller_id, quantity
               FROM order_items
               WHERE order_id = ?''',
            (order_id,)
        ).fetchall()

        if not items:
            raise ValueError('No order items were found.')

        # Step 4: Prevent duplicate return requests.
        for item in items:
            existing = cursor.execute(
                '''SELECT id FROM return_requests
                   WHERE order_item_id = ?
                   LIMIT 1''',
                (item['id'],)
            ).fetchone()

            if existing:
                raise ValueError(
                    'A return request already exists for one or more '
                    'products in this order.'
                )

        # Step 5: Create one return request for each product.
        seller_ids = set()

        for item in items:
            cursor.execute(
                '''INSERT INTO return_requests
                   (order_id, order_item_id, product_id, seller_id,
                    customer_name, customer_phone, customer_address,
                    quantity, reason)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                (
                    order_id,
                    item['id'],
                    item['product_id'],
                    item['seller_id'],
                    customer['name'],
                    customer['mobile'],
                    order['customer_address'],
                    item['quantity'],
                    full_reason
                )
            )

            seller_ids.add(item['seller_id'])

        # Step 6: Notify the sellers.
        for seller_id in seller_ids:
            cursor.execute(
                '''INSERT INTO notifications
                   (seller_id, title, message)
                   VALUES (?, ?, ?)''',
                (
                    seller_id,
                    'Customer Return Request',
                    f'Customer has requested a return for Order '
                    f'#{order_id}. Reason: {full_reason}'
                )
            )

        # Step 7: Save all return requests and notifications.
        conn.commit()

        flash(
            f'Return request submitted successfully for Order #{order_id}.',
            'success'
        )

    except Exception as e:
        conn.rollback()
        print('Return request error:', e)
        flash(str(e), 'warning')

    finally:
        conn.close()

    return redirect(url_for('orders'))



# Customer profile support pages

@app.route('/account/notifications')
@login_required
def notifications():
    conn = get_db()
    cursor = conn.cursor()

    try:
        customer = cursor.execute(
            'SELECT mobile FROM users WHERE id = ?',
            (session['user_id'],)
        ).fetchone()

        if not customer:
            flash('Customer account not found.', 'danger')
            conn.close()
            return redirect(url_for('login'))

        user_notifications = cursor.execute(
            '''
            SELECT
                id,
                id AS order_code,
                order_status AS delivery_status,
                'Cash on Delivery' AS payment_status,
                created_at AS order_date
            FROM orders
            WHERE customer_phone = ?
            ORDER BY id DESC
            LIMIT 20
            ''',
            (customer['mobile'],)
        ).fetchall()

        conn.close()

        return render_template(
            'account_info.html',
            page='notifications',
            notifications=user_notifications
        )

    except Exception as e:
        conn.close()
        print('Customer notifications error:', e)
        flash('Unable to load notifications right now.', 'danger')
        return redirect(url_for('products'))


# 7. MY ACCOUNT

@app.route('/account', methods=['GET', 'POST'])
@login_required
def account():
    conn = get_db()
    user_id = session['user_id']

    if request.method == 'POST':
        form_type = request.form.get('form_type')

        if form_type == 'edit_profile':
            name = request.form.get('name', '').strip()
            mobile = request.form.get('mobile', '').strip()
            email = request.form.get('email', '').strip()

            if not name or not mobile or not email:
                flash('Please fill in all profile fields.', 'danger')
                conn.close()
                return redirect(url_for('account'))

            conn.execute(
                '''UPDATE users
                SET name = ?, mobile = ?, email = ?
                WHERE id = ?''',
                (name, mobile, email, user_id)
            )
            conn.commit()

            session['user_name'] = name
            flash('Profile updated successfully!', 'success')

        elif form_type == 'change_password':
            old_pw = request.form.get('old_password', '')
            new_pw = request.form.get('new_password', '')

            user = conn.execute(
                'SELECT * FROM users WHERE id = ?',
                (user_id,)
            ).fetchone()

            if user and check_password_hash(user['password'], old_pw):
                conn.execute(
                    'UPDATE users SET password = ? WHERE id = ?',
                    (generate_password_hash(new_pw), user_id)
                )
                conn.commit()
                flash('Password changed successfully!', 'success')
            else:
                flash('Current password is incorrect.', 'danger')

        elif form_type == 'add_address':
            label = request.form.get('label', 'Home')
            full_addr = request.form.get('full_address', '').strip()
            city = request.form.get('city', '').strip()
            pincode = request.form.get('pincode', '').strip()

            if not full_addr or not city or not pincode:
                flash('Please fill in all address fields.', 'danger')
                conn.close()
                return redirect(url_for('account'))

            conn.execute(
                '''INSERT INTO addresses
                (user_id, label, full_address, city, pincode)
                VALUES (?, ?, ?, ?, ?)''',
                (user_id, label, full_addr, city, pincode)
            )
            conn.commit()
            flash('New address saved!', 'success')

        elif form_type == 'delete_address':
            addr_id = request.form.get('address_id')

            conn.execute(
                'DELETE FROM addresses WHERE id = ? AND user_id = ?',
                (addr_id, user_id)
            )
            conn.commit()
            flash('Address removed.', 'info')

        conn.close()
        return redirect(url_for('account'))

    # Get customer profile.
    user = conn.execute(
        'SELECT * FROM users WHERE id = ?',
        (user_id,)
    ).fetchone()

    # Get saved addresses.
    addresses = conn.execute(
        'SELECT * FROM addresses WHERE user_id = ?',
        (user_id,)
    ).fetchall()

    # Get orders using the customer's registered mobile number.
    order_history = conn.execute(
        '''
        SELECT
            id,
            id AS order_code,
            customer_name,
            customer_phone,
            customer_address,
            total_amount,
            order_status,
            order_status AS delivery_status,
            created_at,
            created_at AS order_date,
            'Not recorded' AS payment_status
        FROM orders
        WHERE customer_phone = ?
        ORDER BY id DESC
        ''',
        (user['mobile'],)
    ).fetchall()

    conn.close()

    return render_template(
        'account.html',
        user=user,
        addresses=addresses,
        orders=order_history
    )


if __name__ == '__main__':
    app.run(debug=True, port=5000)
