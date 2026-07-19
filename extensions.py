from flask_sqlalchemy import SQLAlchemy
from flask_wtf.csrf import CSRFProtect
import os
from flask import current_app

db = SQLAlchemy()
csrf = CSRFProtect()

def get_upload_path(filename):
    """
    Return the path of an uploaded file, checking both current upload folder
    and the default repo static/uploads folder as a fallback.
    """
    folder = current_app.config.get('UPLOAD_FOLDER')
    if not folder:
        # Fallback if config is not initialized
        return os.path.join(current_app.root_path, 'static', 'uploads', filename)
        
    file_path = os.path.join(folder, filename)
    if not os.path.isfile(file_path):
        fallback_path = os.path.join(current_app.root_path, 'static', 'uploads', filename)
        if os.path.isfile(fallback_path):
            return fallback_path
    return file_path