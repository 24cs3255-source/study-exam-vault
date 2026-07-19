from flask import (
    Blueprint, render_template, request, redirect,
    url_for, flash, current_app, jsonify, session
)
from werkzeug.utils import secure_filename
from models import QuestionPaper, AdminUser, AdminNotification
from extensions import db, csrf, get_upload_path
from routes.auth import moderator_required
import os, uuid
from datetime import datetime

moderator_bp = Blueprint('moderator', __name__)

ALLOWED_EXTENSIONS = {'pdf'}
CLASS_CHOICES = ['I-year', 'II-year', 'III-year']


def allowed_file(filename: str) -> bool:
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def get_current_moderator():
    username = session.get('admin_username')
    if username:
        return AdminUser.query.filter_by(username=username).first()
    return None


# ─────────────────────────────────────────────────────────────────────────────
#  Moderator Dashboard
# ─────────────────────────────────────────────────────────────────────────────

@moderator_bp.route('/')
@moderator_required
def dashboard():
    moderator = get_current_moderator()
    dept = session.get('admin_dept', '').strip()
    dept_upper = dept.upper()

    # Papers belonging to this moderator's department (approved)
    papers = QuestionPaper.query.filter(
        db.or_(QuestionPaper.status == 'approved', QuestionPaper.status.is_(None)),
        db.func.upper(QuestionPaper.department).like(f'%{dept_upper}%')
    ).order_by(QuestionPaper.uploaded_at.desc()).all()

    # Pending student contributions for this department
    pending_papers = QuestionPaper.query.filter(
        QuestionPaper.status == 'pending',
        db.func.upper(QuestionPaper.department).like(f'%{dept_upper}%')
    ).order_by(QuestionPaper.uploaded_at.desc()).all()

    total   = len(papers)
    pending = len(pending_papers)

    return render_template(
        'moderator/dashboard.html',
        moderator=moderator,
        dept=dept,
        papers=papers,
        pending_papers=pending_papers,
        total=total,
        pending=pending,
        class_choices=CLASS_CHOICES,
    )


# ─────────────────────────────────────────────────────────────────────────────
#  Upload
# ─────────────────────────────────────────────────────────────────────────────

@moderator_bp.route('/upload', methods=['POST'])
@moderator_required
def upload():
    dept = session.get('admin_dept', '').strip()

    if 'pdf_file' not in request.files:
        flash('No file selected.', 'error')
        return redirect('/moderator')

    file = request.files['pdf_file']
    if file.filename == '':
        flash('No file selected.', 'error')
        return redirect('/moderator')

    if not allowed_file(file.filename):
        flash('Only PDF files are allowed.', 'error')
        return redirect('/moderator')

    original_name = secure_filename(file.filename)
    unique_name   = f'{uuid.uuid4().hex}_{original_name}'
    save_path     = os.path.join(current_app.config['UPLOAD_FOLDER'], unique_name)
    file.save(save_path)

    paper = QuestionPaper(
        department   = dept,
        semester     = request.form['semester'].strip(),
        subject_code = request.form['subject_code'].strip().upper(),
        subject_name = request.form['subject_name'].strip(),
        exam_type    = request.form['exam_type'].strip(),
        year         = int(request.form['year']),
        class_name   = request.form.get('class_name', '').strip() or None,
        filename     = unique_name,
        original_name= original_name,
        status       = 'approved',   # Moderator uploads go live immediately
    )
    db.session.add(paper)
    db.session.commit()
    flash('Paper uploaded and published successfully!', 'success')
    return redirect('/moderator')


# ─────────────────────────────────────────────────────────────────────────────
#  Approve / Reject student contributions
# ─────────────────────────────────────────────────────────────────────────────

@moderator_bp.route('/approve/<int:paper_id>', methods=['POST'])
@csrf.exempt
@moderator_required
def approve_paper(paper_id):
    dept = session.get('admin_dept', '').upper()
    paper = QuestionPaper.query.get_or_404(paper_id)

    if dept not in paper.department.upper():
        return jsonify({'success': False, 'error': 'Unauthorized: outside your department.'}), 403

    paper.status = 'approved'
    db.session.commit()
    return jsonify({'success': True, 'message': 'Paper approved!'})


@moderator_bp.route('/reject/<int:paper_id>', methods=['POST'])
@csrf.exempt
@moderator_required
def reject_paper(paper_id):
    dept = session.get('admin_dept', '').upper()
    paper = QuestionPaper.query.get_or_404(paper_id)

    if dept not in paper.department.upper():
        return jsonify({'success': False, 'error': 'Unauthorized: outside your department.'}), 403

    file_path = get_upload_path(paper.filename)
    try:
        if os.path.exists(file_path):
            os.remove(file_path)
    except Exception:
        pass

    db.session.delete(paper)
    db.session.commit()
    return jsonify({'success': True, 'message': 'Paper rejected and removed.'})


@moderator_bp.route('/delete/<int:paper_id>', methods=['POST'])
@csrf.exempt
@moderator_required
def delete_paper(paper_id):
    dept = session.get('admin_dept', '').upper()
    paper = QuestionPaper.query.get_or_404(paper_id)

    if dept and dept not in paper.department.upper():
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.is_json:
            return jsonify({'success': False, 'error': 'Unauthorized: outside your department.'}), 403
        flash('Unauthorized: you can only delete papers belonging to your department.', 'error')
        return redirect('/moderator')

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
    flash('Question paper deleted successfully.', 'success')
    return redirect('/moderator')


# ─────────────────────────────────────────────────────────────────────────────
#  Edit Paper
# ─────────────────────────────────────────────────────────────────────────────

@moderator_bp.route('/edit/<int:paper_id>', methods=['GET', 'POST'])
@moderator_required
def edit_paper(paper_id):
    paper = QuestionPaper.query.get_or_404(paper_id)
    dept = session.get('admin_dept', '').upper()
    
    if dept and dept not in paper.department.upper():
        flash('Unauthorized: you can only edit papers belonging to your department.', 'error')
        return redirect('/moderator')

    if request.method == 'POST':
        paper.department   = request.form['department'].strip()
        paper.semester     = request.form['semester'].strip()
        paper.subject_code = request.form['subject_code'].strip().upper()
        paper.subject_name = request.form['subject_name'].strip()
        paper.exam_type    = request.form['exam_type'].strip()
        paper.year         = int(request.form['year'])
        paper.class_name   = request.form.get('class_name', '').strip() or None

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
        return redirect('/moderator')

    return render_template('admin/edit.html', paper=paper, class_choices=CLASS_CHOICES)


# ─────────────────────────────────────────────────────────────────────────────
#  Bulk Upload (Moderator)
# ─────────────────────────────────────────────────────────────────────────────

@moderator_bp.route('/bulk-upload')
@moderator_required
def bulk_upload():
    dept = session.get('admin_dept', '').strip()
    return render_template('moderator/bulk_upload.html', class_choices=CLASS_CHOICES, dept=dept)


@moderator_bp.route('/parse-pdf', methods=['POST'])
@moderator_required
def parse_pdf():
    if 'pdf_file' not in request.files:
        return jsonify({'success': False, 'error': 'No file uploaded'}), 400
        
    file = request.files['pdf_file']
    if file.filename == '':
        return jsonify({'success': False, 'error': 'No file selected'}), 400
        
    try:
        import io
        from routes.admin import parse_pdf_metadata
        file.seek(0)
        file_bytes = file.read()
        file_stream = io.BytesIO(file_bytes)
        
        metadata = parse_pdf_metadata(file_stream, file.filename)
        return jsonify({'success': True, 'metadata': metadata})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@moderator_bp.route('/upload-ajax', methods=['POST'])
@csrf.exempt
@moderator_required
def upload_ajax():
    dept = session.get('admin_dept', '').strip()

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

        sem      = request.form.get('semester', '').strip()
        sub_code = request.form.get('subject_code', '').strip().upper()
        sub_name = request.form.get('subject_name', '').strip()
        exam_t   = request.form.get('exam_type', '').strip()
        yr       = request.form.get('year')
        cl_name  = request.form.get('class_name', '').strip() or None

        if not (sem and sub_code and sub_name and exam_t and yr):
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
            status       = 'approved',  # Moderator uploads go live immediately
        )
        db.session.add(paper)
        db.session.commit()
        return jsonify({'success': True, 'paper': paper.to_dict()})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500


# ─────────────────────────────────────────────────────────────────────────────
#  Logout
# ─────────────────────────────────────────────────────────────────────────────

@moderator_bp.route('/logout')
def logout():
    session.clear()
    flash('Logged out from Moderator Panel.', 'info')
    return redirect('/')
