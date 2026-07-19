# ⟨ QuestBank /⟩ — College Question Bank

A full-stack Flask application for managing and serving college question papers
with an anime-inspired glassmorphism UI, aurora effects, and dynamic filtering.

---

## 📁 Project Structure

```
question_bank/
├── app.py                    # Flask application factory
├── extensions.py             # SQLAlchemy singleton
├── models.py                 # DB schema (QuestionPaper model)
├── requirements.txt
├── seed.py                   # Optional: load sample data
│
├── routes/
│   ├── __init__.py
│   ├── main.py               # Student-facing routes + JSON API
│   └── admin.py              # Admin upload / edit / delete
│
├── templates/
│   ├── base.html             # Shared layout (aurora, starfield, navbar)
│   ├── index.html            # Student search portal
│   └── admin/
│       ├── dashboard.html    # Admin paper list
│       ├── upload.html       # Upload form with drag-and-drop
│       └── edit.html         # Edit metadata
│
└── static/
    └── uploads/
        └── pdfs/             # Uploaded PDFs stored here
```

---

## 🗄️ Database Schema

```
QuestionPaper
─────────────────────────────────────────────────────
id            INTEGER  PRIMARY KEY AUTOINCREMENT
department    TEXT     NOT NULL  INDEX   e.g. "CSE"
semester      TEXT     NOT NULL  INDEX   e.g. "3rd"
subject_code  TEXT     NOT NULL  INDEX   e.g. "CS3352"
subject_name  TEXT     NOT NULL          e.g. "Foundations of Data Science"
exam_type     TEXT     NOT NULL  INDEX   "Model Exam" | "University Semester"
year          INTEGER  NOT NULL  INDEX   e.g. 2024
filename      TEXT     NOT NULL          UUID-prefixed stored filename
original_name TEXT                       Original upload filename
uploaded_at   DATETIME          DEFAULT now()
```

---

## ⚡ Quick Start

### 1. Clone & create virtual environment
```bash
git clone <repo>
cd question_bank
python -m venv venv
source venv/bin/activate       # Windows: venv\Scripts\activate
```

### 2. Install dependencies
```bash
pip install -r requirements.txt
```

### 3. Run the app
```bash
python app.py
```
Visit → http://127.0.0.1:5000

### 4. (Optional) Seed sample data
```bash
python seed.py
```

### 5. Access admin panel
→ http://127.0.0.1:5000/admin

---

## 🔑 Key Features

| Feature | Details |
|---|---|
| **Dynamic Filtering** | Dept → Sem → Subject auto-cascade via AJAX |
| **Search** | Full-text search across name, code, department |
| **PDF Preview** | Opens in browser tab (inline) |
| **PDF Download** | Force-downloads with original filename |
| **Drag & Drop Upload** | Admin upload with metadata form |
| **Edit / Delete** | Full CRUD on paper metadata |
| **Aurora UI** | Animated aurora, starfield, city silhouette |

---

## 💾 File Storage Strategy (Best Practices)

### ✅ Recommended: Local static folder (this project)
Best for: Small-to-medium college use, single server, < 5 GB total PDFs.

```
static/uploads/pdfs/<uuid>_originalname.pdf
```
- Files served directly by Flask (`send_from_directory`)
- In production, serve with **nginx** for better performance
- Unique UUID prefix prevents filename collisions

### ☁️ Alternative: Google Drive (free, shared)
Use `google-api-python-client`. Store the Drive file ID in the DB.
Best for: Collaborative admin access, free large storage.

### 🪣 Alternative: AWS S3 / Cloudflare R2
Use `boto3`. Store the S3 object key in the DB.
Best for: Production-scale, CDN delivery, high availability.

---

## 🚀 Production Deployment

```bash
pip install gunicorn
gunicorn -w 4 -b 0.0.0.0:8000 "app:create_app()"
```

Add nginx reverse proxy + SSL (Let's Encrypt) for public access.

Set environment variable:
```bash
export SECRET_KEY="your-very-secure-random-key"
```
