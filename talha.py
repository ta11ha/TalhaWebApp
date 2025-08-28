from flask import Flask, render_template_string, request, redirect, url_for, session, jsonify, flash
import pyodbc
from azure.storage.blob import BlobServiceClient
import os
import uuid
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
import json
from datetime import datetime
from flask_login import LoginManager, UserMixin, login_user, logout_user, current_user, login_required
from textblob import TextBlob
import cv2
import tempfile

app = Flask(__name__)
app.secret_key = 'b9e4f7a1c02d8e93f67a4c5d2e8ab91ff4763a6d85c24550'

# ================= Azure SQL Database =================
AZURE_SQL_SERVER = "talha1.database.windows.net"
AZURE_SQL_DATABASE = "talha"
AZURE_SQL_USERNAME = "talha1@talha1"
AZURE_SQL_PASSWORD = "Database123"

# ================= Azure Blob Storage =================
AZURE_STORAGE_CONNECTION_STRING = (
    "DefaultEndpointsProtocol=https;"
    "AccountName=talha123;"
    "AccountKey=90ixQC6EC5myJOjGoOVPIe8Gt72zBGp9smujA1P47/vFQEUd1/wn2WffYF9mMMX1oZUhno15btQY+ASti2nMJg==;"
    "EndpointSuffix=core.windows.net"
)
AZURE_STORAGE_CONTAINER = "files"

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'


class User(UserMixin):
    def __init__(self, id, username, user_type):
        self.id = id
        self.username = username
        self.user_type = user_type


@login_manager.user_loader
def load_user(user_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id, username, user_type FROM users WHERE id = ?", user_id)
    user_data = cursor.fetchone()
    conn.close()
    if user_data:
        return User(user_data[0], user_data[1], user_data[2])
    return None


def get_db_connection():
    connection_string = f'DRIVER={{ODBC Driver 17 for SQL Server}};SERVER={AZURE_SQL_SERVER};DATABASE={AZURE_SQL_DATABASE};UID={AZURE_SQL_USERNAME};PWD={AZURE_SQL_PASSWORD}'
    return pyodbc.connect(connection_string)


def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute('''
        IF NOT EXISTS (SELECT * FROM sysobjects WHERE name='users' AND xtype='U')
        CREATE TABLE users (
            id INT IDENTITY(1,1) PRIMARY KEY,
            username NVARCHAR(50) UNIQUE NOT NULL,
            email NVARCHAR(100) UNIQUE NOT NULL,
            password_hash NVARCHAR(255) NOT NULL,
            user_type NVARCHAR(10) NOT NULL,
            created_at DATETIME DEFAULT GETDATE()
        )
    ''')

    cursor.execute('''
        IF NOT EXISTS (SELECT * FROM sysobjects WHERE name='videos' AND xtype='U')
        CREATE TABLE videos (
            id INT IDENTITY(1,1) PRIMARY KEY,
            title NVARCHAR(200) NOT NULL,
            publisher NVARCHAR(100) NOT NULL,
            producer NVARCHAR(100) NOT NULL,
            genre NVARCHAR(50) NOT NULL,
            age_rating NVARCHAR(10) NOT NULL,
            video_url NVARCHAR(500) NOT NULL,
            thumbnail_url NVARCHAR(500),
            creator_id INT NOT NULL,
            created_at DATETIME DEFAULT GETDATE(),
            FOREIGN KEY (creator_id) REFERENCES users(id)
        )
    ''')

    cursor.execute('''
        IF NOT EXISTS (SELECT * FROM sysobjects WHERE name='ratings' AND xtype='U')
        CREATE TABLE ratings (
            id INT IDENTITY(1,1) PRIMARY KEY,
            video_id INT NOT NULL,
            user_id INT NOT NULL,
            rating INT NOT NULL,
            created_at DATETIME DEFAULT GETDATE(),
            FOREIGN KEY (video_id) REFERENCES videos(id),
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    ''')

    cursor.execute('''
        IF NOT EXISTS (SELECT * FROM sysobjects WHERE name='comments' AND xtype='U')
        CREATE TABLE comments (
            id INT IDENTITY(1,1) PRIMARY KEY,
            video_id INT NOT NULL,
            user_id INT NOT NULL,
            comment NVARCHAR(500) NOT NULL,
            sentiment NVARCHAR(10),
            created_at DATETIME DEFAULT GETDATE(),
            FOREIGN KEY (video_id) REFERENCES videos(id),
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    ''')

    conn.commit()
    conn.close()


blob_service_client = BlobServiceClient.from_connection_string(AZURE_STORAGE_CONNECTION_STRING)


@app.route('/')
def home():
    return render_template_string(HOME_TEMPLATE)


@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username']
        email = request.form['email']
        password = request.form['password']
        user_type = request.form['user_type']

        password_hash = generate_password_hash(password)

        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO users (username, email, password_hash, user_type) VALUES (?, ?, ?, ?)",
                username, email, password_hash, user_type
            )
            conn.commit()
            conn.close()
            flash('Registration successful!', 'success')
            return redirect(url_for('login'))
        except Exception as e:
            flash('Username or email already exists!', 'error')

    return render_template_string(REGISTER_TEMPLATE)


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']

        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT id, username, password_hash, user_type FROM users WHERE username = ?", username)
        user_data = cursor.fetchone()
        conn.close()

        if user_data and check_password_hash(user_data[2], password):
            user = User(user_data[0], user_data[1], user_data[3])
            login_user(user)
            if user.user_type == 'creator':
                return redirect(url_for('creator_dashboard'))
            else:
                return redirect(url_for('consumer_dashboard'))
        else:
            flash('Invalid credentials!', 'error')

    return render_template_string(LOGIN_TEMPLATE)


@app.route('/creator-dashboard')
@login_required
def creator_dashboard():
    if current_user.user_type != 'creator':
        return redirect(url_for('login'))
    return render_template_string(CREATOR_DASHBOARD_TEMPLATE)


@app.route('/consumer-dashboard')
@login_required
def consumer_dashboard():
    if current_user.user_type != 'consumer':
        return redirect(url_for('login'))

    query = request.args.get('search', '')

    conn = get_db_connection()
    cursor = conn.cursor()

    sql = '''
          SELECT v.id,
                 v.title,
                 v.publisher,
                 v.producer,
                 v.genre,
                 v.age_rating,
                 v.video_url,
                 AVG(CAST(r.rating AS FLOAT)) as avg_rating,
                 v.thumbnail_url
          FROM videos v
                   LEFT JOIN ratings r ON v.id = r.video_id \
          '''
    params = []
    if query:
        sql += ' WHERE v.title LIKE ? OR v.genre LIKE ? OR v.publisher LIKE ?'
        params = [f'%{query}%', f'%{query}%', f'%{query}%']
    sql += '''
        GROUP BY v.id, v.title, v.publisher, v.producer, v.genre, v.age_rating, v.video_url, v.created_at, v.thumbnail_url
        ORDER BY v.created_at DESC
    '''
    cursor.execute(sql, *params)
    videos = cursor.fetchall()

    videos_list = [
        {
            'id': v[0],
            'title': v[1],
            'publisher': v[2],
            'producer': v[3],
            'genre': v[4],
            'age_rating': v[5],
            'video_url': v[6],
            'avg_rating': round(v[7], 1) if v[7] is not None else 'N/A',
            'thumbnail_url': v[8]
        } for v in videos
    ]

    # Fetch user ratings
    user_ratings = {}
    cursor.execute('''
                   SELECT video_id, rating
                   FROM ratings
                   WHERE user_id = ?
                   ''', current_user.id)
    for row in cursor.fetchall():
        user_ratings[row[0]] = row[1]

    # Fetch comments
    comments_dict = {}
    cursor.execute('''
                   SELECT c.video_id, u.username, c.comment, c.created_at, c.sentiment
                   FROM comments c
                            JOIN users u ON c.user_id = u.id
                   ORDER BY c.created_at DESC
                   ''')
    all_comments = cursor.fetchall()
    for comment in all_comments:
        vid = comment[0]
        if vid not in comments_dict:
            comments_dict[vid] = []
        comments_dict[vid].append({
            'username': comment[1],
            'comment': comment[2],
            'created_at': comment[3].strftime('%Y-%m-%d %H:%M:%S'),
            'sentiment': comment[4]
        })

    # Add to videos_list
    for video in videos_list:
        video['user_rating'] = user_ratings.get(video['id'], 0)
        video['comments'] = comments_dict.get(video['id'], [])

    conn.close()

    return render_template_string(CONSUMER_DASHBOARD_TEMPLATE, videos=videos_list)


@app.route('/upload-video', methods=['POST'])
@login_required
def upload_video():
    if current_user.user_type != 'creator':
        return redirect(url_for('login'))

    title = request.form['title']
    publisher = request.form['publisher']
    producer = request.form['producer']
    genre = request.form['genre']
    age_rating = request.form['age_rating']
    video_file = request.files['video']

    if video_file:
        filename = secure_filename(video_file.filename)
        blob_name = f"{uuid.uuid4()}_{filename}"

        try:
            # Save video to temp file
            with tempfile.NamedTemporaryFile(delete=False) as temp_video:
                video_file.save(temp_video.name)
                temp_video_path = temp_video.name

            # Upload video
            blob_client = blob_service_client.get_blob_client(
                container=AZURE_STORAGE_CONTAINER,
                blob=blob_name
            )
            with open(temp_video_path, "rb") as f:
                blob_client.upload_blob(f, overwrite=True)
            video_url = blob_client.url

            # Generate thumbnail
            thumbnail_url = None
            cap = cv2.VideoCapture(temp_video_path)
            success, frame = cap.read()
            if success:
                thumbnail_blob_name = f"{uuid.uuid4()}_thumb.jpg"
                with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False) as temp_thumb:
                    cv2.imwrite(temp_thumb.name, frame)
                    temp_thumb_path = temp_thumb.name

                blob_client_thumb = blob_service_client.get_blob_client(
                    container=AZURE_STORAGE_CONTAINER,
                    blob=thumbnail_blob_name
                )
                with open(temp_thumb_path, "rb") as f:
                    blob_client_thumb.upload_blob(f, overwrite=True)
                thumbnail_url = blob_client_thumb.url

                os.unlink(temp_thumb_path)

            cap.release()
            os.unlink(temp_video_path)

            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO videos (title, publisher, producer, genre, age_rating, video_url, thumbnail_url, creator_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                title, publisher, producer, genre, age_rating, video_url, thumbnail_url, current_user.id
            )
            conn.commit()
            conn.close()

            flash('Video uploaded successfully!', 'success')
        except Exception as e:
            flash(f'Upload failed: {str(e)}', 'error')

    return redirect(url_for('creator_dashboard'))


@app.route('/rate-video', methods=['POST'])
@login_required
def rate_video():
    if current_user.user_type != 'consumer':
        return jsonify({'error': 'Unauthorized'}), 401

    data = request.json
    video_id = data['video_id']
    rating = data['rating']

    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT id FROM ratings WHERE video_id = ? AND user_id = ?", video_id, current_user.id)
    existing = cursor.fetchone()

    if existing:
        cursor.execute("UPDATE ratings SET rating = ? WHERE video_id = ? AND user_id = ?",
                       rating, video_id, current_user.id)
    else:
        cursor.execute("INSERT INTO ratings (video_id, user_id, rating) VALUES (?, ?, ?)",
                       video_id, current_user.id, rating)

    conn.commit()

    # Fetch new average
    cursor.execute("SELECT AVG(CAST(rating AS FLOAT)) FROM ratings WHERE video_id = ?", video_id)
    new_avg = cursor.fetchone()[0]

    conn.close()

    return jsonify({'success': True, 'avg_rating': round(new_avg, 1) if new_avg is not None else 'N/A'})


@app.route('/add-comment', methods=['POST'])
@login_required
def add_comment():
    if current_user.user_type != 'consumer':
        return jsonify({'error': 'Unauthorized'}), 401

    data = request.json
    video_id = data['video_id']
    comment_text = data['comment']

    # Perform sentiment analysis
    blob = TextBlob(comment_text)
    polarity = blob.sentiment.polarity
    if polarity > 0:
        sentiment = 'positive'
    elif polarity < 0:
        sentiment = 'negative'
    else:
        sentiment = 'neutral'

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("INSERT INTO comments (video_id, user_id, comment, sentiment) VALUES (?, ?, ?, ?)",
                   video_id, current_user.id, comment_text, sentiment)
    conn.commit()
    conn.close()

    created_at = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    return jsonify({'success': True,
                    'comment': {'username': current_user.username, 'comment': comment_text, 'created_at': created_at,
                                'sentiment': sentiment}})


@app.route('/search-videos')
@login_required
def search_videos():
    query = request.args.get('q', '')

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
                   SELECT v.id,
                          v.title,
                          v.publisher,
                          v.producer,
                          v.genre,
                          v.age_rating,
                          v.video_url,
                          AVG(CAST(r.rating AS FLOAT)) as avg_rating,
                          v.thumbnail_url
                   FROM videos v
                            LEFT JOIN ratings r ON v.id = r.video_id
                   WHERE v.title LIKE ?
                      OR v.genre LIKE ?
                      OR v.publisher LIKE ?
                   GROUP BY v.id, v.title, v.publisher, v.producer, v.genre, v.age_rating, v.video_url, v.thumbnail_url
                   ''', f'%{query}%', f'%{query}%', f'%{query}%')
    videos = cursor.fetchall()

    video_list = [{
        'id': v[0], 'title': v[1], 'publisher': v[2], 'producer': v[3],
        'genre': v[4], 'age_rating': v[5], 'video_url': v[6], 'avg_rating': round(v[7], 1) if v[7] is not None else 'N/A', 'thumbnail_url': v[8]
    } for v in videos]

    # Fetch user ratings
    user_ratings = {}
    cursor.execute('''
                   SELECT video_id, rating
                   FROM ratings
                   WHERE user_id = ?
                   ''', current_user.id)
    for row in cursor.fetchall():
        user_ratings[row[0]] = row[1]

    for video in video_list:
        video['user_rating'] = user_ratings.get(video['id'], 0)

    # Fetch comments
    comments_dict = {}
    if video_list:
        video_ids = [v['id'] for v in video_list]
        placeholders = ','.join(['?'] * len(video_ids))
        cursor.execute(f'''
            SELECT c.video_id, u.username, c.comment, c.created_at, c.sentiment
            FROM comments c
            JOIN users u ON c.user_id = u.id
            WHERE c.video_id IN ({placeholders})
            ORDER BY c.created_at DESC
        ''', video_ids)
        all_comments = cursor.fetchall()
        for comment in all_comments:
            vid = comment[0]
            if vid not in comments_dict:
                comments_dict[vid] = []
            comments_dict[vid].append({
                'username': comment[1],
                'comment': comment[2],
                'created_at': comment[3].strftime('%Y-%m-%d %H:%M:%S'),
                'sentiment': comment[4]
            })

    for video in video_list:
        video['comments'] = comments_dict.get(video['id'], [])

    conn.close()

    return jsonify(video_list)


@app.route('/watch-video/<int:video_id>')
@login_required
def watch_video(video_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT title, video_url FROM videos WHERE id = ?", video_id)
    video = cursor.fetchone()
    conn.close()
    if not video:
        flash('Video not found', 'error')
        return redirect(url_for('consumer_dashboard'))
    return render_template_string(WATCH_TEMPLATE, title=video[0], video_url=video[1])


@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('home'))


HOME_TEMPLATE = '''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>VideoVault - Premium Streaming Platform</title>
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }

        body {
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background: linear-gradient(135deg, #1e3c72 0%, #2a5298 100%);
            color: white;
            overflow-x: hidden;
        }

        .navbar {
            position: fixed;
            top: 0;
            width: 100%;
            background: rgba(0, 0, 0, 0.9);
            backdrop-filter: blur(10px);
            padding: 1rem 0;
            z-index: 1000;
        }

        .nav-container {
            max-width: 1400px;
            margin: 0 auto;
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 0 3rem;
        }

        .brand {
            font-size: 2rem;
            font-weight: 700;
            background: linear-gradient(45deg, #4facfe 0%, #00f2fe 100%);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            background-clip: text;
        }

        .nav-buttons {
            display: flex;
            gap: 1.5rem;
        }

        .nav-btn {
            padding: 12px 30px;
            text-decoration: none;
            border-radius: 50px;
            font-weight: 600;
            transition: all 0.3s ease;
            border: 2px solid transparent;
        }

        .nav-btn.primary {
            background: linear-gradient(45deg, #667eea 0%, #764ba2 100%);
            color: white;
        }

        .nav-btn.secondary {
            border: 2px solid #4facfe;
            color: #4facfe;
            background: transparent;
        }

        .nav-btn:hover {
            transform: translateY(-3px);
            box-shadow: 0 10px 25px rgba(0,0,0,0.3);
        }

        .hero {
            min-height: 100vh;
            display: flex;
            align-items: center;
            position: relative;
            overflow: hidden;
        }

        .hero::before {
            content: '';
            position: absolute;
            top: 0;
            left: 0;
            right: 0;
            bottom: 0;
            background: url('data:image/svg+xml,<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1000 1000"><defs><radialGradient id="a"><stop offset="0" stop-color="%234facfe" stop-opacity=".1"/><stop offset="1" stop-color="%2300f2fe" stop-opacity=".05"/></radialGradient></defs><circle cx="200" cy="200" r="150" fill="url(%23a)"/><circle cx="800" cy="800" r="200" fill="url(%23a)"/></svg>') no-repeat center;
            background-size: cover;
            z-index: -1;
        }

        .hero-content {
            max-width: 1400px;
            margin: 0 auto;
            padding: 0 3rem;
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 4rem;
            align-items: center;
        }

        .hero-text {
            padding-top: 100px;
        }

        .hero-title {
            font-size: 4.5rem;
            font-weight: 800;
            line-height: 1.1;
            margin-bottom: 2rem;
            background: linear-gradient(45deg, white 0%, #4facfe 100%);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            background-clip: text;
        }

        .hero-subtitle {
            font-size: 1.4rem;
            margin-bottom: 3rem;
            color: rgba(255,255,255,0.8);
            line-height: 1.6;
        }

        .cta-buttons {
            display: flex;
            gap: 2rem;
            margin-bottom: 3rem;
        }

        .cta-btn {
            padding: 18px 40px;
            font-size: 1.1rem;
            font-weight: 700;
            text-decoration: none;
            border-radius: 15px;
            transition: all 0.3s ease;
            display: inline-block;
        }

        .cta-primary {
            background: linear-gradient(45deg, #4facfe 0%, #00f2fe 100%);
            color: black;
            border: none;
        }

        .cta-secondary {
            background: transparent;
            color: white;
            border: 3px solid rgba(255,255,255,0.3);
        }

        .cta-btn:hover {
            transform: translateY(-5px);
            box-shadow: 0 15px 35px rgba(0,0,0,0.3);
        }

        .hero-visual {
            display: flex;
            justify-content: center;
            align-items: center;
            position: relative;
        }

        .visual-circle {
            width: 400px;
            height: 400px;
            border: 3px solid rgba(79, 172, 254, 0.3);
            border-radius: 50%;
            display: flex;
            align-items: center;
            justify-content: center;
            position: relative;
            animation: rotate 20s linear infinite;
        }

        .visual-center {
            width: 200px;
            height: 200px;
            background: linear-gradient(45deg, #667eea 0%, #764ba2 100%);
            border-radius: 50%;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 3rem;
            color: white;
            box-shadow: 0 20px 40px rgba(0,0,0,0.3);
        }

        @keyframes rotate {
            from { transform: rotate(0deg); }
            to { transform: rotate(360deg); }
        }

        .features {
            padding: 8rem 0;
            background: rgba(0,0,0,0.2);
            position: relative;
        }

        .features-container {
            max-width: 1400px;
            margin: 0 auto;
            padding: 0 3rem;
        }

        .section-title {
            text-align: center;
            font-size: 3rem;
            font-weight: 700;
            margin-bottom: 4rem;
            color: white;
        }

        .features-grid {
            display: grid;
            grid-template-columns: repeat(3, 1fr);
            gap: 3rem;
        }

        .feature-card {
            background: rgba(255,255,255,0.1);
            backdrop-filter: blur(10px);
            border: 1px solid rgba(255,255,255,0.2);
            border-radius: 20px;
            padding: 3rem 2rem;
            text-align: center;
            transition: all 0.3s ease;
            position: relative;
            overflow: hidden;
        }

        .feature-card::before {
            content: '';
            position: absolute;
            top: 0;
            left: 0;
            right: 0;
            bottom: 0;
            background: linear-gradient(135deg, rgba(79, 172, 254, 0.1) 0%, rgba(0, 242, 254, 0.1) 100%);
            opacity: 0;
            transition: opacity 0.3s ease;
        }

        .feature-card:hover::before {
            opacity: 1;
        }

        .feature-card:hover {
            transform: translateY(-10px);
            box-shadow: 0 20px 40px rgba(0,0,0,0.3);
        }

        .feature-icon {
            font-size: 4rem;
            margin-bottom: 2rem;
            background: linear-gradient(45deg, #4facfe 0%, #00f2fe 100%);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            background-clip: text;
        }

        .feature-title {
            font-size: 1.5rem;
            font-weight: 700;
            margin-bottom: 1rem;
            color: white;
        }

        .feature-desc {
            color: rgba(255,255,255,0.8);
            line-height: 1.6;
        }

        .footer {
            background: black;
            padding: 4rem 0;
            text-align: center;
        }

        .footer-container {
            max-width: 1400px;
            margin: 0 auto;
            padding: 0 3rem;
        }

        .footer-text {
            font-size: 1.2rem;
            color: rgba(255,255,255,0.6);
        }

        @media (max-width: 768px) {
            .hero-content {
                grid-template-columns: 1fr;
                text-align: center;
            }

            .features-grid {
                grid-template-columns: 1fr;
            }

            .hero-title {
                font-size: 3rem;
            }

            .nav-container {
                padding: 0 1rem;
            }
        }
    </style>
</head>
<body>
    <nav class="navbar">
        <div class="nav-container">
            <div class="brand">VideoVault</div>
            <div class="nav-buttons">
                <a href="{{ url_for('login') }}" class="nav-btn primary">Access Portal</a>
                <a href="{{ url_for('register') }}" class="nav-btn secondary">Join Community</a>
            </div>
        </div>
    </nav>

    <section class="hero">
        <div class="hero-content">
            <div class="hero-text">
                <h1 class="hero-title">VideoVault</h1>
                <p class="hero-subtitle">Experience premium video streaming like never before. Discover, upload, and engage with content in our cutting-edge platform designed for creators and viewers.</p>
                <div class="cta-buttons">
                    <a href="{{ url_for('register') }}" class="cta-btn cta-primary">Start Journey</a>
                    <a href="{{ url_for('login') }}" class="cta-btn cta-secondary">Member Login</a>
                </div>
            </div>
            <div class="hero-visual">
                <div class="visual-circle">
                    <div class="visual-center">VV</div>
                </div>
            </div>
        </div>
    </section>

    <section class="features">
        <div class="features-container">
            <h2 class="section-title">Platform Features</h2>
            <div class="features-grid">
                <div class="feature-card">
                    <div class="feature-icon">📤</div>
                    <h3 class="feature-title">Content Creation</h3>
                    <p class="feature-desc">Upload and share your videos with advanced encoding and streaming technology</p>
                </div>
                <div class="feature-card">
                    <div class="feature-icon">🔍</div>
                    <h3 class="feature-title">Smart Discovery</h3>
                    <p class="feature-desc">Find content that matches your interests with intelligent search and recommendations</p>
                </div>
                <div class="feature-card">
                    <div class="feature-icon">💬</div>
                    <h3 class="feature-title">Community Hub</h3>
                    <p class="feature-desc">Connect with creators through ratings, comments, and interactive discussions</p>
                </div>
            </div>
        </div>
    </section>

    <footer class="footer">
        <div class="footer-container">
            <p class="footer-text">&copy; 2024 VideoVault. Premium Streaming Experience.</p>
        </div>
    </footer>
</body>
</html>
'''

REGISTER_TEMPLATE = '''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>VideoVault - Join Our Community</title>
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }

        body {
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background: linear-gradient(45deg, #0f0f23 0%, #1a1a2e 50%, #16213e 100%);
            min-height: 100vh;
            display: flex;
            align-items: center;
            color: white;
        }

        .register-container {
            width: 100%;
            max-width: 1200px;
            margin: 0 auto;
            display: grid;
            grid-template-columns: 1fr 1fr;
            box-shadow: 0 25px 50px rgba(0,0,0,0.5);
            border-radius: 25px;
            overflow: hidden;
            background: rgba(255,255,255,0.02);
            backdrop-filter: blur(10px);
            border: 1px solid rgba(255,255,255,0.1);
        }

        .info-panel {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            padding: 4rem 3rem;
            display: flex;
            flex-direction: column;
            justify-content: center;
            position: relative;
            overflow: hidden;
        }

        .info-panel::before {
            content: '';
            position: absolute;
            top: -50%;
            right: -50%;
            width: 200%;
            height: 200%;
            background: radial-gradient(circle, rgba(255,255,255,0.1) 1px, transparent 1px);
            background-size: 50px 50px;
            animation: float 20s infinite linear;
        }

        @keyframes float {
            0% { transform: translate(-50%, -50%) rotate(0deg); }
            100% { transform: translate(-50%, -50%) rotate(360deg); }
        }

        .info-content {
            position: relative;
            z-index: 2;
        }

        .info-title {
            font-size: 3.5rem;
            font-weight: 800;
            margin-bottom: 2rem;
            line-height: 1.1;
        }

        .info-text {
            font-size: 1.2rem;
            margin-bottom: 3rem;
            line-height: 1.6;
            opacity: 0.9;
        }

        .info-features {
            list-style: none;
        }

        .info-features li {
            padding: 0.8rem 0;
            display: flex;
            align-items: center;
            font-size: 1.1rem;
        }

        .info-features li::before {
            content: '✦';
            margin-right: 1rem;
            color: #4facfe;
            font-size: 1.2rem;
        }

        .form-panel {
            padding: 4rem 3rem;
            background: rgba(0,0,0,0.3);
        }

        .form-header {
            text-align: center;
            margin-bottom: 3rem;
        }

        .form-title {
            font-size: 2.5rem;
            font-weight: 700;
            margin-bottom: 1rem;
            background: linear-gradient(45deg, #4facfe 0%, #00f2fe 100%);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            background-clip: text;
        }

        .form-subtitle {
            color: rgba(255,255,255,0.7);
            font-size: 1.1rem;
        }

        .alert {
            padding: 1rem 1.5rem;
            border-radius: 10px;
            margin-bottom: 2rem;
            border-left: 4px solid;
            font-size: 0.95rem;
        }

        .alert-success {
            background: rgba(40, 167, 69, 0.2);
            color: #28a745;
            border-color: #28a745;
        }

        .alert-error {
            background: rgba(220, 53, 69, 0.2);
            color: #dc3545;
            border-color: #dc3545;
        }

        .input-group {
            margin-bottom: 2rem;
            position: relative;
        }

        .input-label {
            display: block;
            margin-bottom: 0.8rem;
            font-weight: 600;
            color: rgba(255,255,255,0.9);
            font-size: 0.95rem;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }

        .input-field, .input-select {
            width: 100%;
            padding: 1.2rem 1.5rem;
            background: rgba(255,255,255,0.1);
            border: 2px solid rgba(255,255,255,0.2);
            border-radius: 12px;
            color: white;
            font-size: 1rem;
            transition: all 0.3s ease;
            backdrop-filter: blur(10px);
        }

        .input-field:focus, .input-select:focus {
            outline: none;
            border-color: #4facfe;
            background: rgba(255,255,255,0.15);
            box-shadow: 0 0 20px rgba(79, 172, 254, 0.3);
        }

        .input-field::placeholder {
            color: rgba(255,255,255,0.5);
        }

        .role-selector {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 1rem;
            margin-top: 1rem;
        }

        .role-card {
            position: relative;
        }

        .role-input {
            display: none;
        }

        .role-label {
            display: block;
            padding: 2rem 1.5rem;
            background: rgba(255,255,255,0.05);
            border: 2px solid rgba(255,255,255,0.2);
            border-radius: 15px;
            text-align: center;
            cursor: pointer;
            transition: all 0.3s ease;
            font-weight: 600;
        }

        .role-label:hover {
            background: rgba(255,255,255,0.1);
            transform: translateY(-5px);
        }

        .role-input:checked + .role-label {
            background: linear-gradient(45deg, #4facfe 0%, #00f2fe 100%);
            color: black;
            border-color: transparent;
            box-shadow: 0 10px 30px rgba(79, 172, 254, 0.4);
        }

        .submit-btn {
            width: 100%;
            padding: 1.5rem;
            background: linear-gradient(45deg, #667eea 0%, #764ba2 100%);
            border: none;
            border-radius: 15px;
            color: white;
            font-size: 1.1rem;
            font-weight: 700;
            cursor: pointer;
            transition: all 0.3s ease;
            margin: 2rem 0 1rem;
            text-transform: uppercase;
            letter-spacing: 1px;
        }

        .submit-btn:hover {
            transform: translateY(-3px);
            box-shadow: 0 15px 35px rgba(0,0,0,0.3);
        }

        .form-footer {
            text-align: center;
            padding-top: 2rem;
            border-top: 1px solid rgba(255,255,255,0.1);
        }

        .form-footer a {
            color: #4facfe;
            text-decoration: none;
            font-weight: 600;
            transition: color 0.3s ease;
        }

        .form-footer a:hover {
            color: #00f2fe;
        }

        @media (max-width: 768px) {
            .register-container {
                grid-template-columns: 1fr;
                margin: 1rem;
            }

            .info-panel, .form-panel {
                padding: 2rem 1.5rem;
            }

            .info-title {
                font-size: 2.5rem;
            }
        }
    </style>
</head>
<body>
    <div class="register-container">
        <div class="info-panel">
            <div class="info-content">
                <h1 class="info-title">Welcome to VideoVault</h1>
                <p class="info-text">Join thousands of creators and viewers in the ultimate video streaming community.</p>
                <ul class="info-features">
                    <li>Premium video hosting and streaming</li>
                    <li>Advanced content discovery tools</li>
                    <li>Interactive community features</li>
                    <li>Professional analytics dashboard</li>
                </ul>
            </div>
        </div>

        <div class="form-panel">
            <div class="form-header">
                <h2 class="form-title">Create Account</h2>
                <p class="form-subtitle">Begin your VideoVault experience</p>
            </div>

            {% with messages = get_flashed_messages(with_categories=true) %}
                {% if messages %}
                    {% for category, message in messages %}
                        <div class="alert alert-{{ category }}">{{ message }}</div>
                    {% endfor %}
                {% endif %}
            {% endwith %}

            <form method="POST">
                <div class="input-group">
                    <label for="username" class="input-label">Username</label>
                    <input type="text" id="username" name="username" class="input-field" placeholder="Choose your username" required>
                </div>

                <div class="input-group">
                    <label for="email" class="input-label">Email Address</label>
                    <input type="email" id="email" name="email" class="input-field" placeholder="your.email@domain.com" required>
                </div>

                <div class="input-group">
                    <label for="password" class="input-label">Password</label>
                    <input type="password" id="password" name="password" class="input-field" placeholder="Create secure password" required>
                </div>

                <div class="input-group">
                    <label class="input-label">Select Your Role</label>
                    <div class="role-selector">
                        <div class="role-card">
                            <input type="radio" id="creator" name="user_type" value="creator" class="role-input" required>
                            <label for="creator" class="role-label">Content Creator</label>
                        </div>
                        <div class="role-card">
                            <input type="radio" id="consumer" name="user_type" value="consumer" class="role-input" required>
                            <label for="consumer" class="role-label">Content Viewer</label>
                        </div>
                    </div>
                </div>

                <button type="submit" class="submit-btn">Launch Account</button>
            </form>

            <div class="form-footer">
                <a href="{{ url_for('home') }}">← Return to Homepage</a>
            </div>
        </div>
    </div>
</body>
</html>
'''

LOGIN_TEMPLATE = '''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>VideoVault - Access Portal</title>
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }

        body {
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background: radial-gradient(ellipse at top, #1e3c72 0%, #2a5298 100%);
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
            color: white;
            position: relative;
            overflow-x: hidden;
            padding: 2rem 0;
        }

        body::before {
            content: '';
            position: fixed;
            top: 0;
            left: 0;
            right: 0;
            bottom: 0;
            background: url('data:image/svg+xml,<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><circle cx="20" cy="20" r="2" fill="%234facfe" opacity="0.3"/><circle cx="80" cy="80" r="3" fill="%2300f2fe" opacity="0.2"/><circle cx="40" cy="70" r="1" fill="%23667eea" opacity="0.4"/></svg>') repeat;
            animation: drift 30s infinite linear;
            z-index: -1;
        }

        @keyframes drift {
            0% { transform: translateX(0); }
            100% { transform: translateX(-100px); }
        }

        .login-wrapper {
            position: relative;
            z-index: 10;
            width: 100%;
            max-width: 500px;
            margin: 0 auto;
        }

        .login-container {
            background: rgba(0,0,0,0.4);
            backdrop-filter: blur(20px);
            border: 1px solid rgba(255,255,255,0.1);
            border-radius: 30px;
            overflow: hidden;
            box-shadow: 0 30px 60px rgba(0,0,0,0.5);
        }

        .login-header {
            text-align: center;
            padding: 2rem 0 1.5rem;
            background: linear-gradient(135deg, rgba(79, 172, 254, 0.2) 0%, rgba(0, 242, 254, 0.2) 100%);
            border-bottom: 1px solid rgba(255,255,255,0.1);
        }

        .login-logo {
            width: 80px;
            height: 80px;
            margin: 0 auto 1.5rem;
            background: linear-gradient(45deg, #4facfe 0%, #00f2fe 100%);
            border-radius: 50%;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 2rem;
            font-weight: 800;
            color: black;
            box-shadow: 0 15px 30px rgba(79, 172, 254, 0.3);
        }

        .login-title {
            font-size: 2.2rem;
            font-weight: 800;
            margin-bottom: 0.5rem;
            background: linear-gradient(45deg, white 0%, #4facfe 100%);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            background-clip: text;
        }

        .login-subtitle {
            font-size: 1rem;
            color: rgba(255,255,255,0.7);
        }

        .login-form {
            padding: 2rem 2.5rem 2rem;
        }

        .alert {
            padding: 1rem 1.2rem;
            border-radius: 12px;
            margin-bottom: 1.5rem;
            border-left: 4px solid;
            font-size: 0.9rem;
            backdrop-filter: blur(10px);
        }

        .alert-success {
            background: rgba(40, 167, 69, 0.2);
            color: #4ade80;
            border-color: #4ade80;
        }

        .alert-error {
            background: rgba(220, 53, 69, 0.2);
            color: #f87171;
            border-color: #f87171;
        }

        .form-group {
            margin-bottom: 2rem;
            position: relative;
        }

        .form-label {
            display: block;
            margin-bottom: 0.8rem;
            font-weight: 700;
            color: rgba(255,255,255,0.9);
            font-size: 0.85rem;
            text-transform: uppercase;
            letter-spacing: 1.5px;
        }

        .form-input {
            width: 100%;
            padding: 1.2rem 1.8rem;
            background: rgba(255,255,255,0.08);
            border: 2px solid rgba(255,255,255,0.15);
            border-radius: 50px;
            color: white;
            font-size: 1rem;
            transition: all 0.4s ease;
            backdrop-filter: blur(10px);
        }

        .form-input:focus {
            outline: none;
            border-color: #4facfe;
            background: rgba(255,255,255,0.12);
            box-shadow: 0 0 25px rgba(79, 172, 254, 0.3);
            transform: translateY(-2px);
        }

        .form-input::placeholder {
            color: rgba(255,255,255,0.4);
        }

        .login-button {
            width: 100%;
            padding: 1.5rem;
            background: linear-gradient(45deg, #4facfe 0%, #00f2fe 100%);
            border: none;
            border-radius: 50px;
            color: black;
            font-size: 1.1rem;
            font-weight: 800;
            cursor: pointer;
            transition: all 0.4s ease;
            margin: 1.5rem 0 1rem;
            text-transform: uppercase;
            letter-spacing: 1.5px;
            position: relative;
            overflow: hidden;
        }

        .login-button::before {
            content: '';
            position: absolute;
            top: 0;
            left: -100%;
            width: 100%;
            height: 100%;
            background: linear-gradient(90deg, transparent, rgba(255,255,255,0.3), transparent);
            transition: left 0.5s;
        }

        .login-button:hover::before {
           left: 100%;
       }

       .login-button:hover {
           transform: translateY(-3px);
           box-shadow: 0 15px 35px rgba(79, 172, 254, 0.4);
       }

       .form-footer {
           text-align: center;
           padding-top: 1.5rem;
           border-top: 1px solid rgba(255,255,255,0.1);
       }

       .back-link {
           color: #4facfe;
           text-decoration: none;
           font-weight: 600;
           font-size: 0.9rem;
           transition: all 0.3s ease;
           padding: 0.5rem 1rem;
           border-radius: 20px;
           border: 1px solid rgba(79, 172, 254, 0.3);
           display: inline-block;
       }

       .back-link:hover {
           background: rgba(79, 172, 254, 0.1);
           color: #00f2fe;
           transform: translateY(-2px);
       }

       @media (max-width: 768px) {
           body {
               padding: 1rem;
           }

           .login-wrapper {
               max-width: 100%;
           }

           .login-form {
               padding: 1.5rem 2rem;
           }

           .login-title {
               font-size: 1.8rem;
           }

           .login-logo {
               width: 70px;
               height: 70px;
               font-size: 1.8rem;
           }

           .form-input {
               padding: 1rem 1.5rem;
               font-size: 0.95rem;
           }

           .login-button {
               padding: 1.3rem;
               font-size: 1rem;
           }
       }

       @media (max-height: 700px) {
           .login-header {
               padding: 1.5rem 0 1rem;
           }

           .login-logo {
               width: 60px;
               height: 60px;
               margin-bottom: 1rem;
               font-size: 1.5rem;
           }

           .login-title {
               font-size: 1.8rem;
               margin-bottom: 0.3rem;
           }

           .login-subtitle {
               font-size: 0.9rem;
           }

           .form-group {
               margin-bottom: 1.5rem;
           }

           .login-form {
               padding: 1.5rem 2.5rem 1.5rem;
           }
       }
   </style>
</head>
<body>
   <div class="login-wrapper">
       <div class="login-container">
           <div class="login-header">
               <div class="login-logo">VV</div>
               <h1 class="login-title">Access Portal</h1>
               <p class="login-subtitle">Enter your VideoVault credentials</p>
           </div>

           <div class="login-form">
               {% with messages = get_flashed_messages(with_categories=true) %}
                   {% if messages %}
                       {% for category, message in messages %}
                           <div class="alert alert-{{ category }}">{{ message }}</div>
                       {% endfor %}
                   {% endif %}
               {% endwith %}

               <form method="POST">
                   <div class="form-group">
                       <label for="username" class="form-label">Username</label>
                       <input type="text" id="username" name="username" class="form-input" placeholder="Enter your username" required>
                   </div>

                   <div class="form-group">
                       <label for="password" class="form-label">Password</label>
                       <input type="password" id="password" name="password" class="form-input" placeholder="Enter your password" required>
                   </div>

                   <button type="submit" class="login-button">Access Account</button>
               </form>

               <div class="form-footer">
                   <a href="{{ url_for('home') }}" class="back-link">← Back to Homepage</a>
               </div>
           </div>
       </div>
   </div>
</body>
</html>
'''

CREATOR_DASHBOARD_TEMPLATE = '''
<!DOCTYPE html>
<html lang="en">
<head>
   <meta charset="UTF-8">
   <meta name="viewport" content="width=device-width, initial-scale=1.0">
   <title>VideoVault - Creator Studio</title>
   <style>
       * {
           margin: 0;
           padding: 0;
           box-sizing: border-box;
       }

       body {
           font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
           background: linear-gradient(135deg, #0c0c0c 0%, #1a1a2e 50%, #16213e 100%);
           color: white;
           min-height: 100vh;
       }

       .dashboard-header {
           background: rgba(0,0,0,0.8);
           backdrop-filter: blur(20px);
           border-bottom: 2px solid rgba(79, 172, 254, 0.3);
           padding: 1.5rem 0;
           position: sticky;
           top: 0;
           z-index: 100;
       }

       .header-content {
           max-width: 1400px;
           margin: 0 auto;
           padding: 0 2rem;
           display: flex;
           justify-content: space-between;
           align-items: center;
       }

       .brand-section {
           display: flex;
           align-items: center;
           gap: 1rem;
       }

       .brand-icon {
           width: 50px;
           height: 50px;
           background: linear-gradient(45deg, #4facfe 0%, #00f2fe 100%);
           border-radius: 50%;
           display: flex;
           align-items: center;
           justify-content: center;
           font-weight: 800;
           color: black;
           font-size: 1.2rem;
       }

       .brand-text {
           font-size: 1.8rem;
           font-weight: 700;
           background: linear-gradient(45deg, #4facfe 0%, #00f2fe 100%);
           -webkit-background-clip: text;
           -webkit-text-fill-color: transparent;
           background-clip: text;
       }

       .user-controls {
           display: flex;
           align-items: center;
           gap: 2rem;
       }

       .welcome-text {
           font-size: 1.1rem;
           color: rgba(255,255,255,0.8);
       }

       .username-display {
           font-weight: 700;
           color: #4facfe;
       }

       .logout-button {
           padding: 0.8rem 2rem;
           background: linear-gradient(45deg, #667eea 0%, #764ba2 100%);
           color: white;
           text-decoration: none;
           border-radius: 25px;
           font-weight: 600;
           transition: all 0.3s ease;
           border: 1px solid rgba(255,255,255,0.2);
       }

       .logout-button:hover {
           transform: translateY(-2px);
           box-shadow: 0 10px 25px rgba(0,0,0,0.3);
       }

       .main-content {
           max-width: 1000px;
           margin: 0 auto;
           padding: 4rem 2rem;
       }

       .studio-card {
           background: rgba(255,255,255,0.05);
           backdrop-filter: blur(20px);
           border: 1px solid rgba(255,255,255,0.1);
           border-radius: 25px;
           padding: 4rem;
           box-shadow: 0 25px 50px rgba(0,0,0,0.3);
           position: relative;
           overflow: hidden;
       }

       .studio-card::before {
           content: '';
           position: absolute;
           top: 0;
           left: 0;
           right: 0;
           height: 4px;
           background: linear-gradient(90deg, #4facfe 0%, #00f2fe 100%);
       }

       .card-header {
           text-align: center;
           margin-bottom: 4rem;
           position: relative;
       }

       .studio-title {
           font-size: 3rem;
           font-weight: 800;
           margin-bottom: 1rem;
           background: linear-gradient(45deg, white 0%, #4facfe 100%);
           -webkit-background-clip: text;
           -webkit-text-fill-color: transparent;
           background-clip: text;
       }

       .studio-subtitle {
           font-size: 1.3rem;
           color: rgba(255,255,255,0.7);
           line-height: 1.6;
       }

       .alert {
           padding: 1.5rem 2rem;
           border-radius: 15px;
           margin-bottom: 3rem;
           border-left: 5px solid;
           font-size: 1rem;
           backdrop-filter: blur(10px);
       }

       .alert-success {
           background: rgba(34, 197, 94, 0.2);
           color: #4ade80;
           border-color: #4ade80;
       }

       .alert-error {
           background: rgba(239, 68, 68, 0.2);
           color: #f87171;
           border-color: #f87171;
       }

       .upload-form {
           display: grid;
           gap: 2.5rem;
       }

       .form-section {
           display: grid;
           grid-template-columns: 1fr 1fr;
           gap: 2rem;
       }

       .form-section.full {
           grid-template-columns: 1fr;
       }

       .field-group {
           display: flex;
           flex-direction: column;
           gap: 0.8rem;
       }

       .field-label {
           font-weight: 700;
           color: rgba(255,255,255,0.9);
           font-size: 0.95rem;
           text-transform: uppercase;
           letter-spacing: 1px;
       }

       .field-input, .field-select {
           padding: 1.5rem 1.8rem;
           background: rgba(255,255,255,0.08);
           border: 2px solid rgba(255,255,255,0.15);
           border-radius: 15px;
           color: white;
           font-size: 1.1rem;
           transition: all 0.3s ease;
           backdrop-filter: blur(10px);
       }

       .field-input:focus, .field-select:focus {
           outline: none;
           border-color: #4facfe;
           background: rgba(255,255,255,0.12);
           box-shadow: 0 0 25px rgba(79, 172, 254, 0.3);
       }

       .field-input::placeholder {
           color: rgba(255,255,255,0.4);
       }

       select option {
           background-color: white;
           color: black;
       }

       .file-upload-zone {
           border: 3px dashed rgba(79, 172, 254, 0.5);
           border-radius: 20px;
           padding: 4rem 2rem;
           text-align: center;
           background: rgba(79, 172, 254, 0.05);
           cursor: pointer;
           transition: all 0.4s ease;
           position: relative;
           overflow: hidden;
       }

       .file-upload-zone::before {
           content: '';
           position: absolute;
           top: 0;
           left: -100%;
           width: 100%;
           height: 100%;
           background: linear-gradient(90deg, transparent, rgba(79, 172, 254, 0.1), transparent);
           transition: left 0.5s;
       }

       .file-upload-zone:hover::before {
           left: 100%;
       }

       .file-upload-zone:hover {
           border-color: #4facfe;
           background: rgba(79, 172, 254, 0.1);
           transform: scale(1.02);
       }

       .file-upload-zone.active {
           border-color: #00f2fe;
           background: rgba(0, 242, 254, 0.1);
           transform: scale(1.05);
       }

       .upload-icon {
           font-size: 4rem;
           margin-bottom: 2rem;
           color: #4facfe;
       }

       .upload-text {
           font-size: 1.4rem;
           font-weight: 600;
           margin-bottom: 0.8rem;
           color: white;
       }

       .upload-hint {
           color: rgba(255,255,255,0.6);
           font-size: 1rem;
       }

       .file-preview {
           background: rgba(34, 197, 94, 0.2);
           border: 2px solid #4ade80;
           border-radius: 15px;
           padding: 2rem;
           margin-top: 2rem;
           color: #4ade80;
           display: none;
       }

       .progress-section {
           margin: 2rem 0;
           display: none;
       }

       .progress-text {
           font-size: 1rem;
           color: rgba(255,255,255,0.8);
           margin-bottom: 1rem;
           text-align: center;
       }

       .progress-bar {
           width: 100%;
           height: 12px;
           background: rgba(255,255,255,0.1);
           border-radius: 6px;
           overflow: hidden;
           position: relative;
       }

       .progress-fill {
           height: 100%;
           background: linear-gradient(90deg, #4facfe 0%, #00f2fe 100%);
           width: 0%;
           transition: width 0.3s ease;
           position: relative;
       }

       .progress-fill::after {
           content: '';
           position: absolute;
           top: 0;
           left: 0;
           right: 0;
           bottom: 0;
           background: linear-gradient(90deg, transparent, rgba(255,255,255,0.3), transparent);
           animation: shimmer 2s infinite;
       }

       @keyframes shimmer {
           0% { transform: translateX(-100%); }
           100% { transform: translateX(100%); }
       }

       .upload-button {
           width: 100%;
           padding: 2rem;
           background: linear-gradient(45deg, #4facfe 0%, #00f2fe 100%);
           border: none;
           border-radius: 20px;
           color: black;
           font-size: 1.3rem;
           font-weight: 800;
           cursor: pointer;
           transition: all 0.4s ease;
           text-transform: uppercase;
           letter-spacing: 2px;
           margin-top: 2rem;
           position: relative;
           overflow: hidden;
       }

       .upload-button::before {
           content: '';
           position: absolute;
           top: 0;
           left: -100%;
           width: 100%;
           height: 100%;
           background: linear-gradient(90deg, transparent, rgba(255,255,255,0.3), transparent);
           transition: left 0.5s;
       }

       .upload-button:hover::before {
           left: 100%;
       }

       .upload-button:hover {
           transform: translateY(-5px);
           box-shadow: 0 20px 40px rgba(79, 172, 254, 0.4);
       }

       .upload-button:disabled {
           background: rgba(255,255,255,0.2);
           color: rgba(255,255,255,0.5);
           cursor: not-allowed;
           transform: none;
           box-shadow: none;
       }

       #fileInput {
           display: none;
       }

       @media (max-width: 768px) {
           .form-section {
               grid-template-columns: 1fr;
           }

           .main-content {
               padding: 2rem 1rem;
           }

           .studio-card {
               padding: 2rem;
           }

           .studio-title {
               font-size: 2rem;
           }

           .header-content {
               padding: 0 1rem;
           }
       }
   </style>
</head>
<body>
   <header class="dashboard-header">
       <div class="header-content">
           <div class="brand-section">
               <div class="brand-icon">VV</div>
               <div class="brand-text">Creator Studio</div>
           </div>
           <div class="user-controls">
               <div class="welcome-text">
                   Welcome back, <span class="username-display">{{ current_user.username }}</span>
               </div>
               <a href="{{ url_for('logout') }}" class="logout-button">Exit Studio</a>
           </div>
       </div>
   </header>

   <main class="main-content">
       <div class="studio-card">
           <div class="card-header">
               <h1 class="studio-title">Content Upload</h1>
               <p class="studio-subtitle">Share your creative vision with the VideoVault community</p>
           </div>

           {% with messages = get_flashed_messages(with_categories=true) %}
               {% if messages %}
                   {% for category, message in messages %}
                       <div class="alert alert-{{ category }}">{{ message }}</div>
                   {% endfor %}
               {% endif %}
           {% endwith %}

           <form method="POST" action="{{ url_for('upload_video') }}" enctype="multipart/form-data" id="uploadForm" class="upload-form">
               <div class="form-section">
                   <div class="field-group">
                       <label for="title" class="field-label">Content Title</label>
                       <input type="text" id="title" name="title" class="field-input" placeholder="Enter video title" required>
                   </div>
                   <div class="field-group">
                       <label for="publisher" class="field-label">Publisher Name</label>
                       <input type="text" id="publisher" name="publisher" class="field-input" placeholder="Publisher information" required>
                   </div>
               </div>

               <div class="form-section">
                   <div class="field-group">
                       <label for="producer" class="field-label">Producer Details</label>
                       <input type="text" id="producer" name="producer" class="field-input" placeholder="Producer information" required>
                   </div>
                   <div class="field-group">
                       <label for="genre" class="field-label">Content Category</label>
                       <select id="genre" name="genre" class="field-select" required>
                           <option value="">Select Category</option>
                           <option value="Action">Action & Adventure</option>
                           <option value="Comedy">Comedy & Humor</option>
                           <option value="Drama">Drama & Theater</option>
                           <option value="Horror">Horror & Thriller</option>
                           <option value="Romance">Romance & Love</option>
                           <option value="Sci-Fi">Science Fiction</option>
                           <option value="Documentary">Documentary</option>
                           <option value="Animation">Animation & Cartoon</option>
                           <option value="Thriller">Suspense & Thriller</option>
                           <option value="Adventure">Adventure & Quest</option>
                       </select>
                   </div>
               </div>

               <div class="form-section full">
                   <div class="field-group">
                       <label for="age_rating" class="field-label">Audience Rating</label>
                       <select id="age_rating" name="age_rating" class="field-select" required>
                           <option value="">Choose Rating</option>
                           <option value="G">G - All Ages Welcome</option>
                           <option value="PG">PG - Parental Guidance Suggested</option>
                           <option value="PG-13">PG-13 - Teen & Adult Content</option>
                           <option value="R">R - Mature Audiences Only</option>
                           <option value="NC-17">NC-17 - Adult Exclusive</option>
                           <option value="18">18+ - Adult Premium Content</option>
                       </select>
                   </div>
               </div>

               <div class="field-group">
                   <label class="field-label">Video File Upload</label>
                   <div class="file-upload-zone" onclick="document.getElementById('fileInput').click()">
                       <div class="upload-icon">🎬</div>
                       <div class="upload-text">Select Video File</div>
                       <div class="upload-hint">Click here or drag & drop your video</div>
                   </div>
                   <input type="file" id="fileInput" name="video" accept="video/*" required>
                   <div class="file-preview" id="filePreview"></div>
               </div>

               <div class="progress-section" id="progressSection">
                   <div class="progress-text">Processing upload...</div>
                   <div class="progress-bar">
                       <div class="progress-fill" id="progressFill"></div>
                   </div>
               </div>

               <button type="submit" class="upload-button" id="uploadButton">Launch Content</button>
           </form>
       </div>
   </main>

   <script>
       const fileInput = document.getElementById('fileInput');
       const uploadZone = document.querySelector('.file-upload-zone');
       const filePreview = document.getElementById('filePreview');
       const uploadForm = document.getElementById('uploadForm');
       const progressSection = document.getElementById('progressSection');
       const progressFill = document.getElementById('progressFill');
       const uploadButton = document.getElementById('uploadButton');

       fileInput.addEventListener('change', handleFileSelection);

       function handleFileSelection(event) {
           const file = event.target.files[0];
           if (file) {
               showFilePreview(file);
               uploadZone.classList.add('active');
           }
       }

       function showFilePreview(file) {
           filePreview.style.display = 'block';
           filePreview.innerHTML = `
               <strong>File Ready:</strong> ${file.name}<br>
               <strong>Size:</strong> ${(file.size / 1024 / 1024).toFixed(2)} MB<br>
               <strong>Type:</strong> ${file.type}<br>
               <strong>Status:</strong> Ready for upload
           `;
       }

       // Drag and drop functionality
       ['dragenter', 'dragover', 'dragleave', 'drop'].forEach(eventName => {
           uploadZone.addEventListener(eventName, preventDefaults, false);
           document.body.addEventListener(eventName, preventDefaults, false);
       });

       function preventDefaults(e) {
           e.preventDefault();
           e.stopPropagation();
       }

       ['dragenter', 'dragover'].forEach(eventName => {
           uploadZone.addEventListener(eventName, () => {
               uploadZone.classList.add('active');
           }, false);
       });

       ['dragleave', 'drop'].forEach(eventName => {
           uploadZone.addEventListener(eventName, () => {
               uploadZone.classList.remove('active');
           }, false);
       });

       uploadZone.addEventListener('drop', handleFileDrop, false);

       function handleFileDrop(e) {
           const files = e.dataTransfer.files;
           if (files.length > 0) {
               fileInput.files = files;
               handleFileSelection({ target: { files } });
           }
       }

       uploadForm.addEventListener('submit', function(e) {
           uploadButton.textContent = 'Processing Upload...';
           uploadButton.disabled = true;
           progressSection.style.display = 'block';

           simulateProgress();
       });

       function simulateProgress() {
           let progress = 0;
           const interval = setInterval(() => {
               progress += Math.random() * 12;
               if (progress > 95) progress = 95;
               progressFill.style.width = progress + '%';
           }, 400);

           setTimeout(() => {
               clearInterval(interval);
               progressFill.style.width = '100%';
           }, 4000);
       }
   </script>
</body>
</html>
'''

CONSUMER_DASHBOARD_TEMPLATE = '''
<!DOCTYPE html>
<html lang="en">
<head>
   <meta charset="UTF-8">
   <meta name="viewport" content="width=device-width, initial-scale=1.0">
   <title>VideoVault - Content Hub</title>
   <style>
       * {
           margin: 0;
           padding: 0;
           box-sizing: border-box;
       }

       body {
           font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
           background: linear-gradient(135deg, #0f0f23 0%, #1a1a2e 50%, #16213e 100%);
           color: white;
           min-height: 100vh;
       }

       .top-navigation {
           background: rgba(0,0,0,0.9);
           backdrop-filter: blur(20px);
           border-bottom: 2px solid rgba(79, 172, 254, 0.3);
           padding: 1.5rem 0;
           position: sticky;
           top: 0;
           z-index: 1000;
       }

       .nav-content {
           max-width: 1600px;
           margin: 0 auto;
           padding: 0 2rem;
           display: grid;
           grid-template-columns: auto 1fr auto;
           gap: 3rem;
           align-items: center;
       }

       .platform-brand {
           display: flex;
           align-items: center;
           gap: 1rem;
       }

       .brand-logo {
           width: 50px;
           height: 50px;
           background: linear-gradient(45deg, #4facfe 0%, #00f2fe 100%);
           border-radius: 50%;
           display: flex;
           align-items: center;
           justify-content: center;
           font-weight: 800;
           color: black;
           font-size: 1.2rem;
       }

       .brand-name {
           font-size: 1.8rem;
           font-weight: 800;
           background: linear-gradient(45deg, #4facfe 0%, #00f2fe 100%);
           -webkit-background-clip: text;
           -webkit-text-fill-color: transparent;
           background-clip: text;
       }

       .search-area {
           position: relative;
           max-width: 600px;
           width: 100%;
       }

       .search-box {
           width: 100%;
           padding: 1.2rem 2rem;
           background: rgba(255,255,255,0.1);
           border: 2px solid rgba(255,255,255,0.2);
           border-radius: 50px;
           color: white;
           font-size: 1.1rem;
           transition: all 0.3s ease;
           backdrop-filter: blur(10px);
       }

       .search-box:focus {
           outline: none;
           border-color: #4facfe;
           background: rgba(255,255,255,0.15);
           box-shadow: 0 0 25px rgba(79, 172, 254, 0.3);
       }

       .search-box::placeholder {
           color: rgba(255,255,255,0.5);
       }

       .search-button {
           position: absolute;
           right: 8px;
           top: 50%;
           transform: translateY(-50%);
           background: linear-gradient(45deg, #4facfe 0%, #00f2fe 100%);
           border: none;
           padding: 0.8rem 1.8rem;
           border-radius: 25px;
           color: black;
           font-weight: 700;
           cursor: pointer;
           transition: all 0.3s ease;
       }

       .search-button:hover {
           transform: translateY(-50%) scale(1.05);
           box-shadow: 0 5px 15px rgba(79, 172, 254, 0.4);
       }

       .user-section {
           display: flex;
           align-items: center;
           gap: 2rem;
       }

       .user-greeting {
           font-size: 1.1rem;
           color: rgba(255,255,255,0.8);
       }

       .current-user {
           font-weight: 700;
           color: #4facfe;
       }

       .exit-button {
           padding: 0.8rem 2rem;
           background: linear-gradient(45deg, #667eea 0%, #764ba2 100%);
           color: white;
           text-decoration: none;
           border-radius: 25px;
           font-weight: 600;
           transition: all 0.3s ease;
           border: 1px solid rgba(255,255,255,0.2);
       }

       .exit-button:hover {
           transform: translateY(-2px);
           box-shadow: 0 10px 25px rgba(0,0,0,0.3);
       }

       .content-area {
           max-width: 1600px;
           margin: 0 auto;
           padding: 3rem 2rem;
       }

       .page-header {
           text-align: center;
           margin-bottom: 4rem;
       }

       .main-title {
           font-size: 3.5rem;
           font-weight: 800;
           margin-bottom: 1.5rem;
           background: linear-gradient(45deg, white 0%, #4facfe 100%);
           -webkit-background-clip: text;
           -webkit-text-fill-color: transparent;
           background-clip: text;
       }

       .subtitle {
           font-size: 1.3rem;
           color: rgba(255,255,255,0.7);
       }

       .content-grid {
           display: grid;
           grid-template-columns: repeat(auto-fill, minmax(450px, 1fr));
           gap: 3rem;
       }

       .video-container {
           background: rgba(255,255,255,0.05);
           backdrop-filter: blur(20px);
           border: 1px solid rgba(255,255,255,0.1);
           border-radius: 25px;
           overflow: hidden;
           transition: all 0.4s ease;
           position: relative;
       }

       .video-container::before {
           content: '';
           position: absolute;
           top: 0;
           left: 0;
           right: 0;
           height: 3px;
           background: linear-gradient(90deg, #4facfe 0%, #00f2fe 100%);
       }

       .video-container:hover {
           transform: translateY(-8px);
           box-shadow: 0 25px 50px rgba(0,0,0,0.4);
       }

       .video-thumbnail {
           width: 100%;
           height: auto;
           display: block;
       }

       .video-title-bar {
           background: rgba(0,0,0,0.6);
           padding: 1.5rem 2rem;
           font-size: 1.3rem;
           font-weight: 700;
           color: white;
           text-align: center;
       }

       .video-info-grid {
           display: grid;
           grid-template-columns: 1fr 1fr;
           gap: 0;
           background: rgba(255,255,255,0.08);
           border-bottom: 1px solid rgba(255,255,255,0.1);
       }

       .info-cell {
           padding: 1.2rem 1.5rem;
           border-right: 1px solid rgba(255,255,255,0.1);
           display: flex;
           flex-direction: column;
           gap: 0.5rem;
       }

       .info-cell:last-child {
           border-right: none;
       }

       .info-label {
           font-size: 0.85rem;
           color: rgba(255,255,255,0.6);
           text-transform: uppercase;
           letter-spacing: 1px;
       }

       .info-value {
           font-size: 1rem;
           color: white;
           font-weight: 600;
       }

       .video-actions {
           display: grid;
           grid-template-columns: 1fr auto auto;
           gap: 1rem;
           padding: 2rem;
           align-items: center;
       }

       .rating-section {
           display: flex;
           align-items: center;
           gap: 1rem;
       }

       .rating-stars {
           display: flex;
           gap: 0.3rem;
       }

       .star {
           font-size: 1.5rem;
           color: rgba(255,255,255,0.3);
           cursor: pointer;
           transition: all 0.2s ease;
       }

       .star.active {
           color: #fbbf24;
       }

       .star:hover {
           color: #fbbf24;
           transform: scale(1.2);
       }

       .avg-rating {
           font-size: 1rem;
           color: #fbbf24;
           font-weight: 600;
       }

       .action-buttons {
           display: flex;
           gap: 1rem;
       }

       .action-button {
           padding: 0.8rem 1.5rem;
           border-radius: 20px;
           border: none;
           font-weight: 600;
           cursor: pointer;
           transition: all 0.3s ease;
           text-decoration: none;
           font-size: 0.9rem;
           display: flex;
           align-items: center;
           gap: 0.5rem;
       }

       .watch-button {
           background: linear-gradient(45deg, #4facfe 0%, #00f2fe 100%);
           color: black;
       }

       .comment-button {
           background: rgba(255,255,255,0.1);
           color: white;
           border: 1px solid rgba(255,255,255,0.3);
       }

       .action-button:hover {
           transform: translateY(-2px);
           box-shadow: 0 8px 20px rgba(0,0,0,0.3);
       }

       .no-videos {
           text-align: center;
           padding: 6rem 2rem;
           background: rgba(255,255,255,0.05);
           backdrop-filter: blur(20px);
           border: 1px solid rgba(255,255,255,0.1);
           border-radius: 25px;
           grid-column: 1 / -1;
       }

       .no-videos-icon {
           font-size: 4rem;
           margin-bottom: 2rem;
           color: rgba(255,255,255,0.4);
       }

       .no-videos-title {
           font-size: 2rem;
           font-weight: 700;
           margin-bottom: 1rem;
           color: rgba(255,255,255,0.8);
       }

       .no-videos-text {
           font-size: 1.1rem;
           color: rgba(255,255,255,0.6);
           line-height: 1.6;
       }

       .comment-section {
           background: rgba(0,0,0,0.3);
           border-top: 1px solid rgba(255,255,255,0.1);
           padding: 1.5rem 2rem;
           display: none;
       }

       .comment-form {
           display: flex;
           gap: 1rem;
           margin-bottom: 1.5rem;
       }

       .comment-input {
           flex: 1;
           padding: 0.8rem 1.2rem;
           background: rgba(255,255,255,0.1);
           border: 1px solid rgba(255,255,255,0.2);
           border-radius: 20px;
           color: white;
           resize: none;
           min-height: 40px;
       }

       .comment-input::placeholder {
           color: rgba(255,255,255,0.5);
       }

       .comment-submit {
           padding: 0.8rem 1.5rem;
           background: linear-gradient(45deg, #4facfe 0%, #00f2fe 100%);
           color: black;
           border: none;
           border-radius: 20px;
           font-weight: 600;
           cursor: pointer;
           transition: all 0.3s ease;
       }

       .comment-submit:hover {
           transform: translateY(-2px);
           box-shadow: 0 5px 15px rgba(79, 172, 254, 0.3);
       }

       .comments-list {
           display: flex;
           flex-direction: column;
           gap: 1rem;
       }

       .comment-item {
           background: rgba(255,255,255,0.05);
           padding: 1rem 1.5rem;
           border-radius: 15px;
           border-left: 3px solid #4facfe;
       }

       .comment-header {
           display: flex;
           justify-content: space-between;
           align-items: center;
           margin-bottom: 0.5rem;
       }

       .comment-author {
           font-weight: 700;
           color: #4facfe;
           font-size: 0.9rem;
       }

       .comment-sentiment {
           padding: 0.3rem 0.8rem;
           border-radius: 15px;
           font-size: 0.75rem;
           font-weight: 600;
           text-transform: capitalize;
       }

       .comment-sentiment.positive {
           background: rgba(34, 197, 94, 0.2);
           color: #4ade80;
       }

       .comment-sentiment.negative {
           background: rgba(239, 68, 68, 0.2);
           color: #f87171;
       }

       .comment-sentiment.neutral {
           background: rgba(234, 179, 8, 0.2);
           color: #eab308;
       }

       .comment-text {
           color: rgba(255,255,255,0.8);
           line-height: 1.5;
       }

       .comment-time {
           font-size: 0.8rem;
           color: rgba(255,255,255,0.5);
           margin-top: 0.5rem;
       }

       .alert {
           padding: 1.2rem 2rem;
           border-radius: 15px;
           margin-bottom: 2rem;
           border-left: 4px solid;
           font-size: 1rem;
           backdrop-filter: blur(10px);
       }

       .alert-success {
           background: rgba(34, 197, 94, 0.2);
           color: #4ade80;
           border-color: #4ade80;
       }

       .alert-error {
           background: rgba(239, 68, 68, 0.2);
           color: #f87171;
           border-color: #f87171;
       }

       @media (max-width: 768px) {
           .nav-content {
               grid-template-columns: 1fr;
               gap: 1.5rem;
               text-align: center;
           }

           .search-area {
               order: 2;
           }

           .user-section {
               order: 3;
               justify-content: center;
           }

           .content-grid {
               grid-template-columns: 1fr;
           }

           .video-actions {
               grid-template-columns: 1fr;
               gap: 1.5rem;
           }

           .main-title {
               font-size: 2.5rem;
           }

           .content-area {
               padding: 2rem 1rem;
           }
       }
   </style>
</head>
<body>
   <nav class="top-navigation">
       <div class="nav-content">
           <div class="platform-brand">
               <div class="brand-logo">VV</div>
               <div class="brand-name">VideoVault</div>
           </div>
           <div class="search-area">
               <form method="GET" action="{{ url_for('consumer_dashboard') }}">
                   <input type="text" name="search" class="search-box" placeholder="Search for videos, creators, genres..." value="{{ request.args.get('search', '') }}">
                   <button type="submit" class="search-button">Search</button>
               </form>
           </div>
           <div class="user-section">
               <div class="user-greeting">
                   Welcome, <span class="current-user">{{ current_user.username }}</span>
               </div>
               <a href="{{ url_for('logout') }}" class="exit-button">Exit Hub</a>
           </div>
       </div>
   </nav>

   <main class="content-area">
       <header class="page-header">
           <h1 class="main-title">Content Library</h1>
           <p class="subtitle">Discover and enjoy premium video content from our creators</p>
       </header>

       {% with messages = get_flashed_messages(with_categories=true) %}
           {% if messages %}
               {% for category, message in messages %}
                   <div class="alert alert-{{ category }}">{{ message }}</div>
               {% endfor %}
           {% endif %}
       {% endwith %}

       <div class="content-grid">
           {% if videos %}
               {% for video in videos %}
                   <article class="video-container">
                       {% if video.thumbnail_url %}
                           <img src="{{ video.thumbnail_url }}" alt="Thumbnail for {{ video.title }}" class="video-thumbnail">
                       {% endif %}
                       <header class="video-title-bar">{{ video.title }}</header>

                       <div class="video-info-grid">
                           <div class="info-cell">
                               <span class="info-label">Publisher</span>
                               <span class="info-value">{{ video.publisher }}</span>
                           </div>
                           <div class="info-cell">
                               <span class="info-label">Producer</span>
                               <span class="info-value">{{ video.producer }}</span>
                           </div>
                           <div class="info-cell">
                               <span class="info-label">Genre</span>
                               <span class="info-value">{{ video.genre }}</span>
                           </div>
                           <div class="info-cell">
                               <span class="info-label">Rating</span>
                               <span class="info-value">{{ video.age_rating }}</span>
                           </div>
                       </div>

                       <div class="video-actions">
                           <div class="rating-section">
                               <div class="rating-stars" data-video-id="{{ video.id }}">
                                   {% for i in range(1, 6) %}
                                       <span class="star {{ 'active' if i <= video.user_rating else '' }}" data-rating="{{ i }}">★</span>
                                   {% endfor %}
                               </div>
                               <span class="avg-rating">Avg: {{ video.avg_rating }}</span>
                           </div>

                           <div class="action-buttons">
                               <a href="{{ url_for('watch_video', video_id=video.id) }}" class="action-button watch-button">
                                   ▶ Watch
                               </a>
                               <button class="action-button comment-button" onclick="toggleComments({{ video.id }})">
                                   💬 Comment
                               </button>
                           </div>
                       </div>

                       <div class="comment-section" id="comments-{{ video.id }}">
                           <form class="comment-form" onsubmit="submitComment(event, {{ video.id }})">
                               <textarea class="comment-input" placeholder="Share your thoughts about this video..." required></textarea>
                               <button type="submit" class="comment-submit">Post</button>
                           </form>

                           <div class="comments-list" id="comments-list-{{ video.id }}">
                               {% for comment in video.comments %}
                                   <div class="comment-item">
                                       <div class="comment-header">
                                           <div class="comment-author">{{ comment.username }}</div>
                                           <div class="comment-sentiment {{ comment.sentiment }}">{{ comment.sentiment }}</div>
                                       </div>
                                       <div class="comment-text">{{ comment.comment }}</div>
                                       <div class="comment-time">{{ comment.created_at }}</div>
                                   </div>
                               {% endfor %}
                           </div>
                       </div>
                   </article>
               {% endfor %}
           {% else %}
               <div class="no-videos">
                   <div class="no-videos-icon">🎬</div>
                   <h2 class="no-videos-title">No Content Available</h2>
                   <p class="no-videos-text">
                       {% if request.args.get('search') %}
                           No videos found matching your search criteria. Try different keywords or browse all content.
                       {% else %}
                           The content library is currently empty. Check back soon for new uploads from our creators.
                       {% endif %}
                   </p>
               </div>
           {% endif %}
       </div>
   </main>

   <script>
       // Star rating functionality
       document.addEventListener('DOMContentLoaded', function() {
           const ratingContainers = document.querySelectorAll('.rating-stars');

           ratingContainers.forEach(container => {
               const stars = container.querySelectorAll('.star');
               const videoId = container.dataset.videoId;

               stars.forEach((star, index) => {
                   star.addEventListener('click', function() {
                       const rating = index + 1;
                       submitRating(videoId, rating);
                       updateStarDisplay(container, rating);
                   });

                   star.addEventListener('mouseenter', function() {
                       highlightStars(container, index + 1);
                   });
               });

               container.addEventListener('mouseleave', function() {
                   const activeStars = container.querySelectorAll('.star.active').length;
                   highlightStars(container, activeStars);
               });
           });
       });

       function highlightStars(container, count) {
           const stars = container.querySelectorAll('.star');
           stars.forEach((star, index) => {
               if (index < count) {
                   star.style.color = '#fbbf24';
               } else {
                   star.style.color = 'rgba(255,255,255,0.3)';
               }
           });
       }

       function updateStarDisplay(container, rating) {
           const stars = container.querySelectorAll('.star');
           stars.forEach((star, index) => {
               if (index < rating) {
                   star.classList.add('active');
               } else {
                   star.classList.remove('active');
               }
           });
       }

       function submitRating(videoId, rating) {
           fetch('/rate-video', {
               method: 'POST',
               headers: {
                   'Content-Type': 'application/json',
               },
               body: JSON.stringify({ video_id: videoId, rating: rating })
           })
           .then(response => response.json())
           .then(data => {
               if (data.success) {
                   console.log('Rating submitted successfully');
               } else {
                   console.error('Error submitting rating');
               }
           })
           .catch(error => {
               console.error('Error:', error);
           });
       }

       function toggleComments(videoId) {
           const commentsSection = document.getElementById(`comments-${videoId}`);
           if (commentsSection.style.display === 'block') {
               commentsSection.style.display = 'none';
           } else {
               commentsSection.style.display = 'block';
           }
       }

       function submitComment(event, videoId) {
           event.preventDefault();
           const form = event.target;
           const textarea = form.querySelector('.comment-input');
           const comment = textarea.value.trim();

           if (!comment) return;

           fetch('/add-comment', {
               method: 'POST',
               headers: {
                   'Content-Type': 'application/json',
               },
               body: JSON.stringify({ video_id: videoId, comment: comment })
           })
           .then(response => response.json())
           .then(data => {
               if (data.success) {
                   textarea.value = '';
                   location.reload(); // Reload to show new comment
               } else {
                   alert('Error posting comment');
               }
           })
           .catch(error => {
               console.error('Error:', error);
               alert('Error posting comment');
           });
       }
   </script>
</body>
</html>
'''

WATCH_TEMPLATE = '''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{{ title }} - VideoVault</title>
    <style>
        body {
            background: #000;
            color: #fff;
            font-family: sans-serif;
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            height: 100vh;
            margin: 0;
        }
        video {
            max-width: 90%;
            max-height: 80vh;
        }
    </style>
</head>
<body>
    <h1>{{ title }}</h1>
    <video controls width="800">
        <source src="{{ video_url }}" type="video/mp4">
        Your browser does not support the video tag.
    </video>
    <a href="{{ url_for('consumer_dashboard') }}" style="color: #4facfe; margin-top: 20px;">Back to Dashboard</a>
</body>
</html>
'''

init_db()
if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)