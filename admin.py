from flask import (
    Blueprint, render_template, request, redirect,
    url_for, flash, current_app, jsonify, session
)
from werkzeug.utils import secure_filename
from models import QuestionPaper, AdminNotification, AdminUser, Student
from extensions import db, csrf, get_upload_path
from routes.auth import admin_required   # ← our custom decorator
import os, uuid, re, io, pdfplumber
from datetime import datetime

admin_bp = Blueprint('admin', __name__)

ALLOWED_EXTENSIONS = {'pdf'}

CLASS_CHOICES = ['I-year', 'II-year', 'III-year']


def allowed_file(filename: str) -> bool:
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def get_current_admin():
    username = session.get('admin_username')
    if username:
        return AdminUser.query.filter_by(username=username).first()
    return None


# ─────────────────────────────────────────────────────────────────────────────
#  Dashboard
# ─────────────────────────────────────────────────────────────────────────────

@admin_bp.route('/')
@admin_required
def dashboard():
    admin = get_current_admin()

    # ── Student stats ──────────────────────────────────────────────────────────
    student_count    = Student.query.count()
    top_students     = Student.query.order_by(Student.points.desc()).limit(8).all()
    recent_students  = Student.query.order_by(Student.created_at.desc()).limit(6).all()

    # Department-wise student breakdown
    all_depts = ['CS', 'BCA', 'BA', 'BOTANY', 'PHYSICS', 'CHEMISTRY',
                 'MATHEMATICS', 'ENGLISH', 'COMMERCE', 'ECONOMICS']
    dept_breakdown = []
    for dept in all_depts:
        count = Student.query.filter(
            db.func.upper(Student.department).like(f'%{dept}%')
        ).count()
        dept_breakdown.append({'dept': dept, 'count': count})

    # ── Papers & activity ─────────────────────────────────────────────────────
    total_papers   = QuestionPaper.query.count()
    pending_papers = QuestionPaper.query.filter_by(status='pending') \
                         .order_by(QuestionPaper.uploaded_at.desc()).all()
    recent_papers  = QuestionPaper.query.filter(
        db.or_(QuestionPaper.status == 'approved', QuestionPaper.status.is_(None))
    ).order_by(QuestionPaper.uploaded_at.desc()).limit(8).all()

    # ── Moderator / staff ─────────────────────────────────────────────────────
    # ── Most downloaded question papers ───────────────────────────────────────
    most_downloaded_papers = QuestionPaper.query.filter(
        db.or_(QuestionPaper.status == 'approved', QuestionPaper.status.is_(None))
    ).order_by(db.func.coalesce(QuestionPaper.download_count, 0).desc()).limit(12).all()

    # ── Department-wise question paper breakdown & lists ──────────────────────
    paper_dept_breakdown = []
    dept_papers_map = {}
    for dept in all_depts + ['All Departments']:
        approved_papers = QuestionPaper.query.filter(
            db.or_(QuestionPaper.status == 'approved', QuestionPaper.status.is_(None)),
            db.func.upper(QuestionPaper.department).like(f'%{dept.upper()}%')
        ).order_by(QuestionPaper.uploaded_at.desc()).all()
        pending_count = QuestionPaper.query.filter(
            QuestionPaper.status == 'pending',
            db.func.upper(QuestionPaper.department).like(f'%{dept.upper()}%')
        ).count()
        paper_dept_breakdown.append({
            'dept': dept,
            'count': len(approved_papers) + pending_count,
            'approved': len(approved_papers),
            'pending': pending_count
        })
        dept_papers_map[dept] = approved_papers

    # ── Moderator / staff ─────────────────────────────────────────────────────
    staff_users      = AdminUser.query.filter_by(role='staff').order_by(AdminUser.username).all()
    moderator_count  = len(staff_users)

    return render_template(
        'admin/dashboard.html',
        admin            = admin,
        # Students
        student_count    = student_count,
        top_students     = top_students,
        recent_students  = recent_students,
        dept_breakdown   = dept_breakdown,
        # Papers
        total_papers     = total_papers,
        pending_papers   = pending_papers,
        recent_papers    = recent_papers,
        most_downloaded_papers = most_downloaded_papers,
        paper_dept_breakdown   = paper_dept_breakdown,
        dept_papers_map        = dept_papers_map,
        # Moderators
        staff_users      = staff_users,
        moderator_count  = moderator_count,
        # Upload helpers
        all_depts        = all_depts,
        class_choices    = CLASS_CHOICES,
    )


# ─────────────────────────────────────────────────────────────────────────────
#  Upload
# ─────────────────────────────────────────────────────────────────────────────

@admin_bp.route('/upload', methods=['GET', 'POST'])
@admin_required
def upload():
    admin = get_current_admin()
    if request.method == 'POST':
        if 'pdf_file' not in request.files:
            flash('No file selected.', 'error')
            return redirect(request.url)

        file = request.files['pdf_file']
        if file.filename == '':
            flash('No file selected.', 'error')
            return redirect(request.url)

        if not allowed_file(file.filename):
            flash('Only PDF files are allowed.', 'error')
            return redirect(request.url)

        if admin and admin.role == 'staff' and admin.department:
            department = admin.department.upper()
        else:
            department = request.form['department'].strip()

        original_name = secure_filename(file.filename)
        unique_name   = f"{uuid.uuid4().hex}_{original_name}"
        save_path     = os.path.join(current_app.config['UPLOAD_FOLDER'], unique_name)
        file.save(save_path)

        paper = QuestionPaper(
            department   = department,
            semester     = request.form['semester'].strip(),
            subject_code = request.form['subject_code'].strip().upper(),
            subject_name = request.form['subject_name'].strip(),
            exam_type    = request.form['exam_type'].strip(),
            year         = int(request.form['year']),
            class_name   = request.form.get('class_name', '').strip() or None,
            filename     = unique_name,
            original_name= original_name,
        )
        db.session.add(paper)
        db.session.commit()
        flash('Question paper uploaded successfully!', 'success')
        return redirect(url_for('admin.dashboard'))

    return render_template('admin/upload.html', class_choices=CLASS_CHOICES, admin=admin)


# ─────────────────────────────────────────────────────────────────────────────
#  Delete
# ─────────────────────────────────────────────────────────────────────────────

@admin_bp.route('/delete/<int:paper_id>', methods=['POST'])
@admin_required
def delete_paper(paper_id):
    admin = get_current_admin()
    paper = QuestionPaper.query.get_or_404(paper_id)
    if admin and admin.role == 'staff' and admin.department:
        if admin.department.upper() not in paper.department.upper():
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.is_json:
                return jsonify({'success': False, 'error': 'Unauthorized. You can only delete papers for your own department.'}), 403
            flash('Unauthorized. You can only delete papers for your own department.', 'error')
            return redirect(url_for('admin.dashboard'))
            
    file_path = get_upload_path(paper.filename)
    try:
        if os.path.exists(file_path):
            os.remove(file_path)
    except Exception:
        pass
    db.session.delete(paper)
    db.session.commit()
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.is_json:
        return jsonify({'success': True, 'message': 'Paper deleted successfully.'})
    flash('Paper deleted.', 'success')
    return redirect(url_for('admin.dashboard'))


# ─────────────────────────────────────────────────────────────────────────────
#  Edit
# ─────────────────────────────────────────────────────────────────────────────

@admin_bp.route('/edit/<int:paper_id>', methods=['GET', 'POST'])
@admin_required                          # ← blocks unauthenticated access
def edit_paper(paper_id):
    paper = QuestionPaper.query.get_or_404(paper_id)

    if request.method == 'POST':
        # Flask-WTF validates CSRF token on this POST automatically.
        paper.department   = request.form['department'].strip()
        paper.semester     = request.form['semester'].strip()
        paper.subject_code = request.form['subject_code'].strip().upper()
        paper.subject_name = request.form['subject_name'].strip()
        paper.exam_type    = request.form['exam_type'].strip()
        paper.year         = int(request.form['year'])
        paper.class_name   = request.form.get('class_name', '').strip() or None  # ← NEW

        new_file = request.files.get('new_file')
        if new_file and new_file.filename != '':
            if not allowed_file(new_file.filename):
                flash('Only PDF files are allowed.', 'error')
                return redirect(request.url)

            old_path = get_upload_path(paper.filename)
            try:
                if os.path.exists(old_path):
                    os.remove(old_path)
            except Exception:
                pass

            original_name = secure_filename(new_file.filename)
            unique_name   = f"{uuid.uuid4().hex}_{original_name}"
            save_path     = os.path.join(current_app.config['UPLOAD_FOLDER'], unique_name)
            new_file.save(save_path)

            paper.filename      = unique_name
            paper.original_name = original_name

        db.session.commit()
        flash('Paper updated successfully!', 'success')
        return redirect(url_for('admin.dashboard'))

    return render_template('admin/edit.html', paper=paper, class_choices=CLASS_CHOICES)  # ← NEW


# ─────────────────────────────────────────────────────────────────────────────
#  Bulk Upload
# ─────────────────────────────────────────────────────────────────────────────

@admin_bp.route('/bulk-upload')
@admin_required
def bulk_upload():
    return render_template('admin/bulk_upload.html', class_choices=CLASS_CHOICES)


@admin_bp.route('/upload-ajax', methods=['POST'])
@admin_required
def upload_ajax():
    # Flask-WTF CSRFProtect automatically validates the CSRF token on POST.
    if 'pdf_file' not in request.files:
        return jsonify({'success': False, 'error': 'No file selected.'}), 400

    file = request.files['pdf_file']
    if file.filename == '':
        return jsonify({'success': False, 'error': 'No file selected.'}), 400

    if not allowed_file(file.filename):
        return jsonify({'success': False, 'error': 'Only PDF files are allowed.'}), 400

    try:
        original_name = secure_filename(file.filename)
        unique_name   = f"{uuid.uuid4().hex}_{original_name}"
        save_path     = os.path.join(current_app.config['UPLOAD_FOLDER'], unique_name)
        file.save(save_path)

        dept     = request.form.get('department', '').strip()
        sem      = request.form.get('semester', '').strip()
        sub_code = request.form.get('subject_code', '').strip().upper()
        sub_name = request.form.get('subject_name', '').strip()
        exam_t   = request.form.get('exam_type', '').strip()
        yr       = request.form.get('year')
        cl_name  = request.form.get('class_name', '').strip() or None

        if not (dept and sem and sub_code and sub_name and exam_t and yr):
            return jsonify({'success': False, 'error': 'All fields are required.'}), 400

        paper = QuestionPaper(
            department   = dept,
            semester     = sem,
            subject_code = sub_code,
            subject_name = sub_name,
            exam_type    = exam_t,
            year         = int(yr),
            class_name   = cl_name,
            filename     = unique_name,
            original_name= original_name,
        )
        db.session.add(paper)
        db.session.commit()
        return jsonify({'success': True, 'paper': paper.to_dict()})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500


# ─────────────────────────────────────────────────────────────────────────────
#  PDF Parsing Helpers
# ─────────────────────────────────────────────────────────────────────────────

def extract_subject_code(text):
    # Match patterns like U18CS501, 20UCA32, 18UCO5C1, CS3352, EC3201
    # Allow boundary of word boundary or underscore
    patterns = [
        r'(?:\b|(?<=_))([A-Z]\d{2}[A-Z]{2,3}\d{1,3}[A-Z0-9]*)(?:\b|(?=_))',
        r'(?:\b|(?<=_))(\d{2}[A-Z]{2,4}\d{1,2}[A-Z0-9]*)(?:\b|(?=_))',
        r'(?:\b|(?<=_))([A-Z]{2,4}\d{3,5}[A-Z0-9]*)(?:\b|(?=_))',
    ]
    for pattern in patterns:
        matches = re.findall(pattern, text)
        for match in matches:
            code = match.upper()
            if code not in ['2020', '2021', '2022', '2023', '2024', '2025', '2026', 'PAGE']:
                return code
    return None

def extract_subject_name(text):
    # Look for lines with labels
    labels = [
        r'(?:title\s+of\s+the\s+paper|subject|paper\s+title|name\s+of\s+the\s+paper)\s*[:\-]\s*(.+)',
        r'(?:title|paper)\s*[:\-]\s*(.+)'
    ]
    for label in labels:
        match = re.search(label, text, re.IGNORECASE)
        if match:
            name = match.group(1).strip()
            name = re.sub(r'\s*\([^)]*\)', '', name)
            if len(name) > 3 and len(name) < 100:
                return name.strip().title()
    return None

def extract_exam_year(text):
    match = re.search(r'\b(202[0-9]|2030|201[8-9])\b', text)
    if match:
        return int(match.group(1))
    return None

def extract_exam_type(text):
    text_lower = text.lower()
    if 'model' in text_lower:
        return 'Model Exam'
    if 'internal' in text_lower or 'cia' in text_lower:
        return 'Internal Exam (CIA)'
    return 'Semester Exam'

def extract_semester(text):
    romans = {'I': '1st', 'II': '2nd', 'III': '3rd', 'IV': '4th', 'V': '5th', 'VI': '6th'}
    words = {
        'first': '1st', 'second': '2nd', 'third': '3rd', 'fourth': '4th', 'fifth': '5th', 'sixth': '6th',
        'one': '1st', 'two': '2nd', 'three': '3rd', 'four': '4th', 'five': '5th', 'six': '6th'
    }
    match = re.search(r'\bsemester\s*[-–:]?\s*\b(I{1,3}|IV|V|VI)\b', text, re.IGNORECASE)
    if match:
        sem = romans.get(match.group(1).upper())
        if sem:
            return sem
    match = re.search(r'\b(first|second|third|fourth|fifth|sixth)\s+semester\b', text, re.IGNORECASE)
    if match:
        sem = words.get(match.group(1).lower())
        if sem:
            return sem
    match = re.search(r'\bsem(?:ester)?\s*[-–:]?\s*([1-6])\b', text, re.IGNORECASE)
    if match:
        val = match.group(1)
        sems = {'1': '1st', '2': '2nd', '3': '3rd', '4': '4th', '5': '5th', '6': '6th'}
        return sems.get(val)
    return None

def extract_department(text):
    text_upper = text.upper()
    departments = {
        'B.Sc Computer Science': ['COMPUTER SCIENCE', 'B.SC CS', 'B.SC. COMPUTER SCIENCE'],
        'BCA': ['COMPUTER APPLICATIONS', 'BCA', 'B.C.A.'],
        'B.Sc Information Technology': ['INFORMATION TECHNOLOGY', 'B.SC IT', 'B.SC. IT'],
        'B.Sc Mathematics': ['MATHEMATICS', 'B.SC MATHS', 'B.SC. MATHEMATICS'],
        'B.Sc Physics': ['PHYSICS', 'B.SC PHYSICS', 'B.SC. PHYSICS'],
        'B.Sc Chemistry': ['CHEMISTRY', 'B.SC CHEMISTRY', 'B.SC. CHEMISTRY'],
        'B.Com General': ['B.COM GENERAL', 'COMMERCE', 'B.COM.', 'B.COM GENERAL'],
        'B.Com Computer Applications': ['B.COM CA', 'B.COM. (CA)', 'B.COM COMPUTER APPLICATIONS'],
        'B.Com Professional Accounting': ['B.COM PA', 'B.COM. (PA)', 'B.COM PROFESSIONAL ACCOUNTING'],
        'BBA': ['BUSINESS ADMINISTRATION', 'BBA', 'B.B.A.'],
        'B.A English Literature': ['ENGLISH LITERATURE', 'B.A. ENGLISH', 'B.A ENGLISH'],
        'B.A Tamil Literature': ['TAMIL LITERATURE', 'B.A. TAMIL', 'B.A TAMIL'],
        'B.A Economics': ['ECONOMICS', 'B.A. ECONOMICS', 'B.A ECONOMICS'],
        'M.Sc Computer Science': ['M.SC COMPUTER SCIENCE', 'M.SC. COMPUTER SCIENCE', 'M.SC. CS'],
        'MCA': ['MCA', 'M.C.A.', 'MASTER OF COMPUTER APPLICATIONS'],
        'M.Com': ['M.COM', 'M.COM.', 'MASTER OF COMMERCE'],
        'M.A English': ['M.A. ENGLISH', 'M.A ENGLISH', 'MASTER OF ARTS IN ENGLISH']
    }
    for dept, keywords in departments.items():
        for kw in keywords:
            if kw in text_upper:
                return dept
    return None

def extract_metadata_from_filename(filename):
    base = os.path.splitext(filename)[0]
    
    code = None
    code_match = re.search(r'(?:\b|(?<=_))([A-Z]\d{2}[A-Z]{2,3}\d{1,3}[A-Z0-9]*)(?:\b|(?=_))', base, re.IGNORECASE)
    if not code_match:
        code_match = re.search(r'(?:\b|(?<=_))(\d{2}[A-Z]{2,4}\d{1,2}[A-Z0-9]*)(?:\b|(?=_))', base, re.IGNORECASE)
    if not code_match:
        code_match = re.search(r'(?:\b|(?<=_))([A-Z]{2,4}\d{3,5}[A-Z0-9]*)(?:\b|(?=_))', base, re.IGNORECASE)
        
    if code_match:
        code = code_match.group(1).upper()
        base = base.replace(code_match.group(0), "")
        
    clean_base = re.sub(r'[\s_\-]+', ' ', base).strip()
    
    name = clean_base
    name = re.sub(r'\b(202[0-9]|2030|201[8-9])\b', '', name)
    name = re.sub(r'\b(model|semester|exam|cia|internal|university|part\s*iv|paper\s*[a-z0-9\-]+)\b', '', name, flags=re.IGNORECASE)
    name = re.sub(r'\s+', ' ', name).strip()
    
    subject_name = name.title() if len(name) > 2 else ""
    
    year = None
    year_match = re.search(r'\b(202[0-9]|2030|201[8-9])\b', clean_base)
    if year_match:
        year = int(year_match.group(1))
        
    exam_type = 'Semester Exam'
    clean_base_lower = clean_base.lower()
    if 'model' in clean_base_lower:
        exam_type = 'Model Exam'
    elif 'cia' in clean_base_lower or 'internal' in clean_base_lower:
        exam_type = 'Internal Exam (CIA)'
        
    semester = None
    sem_match = re.search(r'\bsem(?:ester)?\s*[-–:_]?\s*([1-6])\b', clean_base_lower)
    if sem_match:
        sems = {'1': '1st', '2': '2nd', '3': '3rd', '4': '4th', '5': '5th', '6': '6th'}
        semester = sems.get(sem_match.group(1))
    else:
        for w, sem_val in [('first', '1st'), ('second', '2nd'), ('third', '3rd'), ('fourth', '4th'), ('fifth', '5th'), ('sixth', '6th')]:
            if w in clean_base_lower:
                semester = sem_val
                break
                
    department = None
    departments = {
        'B.Sc Computer Science': ['computer science', 'cs'],
        'BCA': ['computer applications', 'bca'],
        'B.Sc Information Technology': ['information technology', 'it'],
        'B.Sc Mathematics': ['mathematics', 'maths'],
        'B.Sc Physics': ['physics'],
        'B.Sc Chemistry': ['chemistry'],
        'B.Com General': ['commerce', 'bcom', 'b.com'],
        'B.Com Computer Applications': ['bcom ca', 'b.com ca'],
        'BBA': ['business administration', 'bba'],
        'B.A English Literature': ['english'],
        'B.A Tamil Literature': ['tamil'],
        'B.A Economics': ['economics']
    }
    for dept, keywords in departments.items():
        for kw in keywords:
            if f" {kw} " in f" {clean_base_lower} " or clean_base_lower.startswith(kw) or clean_base_lower.endswith(kw):
                department = dept
                break
        if department:
            break
            
    return {
        'subject_code': code or "",
        'subject_name': subject_name,
        'year': year or "",
        'exam_type': exam_type,
        'semester': semester or "",
        'department': department or ""
    }

def parse_pdf_metadata(file_stream, filename):
    extracted_text = ""
    try:
        with pdfplumber.open(file_stream) as pdf:
            pages_to_check = pdf.pages[:2]
            for page in pages_to_check:
                text = page.extract_text()
                if text and len(text.strip()) > 50:
                    extracted_text += "\n" + text
    except Exception as e:
        print(f"pdfplumber extraction error: {e}")
        
    extracted_text = extracted_text.strip()
    
    subject_code = None
    subject_name = None
    year = None
    exam_type = None
    semester = None
    department = None
    
    if extracted_text:
        subject_code = extract_subject_code(extracted_text)
        subject_name = extract_subject_name(extracted_text)
        year = extract_exam_year(extracted_text)
        exam_type = extract_exam_type(extracted_text)
        semester = extract_semester(extracted_text)
        department = extract_department(extracted_text)
        
    fn_meta = extract_metadata_from_filename(filename)
    
    if not subject_code:
        subject_code = fn_meta['subject_code']
    if not subject_name:
        subject_name = fn_meta['subject_name']
    if not year:
        year = fn_meta['year']
    if not exam_type:
        exam_type = fn_meta['exam_type']
    if not semester:
        semester = fn_meta['semester']
    if not department:
        department = fn_meta['department']
        
    if not exam_type:
        exam_type = 'Semester Exam'
        
    if not year:
        year = datetime.now().year
        
    class_name = ""
    if semester:
        if semester in ['1st', '2nd']:
            class_name = 'I-year'
        elif semester in ['3rd', '4th']:
            class_name = 'II-year'
        elif semester in ['5th', '6th']:
            class_name = 'III-year'
            
    return {
        'subject_code': subject_code or "",
        'subject_name': subject_name or "",
        'year': year,
        'exam_type': exam_type,
        'semester': semester or "",
        'department': department or "",
        'class_name': class_name
    }


# ─────────────────────────────────────────────────────────────────────────────
#  PDF Parsing Route
# ─────────────────────────────────────────────────────────────────────────────

@admin_bp.route('/parse-pdf', methods=['POST'])
@admin_required
def parse_pdf():
    if 'pdf_file' not in request.files:
        return jsonify({'success': False, 'error': 'No file uploaded'}), 400
        
    file = request.files['pdf_file']
    if file.filename == '':
        return jsonify({'success': False, 'error': 'No file selected'}), 400
        
    try:
        file.seek(0)
        file_bytes = file.read()
        file_stream = io.BytesIO(file_bytes)
        
        metadata = parse_pdf_metadata(file_stream, file.filename)
        return jsonify({'success': True, 'metadata': metadata})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# ─────────────────────────────────────────────────────────────────────────────
#  Moderation Actions (Approve / Reject contributions)
# ─────────────────────────────────────────────────────────────────────────────

@admin_bp.route('/approve/<int:paper_id>', methods=['POST'])
@csrf.exempt
@admin_required
def approve_paper(paper_id):
    admin = get_current_admin()
    paper = QuestionPaper.query.get_or_404(paper_id)
    if admin and admin.role == 'staff' and admin.department:
        if admin.department.upper() not in paper.department.upper():
            return jsonify({'success': False, 'error': 'You can only approve papers for your own department.'}), 403

    paper.status = 'approved'
    db.session.commit()
    return jsonify({'success': True, 'message': 'Paper approved successfully!'})


@admin_bp.route('/reject/<int:paper_id>', methods=['POST'])
@csrf.exempt
@admin_required
def reject_paper(paper_id):
    admin = get_current_admin()
    paper = QuestionPaper.query.get_or_404(paper_id)
    if admin and admin.role == 'staff' and admin.department:
        if admin.department.upper() not in paper.department.upper():
            return jsonify({'success': False, 'error': 'You can only reject papers for your own department.'}), 403

    file_path = get_upload_path(paper.filename)
    try:
        if os.path.exists(file_path):
            os.remove(file_path)
    except Exception:
        pass
        
    db.session.delete(paper)
    db.session.commit()
    return jsonify({'success': True, 'message': 'Paper rejected and deleted successfully!'})


@admin_bp.route('/notifications/dismiss/<int:notif_id>', methods=['POST'])
@csrf.exempt
@admin_required
def dismiss_notification(notif_id):
    notif = AdminNotification.query.get_or_404(notif_id)
    notif.is_read = True
    db.session.commit()
    return jsonify({'success': True, 'message': 'Notification marked as read'})


# ─────────────────────────────────────────────────────────────────────────────
#  Staff Management Routes
# ─────────────────────────────────────────────────────────────────────────────

@admin_bp.route('/staff/create', methods=['POST'])
@admin_required
def create_staff():
    admin = get_current_admin()
    if not admin or admin.role != 'admin':
        flash('Unauthorized. Only master administrators can manage staff accounts.', 'error')
        return redirect('/admin')

    username   = request.form.get('username', '').strip().lower()
    password   = request.form.get('password', '').strip()
    department = request.form.get('department', '').strip().upper()

    if not username or not password or not department:
        flash('All fields are required.', 'error')
        return redirect('/admin')

    existing = AdminUser.query.filter_by(username=username).first()
    if existing:
        flash('Username is already taken.', 'error')
        return redirect('/admin')

    staff = AdminUser(
        username=username,
        role='staff',
        department=department
    )
    staff.set_password(password)
    db.session.add(staff)
    db.session.commit()

    flash(f"Successfully created staff moderator account for {username} (locked to {department})!", 'success')
    return redirect('/admin')


@admin_bp.route('/staff/delete/<int:staff_id>', methods=['POST'])
@admin_required
def delete_staff(staff_id):
    admin = get_current_admin()
    if not admin or admin.role != 'admin':
        flash('Unauthorized. Only master administrators can manage staff accounts.', 'error')
        return redirect('/admin')

    staff = AdminUser.query.get_or_404(staff_id)
    if staff.role != 'staff':
        flash('Cannot delete master administrators.', 'error')
        return redirect('/admin')

    username = staff.username
    db.session.delete(staff)
    db.session.commit()

    flash(f"Successfully deleted staff moderator account for {username}.", 'success')
    return redirect('/admin')