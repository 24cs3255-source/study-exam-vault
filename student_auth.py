from flask import (
    Blueprint, render_template, request, session,
    redirect, url_for, flash, jsonify
)
from models import Student, Badge, StudentBadge, StudentActivity
from extensions import db, csrf
from datetime import datetime
import random

student_auth_bp = Blueprint('student_auth', __name__)

# ── Avatar color palette ──────────────────────────────────────────────────────
AVATAR_COLORS = [
    '#007aff', '#5856d6', '#af52de', '#ff2d55', '#ff9500',
    '#30b0c7', '#34c759', '#5ac8fa', '#ff6482', '#8e8e93',
]


# ─────────────────────────────────────────────────────────────────────────────
#  Student Registration
# ─────────────────────────────────────────────────────────────────────────────

@student_auth_bp.route('/student/register', methods=['GET', 'POST'])
def register():
    if session.get('student_logged_in'):
        return redirect('/student/dashboard')

    error = None
    departments = ['CS', 'BCA', 'BA', 'BOTANY', 'PHYSICS', 'CHEMISTRY', 'MATHEMATICS', 'ENGLISH', 'COMMERCE', 'ECONOMICS']

    if request.method == 'POST':
        username     = request.form.get('username', '').strip().lower()
        email        = request.form.get('email', '').strip().lower()
        display_name = request.form.get('display_name', '').strip()
        password     = request.form.get('password', '')
        confirm      = request.form.get('confirm_password', '')
        department   = request.form.get('department', '').strip().upper()

        # ── Validation ────────────────────────────────────────────────────────
        if not username or not email or not password or not department:
            error = 'All fields are required.'
        elif len(username) < 3:
            error = 'Username must be at least 3 characters.'
        elif len(password) < 6:
            error = 'Password must be at least 6 characters.'
        elif password != confirm:
            error = 'Passwords do not match.'
        elif department not in departments:
            error = 'Please select a valid department.'
        elif Student.query.filter_by(username=username).first():
            error = 'Username is already taken.'
        elif Student.query.filter_by(email=email).first():
            error = 'Email is already registered.'
        else:
            # ── Create the student ────────────────────────────────────────────
            student = Student(
                username=username,
                email=email,
                display_name=display_name or username.title(),
                avatar_color=random.choice(AVATAR_COLORS),
                department=department,
            )
            student.set_password(password)
            db.session.add(student)
            db.session.flush()

            # Award "First Steps" badge
            first_badge = Badge.query.filter_by(name='First Steps').first()
            if first_badge:
                sb = StudentBadge(student_id=student.id, badge_id=first_badge.id)
                db.session.add(sb)
                activity = StudentActivity(
                    student_id=student.id,
                    activity_type='badge',
                    detail=f'Earned badge: {first_badge.icon} {first_badge.name}',
                    points_earned=0,
                )
                db.session.add(activity)

            db.session.commit()

            # Auto-login after registration
            session['student_logged_in'] = True
            session['student_id']        = student.id
            session['student_username']   = student.username

            flash('Welcome to EXAM-STUDY-VAULT! Your account has been created.', 'success')
            return redirect('/student/dashboard')

    return render_template('student_register.html', error=error)


# ─────────────────────────────────────────────────────────────────────────────
#  Student Login
# ─────────────────────────────────────────────────────────────────────────────

@student_auth_bp.route('/student/login', methods=['GET', 'POST'])
def student_login():
    if session.get('student_logged_in'):
        return redirect('/student/dashboard')

    error = None

    if request.method == 'POST':
        login_id = request.form.get('login_id', '').strip().lower()
        password = request.form.get('password', '')

        # Find by username or email
        student = Student.query.filter(
            db.or_(
                Student.username == login_id,
                Student.email == login_id
            )
        ).first()

        if student and student.check_password(password):
            session['student_logged_in'] = True
            session['student_id']        = student.id
            session['student_username']   = student.username

            flash(f'Welcome back, {student.display_name or student.username}!', 'success')
            return redirect('/student/dashboard')

        error = 'Invalid username/email or password.'

    return render_template('student_login.html', error=error)


# ─────────────────────────────────────────────────────────────────────────────
#  Student Logout
# ─────────────────────────────────────────────────────────────────────────────

@student_auth_bp.route('/student/logout')
def student_logout():
    # Preserve admin session if exists
    admin_logged_in = session.get('admin_logged_in')
    admin_username = session.get('admin_username')
    last_active = session.get('last_active')

    session.pop('student_logged_in', None)
    session.pop('student_id', None)
    session.pop('student_username', None)

    # Restore admin session
    if admin_logged_in:
        session['admin_logged_in'] = admin_logged_in
        session['admin_username'] = admin_username
        session['last_active'] = last_active

    flash('You have been logged out.', 'info')
    return redirect('/')
