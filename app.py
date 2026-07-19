from flask import Flask, session, redirect, url_for, request, flash
from extensions import db, csrf
from routes.main import main_bp
from routes.admin import admin_bp
from routes.auth import auth_bp
from routes.student_auth import student_auth_bp
from routes.student import student_bp
from routes.moderator import moderator_bp
import os
from dotenv import load_dotenv

load_dotenv()

def create_app():
    app = Flask(__name__)

    # ── Configuration ──────────────────────────────────────────────
    app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'change-me-in-production')
    
    db_url = os.environ.get('DATABASE_URL')
    if db_url:
        # SQLAlchemy 1.4+ requires postgresql:// instead of postgres://
        if db_url.startswith("postgres://"):
            db_url = db_url.replace("postgres://", "postgresql://", 1)
        app.config['SQLALCHEMY_DATABASE_URI'] = db_url
    else:
        # If running on Vercel, SQLite database must be in /tmp because the project directory is read-only
        if os.environ.get('VERCEL') or os.environ.get('NOW_REGION'):
            app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:////tmp/questionbank.db'
        else:
            app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///questionbank.db'
            
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    
    # Check if we are running in Vercel (read-only environment except for /tmp)
    if os.environ.get('VERCEL') or os.environ.get('NOW_REGION'):
        app.config['UPLOAD_FOLDER'] = '/tmp/uploads'
    else:
        app.config['UPLOAD_FOLDER'] = os.path.join(app.root_path, 'static', 'uploads')
    
    app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024   # 50 MB cap

    # Folder illana automatic ah create pannidum
    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

    # ── Extensions ─────────────────────────────────────────────────
    db.init_app(app)
    csrf.init_app(app)

    # ── Global Request Filter (Login Requirement) ──────────────────
    @app.before_request
    def require_login():
        if request.path.startswith('/static/') or request.path == '/favicon.ico':
            return
            
        public_paths = {
            '/',
            '/student/login',
            '/student/register',
            '/login',
            '/logout',
            '/student/logout',
            # Publicly accessible API routes (no login required to browse papers)
            '/api/papers',
            '/api/papers/bookmarks',
        }
        
        if request.path not in public_paths:
            if not session.get('student_logged_in') and not session.get('admin_logged_in'):
                # Return JSON for API/AJAX requests instead of an HTML redirect
                if request.path.startswith('/api/') or request.is_json or \
                        request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                    from flask import jsonify as _jsonify
                    return _jsonify({
                        'success': False,
                        'error': 'Please log in to access this feature.',
                        'redirect': '/student/login'
                    }), 401
                flash('Please register or log in to access the QuestBank portal.', 'info')
                return redirect(url_for('student_auth.student_login'))

    # ── Step 1: attach hooks to blueprints BEFORE registering them ─
    @admin_bp.before_request
    def require_admin_login():
        # BUG FIX 2: Check endpoint to prevent infinite redirect loops
        if not session.get('admin_logged_in') and request.endpoint != 'auth.login':
            return redirect(url_for('auth.login', next=request.url))
        # Staff moderators must use /moderator — block them from /admin
        if session.get('admin_logged_in') and session.get('admin_role') == 'staff':
            return redirect('/moderator')

    # ── Step 2: register blueprints AFTER all hooks are defined ────
    app.register_blueprint(main_bp)
    app.register_blueprint(admin_bp, url_prefix='/admin')
    app.register_blueprint(auth_bp)            # provides /login and /logout
    app.register_blueprint(student_auth_bp)    # provides /student/login, /student/register, /student/logout
    app.register_blueprint(student_bp)         # provides /student/dashboard, /leaderboard, /student/planner
    app.register_blueprint(moderator_bp, url_prefix='/moderator')  # Staff moderator portal

    # ── Custom uploads serving route (to handle Vercel /tmp uploads and local fallback) ──
    @app.route('/static/uploads/<path:filename>')
    def serve_uploads(filename):
        from flask import send_from_directory
        folder = app.config['UPLOAD_FOLDER']
        if not os.path.exists(os.path.join(folder, filename)):
            fallback = os.path.join(app.root_path, 'static', 'uploads')
            if os.path.exists(os.path.join(fallback, filename)):
                return send_from_directory(fallback, filename)
        return send_from_directory(folder, filename)

    # ── DB init ────────────────────────────────────────────────────
    with app.app_context():
        db.create_all()
        try:
            with db.engine.connect() as conn:
                conn.execute(db.text("ALTER TABLE question_papers ADD COLUMN download_count INTEGER DEFAULT 0"))
                conn.commit()
        except Exception:
            pass

        try:
            with db.engine.connect() as conn:
                conn.execute(db.text("ALTER TABLE group_messages ADD COLUMN reactions TEXT DEFAULT '{}'"))
                conn.commit()
        except Exception:
            pass

        try:
            with db.engine.connect() as conn:
                conn.execute(db.text("ALTER TABLE group_messages ADD COLUMN parent_id INTEGER REFERENCES group_messages(id) ON DELETE SET NULL"))
                conn.commit()
        except Exception:
            pass

        try:
            with db.engine.connect() as conn:
                conn.execute(db.text("ALTER TABLE questions ADD COLUMN ai_answer TEXT"))
                conn.commit()
        except Exception:
            pass

    return app

# VERCEL FIX: Intha line ulla irunthatha veliya eduthu potachu
app = create_app()

if __name__ == '__main__':
    # app = create_app() <--- Ithu munnadi inga irunthuchu, athu thaan thappu
    app.run(debug=True)