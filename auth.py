from flask import (
    Blueprint, render_template, request, session,
    redirect, url_for, flash, current_app
)
from urllib.parse import urlparse, urljoin
from datetime import datetime, timedelta
from functools import wraps
from models import AdminUser

auth_bp = Blueprint('auth', __name__)


# ─────────────────────────────────────────────────────────────────────────────
#  admin_required  –  decorator for master-admin-only routes
# ─────────────────────────────────────────────────────────────────────────────

def admin_required(f):
    """Blocks access unless the session holds a verified MASTER admin login (role=admin)."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('admin_logged_in'):
            flash('Please log in to access the admin panel.', 'warning')
            return redirect(url_for('auth.login', next=request.url))

        # Staff moderators must NOT access the master admin panel
        if session.get('admin_role') == 'staff':
            return redirect('/moderator')

        last_active = session.get('last_active')
        if last_active:
            elapsed = datetime.utcnow() - datetime.fromisoformat(last_active)
            timeout = timedelta(minutes=current_app.config.get('SESSION_TIMEOUT_MINUTES', 30))
            if elapsed > timeout:
                session.clear()
                flash('Your session has expired. Please log in again.', 'warning')
                return redirect(url_for('auth.login'))

        session['last_active'] = datetime.utcnow().isoformat()
        return f(*args, **kwargs)

    return decorated_function


# ─────────────────────────────────────────────────────────────────────────────
#  moderator_required  –  decorator for staff-moderator-only routes
# ─────────────────────────────────────────────────────────────────────────────

def moderator_required(f):
    """Blocks access unless the session holds a verified staff moderator login."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('admin_logged_in'):
            flash('Please log in to access the moderator panel.', 'warning')
            return redirect('/student/login?role=moderator')

        admin_role = session.get('admin_role', 'admin')
        if admin_role == 'admin':
            return redirect('/admin')

        last_active = session.get('last_active')
        if last_active:
            elapsed = datetime.utcnow() - datetime.fromisoformat(last_active)
            timeout = timedelta(minutes=current_app.config.get('SESSION_TIMEOUT_MINUTES', 30))
            if elapsed > timeout:
                session.clear()
                flash('Your session has expired. Please log in again.', 'warning')
                return redirect('/student/login?role=moderator')

        session['last_active'] = datetime.utcnow().isoformat()
        return f(*args, **kwargs)

    return decorated_function


# ─────────────────────────────────────────────────────────────────────────────
#  Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _is_safe_redirect(target: str) -> bool:
    """Reject open-redirect attacks by verifying the target stays on this host."""
    ref  = urlparse(request.host_url)
    test = urlparse(urljoin(request.host_url, target))
    return test.scheme in ('http', 'https') and ref.netloc == test.netloc


# ─────────────────────────────────────────────────────────────────────────────
#  Routes
# ─────────────────────────────────────────────────────────────────────────────

@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    if session.get('admin_logged_in'):
        if session.get('admin_role') == 'staff':
            return redirect('/moderator')
        return redirect('/admin')

    if request.method == 'GET':
        return redirect('/student/login?role=moderator')

    error = None

    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')

        user = AdminUser.query.filter_by(username=username).first()

        if user and user.check_password(password):
            # Retrieve selected role from form (default to moderator)
            login_role = request.form.get('login_role', 'moderator')
            # Verify role matches the stored role
            if user.role != login_role:
                error = 'Role mismatch. Please select the correct role for this account.'
                flash(error, 'error')
                return render_template('student_login.html', error=error, active_role=login_role)

            session.clear()
            session['admin_logged_in'] = True
            session['admin_username']  = user.username
            session['admin_role']      = user.role            # 'admin' or 'staff'
            session['user_role']       = user.role            # unified role identifier
            session['admin_dept']      = user.department or ''
            session['last_active']     = datetime.utcnow().isoformat()
            session.permanent          = True

            if user.role == 'staff':
                return redirect('/moderator')
            return redirect('/admin')

        error = 'Invalid username or password. Please try again.'

    # Determine which tab to show on error based on submitted role
    submitted_role = request.form.get('login_role', 'moderator')
    active_role = 'admin' if submitted_role == 'admin' else 'moderator'
    return render_template('student_login.html', error=error, active_role=active_role)


@auth_bp.route('/logout')
def logout():
    session.clear()
    flash('Logged out successfully.', 'info')
    return redirect('/')